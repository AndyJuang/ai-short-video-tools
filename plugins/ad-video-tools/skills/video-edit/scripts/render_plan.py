#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10", "PyYAML>=6", "jieba>=0.42", "opencc-python-reimplemented>=0.1.7", "budoux>=0.6"]
# ///
"""Render an ACOS edit plan to a finished short (ACOS Path C): 9:16, or 4:5 / 1:1 for feed.

    uv run render_plan.py <edit_plan.yaml> <asset_dir> [--out PATH] [--lang tc] [--brand brand.json] [--ratio 4x5]
    uv run render_plan.py <edit_plan.yaml> <asset_dir> --picks     # one frame per scene, for review
    uv run render_plan.py <edit_plan.yaml> <asset_dir> --check <out>.mp4   # transcribe it, diff vs the plan
    uv run render_plan.py --self-test

The plan's format is references/edit-plan.md (from ACOS; content-tools owns it there),
the delivery constants references/shorts-output-spec.md. This file implements
them; it does not redefine them.

Writes, beside the output (default: next to the plan, <content_id>_v<n>.mp4,
n = the next free number):
    <out>.mp4         1080x1920 (or 1080x1350 / 1080x1080), brand logo if brand.json has one, 30 fps, H.264 crf 18, AAC 192k, -14 LUFS / -1.5 dBTP
    <out>.srt         one cue per caption
    <out>.qc.json     dimensions, fps, measured loudness, caption safe-area check,
                      generated (scenes made by generate.py), warnings
    <out>.sheet.jpg   one frame per second, six across

Beyond the ACOS schema (see SKILL.md Phase 4): `captions: [...]` on a scene, `kind: card`
scenes drawn from brand.json, `voiceover.cuts` / `room_tone` / `gap` / `tail`, and
`pace: sns` / `sync: {snap: beat|bar}` (unvoiced cuts land on the bgm's beat grid).
`fx: fade|type|pop` on a caption.

A missing timeline asset fails before anything renders. Everything else that is
unknown or missing — an animation name, an sfx file, the bgm — takes a fallback
and is listed in `warnings`.
"""
import argparse
import difflib
import functools
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "video-style-clone", "scripts"))
import build  # noqa: E402  font table, fitted(), missing_glyphs(), has_audio(), run()
import beats  # noqa: E402  grid() runs librosa in its own uv env
from ffsafe import inside, media, sdr  # noqa: E402  refuse playlist/list files posing as media; HDR->SDR

W, H, FPS = 1080, 1920, 30
RATIOS = ("9x16", "4x5", "1x1")
LOGO_H, LOGO_MAX_W = 90, 360


def set_canvas(ratio):
    """Canvas and safe area for `ratio`. 9:16 is Reels/Stories: keep clear of the
    top header and the right-hand action rail. 4:5 and 1:1 are feed: no UI over
    the frame, only margins."""
    global H, CX, SAFE, CAPTION_BOTTOM, MAX_W
    H, CX = build.ratio_height(W, ratio), W // 2
    SAFE = ({"bottom": round(H * 0.85), "right": W - 150, "left": 60, "top": 220} if ratio == "9x16"
            else {"bottom": H - 60, "right": W - 60, "left": 60, "top": 60})
    CAPTION_BOTTOM = round(H * 0.78) if ratio == "9x16" else round(H * 0.88)
    MAX_W = 2 * (SAFE["right"] - CX)  # centred, so the right rail binds both sides


set_canvas("9x16")
FADE = 0.2                            # dip length either side of a `fade` cut
STYLES = {"hook": 104, "body": 76, "cta": 88, "info_card": 68}
LEGACY_STYLES = {"bold_white_outline": "body"}
ANIMATIONS = ("zoom_in_punch", "ken_burns", "pan_fast_l", "pan_fast_r", "shake", "none")
TRANSITIONS = ("hard_cut", "fade")
CAPTION_FX = ("fade", "type", "pop")
POP = (1.3, 5)                        # fx: pop starts at 1.3x and settles to 1.0 over 5 frames
TYPE_STEP = 0.1                       # fx: type, seconds per character without a beat grid
DEFAULT_ACCENT = "#FFD400"            # ACOS「黃字強調」when no brand.json says otherwise
# Music under speech/clip audio. Hard compression into mix=0.75 pins the duck
# at ≈ -12 dB (the dry 25% floor) whatever the speech level; 400 ms recovery.
DUCK = "threshold=0.003:ratio=20:attack=20:release=400:mix=0.75"
CLIP_UNDER_VO = 0.3
MIN_SHOT = 0.5                        # sync.snap never leaves a shot shorter than this
VO_GAP, VO_TAIL = 0.15, 0.4           # defaults for voiceover.gap / voiceover.tail
JOIN_IN, JOIN_OUT = 0.04, 0.06        # fades at each join of a cut-list voiceover
NEAR = 120                            # RGB distance under which two colours read as one


def ffprobe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "json", media(path)], capture_output=True, encoding="utf-8",
                         errors="replace").stdout
    return json.loads(out or "{}")


def duration(path):
    return float(ffprobe(path).get("format", {}).get("duration") or 0)


def is_still(path):
    return path.lower().endswith(build.IMAGE_EXT)


def next_out(plan_path, content_id):
    """Beside the plan — except in an init-video project, where a plan in
    input/<video>/ renders to output/<video>_v<n>.mp4 like every other cut."""
    vdir = os.path.dirname(os.path.abspath(plan_path))
    if os.path.basename(os.path.dirname(vdir)) == "input":
        base = os.path.join(os.path.dirname(os.path.dirname(vdir)), "output", os.path.basename(vdir))
    else:
        base = os.path.join(vdir, content_id)
    n = 1
    while os.path.exists(f"{base}_v{n}.mp4"):
        n += 1
    return f"{base}_v{n}.mp4"


# ---- validation -------------------------------------------------------------
def plan_file(asset_dir, rel):
    """A path from the plan: relative to the material folder, and inside it or inside the
    project the scripts run from (so ../../assets/sfx/whoosh.wav works), nowhere else."""
    from transcribe import project_root
    return inside(os.path.join(asset_dir, str(rel)), (asset_dir, project_root()), f"plan path {rel!r}")


def load(plan_path, asset_dir):
    """The plan, its scenes in order, and the warnings so far. Exits on what
    cannot be rendered around: a missing timeline asset or an empty scene."""
    plan = yaml.safe_load(open(plan_path, encoding="utf-8"))
    scenes = plan.get("timeline") or []
    if not scenes:
        sys.exit(f"{plan_path}: empty timeline")
    warns, missing, prev = [], [], 0.0
    for sc in scenes:
        n = sc.get("scene", "?")
        f = plan_file(asset_dir, str(sc.get("asset_file") or ""))
        if sc.get("kind") == "card":
            pass                      # drawn from brand.json at render time
        elif not sc.get("asset_file") or not os.path.isfile(f):
            missing.append(f"scene {n}: {sc.get('asset_file')!r}")
        if float(sc["end_sec"]) <= float(sc["start_sec"]):
            sys.exit(f"scene {n}: end_sec {sc['end_sec']} is not after start_sec {sc['start_sec']}")
        if abs(float(sc["start_sec"]) - prev) > 0.5 / FPS:
            warns.append(f"scene {n} starts at {sc['start_sec']}s, previous ended at {prev}s — "
                         f"scenes are laid end to end by their durations")
        prev = float(sc["end_sec"])
    for c in (plan.get("voiceover") or {}).get("cuts") or []:
        if "pause" not in c and not os.path.isfile(plan_file(asset_dir, str(c.get("src") or ""))):
            missing.append(f"voiceover cut: {c.get('src')!r}")
    if missing:
        sys.exit("missing required assets in " + asset_dir + ":\n  " + "\n  ".join(missing))
    total = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in scenes)
    if plan.get("total_duration_sec") and abs(float(plan["total_duration_sec"]) - total) > 0.5:
        warns.append(f"total_duration_sec {plan['total_duration_sec']} but the timeline sums to {total:.2f}s")
    return plan, scenes, warns


def beat_grid(plan, bgm, warns):
    """The grid sync.snap cuts to: the bgm's beats or bars, in cut time (the bgm plays
    from 0). sync.snap defaults to "beat" under pace: sns. [] when off or beatless."""
    mode = (plan.get("sync") or {}).get("snap") or ("beat" if plan.get("pace") == "sns" else None)
    if not mode:
        return []
    if mode not in ("beat", "bar"):
        warns.append(f"sync.snap {mode!r} is not beat or bar — unsnapped")
        return []
    if not bgm:
        warns.append("sync.snap is set but there is no bgm to take a beat from — unsnapped")
        return []
    # ponytail: re-analysed every render (seconds); cache by bgm hash as build.beat_grid does if it drags
    grid = beats.grid(bgm)[{"beat": "beats", "bar": "bars"}[mode]]
    if not grid:
        warns.append(f"no beat found in {os.path.basename(bgm)} — unsnapped")
    return grid


def snapped(t, dur, grid):
    """The length of a scene starting at t, planned `dur` long, whose end moves onto
    `grid` (sorted cut times): the first point at or after the planned end, so a scene
    only ever grows. At least MIN_SHOT, ending on a whole frame; past the grid, unmoved."""
    want = t + max(dur, MIN_SHOT)
    end = next((g for g in grid if g >= want - 0.5 / FPS), want)
    return round(end * FPS) / FPS - t


