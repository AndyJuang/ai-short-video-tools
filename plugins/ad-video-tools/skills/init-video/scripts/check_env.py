#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10"]
# ///
"""Check a video-ad project's environment before any copy gets written.

    uv run check_env.py                # every language named in brand.json
    uv run check_env.py tc ko          # only these
    uv run check_env.py --ready        # scaffold only; exit 2 = run init-video
    uv run check_env.py --render [tc]  # tools and caption faces only: render_plan.py
                                       #   in a workspace that is not a video project
    uv run check_env.py --self-test    # check the checker

Verifies what otherwise fails late: ffmpeg on PATH, Pillow importable, the
project scaffold in place with git ignoring media and tracking params,
brand.json parses, every logo exists with real alpha, and every language's
heavy face is installed and can actually draw that language's script.

Run from the project directory (the one holding brand.json and input/).
"""
import json
import os
import shutil
import subprocess
import sys

# The face table and the tofu check live in build.py; import them so the
# checker can never disagree with the builder about which font a language gets.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "video-style-clone", "scripts"))
SAMPLES = {"tc": "繁體中文限時優惠", "sc": "简体中文限时优惠", "jp": "日本語カタカナ限定",
           "ko": "한국어 특별 혜택", "en": "Limited offer"}
PALETTE_KEYS = ("accent", "panel", "ink", "surface", "white", "muted")

fails, warns = [], []


def ok(msg):
    print(f"  ok    {msg}")


def warn(msg):
    warns.append(msg)
    print(f"  warn  {msg}")


def fail(msg):
    fails.append(msg)
    print(f"  FAIL  {msg}")


# How to get each tool, per OS. winget's FFmpeg puts ffmpeg and ffprobe on PATH
# only for terminals opened after the install — restart Claude afterwards.
OS = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
INSTALL = {
    "ffmpeg": {"mac": "brew install ffmpeg", "windows": "winget install Gyan.FFmpeg",
               "linux": "sudo apt install ffmpeg"},
    "uv": {"mac": "brew install uv", "windows": "winget install astral-sh.uv",
           "linux": "curl -LsSf https://astral.sh/uv/install.sh | sh"},
}


def check_tools():
    print(f"tools  ({OS})")
    for exe in ("ffmpeg", "ffprobe"):
        (ok if shutil.which(exe) else fail)(
            f"{exe} on PATH" + ("" if shutil.which(exe) else f" — {INSTALL['ffmpeg'][OS]}"))
    if shutil.which("uv"):
        ok("uv on PATH")
    else:
        warn(f"uv not on PATH — beat sync needs it, and it installs every script's "
             f"dependencies: {INSTALL['uv'][OS]}")
    try:
        import PIL
        ok(f"Pillow {PIL.__version__}")
    except ImportError:
        fail("Pillow not importable — pip install Pillow")


