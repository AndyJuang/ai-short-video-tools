#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10"]
# ///
"""Build one cut of one video from its params file.

    uv run build.py spring-sale                  # default language
    uv run build.py spring-sale ko               # another language cut of the same edit
    uv run build.py spring-sale --all            # every language in video.json
    uv run build.py spring-sale --ratio 9x16,1x1 # retarget the template's ratio
    uv run build.py --stale                      # rebuild outputs older than their params
    uv run build.py --self-test                  # build the fixture project every way

Run from the project root. `spring-sale` is a directory under input/ that holds
the materials (gitignored) and a video.json (tracked) describing the edit:

    {"template": "single-offer-9x16",       // templates/<name>.json, or a path
     "src": "clip.mov",                     // file, folder or list, relative
     "music": null,                         //   to this directory
     "music_in": 0,                         // seconds into the bed to start
     "mode": "auto",                        // auto | video | slides
     "crop_band": null,                     // [y0, y1] clear band in the source
     "shots": [[0.4, 2.6], [3.6, 2.4]],     // (in-point, duration) per shot;
                                            //   slides: (image, duration), or
                                            //   omit for every still at SLIDE_DUR
     "copy":     {"tc": {...}, "ko": {...}},
     "captions": {"tc": [["line 1", "line 2"], ...], "ko": [...]},
     "sync": {"snap": "beat", "kenburns": "beat", "pulse": null}}   // optional

`sync` puts the edit on the bed's beat grid (beats.py, cached per bed):
`snap` "beat" | "bar" moves each cut to the next grid point, never closer than
`min_shot` (0.8 s); `kenburns` "beat" pushes in a notch on every beat instead of
drifting; `pulse` "flash" | "zoom" hits the video window on each beat. Off
unless set, and a bed with no beat renders as if it were unset.

Nothing here is edited per job. The layout comes from the template JSON, the
palette, logos and faces from the project's brand.json, and everything that
varies per video from its video.json — which is why git can track a whole
account's worth of edits without ever holding a video file.

Output: output/<video>.<lang>[.<ratio>].mp4 — the template's native ratio keeps
the plain name.  Scratch and a contact sheet: build/<video>/<lang>[.<ratio>]/.
"""
import argparse
import copy
import functools
import glob
import json
import math
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import beats             # noqa: E402  stdlib at import; librosa only in its own uv env
import index_materials   # noqa: E402  digest(), the same hash materials.json records


def plugin_templates():
    """Where the measured layouts ship.

    The project's own templates/ wins (see load_template); this is the fallback
    for the two that ship with the plugin. CLAUDE_PLUGIN_ROOT is set when Claude
    runs this and unset from a plain shell, so look for the install directly too.
    """
    home = os.path.expanduser("~/.claude/plugins")
    pat = "ad-video-tools/skills/video-style-clone/templates"
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    cands = [os.environ.get("AD_VIDEO_TEMPLATES"),
             os.path.normpath(os.path.join(HERE, "..", "templates")),
             os.path.join(root, "skills/video-style-clone/templates") if root else None,
             *glob.glob(f"{home}/marketplaces/*/plugins/{pat}"),
             *glob.glob(f"{home}/repos/*/plugins/{pat}"),
             *glob.glob(f"{home}/synced/*/{pat}"),
             os.path.expanduser("~/.claude/skills/video-style-clone/templates")]
    return next((c for c in cands if c and os.path.isdir(c)), None)


def template_path(name, proj):
    """Project-local templates/ first: a layout measured for this client is the
    client's, and belongs in the client's repo."""
    for p in (name, os.path.join(proj, "templates", f"{name}.json")):
        if os.path.exists(p):
            return p
    shipped = plugin_templates()
    path = os.path.join(shipped or "", f"{name}.json")
    if not shipped or not os.path.exists(path):
        have = sorted(os.path.basename(p)[:-5] for p in
                      glob.glob(os.path.join(proj, "templates", "*.json")) +
                      glob.glob(os.path.join(shipped or "/nonexistent", "*.json")))
        sys.exit(f"no template {name!r}; have: {', '.join(have) or '(none found)'}")
    return path


def load_template(name, proj):
    return json.load(open(template_path(name, proj), encoding="utf-8"))


Y_KEYS = ("y", "top", "y0", "rule_y")     # panel keys that are canvas y-coords


def ratio_height(w, ratio):
    num, den = (int(x) for x in ratio.lower().split("x"))
    return round(w * den / num / 2) * 2


def shift_y(node, dy):
    for k, v in node.items():
        if isinstance(v, dict):
            shift_y(v, dy)
        elif k in Y_KEYS:
            node[k] = [y + dy for y in v] if isinstance(v, list) else v + dy
        elif k == "box":
            node[k] = [v[0], v[1] + dy, v[2], v[3] + dy]