def retime(plan, scenes, asset_dir, warns, grid=()):
    """Fit each voiced scene to its voiceover.lines audio (tts.py output, default
    vo/<id>.mp3): the lines play back to back from the scene's start, and the scene
    lasts until the last one ends plus the tail (voiceover.gap / .tail, default VO_GAP /
    VO_TAIL). Unvoiced scenes keep their length, or end on `grid` (sync.snap) — the VO
    owns a voiced scene's length, so snapping only shifts it. Captions keep their place
    in the scene, scaled with it. Mutates `scenes`.
    Returns ([(audio, at, dur, text)], [{scene, planned, voiced | snapped}])."""
    vo, by_scene = plan.get("voiceover") or {}, {}
    gap, tail = float(vo.get("gap", VO_GAP)), float(vo.get("tail", VO_TAIL))
    for ln in vo.get("lines") or []:
        p = plan_file(asset_dir, ln.get("asset_file") or f"vo/{ln['id']}.mp3")
        if os.path.isfile(p):
            by_scene.setdefault(ln.get("scene"), []).append((p, duration(p), ln.get("text", "")))
        else:
            warns.append(f"voiceover line {ln.get('id')}: {os.path.relpath(p, asset_dir)} not found "
                         f"— run tts.py; line skipped")
    placed, moved, t = [], [], 0.0
    for sc in scenes:
        a0, b0 = float(sc["start_sec"]), float(sc["end_sec"])
        ls = by_scene.get(sc.get("scene"), [])
        dur = sum(d for _, d, _ in ls) + gap * (len(ls) - 1) + tail if ls else b0 - a0
        at = t
        for p, d, text in ls:
            placed.append((p, at, d, text))
            at += d + gap
        if ls:
            sc["_vo"] = placed[-len(ls):]
            moved.append({"scene": sc.get("scene"), "planned": round(b0 - a0, 2), "voiced": round(dur, 2)})
        elif grid:
            dur = snapped(t, dur, grid)
            if abs(dur - (b0 - a0)) > 0.5 / FPS:
                moved.append({"scene": sc.get("scene"), "planned": round(b0 - a0, 2), "snapped": round(dur, 2)})
        for cap in [sc.get("caption"), *(sc.get("captions") or [])]:
            for k in ("start_sec", "end_sec"):
                if isinstance(cap, dict) and k in cap:
                    cap[k] = t + (float(cap[k]) - a0) * dur / (b0 - a0)
        sc["start_sec"], sc["end_sec"] = t, t + dur
        t += dur
    return placed, moved


def words_of(src):
    """transcribe.py's raw words for `src` (beside it, or in .cues/ beside it), in source
    seconds; [] when it was never transcribed. Spaces are dropped; zero-length words are
    kept, as Scribe gives real characters zero length too (个 6.70–6.70), and snap_cut's
    strict bounds never move a cut for one."""
    d, stem = os.path.dirname(src), os.path.splitext(os.path.basename(src))[0]
    for p in (os.path.join(d, ".cues", stem + ".transcript.json"), os.path.join(d, stem + ".transcript.json")):
        if os.path.isfile(p):
            return [w for w in json.load(open(p, encoding="utf-8")) if w.get("text", "").strip()]
    return []


def snap_cut(a, b, words):
    """(in, out) moved off any word they land inside, so a cut never keeps part of a
    syllable. `words` are [{start, end, text}] in source seconds, in order."""
    for w in words:
        if w["start"] < a < w["end"]:
            a = w["start"]          # keep the word the line opens on
        if w["start"] < b < w["end"]:
            b = w["start"]          # drop the word the cut runs into
    return a, b


def piece(args, dur, out, fade=True):
    """One stretch of the voiceover as 48 kHz stereo wav, exactly `dur` long."""
    af = "aresample=48000,aformat=channel_layouts=stereo,apad"
    if fade:
        af += f",afade=t=in:d={JOIN_IN},afade=t=out:st={max(dur - JOIN_OUT, 0):.3f}:d={JOIN_OUT}"
    build.run(["ffmpeg", "-v", "error", "-y", *args, "-vn", "-af", af, "-t", f"{dur}",
               "-c:a", "pcm_s16le", out])
    return out


def room_tone(vo, asset_dir, work):
    """voiceover.room_tone {src, in, out} as a wav to loop, or None."""
    rt = vo.get("room_tone")
    if not rt:
        return None
    src = plan_file(asset_dir, str(rt.get("src")))
    if not os.path.isfile(src):
        sys.exit(f"voiceover.room_tone: {rt.get('src')!r} not found in {asset_dir}")
    a, b = float(rt["in"]), float(rt["out"])
    return piece(["-ss", f"{a}", "-t", f"{b - a}", "-i", media(src)], b - a, os.path.join(work, "tone.wav"), fade=False)


def cut_track(vo, asset_dir, work, warns):
    """voiceover.cuts laid end to end into one track: source ranges snapped to word edges,
    pauses filled with room tone (never digital silence), short fades only at the joins.
    Returns (wav, [{src, in, out, at, end} | {pause, at, end}]) — `at`/`end` are track time,
    what scenes and captions are timed against."""
    tone, parts, rows, at = room_tone(vo, asset_dir, work), [], [], 0.0
    for k, c in enumerate(vo["cuts"]):
        out = os.path.join(work, f"vo{k:02d}.wav")
        if "pause" in c:
            dur = float(c["pause"])
            if tone:
                parts.append(piece(["-stream_loop", "-1", "-i", media(tone)], dur, out))
            else:
                warns.append(f"voiceover pause {dur}s is digital silence — set voiceover.room_tone")
                parts.append(piece(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"], dur, out))
            rows.append({"pause": dur, "at": round(at, 3), "end": round(at + dur, 3)})
        else:
            src = plan_file(asset_dir, c["src"])
            a, b = snap_cut(float(c["in"]), float(c["out"]), words_of(src))
            if b <= a:
                sys.exit(f"voiceover cut {c['src']} {c['in']}–{c['out']}: nothing left after snapping to words")
            dur = b - a
            parts.append(piece(["-ss", f"{a}", "-t", f"{dur}", "-i", media(src)], dur, out))
            rows.append({"src": c["src"], "in": round(a, 3), "out": round(b, 3),
                         "at": round(at, 3), "end": round(at + dur, 3)})
        at += dur
    listfile, track = os.path.join(work, "vo.txt"), os.path.join(work, "vo.wav")
    with open(listfile, "w", encoding="utf-8") as fh:
        fh.writelines("file '" + p.replace("\\", "/") + "'\n" for p in parts)
    build.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listfile, "-c", "copy", track])
    return track, rows


def enum(value, allowed, fallback, what, warns, aliases=None):
    value = (aliases or {}).get(value, value)
    if value in (None, ""):
        return fallback
    if value not in allowed:
        warns.append(f"{what}: unknown {value!r}, rendered as {fallback!r}")
        return fallback
    return value


# ---- captions ---------------------------------------------------------------
def runs(line, emphasis):
    """[(text, emphasised)] — longest emphasis word first, so 「曬傷」 inside
    「曬傷了」 doesn't split a longer listed word."""
    words = sorted({w for w in emphasis or [] if w}, key=len, reverse=True)
    if not words:
        return [(line, False)]
    parts = re.split("(" + "|".join(map(re.escape, words)) + ")", line)
    return [(p, p in words) for p in parts if p]


LATIN_DOT = re.compile(r"(?<=[A-Za-z0-9])·(?=[A-Za-z0-9])")   # KA·RA·DA


def dot_kern(d, fnt):
    """How much narrower a middle dot between Latin letters draws than the face's own:
    CJK faces give U+00B7 most of an em, so KA·RA·DA reads K A · R A. Kerned to a period."""
    return d.textlength("·", font=fnt) - d.textlength(".", font=fnt)


def tlen(d, text, fnt):
    """textlength as caption_png draws it."""
    n = len(LATIN_DOT.findall(text))
    return d.textlength(text, font=fnt) - (n and n * dot_kern(d, fnt))


PUNCT = " ，、。！？,.!?：:；;"
NO_START = "，、。！？,.!?：:；;」』）)】》…ー～"      # kinsoku: never begins a line
NO_END = "「『（(【《"                                  # never ends one


@functools.lru_cache(maxsize=None)
def segmenter(lang):
    """A function text -> words, for the languages written without spaces."""
    if lang in ("tc", "sc"):
        import logging
        import warnings
        warnings.filterwarnings("ignore", category=SyntaxWarning)   # jieba's old regex literals
        import jieba
        jieba.setLogLevel(logging.ERROR)
        return jieba.cut
    if lang == "jp":
        import budoux
        return budoux.load_default_japanese_parser().parse
    return None


@functools.lru_cache(maxsize=None)
def t2s():
    import opencc
    return opencc.OpenCC("t2s").convert


def word_breaks(text, lang):
    """Indices i where text[:i] / text[i:] splits no word. Chinese: jieba on the text
    and on its Simplified reading (its dictionary is Simplified), kept only where both
    agree — each alone splits words the other keeps (曬|傷, 台|灣). Japanese: BudouX
    phrases. Anything else: spaces."""
    def edges(words):
        out, i = set(), 0
        for w in words:
            i += len(w)
            out.add(i)
        return out
    cut = segmenter(lang)
    if cut is None:
        b = {i for i, c in enumerate(text) if c == " "}
    else:
        b = edges(cut(text))
        if lang == "tc":
            simple = t2s()(text)
            if len(simple) == len(text):
                b &= edges(cut(simple))
    return sorted(i for i in b if 0 < i < len(text)
                  and text[i] not in NO_START and text[i - 1] not in NO_END)


