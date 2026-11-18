#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6"]
# ///
"""Voice an edit plan's voiceover.lines with ElevenLabs text-to-speech.

    uv run tts.py <edit_plan.yaml> <asset_dir> [--voice ID] [--lang tc|jp|en] [--brand brand.json]
    uv run tts.py --doctor [--voice ID] [--lang tc]   # voice 「測試」 once: key, permission, plan, voice
    uv run tts.py --self-test    # offline, stubbed synthesis

Each line goes to <asset_dir>/<line.asset_file>, default vo/<id>.mp3, the path
render_plan.py looks for; it then times each voiced scene to its lines.
A line whose file exists and was voiced from the same text and voice is skipped,
so editing one line re-bills one line. Each take is saved with the silence before
and after the speech cut off, since render_plan times scenes to the file's length.
A take far longer than its text could take to say is a runaway and is voiced again.

Voice: --voice, else brand.json narrator.<lang>, else stop. Lang: --lang, else the
plan's `lang`, else tc. Key: same as transcribe.py.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import find_key, load_key  # noqa: E402

API = "https://api.elevenlabs.io/v1/text-to-speech/{}?output_format=mp3_44100_128"
MODEL = "eleven_multilingual_v2"    # tc / jp / en from one model
MAX_TAKES = 3
SILENCE = "silenceremove=start_periods=1:start_threshold=-45dB"


def synth(text, voice, key):
    req = urllib.request.Request(API.format(voice), json.dumps({"text": text, "model_id": MODEL}).encode(),
                                 {"xi-api-key": key, "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=120).read()
    except urllib.error.HTTPError as e:
        sys.exit(f"ElevenLabs {e.code}: {e.read().decode(errors='replace')[:300]}")


def trimmed(audio):
    """(`audio` with the silence before and after the speech cut off, its length in s).
    silenceremove only trims the start, so the second pass runs on the reversed clip."""
    with tempfile.TemporaryDirectory(prefix="tts-") as d:
        src, dst = os.path.join(d, "raw.mp3"), os.path.join(d, "trim.mp3")
        open(src, "wb").write(audio)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-af",
                        f"{SILENCE},areverse,{SILENCE},areverse", "-b:a", "128k", dst], check=True)
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", dst], capture_output=True, text=True).stdout
        return open(dst, "rb").read(), float(dur or 0)


def take(say, text, line_id):
    """One voiced, trimmed take of `text` as mp3 bytes. `say(text)` returns raw audio
    and bills one synthesis per call."""
    # ponytail: per-character ceiling, loose for English (counts letters, not syllables)
    ceiling = len(text) * 0.6 + 2.0
    for n in range(1, MAX_TAKES + 1):
        audio, dur = trimmed(say(text))
        if dur <= ceiling:
            return audio
        print(f"  {line_id}: take {n} ran {dur:.1f}s (ceiling {ceiling:.1f}s) — voicing again", flush=True)
    sys.exit(f"voiceover line {line_id}: {MAX_TAKES} takes all ran past {ceiling:.1f}s — "
             f"reword the line and run tts.py again")


def confined(asset_dir, rel, what):
    """asset_dir/rel, refusing anything outside asset_dir (`../`, an absolute path,
    an id with a slash), since this path is written to."""
    root = os.path.realpath(asset_dir)
    p = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, p]) != root:
        sys.exit(f"{what}: {p} is outside {root} — refusing to write there")
    return p


def line_path(asset_dir, ln):
    """Where a line's audio goes."""
    return confined(asset_dir, ln.get("asset_file") or f"vo/{ln['id']}.mp3", f"voiceover line {ln.get('id')}")


def pick_voice(voice, lang, brand):
    if not voice:
        brand = brand or ("brand.json" if os.path.exists("brand.json") else None)
        voice = (json.load(open(brand, encoding="utf-8")).get("narrator") or {}).get(lang) if brand else None
    if not voice:
        sys.exit(f"no voice for {lang!r}: pass --voice <voice_id> or set brand.json narrator.{lang}")
    return voice


def voice_plan(plan_path, asset_dir, voice=None, lang=None, brand=None, say=None):
    plan = yaml.safe_load(open(plan_path, encoding="utf-8"))
    lines = (plan.get("voiceover") or {}).get("lines") or []
    if not lines:
        sys.exit(f"{plan_path}: no voiceover.lines")
    lang = lang or plan.get("lang") or "tc"
    voice = pick_voice(voice, lang, brand)
    key = None
    done = 0
    for ln in lines:
        out, stamp = line_path(asset_dir, ln), {"text": ln["text"], "voice": voice, "model": MODEL}
        side = confined(asset_dir, out + ".json", f"voiceover line {ln.get('id')} stamp")   # a planted symlink is refused too
        if os.path.exists(out) and os.path.exists(side) and json.load(open(side, encoding="utf-8")) == stamp:
            continue
        if say is None:
            key = key or load_key()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        open(out, "wb").write(take(say or (lambda t: synth(t, voice, key)), ln["text"], ln["id"]))
        json.dump(stamp, open(side, "w", encoding="utf-8"), ensure_ascii=False)
        done += 1
        print(f"  {ln['id']}  {ln['text']}", flush=True)
    print(f"{done} voiced, {len(lines) - done} unchanged")
    return done