def retarget(T, ratio):
    """Re-canvas a template to another ratio: bar fixed, panel keeps its
    measured height, the video window absorbs the difference. That is the rule
    single-offer-9x16 was built by and it holds for any panel — the panel reads
    identically across ratios, only the footage shows more or less.

    Returns (template, ratio-or-None); None means native, and the output keeps
    its plain name. A template opts in by listing ratios under `retarget`.
    """
    if not ratio or ratio_height(T["canvas"]["w"], ratio) == T["canvas"]["h"]:
        return T, None
    allowed = T.get("retarget", {}).get("ratios", [])
    if ratio not in allowed:
        sys.exit(f"template {T['name']!r} does not retarget to {ratio}; "
                 f"have: {', '.join(allowed) or '(native only)'}")
    T = copy.deepcopy(T)
    dy = ratio_height(T["canvas"]["w"], ratio) - T["canvas"]["h"]
    T["canvas"]["h"] += dy
    T["bands"]["vid_h"] += dy
    T["bands"]["panel_y"] += dy
    if T["bands"]["vid_h"] < 200:
        sys.exit(f"{ratio} leaves a {T['bands']['vid_h']}px video window on {T['name']!r}")
    shift_y(T["panel"], dy)
    return T, ratio


def find_video(arg):
    """Locate input/<name>/video.json — by name from the project root, or by path.

    Returns (video dir, project root). The project root is the parent of input/,
    which is where brand.json, output/ and the git repo live.
    """
    for c in (os.path.join("input", arg), arg):
        vdir = os.path.abspath(c)
        if os.path.exists(os.path.join(vdir, "video.json")):
            if os.path.basename(os.path.dirname(vdir)) != "input":
                sys.exit(f"{vdir} is not a directory under input/")
            return vdir, os.path.dirname(os.path.dirname(vdir))
    sys.exit(f"no params for {arg!r} — expected input/{arg}/video.json "
             f"(run from the project root)")


DEFAULT_PALETTE = {"accent": "#E91E63", "panel": "#C2185B", "ink": "#2C2C2C",
                   "surface": "#FFF5F8", "white": "#ffffff", "muted": "#8A8084"}
RULE_RGBA = (0, 0, 0, 28)

# Pick the face by the COPY's language, not the reference's: a Japanese face
# draws Traditional Chinese with the wrong glyph shapes and drops TC-only
# characters. No OS ships a Hangul Black, so Korean wants NotoSansKR-Black
# installed; without it the heavy slot falls back to the system's bold Hangul
# face, which reads noticeably lighter in the same layout.
#
# Faces are filenames, found in the OS's font directories; `name#n` picks face n
# of a collection. Each slot lists alternatives: the per-language OTFs from the
# noto-cjk releases, then the one-collection Noto CJK that Linux distros ship
# (index 0 jp, 1 ko, 2 sc, 3 tc). The first directory is where a user install
# lands, and what the error and check_env point at.
if sys.platform == "darwin":
    FONT_DIRS = ["~/Library/Fonts", "/Library/Fonts", "/System/Library/Fonts"]
    KO_SYSTEM = ("AppleSDGothicNeo.ttc#6", "AppleSDGothicNeo.ttc#2")    # Bold, Medium