def layout(d, face, text, size, lang="tc"):
    """(lines, font): one line if it fits at the preset size or a little under,
    else two lines at a word break near the middle (a punctuation break wins
    when it is within about two characters of it), then shrunk until both fit.
    A newline in the text is an explicit break and is kept as written."""
    stroke = lambda s: 2 * round(s * 0.115)   # noqa: E731
    fits = lambda t, s: tlen(d, t, build.font(face, s)) + stroke(s) <= MAX_W  # noqa: E731
    if "\n" in text:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
    else:
        for s in range(size, round(size * 0.85) - 1, -2):
            if fits(text, s):
                return [text], s
        width = lambda t: tlen(d, t.strip(), build.font(face, size))  # noqa: E731
        i = min(word_breaks(text, lang) or range(1, len(text)),
                key=lambda i: max(width(text[:i]), width(text[i:])) - 2 * size * (text[i - 1] in PUNCT))
        lines = [text[:i].strip(), text[i:].strip()]
    s = size
    while s > 8 and not all(fits(l, s) for l in lines):
        s -= 2
    return lines, s


def caption_png(path, text, style, emphasis, face, accent, lang="tc", upto=None):
    """Full-frame RGBA, block bottom at 78% H. Returns (font size, alpha bbox).
    `upto` draws only the first n characters — one frame of fx: type; the layout and
    size are the full caption's, so the text neither moves nor resizes as it types."""
    size = STYLES[style]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lines, s = layout(d, face, text, size, lang)
    fnt, sw = build.font(face, s), round(s * 0.115)
    lead = round(s * 1.25)
    top = CAPTION_BOTTOM - lead * (len(lines) - 1) - s - sw
    if style == "info_card":
        widest = max(tlen(d, l, fnt) for l in lines)
        d.rounded_rectangle([CX - widest / 2 - 36, top - 28, CX + widest / 2 + 36,
                             CAPTION_BOTTOM + 20], radius=24, fill=(0, 0, 0, 150))
    kern = dot_kern(d, fnt)
    left = math.inf if upto is None else upto
    for k, line in enumerate(lines):
        y = top + k * lead - d.textbbox((0, 0), line, font=fnt)[1] + sw
        x, pos = CX - tlen(d, line, fnt) / 2, 0
        for seg, hot in runs(line, emphasis):
            for part in filter(None, re.split("(·)", seg)):
                kk = kern if LATIN_DOT.match(line, pos) else 0
                shown, left = part[:max(0, left)] if left < len(part) else part, left - len(part)
                d.text((x - kk / 2, y), shown, font=fnt, fill=accent if hot else "#FFFFFF",
                       stroke_width=sw, stroke_fill=(0, 0, 0, 255))
                x += d.textlength(part, font=fnt) - kk
                pos += len(part)
    im.save(path)
    return s, im.getchannel("A").getbbox()


def caption_track(stem, png, a, b, fx, draw, n_chars, grid):
    """fx type / pop as a transparent 30 fps video: nothing until `a`, the effect's frames,
    then the finished caption until `b`. Encoded on its own, dense from 0: a sparse concat
    input (one long blank entry, or one shifted by -itsoffset) stalls ffmpeg's input
    scheduler beside the scene concat, and the overlay never sees the caption.
    `draw(path, upto)` renders a partial caption. Type steps one character per 16th note of
    the grid (TYPE_STEP without one), squeezed so typing ends by 60% of the caption's time."""
    frames = []
    if fx == "type":
        beat = (grid[-1] - grid[0]) / (len(grid) - 1) if len(grid) > 1 else 0
        step = min(beat / 4 if beat else TYPE_STEP, (b - a) * 0.6 / max(n_chars, 1))
        for k in range(1, n_chars):
            draw(f := f"{stem}_t{k:03d}.png", k)
            frames.append((f, step))
    else:                                         # pop: scaled about the caption's centre
        im = Image.open(png)
        box = im.getchannel("A").getbbox()
        if box:
            ink, (cx, cy) = im.crop(box), ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            for k in range(POP[1]):
                z = POP[0] - (POP[0] - 1) * k / POP[1]
                big = ink.resize((round(ink.width * z), round(ink.height * z)), Image.LANCZOS)
                fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                fr.paste(big, (round(cx - big.width / 2), round(cy - big.height / 2)))
                fr.save(f := f"{stem}_p{k}.png")
                frames.append((f, 1 / FPS))
    hold = max(b - a - sum(d for _, d in frames), 1 / FPS)
    entries = frames + [(png, hold)]
    q = lambda f: "file '" + f.replace("\\", "/").replace("'", "'\\''") + "'\n"  # noqa: E731
    with open(f"{stem}.txt", "w", encoding="utf-8") as fh:
        fh.writelines(q(f) + f"duration {d:.4f}\n" for f, d in entries)
        fh.write(q(png))                          # the demuxer drops the last duration unless repeated
    build.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{stem}.txt",
               "-vf", f"fps={FPS},format=argb,tpad=start_duration={a:.3f}:color=0x00000000",
               "-c:v", "qtrle", f"{stem}.mov"])
    return f"{stem}.mov"


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ---- picture ----------------------------------------------------------------
def scene_captions(sc, asset_dir):
    """The scene's captions: `caption`, or a `captions` list (each with its own times and
    emphasis — two beats over one picture, without a cut restarting its move). A scene with
    `cues: <x>.cues.json` (transcribe.py output, in source time) and neither gets one caption
    per spoken cue inside its source range; failing that, a voiced scene gets one caption
    per line, timed to its audio."""
    if sc.get("caption") or sc.get("captions"):
        return [c for c in [sc.get("caption"), *(sc.get("captions") or [])] if c]
    if not sc.get("cues"):
        return [{"text": text, "style": "body", "start_sec": at, "end_sec": at + d}
                for _, at, d, text in sc.get("_vo", []) if text]
    lo, t0 = float(sc.get("source_in_sec") or 0), float(sc["start_sec"])
    hi = lo + float(sc["end_sec"]) - t0
    cues = json.load(open(plan_file(asset_dir, sc["cues"]), encoding="utf-8"))
    return [{"text": c["text"], "style": "body", "start_sec": t0 + max(c["start"], lo) - lo,
             "end_sec": t0 + min(c["end"], hi) - lo}
            for c in cues if min(c["end"], hi) - max(c["start"], lo) > 0.1]


def zoom(anim, frames):
    """zoompan on 2x-upscaled frames (no stair-stepping). `on` is the output
    frame number, so the move is the same at any source frame rate."""
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    sweep = "(iw-iw/zoom)*(1-pow(1-min(on/12,1),2))"       # whole slack in 12 frames, eased out
    jolt = "40*sin(on*2.4)*max(0,1-on/10)"                 # an impact that dies out in 10 frames
    z, x, y = {"zoom_in_punch": ("min(1+0.12*on/8,1.12)", cx, cy),
               "ken_burns": (f"1+0.06*on/{max(frames - 1, 1)}", cx, cy),
               "pan_fast_r": ("1.2", sweep, cy),
               "pan_fast_l": ("1.2", f"(iw-iw/zoom)-{sweep}", cy),
               "shake": ("1.08", f"{cx}+{jolt}", f"{cy}+{jolt}*0.7")}[anim]
    return (f"scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':"
            f"y='{y}':d=1:s={W}x{H}:fps={FPS}")


def segment(i, path, ranges, dur, anim, fade_in, fade_out, clip_gain, work, speed=1.0):
    """One full-frame scene with its own sound (or silence): the source `ranges`
    [(in, out)] laid end to end (a jump cut when there are several), played at `speed`,
    and padded or trimmed to exactly `dur` so a short source can never shift the scenes
    after it."""
    out = os.path.join(work, f"seg{i:02d}.mov")
    frames = round(dur * FPS)
    still = is_still(path)
    if still:
        ranges, speed = [(0.0, dur)], 1.0
    path = media(path)
    vin = (["-loop", "1", "-t", f"{dur}", "-i", path] if still
           else [x for a, b in ranges for x in ("-ss", f"{a}", "-t", f"{b - a}", "-i", path)])
    audio, k = not still and build.has_audio(path), len(ranges)
    # setpts then fps, both before zoompan: zoompan d=1 emits one frame per input frame,
    # so a 60 fps or sped-up source would otherwise play at the wrong speed.
    pre = [f"[{j}:v]{sdr(path)}setpts=(PTS-STARTPTS)/{speed},fps={FPS}[v{j}]" for j in range(k)]
    pre += [f"[{j}:a]atempo={speed},asetpts=PTS-STARTPTS[a{j}]" for j in range(k) if audio]
    pre.append("".join(f"[v{j}]" + f"[a{j}]" * audio for j in range(k))
               + f"concat=n={k}:v=1:a={int(audio)}[cv]" + "[ca]" * audio)
    v = (";".join(pre) + f";[cv]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
         f"tpad=stop_mode=clone:stop_duration={dur}")
    if anim != "none":
        v += "," + zoom(anim, frames)
    if fade_in:
        v += f",fade=t=in:st=0:d={FADE}"
    if fade_out:
        v += f",fade=t=out:st={dur - FADE:.3f}:d={FADE}"
    a = "[ca]" if audio else f"[{k}:a]"
    af = f"{a}aresample=48000,aformat=channel_layouts=stereo,volume={clip_gain},apad"
    if fade_in:
        af += f",afade=t=in:st=0:d={FADE}"
    if fade_out:
        af += f",afade=t=out:st={dur - FADE:.3f}:d={FADE}"
    build.run(["ffmpeg", "-v", "error", "-y", *vin,
               "-f", "lavfi", "-t", f"{dur}", "-i", "anullsrc=r=48000:cl=stereo",
               "-filter_complex", f"{v},setsar=1,format=yuv420p[v];{af}[a]",
               "-map", "[v]", "-map", "[a]", "-frames:v", str(frames), "-t", f"{dur}",
               "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-r", str(FPS),
               "-c:a", "pcm_s16le", out])
    return out