def check_project():
    """The scaffold, and whether git is drawing the line in the right place.

    Asked of git rather than of .gitignore's text: a stray `*.json` or a broad
    `*` ignores the params too, and only `git check-ignore` knows.
    """
    print("project")
    for d in ("input", "output", "assets"):
        (ok if os.path.isdir(d) else fail)(f"{d}/ exists")
    if subprocess.run(["git", "-c", "core.fsmonitor=false", "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode:
        warn("not a git repo — params and material indexes are not versioned")
        return
    ok("git repo")
    ignored = lambda p: not subprocess.run(["git", "-c", "core.fsmonitor=false", "check-ignore", "-q", p],
                                           capture_output=True).returncode
    for probe in ("input/x/clip.mp4", "input/x/still.jpg", "input/x/music.wav",
                  "input/x/.sheets/clip.mp4.jpg", "output/x.tc.mp4", "build/x/tc/shot00.mp4"):
        if not ignored(probe):
            fail(f".gitignore does not cover {probe} — media would be committed")
    for probe in ("input/x/video.json", "input/x/materials.json", "brand.json"):
        if ignored(probe):
            fail(f".gitignore swallows {probe} — that is the record git is here to keep")
    if os.path.exists(".env") and not ignored(".env"):
        fail(".env is not gitignored — its API keys would be committed; add `.env` to .gitignore")
    if ignored("input/x/edit_plan.yaml"):     # warn: projects scaffolded before /video-edit
        warn(".gitignore swallows input/*/edit_plan.yaml (video-edit's plan) — "
             "add `!input/**/edit_plan.yaml` under the materials.json line")


def load_brand(path="brand.json"):
    print("brand.json")
    if not os.path.exists(path):
        fail(f"{path} missing — copy assets/brand.example.json into the project")
        return {}
    try:
        brand = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"{path} does not parse: {e}")
        return {}
    for key in brand:
        if key not in ("palette", "logo", "fonts", "voice", "narrator") and not key.startswith("_"):
            warn(f"unknown key {key!r} — nothing reads it")
    for key, val in brand.get("palette", {}).items():
        if key not in PALETTE_KEYS:
            warn(f"palette.{key} is not a key any template names ({', '.join(PALETTE_KEYS)})")
        elif not (str(val).startswith("#") or str(val).startswith("palette-color-")):
            fail(f"palette.{key} = {val!r} is not a hex colour or palette-color-N name")
    missing = [k for k in PALETTE_KEYS if k not in brand.get("palette", {})]
    if missing:
        warn(f"palette falls back to template defaults for: {', '.join(missing)}")
    if not brand.get("voice"):
        warn("no voice block — copy drafting has no register, no avoid-list, no must-carry")
    ok(f"parsed, {len(brand.get('logo', {}))} logo(s), "
       f"{len(brand.get('fonts', {}))} font override(s)")
    return brand


def check_logo(path):
    """Report on one logo file. Returns a list of (level, message)."""
    from PIL import Image
    if not os.path.exists(path):
        return [("fail", f"{path} missing")]
    im = Image.open(path)
    out = [("ok", f"{path}  {im.width}x{im.height} {im.mode}")]
    if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
        out.append(("warn", f"{path} has no alpha channel — only safe over matching chrome"))
    elif im.convert("RGBA").getchannel("A").getextrema()[0] > 250:
        out.append(("warn", f"{path} has an alpha channel but no transparent pixel — "
                            f"background is baked in, only safe over matching chrome"))
    if im.height < 120:
        out.append(("warn", f"{path} is {im.height}px tall; the bar draws a logo at "
                            f"~120px, so it will be upscaled and soft"))
    return out


def check_logos(brand):
    print("logos")
    logos = brand.get("logo") or {"default": "logo.png"}
    if "default" not in logos:
        warn("no logo.default — any language without its own entry falls back to logo.png")
    for lang, path in logos.items():
        for level, msg in check_logo(path):
            {"ok": ok, "warn": warn, "fail": fail}[level](f"[{lang}] {msg}")


def check_fonts(brand, langs):
    from build import FONT_DIRS, KO_SYSTEM, USER_FONTS, candidates, locate, missing_glyphs
    print(f"fonts  (searching {', '.join(d for d in FONT_DIRS if os.path.isdir(d))})")
    for lang in langs:
        if lang not in SAMPLES:
            warn(f"[{lang}] no default face — build falls back to the tc faces")
        override = brand.get("fonts", {}).get(lang, {})
        for slot in ("heavy", "body"):
            names = candidates(lang, slot, override.get(slot))
            face = next(filter(None, map(locate, names)), None)
            if not face:
                msg = f"[{lang}] {slot} face not installed: {' or '.join(names)}"
                if lang == "ko" and slot == "heavy" and not override and locate(KO_SYSTEM[0]):
                    warn(msg + f" — build falls back to {KO_SYSTEM[0].split('#')[0]}, which "
                               f"reads lighter. See references/gotchas.md to install it.")
                else:
                    fail(msg + f" — install into {USER_FONTS}")
                continue
            bad = missing_glyphs(face, SAMPLES.get(lang, SAMPLES["en"]))
            if bad:
                fail(f"[{lang}] {slot} face {os.path.basename(face[0])} cannot draw: "
                     f"{''.join(bad)} — copy would render as tofu")
            else:
                ok(f"[{lang}] {slot} {os.path.basename(face[0])}")


def check_speech(brand):
    """Offline: is a key where transcribe.py / tts.py look? Optional: info, or a warn when a narrator is set."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "video-edit", "scripts"))
    from transcribe import find_key
    print("speech  (optional: video-edit transcription and narration; never a FAIL)")
    val, where = find_key()
    if val:
        ok(f"ELEVENLABS_API_KEY in {where}" + (" (1Password reference)" if val.startswith("op://") else "")
           + " — run transcribe.py --doctor and tts.py --doctor to check it works")
    elif brand.get("narrator"):
        warn("brand.json sets a narrator but no ELEVENLABS_API_KEY is in the project's .env, the "
             "environment or ~/.ad-video-tools/.env — see init-video Phase 3b")
    else:
        print("  --    no ELEVENLABS_API_KEY (or still a {placeholder}) — fine unless you'll transcribe or narrate (Phase 3b)")
    val, where = find_key("GEMINI_API_KEY")
    print("generated stills and clips  (optional: video-edit fills gaps with Gemini images / Veo clips; never a FAIL)")
    if val:
        ok(f"GEMINI_API_KEY in {where}" + (" (1Password reference)" if val.startswith("op://") else "")
           + " — run generate.py --doctor to check it works")
    else:
        print("  --    no GEMINI_API_KEY (or still a {placeholder}) — fine unless a plan's gaps get generated stills (Phase 3c)")
    val, where = find_key("FREESOUND_API_KEY")
    print("sound effects  (optional: sfx.py's starter set needs no key; Freesound searches do)")
    if val:
        ok(f"FREESOUND_API_KEY in {where} — run sfx.py --doctor to check it works")
    else:
        print("  --    no FREESOUND_API_KEY (or still a {placeholder}) — fine unless a cut needs a specific sound (Phase 3d)")


def self_test():
    """The logo report is the only non-obvious logic here; check it."""
    import tempfile
    from PIL import Image
    d = tempfile.mkdtemp()
    clear = os.path.join(d, "clear.png")
    Image.new("RGBA", (400, 200), (0, 0, 0, 0)).save(clear)
    assert not [m for lvl, m in check_logo(clear) if lvl != "ok"], "clear logo flagged"

    baked = os.path.join(d, "baked.png")
    Image.new("RGBA", (400, 200), (255, 255, 255, 255)).save(baked)
    assert any("baked in" in m for lvl, m in check_logo(baked)), "baked background missed"

    flat = os.path.join(d, "flat.jpg")
    Image.new("RGB", (400, 200), (255, 255, 255)).save(flat)
    assert any("no alpha" in m for lvl, m in check_logo(flat)), "missing alpha missed"

    small = os.path.join(d, "small.png")
    Image.new("RGBA", (200, 40), (0, 0, 0, 0)).save(small)
    assert any("upscaled" in m for lvl, m in check_logo(small)), "undersized logo missed"

    assert check_logo(os.path.join(d, "nope.png"))[0][0] == "fail", "missing file missed"
    print("self-test ok")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "--self-test" in sys.argv:
        return self_test()
    if "--trust" in sys.argv:              # init-video, once per project: its .env may supply keys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "..", "video-edit", "scripts"))
        from transcribe import config_path, trust
        print(f"trusted: {trust()} (listed in {config_path('projects.txt')})")
        return
    check_tools()
    if fails:
        sys.exit("\nfix the tooling first — nothing below can be checked without it")
    if "--render" in sys.argv:
        check_fonts({}, args or ["tc"])
        check_speech({})
        print(f"\n{len(fails)} fail, {len(warns)} warn")
        sys.exit(1 if fails else 0)
    check_project()
    if "--ready" in sys.argv:
        print(f"\n{len(fails)} fail, {len(warns)} warn")
        sys.exit(2 if fails else 0)          # 2 = scaffold it, then re-run
    brand = load_brand()
    check_logos(brand)
    named = set(brand.get("logo", {})) | set(brand.get("fonts", {})) - {"default"}
    check_fonts(brand, args or sorted(named - {"default"}) or ["tc"])
    check_speech(brand)
    print(f"\n{len(fails)} fail, {len(warns)} warn")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