elif os.name == "nt":
    FONT_DIRS = [os.path.join(os.environ.get("LOCALAPPDATA", "~"), "Microsoft", "Windows", "Fonts"),
                 os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    KO_SYSTEM = ("malgunbd.ttf", "malgun.ttf")                          # Malgun Gothic
else:
    FONT_DIRS = ["~/.local/share/fonts", "~/.fonts", "/usr/local/share/fonts", "/usr/share/fonts"]
    KO_SYSTEM = ("NotoSansCJK-Bold.ttc#1", "NotoSansCJK-Regular.ttc#1")
FONT_DIRS = [os.path.expanduser(d) for d in FONT_DIRS]
USER_FONTS = FONT_DIRS[0]


def _cjk(code, i):
    return ((f"NotoSansCJK{code}-Black.otf", f"NotoSansCJK-Black.ttc#{i}"),
            (f"NotoSansCJK{code}-Medium.otf", f"NotoSansCJK-Medium.ttc#{i}"))


FACES = {
    "tc": _cjk("tc", 3), "sc": _cjk("sc", 2), "jp": _cjk("jp", 0),
    "ko": (("NotoSansKR-Black.otf", "NotoSansCJK-Black.ttc#1"),
           (KO_SYSTEM[1], "NotoSansKR-Medium.otf", "NotoSansCJK-Medium.ttc#1")),
    "en": (("NotoSans-Black.ttf",), ("NotoSans-Medium.ttf",)),
}


@functools.lru_cache(None)
def _font_files():
    """basename (lowercased: Windows ships MALGUNBD.TTF and malgunbd.ttf alike)
    -> path, across every font dir, earlier dirs winning."""
    found = {}
    for d in reversed(FONT_DIRS):
        for root, _, files in os.walk(d):
            found.update({f.lower(): os.path.join(root, f) for f in files})
    return found


def locate(name):
    """'face.ttc#2' -> (path, 2), or None. A path that exists is used as is."""
    name, _, i = name.partition("#")
    path = name if os.path.isfile(name) else _font_files().get(os.path.basename(name).lower())
    return (path, int(i or 0)) if path else None


def candidates(lang, slot, override=None):
    """What a slot may use: the brand's override alone, else the face table."""
    return (override,) if override else FACES.get(lang, FACES["tc"])[slot == "body"]


def pick_face(lang, slot, override=None):
    return next(filter(None, map(locate, candidates(lang, slot, override))), None)


def resolve_faces(lang):
    over = BRAND.get("fonts", {}).get(lang, {})
    heavy, body = (pick_face(lang, s, over.get(s)) for s in ("heavy", "body"))
    if not heavy and lang == "ko" and "heavy" not in over and locate(KO_SYSTEM[0]):
        heavy = locate(KO_SYSTEM[0])
        print(f"! NotoSansKR-Black not installed — falling back to "
              f"{os.path.basename(heavy[0])}, which reads lighter. See references/gotchas.md.")
    for face, slot in ((heavy, "heavy"), (body, "body")):
        if not face:
            sys.exit(f"no {slot} face for {lang!r}: none of "
                     f"{', '.join(candidates(lang, slot, over.get(slot)))} is installed.\n"
                     f"Install one into {USER_FONTS}")
    return heavy, body


def configure(video, lang=None, ratio=None):
    """Turn `<video> [lang] [ratio]` into the globals the machinery below reads.

    Everything that used to be hand-edited at the top of a copied script is
    loaded here instead: the edit from video.json, the layout from the template,
    the brand from brand.json.
    """
    global VIDEO, PROJ, T, W, H, FPS, BAR_H, VID_Y, VID_H, PANEL_Y, P, CAP
    global BRAND, PALETTE, C, W8, W6, LOGO_PNG, COPY, OUT, WORK, LANG, RATIO
    global SRC, MUSIC, MUSIC_IN, MODE, CROP_BAND, SHOTS, CAPTIONS, VDIR, SYNC, GRID

    vdir, PROJ = find_video(video)
    VDIR, VIDEO = vdir, os.path.basename(vdir)
    par = json.load(open(os.path.join(vdir, "video.json"), encoding="utf-8"))

    copy_by_lang = par["copy"]
    LANG = (lang or par.get("default_lang") or next(iter(copy_by_lang))).lower()
    if LANG not in copy_by_lang:
        sys.exit(f"no {LANG!r} copy in {VIDEO}/video.json; "
                 f"have: {', '.join(copy_by_lang)}")
    COPY, CAPTIONS = copy_by_lang[LANG], par["captions"]
    MODE, CROP_BAND = par.get("mode", "auto"), par.get("crop_band")
    MUSIC = os.path.join(vdir, par["music"]) if par.get("music") else None
    MUSIC_IN = par.get("music_in", 0)
    # src defaults to the video's own directory, so a folder of stills needs no
    # entry at all — the slideshow detector just finds the images.
    src = par.get("src", ".")
    SRC = ([os.path.join(vdir, s) for s in src] if isinstance(src, list)
           else os.path.join(vdir, src))
    SHOTS = par.get("shots")
    if SHOTS is None:
        # No shot list: only a slideshow can supply its own — one shot per
        # still, in filename order, SLIDE_DUR each.
        if resolve_mode() != "slides":
            sys.exit(f"{VIDEO}/video.json has no shots — footage needs an explicit shot list")
        SHOTS = [[p, SLIDE_DUR] for p in stills()]
        if not SHOTS:
            sys.exit(f"no stills found in {src!r} for {VIDEO}")

    T, RATIO = retarget(load_template(par["template"], PROJ), ratio)
    W, H, FPS = T["canvas"]["w"], T["canvas"]["h"], T["canvas"]["fps"]
    BAR_H = T["bands"]["bar_h"]
    VID_Y, VID_H = T["bands"]["vid_y"], T["bands"]["vid_h"]
    PANEL_Y = T["bands"]["panel_y"]
    P, CAP = T["panel"], T["caption"]

    SYNC, GRID = par.get("sync") or {}, dict(beats.EMPTY)
    if any(SYNC.get(k) not in (None, "drift") for k in ("snap", "kenburns", "pulse")):
        GRID = beat_grid()
        if SYNC.get("snap"):
            SHOTS = snap(SHOTS, GRID[{"beat": "beats", "bar": "bars"}[SYNC["snap"]]],
                         SYNC.get("min_shot", 0.8))

    bp = os.path.join(PROJ, "brand.json")
    BRAND = json.load(open(bp, encoding="utf-8")) if os.path.exists(bp) else {}
    PALETTE = dict(DEFAULT_PALETTE, **{k: v for k, v in
                                       BRAND.get("palette", {}).items() if v})
    C = PALETTE.__getitem__          # C("accent") -> hex
    W8, W6 = resolve_faces(LANG)
    logos = BRAND.get("logo", {})
    LOGO_PNG = os.path.join(PROJ, logos.get(LANG, logos.get("default", "assets/logo.png")))

    cut = LANG + (f".{RATIO}" if RATIO else "")
    WORK = os.environ.get("WORK", os.path.join(PROJ, "build", VIDEO, cut))
    OUT = os.path.join(PROJ, "output", f"{VIDEO}.{cut}.mp4")


def font(face, size):
    path, index = face
    return ImageFont.truetype(path, size, index=index)


def centered(draw, y, text, fnt, fill, cx=None):
    cx = W // 2 if cx is None else cx
    l, t, r, b = draw.textbbox((0, 0), text, font=fnt)
    draw.text((cx - (r - l) / 2 - l, y - t), text, font=fnt, fill=fill)
    return r - l


def fitted(draw, face, text, size, max_w):
    """Largest size at or below `size` keeping `text` inside `max_w`.

    Copy is rewritten and translated constantly; a size that fit the first
    draft overflows the second. Auto-fitting survives edits without re-measuring
    — and it is what keeps one layout usable across every language.
    """
    while size > 8 and draw.textlength(text, font=font(face, size)) > max_w:
        size -= 1
    return font(face, size)


def _stamp(face, ch, size=40):
    im = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(im).text((2, 2), ch, font=font(face, size), fill=255)
    return im.tobytes()


def missing_glyphs(face, text):
    """Characters this face cannot draw.

    Silent tofu is the most common way a translated cut ships broken, and it is
    invisible until someone watches the file. A missing glyph renders either as
    the .notdef box or as nothing, so compare each character against both. (A
    face that genuinely maps U+E000 would defeat the first test; the blank test
    still catches the rest.)
    """
    tofu, blank = _stamp(face, ""), _stamp(face, " ")
    return [c for c in dict.fromkeys(text)
            if not c.isspace() and _stamp(face, c) in (tofu, blank)]


def preflight():
    if LANG not in CAPTIONS:
        sys.exit(f"no {LANG!r} captions in {VIDEO}/video.json")
    heavy_text = "".join([COPY.get("pill", ""), COPY.get("cta", ""),
                          COPY.get("headline", ""), COPY.get("badge", ""),
                          *(t for t, _ in COPY.get("head", [])),
                          *(l for c in CAPTIONS[LANG] for l in c)])
    body_text = COPY.get("url", "") + COPY.get("label", "")
    for face, text, what in ((W8, heavy_text, "heavy"), (W6, body_text, "body")):
        bad = missing_glyphs(face, text)
        if bad:
            sys.exit(f"{os.path.basename(face[0])} ({what}) cannot draw: "
                     f"{' '.join(bad)}\nPick a face that covers {LANG!r}.")
    if len(CAPTIONS[LANG]) != len(SHOTS):
        sys.exit(f"{len(CAPTIONS[LANG])} captions for {len(SHOTS)} shots in {LANG!r}")


# ---- chrome ---------------------------------------------------------------
def draw_logo(im):
    if not os.path.exists(LOGO_PNG):
        return
    logo = Image.open(LOGO_PNG).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").getbbox())   # trim to the ink
    s = min(T["bar"]["logo_h"] / logo.height, T["bar"]["logo_max_w"] / logo.width)
    logo = logo.resize((round(logo.width * s), round(logo.height * s)), Image.LANCZOS)
    im.alpha_composite(logo, ((W - logo.width) // 2, (BAR_H - logo.height) // 2))


def marker(d, kind, x, cy, colour, size=15):
    """The arrow or chevron on a CTA. Drawn, never typed — U+25B6 and friends
    are tofu in most CJK faces."""
    if kind == "chevron":
        d.line([(x, cy - size), (x + size * 0.62, cy), (x, cy + size)],
               fill=colour, width=6, joint="curve")
    elif kind == "arrow":
        d.polygon([(x, cy - size), (x, cy + size), (x + size * 1.4, cy)], fill=colour)


def render_offer_panel(d):
    d.rectangle([0, PANEL_Y, W, H], fill=C(P["fill"]))

    s = P["pill"]
    fp = font(W8, s["size"])
    pw = d.textbbox((0, 0), COPY["pill"], font=fp)[2] + s["pad"]
    d.rounded_rectangle([(W - pw) // 2, s["y"][0], (W + pw) // 2, s["y"][1]],
                        radius=s["radius"], fill=C("accent"))
    centered(d, s["y"][0] + 15, COPY["pill"], fp, C("white"))

    s = P["headline"]
    line = "".join(t for t, _ in COPY["head"])
    fh = fitted(d, W8, line, s["size"], s["max_w"])
    x = (W - d.textlength(line, font=fh)) / 2
    top = s["top"] - d.textbbox((0, 0), line, font=fh)[1]
    for text, key in COPY["head"]:
        d.text((x, top), text, font=fh, fill=C(key))
        x += d.textlength(text, font=fh)

    s = P["cta"]
    fc = font(W8, s["size"])
    cw = d.textbbox((0, 0), COPY["cta"], font=fc)[2] + s["pad"]
    x0, x1 = (W - cw) // 2, (W + cw) // 2
    d.rounded_rectangle([x0, s["y"][0], x1, s["y"][1]], radius=s["radius"],
                        fill=C("accent"))
    cy = (s["y"][0] + s["y"][1]) // 2
    centered(d, s["y"][0] + 23, COPY["cta"], fc, C("white"), cx=(x0 + x1) // 2 - 26)
    marker(d, s.get("marker", "chevron"), x1 - 66, cy, C("white"))

    d.line([P["rule"]["x"][0], P["rule"]["y"], P["rule"]["x"][1], P["rule"]["y"]],
           fill=RULE_RGBA, width=2)
    centered(d, P["url"]["top"], COPY["url"], font(W6, P["url"]["size"]), C("muted"))


def render_list_panel(d):
    d.rectangle([0, PANEL_Y, W, H], fill=C(P["fill"]))

    s = P["headline"]
    centered(d, s["top"], COPY["headline"],
             fitted(d, W8, COPY["headline"], s["size"], s["max_w"]), C("white"))

    s = P["badge"]
    fb = font(W8, s["size"])
    bw = d.textbbox((0, 0), COPY["badge"], font=fb)[2] + s["pad"]
    d.rounded_rectangle([(W - bw) // 2, s["y"][0], (W + bw) // 2, s["y"][1]],
                        radius=s["radius"], fill=C("accent"))
    centered(d, s["y"][0] + 15, COPY["badge"], fb, C("white"))

    s = P["label"]
    fl = font(W6, s["size"])
    lw = d.textbbox((0, 0), COPY["label"], font=fl)[2]
    centered(d, s["top"], COPY["label"], fl, (255, 255, 255, 200))
    for sx in ((W - lw) // 2 - 90, (W + lw) // 2 + 20):
        d.line([sx, s["rule_y"], sx + s["rule_w"], s["rule_y"]],
               fill=(255, 255, 255, 140), width=2)

    s = P["rows"]
    fc, fd = font(W8, s["chip_size"]), font(W6, s["text_size"])
    cx0, cx1 = s["chip_x"]
    for i, (chip, desc) in enumerate(COPY["points"]):
        y = s["y0"] + round(i * s["pitch"])
        d.rounded_rectangle([cx0, y, cx1, y + s["chip_h"]], radius=s["radius"],
                            fill=C("surface"))
        centered(d, y + 7, chip, fc, C("panel"), cx=(cx0 + cx1) // 2)
        l, t, r, b = d.textbbox((0, 0), desc, font=fd)
        d.text((s["text_x"], y + s["chip_h"] // 2 - (b + t) / 2), desc,
               font=fd, fill=C("white"))

    s = P["cta"]
    x0, y0, x1, y1 = s["box"]
    d.rounded_rectangle([x0, y0, x1, y1], radius=s["radius"], fill=C("white"))
    tw = centered(d, y0 + 22, COPY["cta"], font(W8, s["size"]), C("panel"),
                  cx=W // 2 - 22)
    marker(d, s.get("marker", "arrow"), W // 2 + tw // 2 - 4, (y0 + y1) // 2,
           C("panel"), size=12)


def render_chrome(path):
    """Bar + panel in one RGBA image, transparent over the video window. One
    overlay means one place to fix a misplaced pixel."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, BAR_H - 1], fill=C(T["bar"]["fill"]))
    draw_logo(im)
    {"offer": render_offer_panel, "list": render_list_panel}[P["kind"]](d)
    im.save(path)


def render_caption(path, l1, l2):
    """Caption as its own PNG, the size of the video window.

    ffmpeg drawtext cannot do a per-line size, a second-line indent, or a stroke
    plus a shadow, so the text is drawn here and overlaid as an image.
    """
    im = Image.new("RGBA", (W, VID_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x, y = CAP["x"], CAP["y"]
    for text, size, dx in ((l1, CAP["sizes"][0], 0),
                           (l2, CAP["sizes"][1], CAP["indent"])):
        fnt = fitted(d, W8, text, size, W - x - dx - 90)
        d.text((x + dx + 6, y + 8), text, font=fnt, fill=(0, 0, 0, 90),
               stroke_width=CAP["stroke"], stroke_fill=(0, 0, 0, 90))
        d.text((x + dx, y), text, font=fnt, fill=C("white"),
               stroke_width=CAP["stroke"], stroke_fill=C("accent"))
        y += size + 16
    im.save(path)


# ---- assembly -------------------------------------------------------------
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff")
SLIDE_DUR = 3.0          # per still when video.json gives no shot list
LOUDNESS = "I=-14:TP=-1.5:LRA=11"   # shorts output spec: -14 LUFS, not overridable


def loudnorm_linear(path):
    """Second-pass loudnorm filter from a measuring first pass over `path`."""
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
                          f"loudnorm={LOUDNESS}:print_format=json", "-f", "null", "-"],
                         capture_output=True, encoding="utf-8", errors="replace").stderr
    m = json.loads(err[err.rfind("{"):err.rfind("}") + 1])
    if m["input_i"] == "-inf":                       # silence: nothing to normalise
        return "anull"
    return (f"loudnorm={LOUDNESS}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
            f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
            f"offset={m['target_offset']}:linear=true")


def bed():
    """What the cut is heard over: the music if set, else footage's own sound."""
    return MUSIC or (SRC if resolve_mode() == "video" else None)


def beat_grid():
    """beats.py's grid for the bed from music_in, cached in build/<video>/ by the
    bed's hash — a re-cut or a new language never re-analyses the same track."""
    if not bed():
        print("! sync is set but there is no bed to take a beat from")
        return dict(beats.EMPTY)
    path = os.path.join(PROJ, "build", VIDEO, "beats.json")
    key = {"sha": index_materials.digest(bed()), "start": MUSIC_IN}
    try:
        cached = json.load(open(path, encoding="utf-8"))
        if {k: cached.get(k) for k in key} == key:
            return cached["grid"]
    except (OSError, ValueError):
        pass
    grid = beats.grid(bed(), MUSIC_IN)
    if not grid["beats"]:
        print(f"! no beat found in {os.path.basename(bed())} — the cut renders unsynced")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(dict(key, grid=grid), open(path, "w", encoding="utf-8"), indent=1)
    return grid


def snap(shots, points, min_shot):
    """Move each cut to the first grid point at or after where it was, keeping
    every shot at least min_shot long. Cuts land on whole frames; the grid is in
    bed time, the cuts in cut time, MUSIC_IN apart. Prints what moved."""
    grid = [p - MUSIC_IN for p in points]
    out, prev, cum = [], 0.0, 0.0
    for a, dur in shots:
        cum += dur
        want = max(cum, prev + min_shot)
        cut = next((g for g in grid if g >= want - 0.5 / FPS), want)
        cut = round(cut * FPS) / FPS
        out.append([a, round(cut - prev, 3)])
        prev = cut
    if grid:
        print("  snap: " + "  ".join(f"{d0:.2f}→{d1:.2f}" for (_, d0), (_, d1) in zip(shots, out)))
        if resolve_mode() == "video":
            src_dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", SRC], capture_output=True, encoding="utf-8", errors="replace").stdout or 0)
            for i, (a, d) in enumerate(out):
                if a + d > src_dur:
                    print(f"! shot {i + 1} now runs to {a + d:.2f}s, past the end of "
                          f"{os.path.basename(SRC)} ({src_dur:.2f}s) — move its in-point back")
    return out if grid else shots


def resolve_mode():
    """slides when the input is images, video when it is a clip."""
    if MODE != "auto":
        return MODE
    if isinstance(SRC, (list, tuple)):
        return "slides"
    if os.path.isdir(SRC):
        return "slides"
    return "slides" if SRC.lower().endswith(IMAGE_EXT) else "video"


def stills():
    if isinstance(SRC, (list, tuple)):
        return list(SRC)
    if os.path.isdir(SRC):
        return sorted(p for p in glob.glob(os.path.join(SRC, "*"))
                      if p.lower().endswith(IMAGE_EXT))
    return [SRC]


def still_for(i, a):
    """The image behind shot i: a filename in the shot entry, else the i-th
    still of `src` in filename order (so a numeric in-point is ignored)."""
    if isinstance(a, str):
        return a if os.path.isabs(a) else os.path.join(VDIR, a)
    imgs = stills()
    if i >= len(imgs):
        sys.exit(f"shot {i + 1} has no still — {len(imgs)} image(s) in src, "
                 f"name the file in shots[{i}][0] or add images")
    return imgs[i]


def run(args):
    p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
    if p.returncode:
        sys.exit(p.stderr.strip() or f"ffmpeg failed: {args}")


def kenburns(i, hits=()):
    """Alternating push-in / pull-out. x/y expressions use `zoom`, not `z`.

    With `hits` (frames inside the shot that fall on a beat) the same 10% move
    is taken in steps, one 6-frame notch per beat, instead of a steady drift.
    """
    if hits:
        steps = "+".join(f"clip((on-{f})/6,0,1)" for f in hits)
        z = f"{'1.0+' if i % 2 == 0 else '1.10-'}{0.10 / len(hits):.4f}*({steps})"
    else:
        z = "min(1.0+0.0009*on,1.10)" if i % 2 == 0 else "max(1.10-0.0009*on,1.0)"
    return (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={W}x{VID_H}:fps={FPS},setsar=1")


def caption_overlay():
    # Overlay position can be a function of t, but scale cannot, so the pop-in
    # is a fade plus a 40px rise rather than a zoom.
    return ("[1:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1[cap];"
            "[bg][cap]overlay=x=0:y='40-40*min(t/0.45,1)':format=auto[v]")


def probe_size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path],
                         capture_output=True, encoding="utf-8", errors="replace").stdout.strip()
    w, h = (int(x) for x in out.split(",")[:2])
    return w, h


def has_audio(path):
    """Whether a file carries an audio stream at all.

    Silent sources are common — drone clips, screen recordings, stock exports —
    and mapping [N:a] from one makes ffmpeg fail the whole final pass after
    every shot has already encoded. Check before, not after.
    """
    return bool(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, encoding="utf-8", errors="replace").stdout.strip())


def crop_rect(src_w, src_h):
    """Widest window-aspect rect that fits inside CROP_BAND, centred on it.

    The band between a source's burned-in overlays is usually the binding
    constraint on framing, not taste. When it is shorter than the window, the
    crop loses width and scales up — which is softer, but keeps the counter or
    the minimap out of frame.
    """
    y0, y1 = CROP_BAND or (0, src_h)
    band = y1 - y0
    cw, ch = min(src_w, round(band * W / VID_H)), 0
    ch = round(cw * VID_H / W)
    if ch > band:
        ch, cw = band, round(band * W / VID_H)
    cw, ch = cw // 2 * 2, ch // 2 * 2
    return (src_w - cw) // 2, y0 + (band - ch) // 2, cw, ch


def build_shot(i, a, dur, cap_png, mode, hits=()):
    """One video-window-sized segment with its framing, push-in and caption.

    Building shots separately keeps caption timing relative to the shot, which
    is trivial, instead of to a global timeline, which is not.
    """
    out = os.path.join(WORK, f"shot{i:02d}.mp4")
    if mode == "slides":
        # Cover-fit the still into the window, then the 2x upscale that keeps
        # zoompan from stair-stepping.
        pre = (f"[0:v]scale={W}:{VID_H}:force_original_aspect_ratio=increase,"
               f"crop={W}:{VID_H},scale={W * 2}:{VID_H * 2},")
        inp = ["-loop", "1", "-t", f"{dur}", "-i", still_for(i, a)]
    else:
        x0, y0, cw, ch = crop_rect(*probe_size(SRC))
        # fps first: zoompan d=1 emits one frame per input frame, so 60 fps
        # footage would otherwise play at half speed and lose half the shot.
        pre = (f"[0:v]fps={FPS},crop={cw}:{ch}:{x0}:{y0},scale={W * 2}:{VID_H * 2},")
        inp = ["-ss", f"{a}", "-t", f"{dur}", "-i", SRC]
    vf = pre + kenburns(i, hits) + "[bg];" + caption_overlay()
    run(["ffmpeg", "-v", "error", "-y", *inp,
         # -t on a looped still is not optional: without it the input never ends
         # and the encode runs away producing an endless file.
         "-loop", "1", "-t", f"{dur}", "-i", cap_png,
         "-filter_complex", vf, "-map", "[v]",
         "-frames:v", str(int(round(dur * FPS))), "-an",
         "-c:v", "libx264", "-preset", "medium", "-crf", "16",
         "-pix_fmt", "yuv420p", "-r", str(FPS), out])
    return out


def pulse(beat_t, total, window=0.08):
    """The beat-sync spec's two hits, on the video window only so the chrome
    holds still: a white flash (Option A) or a 4% zoom punch (Option B)."""
    kind = SYNC.get("pulse")
    on = [b for b in beat_t if 0 <= b < total]
    if not kind or not on:
        return ""
    if kind == "flash":
        return f"drawbox=c=white@0.35:t=fill:enable='{beats.expr(on, window)}',"
    return (f"zoompan=z='1+0.04*({beats.expr(on, window, 'it')})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{VID_H}:fps={FPS},")


def main():
    mode = resolve_mode()
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found")
    preflight()

    chrome = os.path.join(WORK, "chrome.png")
    render_chrome(chrome)

    beat_t = [b - MUSIC_IN for b in GRID["beats"]]     # bed time -> cut time
    segments, start = [], 0.0
    for i, ((a, dur), (l1, l2)) in enumerate(zip(SHOTS, CAPTIONS[LANG])):
        cap = os.path.join(WORK, f"cap{i:02d}.png")
        render_caption(cap, l1, l2)
        hits = ([round((b - start) * FPS) for b in beat_t if start - 0.5 / FPS <= b < start + dur]
                if SYNC.get("kenburns") == "beat" else [])
        segments.append(build_shot(i, a, dur, cap, mode, [max(0, h) for h in hits]))
        start += dur
        print(f"  shot {i + 1}/{len(SHOTS)}", flush=True)

    listfile = os.path.join(WORK, "concat.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for s in segments:
            # The concat demuxer reads backslashes as escapes: Windows paths go
            # forward-slashed, and a quote in a path is closed, escaped, reopened.
            fh.write("file '" + s.replace("\\", "/").replace("'", "'\\''") + "'\n")

    total = sum(dur for _, dur in SHOTS)
    # Audio: one continuous bed, decoupled from the picture cuts — chopping the
    # ambience at every cut sounds broken. Stills have no audio of their own, so
    # slideshows take MUSIC or go silent.
    bed_ = bed()
    if bed_ and not has_audio(bed_):
        print(f"! {os.path.basename(bed_)} has no audio track — "
              f"{'set music in video.json' if mode == 'slides' else 'the cut ships silent'}")
        bed_ = None
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "concat", "-safe", "0", "-i", listfile,
           "-loop", "1", "-t", f"{total}", "-i", chrome]
    vfilter = (f"[0:v]{pulse(beat_t, total)}pad={W}:{H}:0:{VID_Y}:color=white[p];"
               f"[p][1:v]overlay=0:0:format=auto,"
               f"fade=t=in:st=0:d=0.4,fade=t=out:st={total - 0.5:.2f}:d=0.5[v]")
    maps = ["-map", "[v]"]
    if bed_:
        cmd += ["-stream_loop", "-1", "-ss", f"{MUSIC_IN}", "-t", f"{total}", "-i", bed_]
        vfilter += (f";[2:a]afade=t=in:d=0.4,afade=t=out:st={total - 0.8:.2f}:d=0.8[a]")
        maps += ["-map", "[a]", "-c:a", "pcm_s16le"]
    master = os.path.join(WORK, "master.mkv")
    run(cmd + ["-filter_complex", vfilter, *maps, "-t", f"{total}",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", "-r", str(FPS), master])
    # Two-pass loudnorm, as render_plan: one dynamic pass undershoots by ~2 LU.
    audio = (["-af", loudnorm_linear(master), "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
             if bed_ else [])
    run(["ffmpeg", "-v", "error", "-y", "-i", master, "-c:v", "copy", *audio,
         "-movflags", "+faststart", OUT])
    # One frame per second, six across: the whole cut at a glance. Phase 7
    # reads this as an image instead of running the one-liner by hand.
    sheet = os.path.join(WORK, "sheet.jpg")
    run(["ffmpeg", "-v", "error", "-y", "-i", OUT, "-vf",
         f"fps=1,scale=250:-1,tile=6x{max(1, math.ceil(total / 6))}",
         "-frames:v", "1", sheet])
    print(f"\n{OUT}  ({total:.1f}s, {mode}, {LANG}{', ' + RATIO if RATIO else ''})"
          f"\n{sheet}")


def stale():
    """(video, lang, ratio) for every output older than the text it was built
    from — its video.json, brand.json or template. Plain mtimes, no manifest:
    `touch` a params file to force its cuts through again."""
    jobs = []
    for vj in sorted(glob.glob("input/*/video.json")):
        video = os.path.basename(os.path.dirname(vj))
        srcs = [vj, "brand.json", template_path(json.load(open(vj, encoding="utf-8"))["template"], ".")]
        newest = max(os.path.getmtime(p) for p in srcs if os.path.exists(p))
        for out in sorted(glob.glob(f"output/{video}.*.mp4")):
            if os.path.getmtime(out) < newest:
                lang, *ratio = os.path.basename(out)[len(video) + 1:-4].split(".")
                jobs.append((video, lang, ratio[0] if ratio else None))
    return jobs


def cli(argv):
    ap = argparse.ArgumentParser(
        usage="build.py <video> [lang] [--all] [--ratio 9x16,4x5] | --stale | --self-test"
              "   (from the project root)")
    ap.add_argument("video", nargs="?")
    ap.add_argument("lang", nargs="?")
    ap.add_argument("--all", action="store_true", help="every language in video.json")
    ap.add_argument("--ratio", help="comma-separated; the template's native ratio "
                                    "keeps the plain output name")
    ap.add_argument("--stale", action="store_true",
                    help="rebuild every output older than its video.json, brand.json or template")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        import fixture
        return fixture.self_test()
    if a.stale:
        jobs = stale()
        if not jobs:
            print("nothing stale")
    elif a.video:
        vdir, _ = find_video(a.video)
        langs = list(json.load(open(os.path.join(vdir, "video.json"), encoding="utf-8"))["copy"]) if a.all else [a.lang]
        jobs = [(a.video, l, r) for l in langs for r in (a.ratio.split(",") if a.ratio else [None])]
    else:
        ap.error("a video name, --stale or --self-test")
    for video, lang, ratio in jobs:
        configure(video, lang, ratio)
        main()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    cli(sys.argv[1:])