# ---- QC ---------------------------------------------------------------------
def loudness(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", media(path),
                          "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, encoding="utf-8", errors="replace").stderr
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    num = lambda m: float(m.group(1)) if m and m.group(1) != "-inf" else None  # noqa: E731
    return num(i), num(p)


def safe_area(bbox):
    if not bbox:
        return []
    l, t, r, b = bbox
    return [msg for bad, msg in ((b > SAFE["bottom"], f"bottom {b} > {SAFE['bottom']} (85% H)"),
                                 (r > SAFE["right"], f"right {r} > {SAFE['right']} (UI rail)"),
                                 (l < SAFE["left"], f"left {l} < {SAFE['left']}"),
                                 (t < SAFE["top"], f"top {t} < {SAFE['top']}")) if bad]


# ---- render -----------------------------------------------------------------
def brand_logo(brand_path, lang, max_w, max_h, warns):
    """brand.json's logo for `lang` (or default), trimmed to its ink and fitted inside
    max_w x max_h, or None. Upscaling past 1.2x is warned: it shows as soft edges."""
    logos = build.BRAND.get("logo") or {}
    rel = logos.get(lang, logos.get("default")) if isinstance(logos, dict) else logos
    src = rel and brand_path and os.path.join(os.path.dirname(os.path.abspath(brand_path)), rel)
    if not src or not os.path.isfile(src):
        return None
    logo = Image.open(src).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())
    s = min(max_h / logo.height, max_w / logo.width)
    if s > 1.2:
        warns.append(f"logo {rel} is {logo.width}x{logo.height}, upscaled {s:.1f}x — supply a larger one")
    return logo.resize((round(logo.width * s), round(logo.height * s)), Image.LANCZOS)