def self_test():
    d = tempfile.mkdtemp(prefix="tts-test-")

    def say(text):      # a tone 0.25 s per character stands in for speech
        p = os.path.join(d, "say.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"sine=f=300:d={0.25 * len(text)}", p], check=True)
        return open(p, "rb").read()

    plan = {"lang": "tc", "voiceover": {"lines": [{"id": "s01a", "scene": 1, "text": "你也是"},
                                                  {"id": "s02a", "scene": 2, "text": "防曬",
                                                   "asset_file": "narr/two.mp3"}]}}
    pp = os.path.join(d, "edit_plan.yaml")
    yaml.safe_dump(plan, open(pp, "w", encoding="utf-8"), allow_unicode=True)
    assert voice_plan(pp, d, voice="v1", say=say) == 2
    assert os.path.exists(os.path.join(d, "vo", "s01a.mp3")) and os.path.exists(os.path.join(d, "narr", "two.mp3"))
    assert voice_plan(pp, d, voice="v1", say=say) == 0, "unchanged lines re-voiced"
    plan["voiceover"]["lines"][0]["text"] = "你也是嗎"
    yaml.safe_dump(plan, open(pp, "w", encoding="utf-8"), allow_unicode=True)
    assert voice_plan(pp, d, voice="v1", say=say) == 1, "edited line not re-voiced"
    assert voice_plan(pp, d, voice="v2", say=say) == 2, "new voice not re-voiced"
    json.dump({"narrator": {"jp": "vj"}}, open(os.path.join(d, "brand.json"), "w"))
    try:
        voice_plan(pp, d, brand=os.path.join(d, "brand.json"), say=say)
        raise AssertionError("tc voiced with no tc narrator")
    except SystemExit as e:
        assert "narrator.tc" in str(e), e
    for bad in ({"id": "x", "asset_file": "../escape.mp3"}, {"id": "../../x"},
                {"id": "x", "asset_file": os.path.join(d, "..", "abs.mp3")}):
        try:
            line_path(os.path.join(d, "assets"), bad)
            raise AssertionError(f"{bad} escaped the asset folder")
        except SystemExit as e:
            assert "outside" in str(e), e
    assert line_path(d, {"id": "s01a"}) == os.path.join(os.path.realpath(d), "vo", "s01a.mp3")

    def tone(sec, pad=0.0):     # `sec` of tone with `pad` of silence either side
        p = os.path.join(d, "tone.mp3")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=300:d={sec}",
                        "-af", f"adelay={int(pad * 1000)},apad=pad_dur={pad}", p], check=True)
        return open(p, "rb").read()
    _, dur = trimmed(tone(1.0, pad=0.6))
    assert abs(dur - 1.0) < 0.1, f"padding kept: {dur:.2f}s for 1.0 s of speech"
    calls = []

    def runaway_once(text):     # first take runs on for 30 s, the second is normal
        calls.append(text)
        return tone(30.0 if len(calls) == 1 else 1.0)
    take(runaway_once, "你好", "s01a")
    assert len(calls) == 2, f"runaway take kept after {len(calls)} call(s)"
    try:
        take(lambda t: tone(30.0), "你好", "s01a")
        raise AssertionError("a line that always runs away was accepted")
    except SystemExit as e:
        assert "s01a" in str(e), e
    print(f"self-test ok  ({d})")


def main():
    ap = argparse.ArgumentParser(usage=__doc__.split("\n\n")[1])
    ap.add_argument("plan", nargs="?")
    ap.add_argument("assets", nargs="?")
    ap.add_argument("--voice")
    ap.add_argument("--lang")
    ap.add_argument("--brand")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.doctor:          # ElevenLabs' own error text names the fix (permission, plan, voice id)
        lang = a.lang or "tc"
        voice = pick_voice(a.voice, lang, a.brand)
        synth("測試", voice, load_key())
        return print(f"text-to-speech: ok ({lang}, voice {voice}, key from {find_key()[1]})")
    if not (a.plan and a.assets):
        ap.error("an edit plan and its asset directory, or --self-test")
    voice_plan(a.plan, a.assets, a.voice, a.lang, a.brand)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