def logo_png(path, brand_path, lang, warns):
    """The logo top-centre inside the safe area, as one full-frame overlay, or None."""
    logo = brand_logo(brand_path, lang, LOGO_MAX_W, LOGO_H, warns)
    if not logo:
        return None
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    im.alpha_composite(logo, ((W - logo.width) // 2, SAFE["top"]))
    im.save(path)
    return path


def rgb(hex_):
    return ImageColor.getrgb(hex_)[:3]


def near(c1, c2):
    return math.dist(rgb(c1), rgb(c2)) < NEAR


def card_spec(sc):
    """brand.json `card` defaults under the scene's own `card` keys."""
    pal = build.BRAND.get("palette") or {}
    spec = {"bg": pal.get("accent") or DEFAULT_ACCENT, "signoff": "", "cta": "", "cta_color": "#FFFFFF",
            **(build.BRAND.get("card") or {}), **(sc.get("card") or {})}
    light = sum(rgb(spec["bg"])) > 3 * 186
    spec["ink"] = (pal.get("ink") or "#1A1A1A") if light else "#FFFFFF"
    return spec


def card_png(path, sc, brand_path, lang, faces, warns):
    """A brand end card: background colour, the logo, the sign-off line and a CTA pill,
    stacked a little above centre, clear of the caption at the bottom. A logo whose
    colour is the background's (an accent logo on an accent card) is knocked out to the
    text colour."""
    spec, (heavy, body) = card_spec(sc), faces
    im = Image.new("RGBA", (W, H), spec["bg"])
    d = ImageDraw.Draw(im)
    y = round(H * 0.3)
    logo = brand_logo(brand_path, lang, round(MAX_W * 0.8), round(H * 0.1), warns)
    if logo:
        alpha = logo.getchannel("A")
        ink = ImageStat.Stat(logo.convert("RGB"), mask=alpha.point(lambda v: 255 * (v > 128))).mean
        if math.dist(ink, rgb(spec["bg"])) < NEAR:
            logo = Image.new("RGBA", logo.size, spec["ink"])
            logo.putalpha(alpha)
        im.alpha_composite(logo, ((W - logo.width) // 2, y))
        y += logo.height + 60
    if spec["signoff"]:
        fnt = build.fitted(d, body, spec["signoff"], 56, MAX_W)
        build.centered(d, y, spec["signoff"], fnt, spec["ink"], CX)
        y += fnt.size + 90
    if spec["cta"]:
        fnt = build.fitted(d, heavy, spec["cta"], 64, MAX_W * 0.7)
        bw, bh = d.textlength(spec["cta"], font=fnt) + 140, fnt.size + 52
        d.rounded_rectangle([CX - bw / 2, y, CX + bw / 2, y + bh], radius=bh / 2, fill=spec["cta_color"])
        label = spec["bg"] if near(spec["cta_color"], "#FFFFFF") else "#FFFFFF"
        build.centered(d, y + 26, spec["cta"], fnt, label, CX)
    im.convert("RGB").save(path)
    return path


def render(plan_path, asset_dir, out=None, lang="tc", brand_path=None, ratio="9x16"):
    set_canvas(ratio)
    plan, scenes, warns = load(plan_path, asset_dir)

    def opt_asset(block, what):
        f = (plan.get(block) or {}).get("asset_file")
        if not f:
            return None
        p = plan_file(asset_dir, f)
        if os.path.isfile(p):
            return p
        warns.append(f"{what} {f!r} not found — rendered without it")
        return None

    bgm = opt_asset("bgm", "bgm")
    grid = beat_grid(plan, bgm, warns)
    lines, moved = retime(plan, scenes, asset_dir, warns, grid)
    lang = plan.get("lang", lang)
    brand_path = brand_path or ("brand.json" if os.path.exists("brand.json") else None)
    build.BRAND = json.load(open(brand_path, encoding="utf-8")) if brand_path else {}
    accent = build.BRAND.get("palette", {}).get("accent") or DEFAULT_ACCENT
    heavy, body = build.resolve_faces(lang)
    cards = [card_spec(sc) for sc in scenes if sc.get("kind") == "card"]
    text = "".join(c.get("text", "") for sc in scenes for c in scene_captions(sc, asset_dir))
    bad = (build.missing_glyphs(heavy, text + "".join(c["cta"] for c in cards))
           + build.missing_glyphs(body, "".join(c["signoff"] for c in cards)))
    if bad:
        sys.exit(f"{os.path.basename(heavy[0])} cannot draw: {' '.join(bad)} — install a face for {lang!r}")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")

    cid = re.sub(r"[\\/]", "_", str(plan.get("content_id") or "render"))   # a filename, never a path
    out = out or next_out(plan_path, cid if ratio == "9x16" else f"{cid}_{ratio}")
    stem = out[:-4] if out.endswith(".mp4") else out
    from transcribe import writable    # the render may sit in a material folder
    for p in (out, stem + ".srt", stem + ".qc.json", stem + ".sheet.jpg"):
        writable(p)
    out = stem + ".mp4"
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    work = tempfile.mkdtemp(prefix="render-plan-")

    vob = plan.get("voiceover") or {}
    vo, vo_cuts, tone = None, [], None
    if lines:
        if vob.get("asset_file") or vob.get("cuts"):
            warns.append("voiceover.asset_file / cuts ignored: the voiced lines replace them")
        tone = room_tone(vob, asset_dir, work)          # under the lines, so their gaps aren't dead air
    elif vob.get("cuts"):
        if vob.get("asset_file"):
            warns.append("voiceover.asset_file ignored: voiceover.cuts builds the track")
        vo, vo_cuts = cut_track(vob, asset_dir, work, warns)
    else:
        vo = opt_asset("voiceover", "voiceover")

    trans = [enum(sc.get("transition_in"), TRANSITIONS, "hard_cut",
                  f"scene {sc.get('scene')} transition_in", warns) for sc in scenes]
    segs, caps, sfx, srt, qc_caps, card_at, t0 = [], [], [], [], [], [], 0.0
    for i, sc in enumerate(scenes):
        n, dur = sc.get("scene", i + 1), float(sc["end_sec"]) - float(sc["start_sec"])
        hot = accent
        if sc.get("kind") == "card":
            path = card_png(os.path.join(work, f"card{i:02d}.png"), sc, brand_path, lang, (heavy, body), warns)
            spec = card_spec(sc)
            hot = "#FFFFFF" if near(accent, spec["bg"]) else accent   # accent words vanish on an accent card
            card_at.append((t0, t0 + dur))
        else:
            path = plan_file(asset_dir, sc["asset_file"])
        anim = enum(sc.get("animation"), ANIMATIONS, "none", f"scene {n} animation", warns)
        src_in = float(sc.get("source_in_sec") or 0)
        speed = float(sc.get("speed") or 1)
        if not 0.5 <= speed <= 4:
            warns.append(f"scene {n}: speed {speed} outside 0.5–4 — clamped")
            speed = min(max(speed, 0.5), 4)
        ranges = ([(float(c["in"]), float(c["out"])) for c in sc["cuts"]] if sc.get("cuts")
                  else [(src_in, src_in + dur * speed)])
        if not is_still(path):
            if max(b for _, b in ranges) > duration(path) + 0.05:
                warns.append(f"scene {n}: {sc['asset_file']} ends at {duration(path):.2f}s, before "
                             f"{max(b for _, b in ranges):.2f}s — last frame held")
            played = sum(b - a for a, b in ranges) / speed
            if sc.get("cuts") and abs(played - dur) > 1 / FPS:
                warns.append(f"scene {n}: cuts play {played:.2f}s in a {dur:.2f}s scene — "
                             + ("last frame held" if played < dur else "trimmed"))
        fade_out = i + 1 < len(trans) and trans[i + 1] == "fade"
        segs.append(segment(i, path, ranges, dur, anim, trans[i] == "fade", fade_out,
                            CLIP_UNDER_VO if vo or lines else 1.0, work, speed))

        for j, cap in enumerate(scene_captions(sc, asset_dir)):
            style = enum(cap.get("style"), STYLES, "body", f"scene {n} caption.style", warns,
                         LEGACY_STYLES)
            a = t0 + float(cap.get("start_sec", sc["start_sec"])) - float(sc["start_sec"])
            b = t0 + float(cap.get("end_sec", sc["end_sec"])) - float(sc["start_sec"])
            png = os.path.join(work, f"cap{i:02d}_{j}.png")
            draw = functools.partial(caption_png, text=cap["text"], style=style, emphasis=cap.get("emphasis"),
                                     face=heavy, accent=hot, lang=lang)
            size, bbox = draw(png)
            fx = enum(cap.get("fx"), CAPTION_FX, "pop" if style == "hook" and plan.get("pace") == "sns"
                      else "fade", f"scene {n} caption.fx", warns)
            chars = len(re.sub(r"\s", "", cap["text"]))
            track = None if fx == "fade" else caption_track(png[:-4], png, a, b, fx,
                                                            lambda f, k: draw(f, upto=k), chars, grid)
            if size < STYLES[style] * 0.6:
                warns.append(f"scene {n}: caption shrunk to {size}px to fit — shorten it")
            problems = safe_area(bbox)
            qc_caps.append({"scene": n, "bbox": list(bbox) if bbox else None, "size": size,
                            "ok": not problems, "problems": problems})
            caps.append((png, a, b, track))
            srt.append(f"{len(srt) + 1}\n{srt_time(a)} --> {srt_time(b)}\n{cap['text']}\n")

        fxs = sc.get("sfx") or []                 # "file", or [{file, at}] — at: seconds into the scene
        for fx in [fxs] if isinstance(fxs, str) else fxs:
            f, at = (fx, 0.0) if isinstance(fx, str) else (fx.get("file"), float(fx.get("at", 0)))
            p = plan_file(asset_dir, str(f))
            if f == "none":
                continue
            if not os.path.isfile(p):
                warns.append(f"scene {n}: sfx {f!r} not found — skipped")
            elif at >= dur:
                warns.append(f"scene {n}: sfx {f!r} at {at}s is past the scene's {dur:.2f}s — skipped")
            else:
                sfx.append((p, t0 + at))
        t0 += dur
        print(f"  scene {i + 1}/{len(scenes)}", flush=True)
    total = t0

    listfile = os.path.join(work, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for s in segs:
            fh.write("file '" + s.replace("\\", "/").replace("'", "'\\''") + "'\n")

    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listfile]
    graph, v = [], "[0:v]"
    for k, (png, a, b, track) in enumerate(caps, 1):
        cmd += ["-i", track] if track else ["-loop", "1", "-t", f"{total}", "-i", png]
        fade = "" if track else f",fade=t=in:st={a:.3f}:d=0.15:alpha=1"   # type and pop are their own entrance
        graph.append(f"[{k}:v]format=rgba{fade}[c{k}];"
                     f"{v}[c{k}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[v{k}]")
        v = f"[v{k}]"
    idx = len(caps) + 1
    logo = logo_png(os.path.join(work, "logo.png"), brand_path, lang, warns)
    if logo:
        cmd += ["-loop", "1", "-t", f"{total}", "-i", media(logo)]
        off = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in card_at)   # a card carries its own logo
        graph.append(f"{v}[{idx}:v]overlay=0:0:format=auto" + (f":enable='not({off})'" if off else "") + "[vl]")
        v, idx = "[vl]", idx + 1
    # BT.709 tags in the graph, not as -color_* flags: ffmpeg 9 takes them from the frames
    graph.append(f"{v}setsar=1,format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709[v]")
    fg, bgm_in = ["[0:a]"], None
    if bgm:
        cmd += ["-stream_loop", "-1", "-t", f"{total}", "-i", media(bgm)]
        bgm_in = f"[{idx}:a]"
        idx += 1
    if vo:
        cmd += ["-i", media(vo)]
        fg.append(f"[{idx}:a]")
        idx += 1
    if tone:
        cmd += ["-stream_loop", "-1", "-t", f"{total}", "-i", media(tone)]
        fg.append(f"[{idx}:a]")
        idx += 1
    for k, (p, at) in enumerate(sfx + [(p, at) for p, at, _, _ in lines]):
        cmd += ["-i", media(p)]
        ms = round(at * 1000)
        graph.append(f"[{idx}:a]adelay={ms}|{ms}[sfx{k}]")
        fg.append(f"[sfx{k}]")
        idx += 1
    tail = f"afade=t=out:st={max(total - 0.5, 0):.2f}:d=0.5[a]"
    fgmix = f"{''.join(fg)}amix=inputs={len(fg)}:duration=first:normalize=0"
    if bgm_in:
        # Music ducks under whatever else is sounding (clip audio, VO, lines,
        # SFX) and comes back up in the gaps — a key, not a fixed level.
        graph.append(f"{fgmix},asplit=2[fg][key]")
        graph.append(f"{bgm_in}[key]sidechaincompress={DUCK}[bgm]")
        graph.append(f"[fg][bgm]amix=inputs=2:duration=first:normalize=0,{tail}")
    else:
        graph.append(f"{fgmix},{tail}")
    master = os.path.join(work, "master.mkv")
    build.run(cmd + ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
                     "-t", f"{total}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                     "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "pcm_s16le", master])
    # Two-pass loudnorm: one dynamic pass undershoots short cuts by a couple of LU,
    # and the spec's -14 is a delivery number, not a target to land near.
    build.run(["ffmpeg", "-v", "error", "-y", "-i", master, "-c:v", "copy",
               "-af", build.loudnorm_linear(master), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
               "-movflags", "+faststart", out])

    open(stem + ".srt", "w", encoding="utf-8").write("\n".join(srt))
    sheet = stem + ".sheet.jpg"
    build.run(["ffmpeg", "-v", "error", "-y", "-i", out, "-vf",
               f"fps=1,scale=180:-1,tile=6x{max(1, math.ceil(total / 6))}", "-frames:v", "1", sheet])

    info = ffprobe(out)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in vs["r_frame_rate"].split("/"))
    lufs, peak = loudness(out)
    if lufs is None or lufs < -40:
        warns.append("the mix is silent — no clip audio, bgm or voiceover")
    elif abs(lufs + 14) > 1.5:
        warns.append(f"integrated loudness {lufs} LUFS, target -14")
    qc = {"file": os.path.basename(out), "width": vs["width"], "height": vs["height"],
          "fps": round(num / den, 3), "duration": round(float(info["format"]["duration"]), 3),
          "loudness_lufs": lufs, "true_peak_dbfs": peak, "captions": qc_caps,
          "safe_area_ok": all(c["ok"] for c in qc_caps), "retimed": moved, "voiceover_cuts": vo_cuts,
          "generated": [sc.get("scene") for sc in scenes if sc.get("generate")], "warnings": warns}
    json.dump(qc, open(stem + ".qc.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\n{out}  ({total:.1f}s)\n{stem}.srt\n{stem}.qc.json\n{sheet}")
    for w in warns:
        print(f"! {w}")
    return qc, stem


# ---- speech check -----------------------------------------------------------
def speech_pieces(plan, asset_dir, qc):
    """What the render should say, as [(text, t0, t1)] in render seconds. With voiceover.cuts:
    one piece per row of the render's qc.json voiceover_cuts (in/out as snapped), read back from
    the source transcript; a pause expects nothing. Comparing piece by piece keeps a stray word
    at the end of one cut and a dropped word at the start of the next from pairing up as one
    same-length swap, which would read as a mishearing. Otherwise voiceover.lines text by scene."""
    vo = plan.get("voiceover") or {}
    if vo.get("cuts"):
        out = []
        for r in qc.get("voiceover_cuts") or []:
            ws = words_of(plan_file(asset_dir, r["src"])) if "src" in r else []
            out.append(("".join(w["text"] for w in ws if r["in"] <= w["start"] and w["end"] <= r["out"]),
                        r["at"], r["end"]))
        return out
    # ponytail: lines are one piece; per-line windows need retime()'s placements in qc.json
    order = {sc.get("scene"): i for i, sc in enumerate(plan.get("timeline") or [])}
    text = "".join(ln.get("text", "") for ln in sorted(vo.get("lines") or [],
                                                       key=lambda ln: order.get(ln.get("scene"), 0)))
    return [(text, 0.0, math.inf)] if text else []


def speech_check(pieces, words, lang="tc"):
    """speech_diff per piece; each heard word goes to the piece its midpoint falls in."""
    def piece_of(w):
        mid = (w["start"] + w["end"]) / 2
        return max([i for i, p in enumerate(pieces) if p[1] <= mid] or [0])
    return [f for k, (text, t0, _) in enumerate(pieces)
            for f in speech_diff(text, [w for w in words if piece_of(w) == k], lang, t0)]


def speech_diff(expected, words, lang="tc", t0=0.0):
    """Planned text vs heard words ([{text, start, end}], render seconds) ->
    [{kind: extra|missing|changed, expected, heard, at, end}]. Compared character by
    character with punctuation and spaces dropped; tc goes through t2s on both sides,
    since Scribe writes Chinese in Simplified."""
    conv = t2s() if lang == "tc" else str
    exp = [c for c in conv(expected).lower() if c.isalnum()]
    heard = [(c, w) for w in words for c in conv(w["text"]).lower() if c.isalnum()]
    sm = difflib.SequenceMatcher(None, exp, [c for c, _ in heard], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        near = heard[min(j1, len(heard) - 1)][1] if heard else {"start": t0, "end": t0}
        span = [w for _, w in heard[j1:j2]] or [near]
        out.append({"kind": {"insert": "extra", "delete": "missing", "replace": "changed"}[op],
                    "expected": "".join(exp[i1:i2]), "heard": "".join(c for c, _ in heard[j1:j2]),
                    "at": round(span[0]["start"], 2), "end": round(span[-1]["end"], 2)})
    return out


def worth_flagging(f):
    """True if a speech_diff entry is a likely edit fault (a clipped or stray word at a
    join, a dropped line) rather than Scribe mishearing clean speech. A same-length
    swap (补→不) is a homophone mishearing; anything added, dropped or resized is not."""
    return f["kind"] != "changed" or len(f["expected"]) != len(f["heard"])


def check(plan_path, asset_dir, video, lang="tc", words=None):
    """Transcribe a render and diff it against speech_pieces; the result goes into its
    qc.json as `speech`. `words` stubs the transcript (self-test)."""
    plan = yaml.safe_load(open(plan_path, encoding="utf-8"))
    lang = plan.get("lang", lang)
    qcp = (video[:-4] if video.endswith(".mp4") else video) + ".qc.json"
    if not os.path.isfile(qcp):
        sys.exit(f"{qcp} not found — --check reads the qc.json render_plan.py wrote beside the render")
    qc = json.load(open(qcp, encoding="utf-8"))
    pieces = speech_pieces(plan, asset_dir, qc)
    if not any(text for text, _, _ in pieces):
        sys.exit("the plan has no voiceover.lines or voiceover.cuts — nothing to check against")
    if words is None:
        from transcribe import run as scribe_run    # keeps the transcript out of <out>.srt's way
        tstem, _ = scribe_run(video, lang, os.path.join(os.path.dirname(os.path.abspath(video)), ".check"))
        words = [w for w in json.load(open(tstem + ".transcript.json", encoding="utf-8")) if w["text"].strip()]
    diff = speech_check(pieces, words, lang)
    flags = [f for f in diff if worth_flagging(f)]
    qc["speech"] = {"ok": not flags, "flags": flags, "ignored": [f for f in diff if f not in flags]}
    from transcribe import writable
    json.dump(qc, open(writable(qcp), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    for f in flags:
        print(f"! {f['at']:.2f}–{f['end']:.2f}s {f['kind']}: planned {f['expected']!r}, heard {f['heard']!r}")
    print(f"speech {'ok' if not flags else f'{len(flags)} flag(s)'} -> {qcp}")
    return qc["speech"]


# ---- picks sheet ------------------------------------------------------------
def picks(plan_path, asset_dir, cols=5, brand_path=None):
    """One 9:16 frame per scene, from where the cut will start, labelled with
    scene, file and in-point — the sheet the user approves before a render."""
    plan, scenes, warns = load(plan_path, asset_dir)
    brand_path = brand_path or ("brand.json" if os.path.exists("brand.json") else None)
    build.BRAND = json.load(open(brand_path, encoding="utf-8")) if brand_path else {}
    tw, th, label = 216, 384, 44
    cols = min(cols, len(scenes))
    rows = math.ceil(len(scenes) / cols)
    sheet = Image.new("RGB", (cols * tw, rows * (th + label)), "white")
    d = ImageDraw.Draw(sheet)
    face = build.pick_face("tc", "body")      # CJK filenames; the default face draws boxes
    fnt = build.font(face, 15) if face else ImageFont.load_default(15)
    work = tempfile.mkdtemp(prefix="picks-")
    for i, sc in enumerate(scenes):
        lang = plan.get("lang", "tc")
        path = (card_png(os.path.join(work, f"card{i}.png"), sc, brand_path, lang, build.resolve_faces(lang), warns)
                if sc.get("kind") == "card" else plan_file(asset_dir, sc["asset_file"]))
        t = float(sc.get("source_in_sec") or 0) + min(0.5, (float(sc["end_sec"]) - float(sc["start_sec"])) / 2)
        if is_still(path):
            im = Image.open(path).convert("RGB")
        else:
            png = os.path.join(work, f"{i}.png")
            build.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", media(path), "-frames:v", "1", png])
            im = Image.open(png).convert("RGB")
        s = max(tw / im.width, th / im.height)
        im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)))
        im = im.crop(((im.width - tw) // 2, (im.height - th) // 2, (im.width + tw) // 2, (im.height + th) // 2))
        x, y = i % cols * tw, i // cols * (th + label)
        sheet.paste(im, (x, y))
        name = "card" if sc.get("kind") == "card" else os.path.basename(sc["asset_file"])
        d.text((x + 4, y + th + 2), f"S{sc.get('scene', i + 1)}  @{sc.get('source_in_sec') or 0}s", font=fnt, fill="black")
        d.text((x + 4, y + th + 20), name if len(name) <= 30 else name[:14] + "…" + name[-14:], font=fnt, fill="#555")
    out = os.path.splitext(os.path.abspath(plan_path))[0] + ".picks.jpg"
    from transcribe import writable
    sheet.save(writable(out), quality=88)
    shutil.rmtree(work, ignore_errors=True)
    print(out)
    return out


# ---- self-test --------------------------------------------------------------
def self_test():
    d = tempfile.mkdtemp(prefix="render-plan-test-")
    sh = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x3366cc:s=640x360:r=30:d=5",
       "-f", "lavfi", "-i", "sine=f=440:r=48000:d=5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-shortest", os.path.join(d, "talk.mp4"))
    # 60 fps and silent: catches a zoompan that plays high-frame-rate footage slow.
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=1080x1920:r=60:d=1",
       "-f", "lavfi", "-i", "color=blue:s=1080x1920:r=60:d=2", "-filter_complex", "concat=n=2:v=1:a=0",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", os.path.join(d, "broll.mov"))
    Image.new("RGB", (1200, 800), "#E91E63").save(os.path.join(d, "product.png"))
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:r=48000:d=4", os.path.join(d, "bgm.wav"))
    # source-time cues: the first ends before the in-point, the second straddles it, the third is inside
    json.dump([{"start": 0.0, "end": 0.9, "text": "太早"}, {"start": 0.8, "end": 1.6, "text": "你也是"},
               {"start": 1.6, "end": 2.9, "text": "每年都曬傷嗎？"}, {"start": 3.5, "end": 4.0, "text": "太晚"}],
              open(os.path.join(d, "talk.cues.json"), "w", encoding="utf-8"), ensure_ascii=False)
    plan = {"content_id": "2026-10_test_v1", "total_duration_sec": 5.5, "render": {"mode": "ffmpeg"},
            "timeline": [
                {"scene": 1, "start_sec": 0.0, "end_sec": 2.0, "asset_file": "talk.mp4",
                 "source_in_sec": 1.0, "cues": "talk.cues.json",
                 "transition_in": "hard_cut", "animation": "zoom_in_punch", "sfx": "whoosh.wav"},
                {"scene": 2, "start_sec": 2.0, "end_sec": 3.5, "asset_file": "broll.mov",
                 "caption": {"text": "一支防曬，從早到晚都不用補擦，海邊也安心", "style": "bold_white_outline",
                             "start_sec": 2.5, "end_sec": 3.5},
                 "transition_in": "fade", "animation": "ken_burns", "sfx": "none"},
                {"scene": 3, "start_sec": 3.5, "end_sec": 5.5, "asset_file": "product.png",
                 "generate": {"brief": "product on pink", "prompt": "stub"},
                 "caption": {"text": "立即選購", "style": "cta", "start_sec": 4.0, "end_sec": 5.5},
                 "transition_in": "fade", "animation": "spin"}],
            "bgm": {"mood": "upbeat", "asset_file": "bgm.wav"}}
    pp = os.path.join(d, "edit_plan.yaml")
    yaml.safe_dump(plan, open(pp, "w", encoding="utf-8"), allow_unicode=True)

    qc, stem = render(pp, d)
    assert (qc["width"], qc["height"]) == (1080, 1920), qc
    assert qc["fps"] == 30, qc["fps"]
    assert abs(qc["duration"] - 5.5) < 0.1, qc["duration"]
    assert qc["loudness_lufs"] is not None and abs(qc["loudness_lufs"] + 14) < 2, qc["loudness_lufs"]
    assert qc["safe_area_ok"], qc["captions"]
    assert qc["generated"] == [3], qc["generated"]
    assert qc["captions"][1]["size"] >= 70, f"20-char body caption shrunk to {qc['captions'][1]['size']}px"
    assert any("'spin'" in w for w in qc["warnings"]), qc["warnings"]
    assert any("whoosh.wav" in w for w in qc["warnings"]), qc["warnings"]
    assert not any("caption.style" in w for w in qc["warnings"]), "legacy style warned"
    cues = open(stem + ".srt", encoding="utf-8").read().strip().split("\n\n")
    assert len(cues) == 4 and "00:00:02,500 --> 00:00:03,500" in cues[2], cues
    assert "00:00:00,000 --> 00:00:00,600\n你也是" in cues[0] and "00:00:00,600 --> 00:00:01,900" in cues[1], cues
    assert stem.endswith("2026-10_test_v1_v1"), stem
    # broll.mov turns blue at 1.0 s. At t=3.2 the cut is 1.2 s into it: blue at
    # real speed, still red if the 60 fps source were playing at half speed.
    png = os.path.join(d, "probe.png")
    sh("ffmpeg", "-v", "error", "-y", "-ss", "3.2", "-i", stem + ".mp4", "-frames:v", "1", png)
    r, _, b = Image.open(png).convert("RGB").getpixel((540, 700))
    assert b > r, f"60 fps source plays slow: ({r}, _, {b}) at 3.2 s"
    assert next_out(pp, "2026-10_test_v1").endswith("_v2.mp4"), "version not bumped"
    scaffold = os.path.join(d, "proj", "input", "spring-sale", "edit_plan.yaml")
    assert next_out(scaffold, "x") == os.path.join(d, "proj", "output", "spring-sale_v1.mp4"), \
        "scaffolded plan did not render to output/"
    print("  ok  render: 1080x1920 30fps 5.5s, -14 LUFS, safe area, 4 cues (2 from transcript), warnings")

    # Wraps land between words: the 0.8.0 live test split 夏|天; t2s-only jieba splits 曬|傷,
    # tc-only jieba splits 台|灣; a line never starts with closing punctuation.
    dd, heavy = ImageDraw.Draw(Image.new("RGBA", (8, 8))), build.resolve_faces("tc")[0]
    for text, first in (("你也是每年夏天都曬傷嗎？", "你也是每年"),
                        ("台灣製造的天然洗髮精，溫和不刺激", "台灣製造的天然")):
        lines, _ = layout(dd, heavy, text, STYLES["body"], "tc")
        assert lines[0] == first, lines
    for text in ("你也是每年夏天都曬傷嗎？", "日焼け止めを毎日しっかり塗っていますか？"):
        assert all(i not in word_breaks(text, "jp" if "焼" in text else "tc")
                   for i in range(len(text)) if text[i] in NO_START), text
    assert layout(dd, heavy, "第一行\n第二行", STYLES["body"])[0] == ["第一行", "第二行"], "explicit break"
    print("  ok  caption wraps at word breaks, kinsoku, explicit newline")

    # Ducking: music sits ~12 dB under a 1 s tone, back to full in the gap after.
    def bgm_rms(a, b):
        err = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:r=48000:d=3",
             "-f", "lavfi", "-i", "sine=f=1000:r=48000:d=3,volume='if(lt(t,1),2,0)':eval=frame",
             "-filter_complex", f"[0:a][1:a]sidechaincompress={DUCK},atrim={a}:{b},"
             "astats=metadata=1:reset=0,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
             "-f", "null", "-"], capture_output=True, encoding="utf-8").stdout
        return float(err.strip().splitlines()[-1].split("=")[1])
    under, gap = bgm_rms(0.2, 1) - bgm_rms(2, 3), bgm_rms(2, 3) - -21.0
    assert -14 < under < -10 and abs(gap) < 1, (under, gap)
    print(f"  ok  ducking {under:+.1f} dB under speech, recovers in the gap")

    # 4:5 feed cut with a brand logo: canvas, safe area and the logo's pixels.
    Image.new("RGBA", (400, 100), "#00FF00").save(os.path.join(d, "logo.png"))
    json.dump({"logo": {"default": "logo.png"}}, open(os.path.join(d, "brand.json"), "w"))
    qc, stem = render(pp, d, brand_path=os.path.join(d, "brand.json"), ratio="4x5")
    assert (qc["width"], qc["height"]) == (1080, 1350) and qc["safe_area_ok"], qc
    assert stem.endswith("2026-10_test_v1_4x5_v1"), stem
    sh("ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", stem + ".mp4", "-frames:v", "1", png)
    r, g, b = Image.open(png).convert("RGB").getpixel((540, 100))
    assert g > 200 and r < 80 and b < 80, f"no logo at the top of the 4:5 cut: ({r}, {g}, {b})"
    print("  ok  4:5 feed cut, logo top-centre")
    build.BRAND = {}
    set_canvas("9x16")

    assert os.path.exists(picks(pp, d)), "no picks sheet"
    print("  ok  picks sheet")

    # Voiced: scene 1 planned 1.0 s carries 2.0 s + 0.5 s of lines; scene 2 keeps its 1.5 s.
    os.makedirs(os.path.join(d, "vo"))
    for name, sec in (("a", 2.0), ("b", 0.5)):
        sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=300:r=48000:d={sec}",
           os.path.join(d, "vo", f"{name}.mp3"))
    vplan = {"content_id": "2026-10_vo", "timeline": [
        {"scene": 1, "start_sec": 0.0, "end_sec": 1.0, "asset_file": "product.png"},
        {"scene": 2, "start_sec": 1.0, "end_sec": 2.5, "asset_file": "broll.mov",
         "caption": {"text": "立即選購", "style": "cta", "start_sec": 1.5, "end_sec": 2.5}}],
        "voiceover": {"lines": [{"id": "a", "scene": 1, "text": "你也是每年都曬傷嗎"},
                                {"id": "b", "scene": 1, "text": "我也是"},
                                {"id": "c", "scene": 2, "text": "還沒錄"}]}}
    vp = os.path.join(d, "vo_plan.yaml")
    yaml.safe_dump(vplan, open(vp, "w", encoding="utf-8"), allow_unicode=True)
    qc, stem = render(vp, d)
    # Expected times come from the encoded files: mp3 padding differs by ffmpeg build.
    da, db = (duration(os.path.join(d, "vo", f"{n}.mp3")) for n in "ab")
    s1 = da + VO_GAP + db + VO_TAIL
    assert abs(qc["duration"] - (s1 + 1.5)) < 0.1, qc["duration"]
    assert qc["retimed"] == [{"scene": 1, "planned": 1.0, "voiced": round(s1, 2)}], qc["retimed"]
    assert any("vo/c.mp3" in w for w in qc["warnings"]), qc["warnings"]
    cues = open(stem + ".srt", encoding="utf-8").read().strip().split("\n\n")
    assert f"00:00:00,000 --> {srt_time(da)}\n你也是每年都曬傷嗎" in cues[0], cues
    assert f"{srt_time(da + VO_GAP)} --> {srt_time(da + VO_GAP + db)}\n我也是" in cues[1], cues
    assert f"{srt_time(s1 + 0.5)} --> {srt_time(s1 + 1.5)}\n立即選購" in cues[2], cues
    print("  ok  voiced: scene stretched to its lines, captions from lines, missing line warned")

    # The joke01 cut ended 0.05 s into the next word and kept its first syllable.
    words = [{"text": "你好", "start": 0.5, "end": 1.0}, {"text": "再見", "start": 1.2, "end": 1.8}]
    assert snap_cut(0.5, 1.25, words) == (0.5, 1.2), snap_cut(0.5, 1.25, words)
    assert snap_cut(0.55, 1.1, words)[0] == 0.5, "an in-point inside a word kept its tail"
    assert snap_cut(1.05, 1.15, words) == (1.05, 1.15), "a cut in a gap moved"
    assert snap_cut(1.1, 1.15, words + [{"text": "個", "start": 1.1, "end": 1.1}]) == (1.1, 1.15)
    print("  ok  cut points snap off partial words")

    grid = [0.5 * k for k in range(1, 40)]                      # 120 bpm
    assert snapped(0.0, 1.0, grid) == 1.0, "a cut already on the beat moved"
    assert snapped(0.0, 0.1, grid) >= MIN_SHOT, "snap left a flash frame"
    assert abs(snapped(1.0, 0.8, grid) * FPS - round(snapped(1.0, 0.8, grid) * FPS)) < 1e-6, "off-frame cut"
    sc = [{"scene": 1, "start_sec": 0, "end_sec": 1.2, "caption": {"text": "x", "start_sec": 0.6, "end_sec": 1.2}},
          {"scene": 2, "start_sec": 1.2, "end_sec": 2.0}, {"scene": 3, "start_sec": 2.0, "end_sec": 3.3}]
    _, mv = retime({}, sc, d, [], grid)
    assert all(x["start_sec"] in grid or x["start_sec"] == 0 for x in sc), [x["start_sec"] for x in sc]
    assert sc[0]["caption"]["end_sec"] == sc[0]["end_sec"], "caption did not move with its scene"
    assert all("snapped" in m for m in mv), mv
    assert beat_grid({"pace": "sns"}, None, w := []) == [] and "no bgm" in w[0], w
    print("  ok  sync.snap lands unvoiced cuts on the beat")

    Image.new("RGB", (1080, 1920), "#204060").save(os.path.join(d, "bg.png"))
    kp = os.path.join(d, "kinetic.yaml")
    yaml.safe_dump({"content_id": "kinetic", "pace": "sns", "timeline": [
        {"scene": 1, "start_sec": 0, "end_sec": 2, "asset_file": "bg.png", "animation": "pan_fast_l",
         "caption": {"text": "一個字一個字打出來", "style": "body", "fx": "type"}},
        {"scene": 2, "start_sec": 2, "end_sec": 3, "asset_file": "bg.png", "animation": "shake",
         "sfx": [{"file": "bgm.wav", "at": 0.5}, {"file": "nope.wav"}, {"file": "bgm.wav", "at": 9}],
         "caption": {"text": "爆點", "style": "hook"}}]}, open(kp, "w", encoding="utf-8"), allow_unicode=True)
    qc, stem = render(kp, d)

    def ink(t):                                   # caption pixels: anything far from the background
        f = os.path.join(d, f"k{t}.png")
        sh("ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", stem + ".mp4", "-frames:v", "1", f)
        b = Image.open(f).convert("RGB").crop((0, 1100, 1080, 1700)).tobytes()
        return sum(1 for i in range(0, len(b), 3) if abs(b[i] - 0x20) + abs(b[i + 2] - 0x60) > 120)
    assert 0 < ink(0.15) < ink(1.0) * 0.5, (ink(0.15), ink(1.0))
    assert ink(2.04) > ink(2.6) * 1.2, (ink(2.04), ink(2.6))
    assert not any("caption" in w for w in qc["warnings"]), qc["warnings"]
    assert sum("sfx" in w for w in qc["warnings"]) == 2, qc["warnings"]
    assert not any("silent" in w for w in qc["warnings"]), "the one good sfx was not mixed in"
    print("  ok  kinetic captions: type reveals, a hook pops under pace: sns")

    sh("ffmpeg", "-v", "error", "-y", *[x for c in ("red", "green", "blue", "white")
                                         for x in ("-f", "lavfi", "-i", f"color={c}:s=640x360:r=30:d=1")],
       "-f", "lavfi", "-i", "sine=f=330:r=48000:d=4", "-filter_complex", "concat=n=4:v=1:a=0[v]",
       "-map", "[v]", "-map", "4:a", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
       os.path.join(d, "rgbw.mp4"))
    jp = os.path.join(d, "jump.yaml")
    yaml.safe_dump({"content_id": "jump", "timeline": [
        {"scene": 1, "start_sec": 0, "end_sec": 1.2, "asset_file": "rgbw.mp4",
         "cuts": [{"in": 0.2, "out": 0.8}, {"in": 2.2, "out": 2.8}]},
        {"scene": 2, "start_sec": 1.2, "end_sec": 2.2, "asset_file": "rgbw.mp4", "speed": 2},
        {"scene": 3, "start_sec": 2.2, "end_sec": 3.2, "asset_file": "rgbw.mp4",
         "cuts": [{"in": 3.0, "out": 3.3}]}]}, open(jp, "w", encoding="utf-8"))
    qc, stem = render(jp, d)

    def rgb_at(t):
        sh("ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", stem + ".mp4", "-frames:v", "1", f := os.path.join(d, "j.png"))
        return tuple(int(c > 90) for c in Image.open(f).convert("RGB").getpixel((540, 300)))
    assert [rgb_at(t) for t in (0.3, 0.9, 1.4, 1.95)] == [(1, 0, 0), (0, 0, 1), (1, 0, 0), (0, 1, 0)], \
        [rgb_at(t) for t in (0.3, 0.9, 1.4, 1.95)]
    assert abs(qc["duration"] - 3.2) < 0.1, qc["duration"]
    assert any("scene 3: cuts play 0.30s" in w for w in qc["warnings"]), qc["warnings"]
    print("  ok  jump cuts and speed: ranges joined, 2x reaches the next colour, short cut held")

    # Speech check: the joke01 fault (a cut keeping the next word's first syllable) is flagged.
    heard = [{"text": "你好", "start": 0.0, "end": 0.5}, {"text": "再", "start": 0.5, "end": 0.6},
             {"text": "我也是", "start": 1.7, "end": 2.4}]
    diff = speech_diff("你好，我也是。", heard)
    assert [(f["kind"], f["heard"], f["at"]) for f in diff] == [("extra", "再", 0.5)], diff
    assert worth_flagging(diff[0]), "a stray syllable at a join was not flagged"
    assert speech_diff("軟體影片", [{"text": "软体影片", "start": 0, "end": 1}]) == [], "tc vs Simplified"
    assert speech_diff("你好我也是", heard[:1])[0]["kind"] == "missing"
    mis = speech_diff("記得補擦", [{"text": "记得不擦", "start": 3, "end": 4}])
    assert len(mis) == 1 and not worth_flagging(mis[0]), "a homophone mishearing was flagged"
    # Real mis-cut test: 对 leaked past cut 2's out, 好 lost from cut 4's in, a pause between.
    # One string diff paired them as a 好→对 swap (a "mishearing"); per piece both flag.
    pieces = [("利一点", 0.0, 2.0), ("", 2.0, 3.0), ("好那么", 3.0, 5.0)]
    heard = [{"text": t, "start": a, "end": a + 0.1}
             for t, a in (("利", 0.1), ("一", 0.3), ("点", 0.5), ("对", 1.8), ("那", 3.3), ("么", 3.5))]
    got = [(f["kind"], f["expected"] or f["heard"], f["at"]) for f in speech_check(pieces, heard)]
    assert got == [("extra", "对", 1.8), ("missing", "好", 3.3)], got
    got = speech_check(pieces, heard + [{"text": "嗯", "start": 2.4, "end": 2.5}])
    assert ("extra", "嗯") in [(f["kind"], f["heard"]) for f in got], "a word in a pause was not flagged"
    print("  ok  speech check: stray syllable flagged, mishearing ignored, tc/sc agree, missing line found")

    # Cut-list voiceover with a room-tone pause, two captions on one picture, a brand card.
    json.dump(words, open(os.path.join(d, "talk.transcript.json"), "w"), ensure_ascii=False)
    Image.new("RGBA", (400, 100), "#00FF00").save(os.path.join(d, "logo.png"))
    accent = "#EC6336"
    json.dump({"palette": {"accent": accent}, "logo": {"default": "logo.png"},
               "card": {"signoff": "明天見", "cta": "立即預約"}}, open(os.path.join(d, "brand.json"), "w"))
    cplan = {"content_id": "2026-10_cut", "timeline": [
        {"scene": 1, "start_sec": 0.0, "end_sec": 1.7, "asset_file": "product.png",
         "captions": [{"text": "第一句", "start_sec": 0.0, "end_sec": 0.7},
                      {"text": "第二句", "start_sec": 0.7, "end_sec": 1.7}]},
        {"scene": 2, "start_sec": 1.7, "end_sec": 2.7, "kind": "card",
         "caption": {"text": "來 KA·RA·DA 放鬆", "style": "cta", "emphasis": ["放鬆"]}}],
        "voiceover": {"cuts": [{"src": "talk.mp4", "in": 0.5, "out": 1.25}, {"pause": 1.0},
                               {"src": "talk.mp4", "in": 2.0, "out": 3.0}],
                      "room_tone": {"src": "talk.mp4", "in": 3.0, "out": 4.0}}}
    cp = os.path.join(d, "cut_plan.yaml")
    yaml.safe_dump(cplan, open(cp, "w", encoding="utf-8"), allow_unicode=True)
    qc, stem = render(cp, d, brand_path=os.path.join(d, "brand.json"))
    assert qc["voiceover_cuts"] == [
        {"src": "talk.mp4", "in": 0.5, "out": 1.2, "at": 0.0, "end": 0.7},
        {"pause": 1.0, "at": 0.7, "end": 1.7},
        {"src": "talk.mp4", "in": 2.0, "out": 3.0, "at": 1.7, "end": 2.7}], qc["voiceover_cuts"]
    assert not any("digital silence" in w for w in qc["warnings"]), qc["warnings"]
    assert any("upscaled" in w for w in qc["warnings"]), "a 100 px logo on a card was not warned"
    cues = open(stem + ".srt", encoding="utf-8").read().strip().split("\n\n")
    assert len(cues) == 3 and "00:00:00,700 --> 00:00:01,700\n第二句" in cues[1], cues

    def rms(a, b):
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a}", "-t", f"{b - a}", "-i", stem + ".mp4",
                              "-af", "astats=metadata=1:reset=0,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
                              "-f", "null", "-"], capture_output=True, encoding="utf-8").stdout
        return float(out.strip().splitlines()[-1].split("=")[1])
    assert rms(0.9, 1.5) > -40, f"the pause is dead air: {rms(0.9, 1.5)} dB"

    def px(t, xy):
        sh("ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", stem + ".mp4", "-frames:v", "1", png)
        return Image.open(png).convert("RGB").getpixel(xy)
    assert math.dist(px(2.2, (40, 40)), rgb(accent)) < 30, "card background is not the brand accent"
    assert px(1.0, (540, 265))[1] > 200, "no logo overlay before the card"
    assert math.dist(px(2.2, (540, 265)), rgb(accent)) < 30, "logo overlay drawn over the card"
    # An accent logo on an accent card is knocked out to the card's ink.
    Image.new("RGBA", (400, 100), "#E86030").save(os.path.join(d, "logo.png"))
    card = card_png(os.path.join(d, "card.png"), {}, os.path.join(d, "brand.json"), "tc",
                    build.resolve_faces("tc"), [])
    logo_y = round(H * 0.3) + 20
    assert Image.open(card).getpixel((540, logo_y)) == (255, 255, 255), "accent logo not knocked out"
    build.BRAND = {}
    dd, f = ImageDraw.Draw(Image.new("RGBA", (8, 8))), build.font(build.resolve_faces("tc")[0], 76)
    assert tlen(dd, "KA·RA", f) < dd.textlength("KA·RA", font=f) and tlen(dd, "中·文", f) == dd.textlength("中·文", font=f)
    print("  ok  cut-list VO + room tone, two captions on one scene, brand card, dot kerning")

    plan["timeline"][1]["asset_file"] = "gone.mov"
    yaml.safe_dump(plan, open(pp, "w", encoding="utf-8"), allow_unicode=True)
    try:
        render(pp, d, out=os.path.join(d, "never.mp4"))
        raise AssertionError("missing asset rendered")
    except SystemExit as e:
        assert "gone.mov" in str(e) and not os.path.exists(os.path.join(d, "never.mp4")), e
    print("  ok  missing required asset fails before rendering")
    print(f"self-test ok  ({d})")


def cli(argv):
    ap = argparse.ArgumentParser(usage=__doc__.split("\n\n")[1])
    ap.add_argument("plan", nargs="?")
    ap.add_argument("assets", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--lang", default="tc")
    ap.add_argument("--brand")
    ap.add_argument("--ratio", choices=RATIOS, default="9x16")
    ap.add_argument("--picks", action="store_true")
    ap.add_argument("--check", metavar="MP4")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not (a.plan and a.assets):
        ap.error("an edit plan and its asset directory, or --self-test")
    if a.picks:
        return picks(a.plan, a.assets, brand_path=a.brand)
    if a.check:
        return check(a.plan, a.assets, a.check, a.lang)
    render(a.plan, a.assets, a.out, a.lang, a.brand, a.ratio)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    cli(sys.argv[1:])
