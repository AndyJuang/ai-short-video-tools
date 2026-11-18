#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10"]
# ///
"""Index the materials of one video, so git has a record of files it never holds.

    uv run index_materials.py spring-sale       # write input/spring-sale/materials.json
    uv run index_materials.py --all
    uv run index_materials.py spring-sale --render ko <drive url>   # where a cut went
    uv run index_materials.py <video|dir> --sheets   # + a timestamped contact sheet per clip
    uv run index_materials.py --self-test

Footage and stills are gitignored — they are too big, and they already live in
Drive. What gets committed instead is this index: what each file was, how long,
how big, and where it came from. Re-run it whenever material is added.

Two things it will not overwrite: `source` (the Drive or client link, filled in
by hand) and any other key you add. Entries whose file is gone are kept and
marked `"present": false` — losing the Drive link the day someone clears the
local copy is the exact failure this file exists to prevent.

`--render <cut> <url>` records the other direction under a `renders` key: the
link a finished output/<video>.<cut>.mp4 was pushed to, its hash and when. The
upload itself is Claude's job (Drive MCP or the synced folder); this only writes
it down.

`--sheets` is for picking footage (video-edit): every clip also gets
`.sheets/<file>.jpg`, one labelled frame per `sheet_step` seconds, and its scene
`cuts`, so Claude can read a folder of raw takes as images and name an in-point.
The argument may be any directory, e.g. an ACOS `content/assets/<id>/raw/`.
Sheets are only redrawn when a file's hash changes.
"""
import glob
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ffsafe import check, media  # noqa: E402  refuse playlist/list files posing as media

MEDIA_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm",
             ".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff",
             ".mp3", ".wav", ".m4a", ".aac")
INDEX = "materials.json"
RENDERS = "renders"          # the one top-level key that is not a material
SHEETS = ".sheets"
VIDEO_EXT = MEDIA_EXT[:6]


def load(vdir):
    path = os.path.join(vdir, INDEX)
    return path, (json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {})


def writable(p):
    """p, unless it is a symlink: the material folder may hold files the user didn't make."""
    if os.path.islink(p):
        sys.exit(f"{p} is a symlink — refusing to write through it")
    return p


def save(path, entries):
    json.dump(entries, open(writable(path), "w", encoding="utf-8"), indent=2, ensure_ascii=False, sort_keys=True)
    open(path, "a", encoding="utf-8").write("\n")


def digest(path):
    """Short content hash: size plus the head and tail of the file.

    ponytail: partial hash, 4MB from each end. Distinguishes takes and catches a
    re-export; a full sha256 of a 2GB master costs minutes for no more certainty
    than this buys. Swap in a full read if you ever need to prove identity.
    """
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(4 << 20))
        if size > 8 << 20:
            fh.seek(-(4 << 20), os.SEEK_END)
            h.update(fh.read())
    return h.hexdigest()[:12]


def probe(path):
    """Duration and resolution, from ffprobe or — for stills — from Pillow.
    Nothing for a file ffsafe won't pass: it is indexed, never opened by ffmpeg."""
    if check(path):
        return {}
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, encoding="utf-8", errors="replace").stdout.split()
    info = {}
    if len(out) >= 2 and out[0].isdigit():
        info["res"] = f"{out[0]}x{out[1]}"
    dur = next((v for v in out if "." in v), None)
    if dur and float(dur) > 0:
        info["dur"] = round(float(dur), 2)
    if "res" not in info:
        try:
            from PIL import Image
            with Image.open(path) as im:
                info["res"] = f"{im.width}x{im.height}"
        except Exception:
            pass
    return info


def sheet(vdir, name, dur, cols=6, max_tiles=36):
    """Contact sheet with each tile's timestamp burned in, plus scene cuts.
    One tile per second up to 36 s, coarser after — enough to name an in-point
    to the second, which is what the edit plan's `source_in_sec` takes."""
    from PIL import Image, ImageDraw, ImageFont
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import measure
    full = os.path.join(vdir, name)
    step = max(1, math.ceil(dur / max_tiles))
    tmp = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-v", "error", "-i", media(full), "-vf", f"fps=1/{step},scale=240:-2",
                    os.path.join(tmp, "%03d.png")], capture_output=True)
    frames = sorted(glob.glob(os.path.join(tmp, "*.png")))
    if not frames:
        shutil.rmtree(tmp, ignore_errors=True)
        return {}
    tw, th = Image.open(frames[0]).size
    rows = math.ceil(len(frames) / cols)
    im = Image.new("RGB", (cols * tw, rows * th), "black")
    d, fnt = ImageDraw.Draw(im), ImageFont.load_default(22)
    for k, f in enumerate(frames):
        x, y = k % cols * tw, k // cols * th
        im.paste(Image.open(f), (x, y))
        d.text((x + 6, y + 4), f"{k * step}s", font=fnt, fill="yellow", stroke_width=2, stroke_fill="black")
    os.makedirs(writable(os.path.join(vdir, SHEETS)), exist_ok=True)
    rel = f"{SHEETS}/{name}.jpg"
    im.save(writable(os.path.join(vdir, rel)), quality=85)
    shutil.rmtree(tmp, ignore_errors=True)
    return {"sheet": rel, "sheet_step": step,
            "cuts": [round(t, 2) for t in measure.scene_cuts(full)]}


def index(vdir, sheets=False):
    path, old = load(vdir)
    renders = old.pop(RENDERS, None)
    entries = {name: dict(rec, present=False) for name, rec in old.items()}
    for name in sorted(os.listdir(vdir)):
        if not name.lower().endswith(MEDIA_EXT):
            continue
        full = os.path.join(vdir, name)
        rec = dict(entries.get(name, {}))
        was = rec.get("sha")
        rec.update(bytes=os.path.getsize(full), sha=digest(full), present=True,
                   **probe(full))
        if sheets and name.lower().endswith(VIDEO_EXT) and rec.get("dur") and not (
                rec["sha"] == was and os.path.exists(os.path.join(vdir, rec.get("sheet", "")))):
            rec.update(sheet(vdir, name, rec["dur"]))
        rec.setdefault("source", "")          # paste the Drive link here
        entries[name] = rec
    gone = [n for n, r in entries.items() if not r["present"]]
    print(f"{path}: {len(entries) - len(gone)} present"
          + (f", {len(gone)} recorded but missing ({', '.join(gone)})" if gone else ""))
    unsourced = [n for n, r in entries.items() if r["present"] and not r["source"]]
    if unsourced:
        print(f"  no source link yet: {', '.join(unsourced)}")
    if renders:
        entries[RENDERS] = renders
    save(path, entries)
    return entries


def record_render(vdir, cut, url):
    """Where output/<video>.<cut>.mp4 was pushed. Hash and time come from the
    file, so a re-render after the upload shows as a mismatch, not a mystery."""
    vdir = os.path.abspath(vdir)
    video = os.path.basename(vdir)
    out = os.path.join(os.path.dirname(os.path.dirname(vdir)), "output", f"{video}.{cut}.mp4")
    if not os.path.exists(out):
        sys.exit(f"no render at {out} — build it before recording where it went")
    path, entries = load(vdir)
    entries.setdefault(RENDERS, {})[cut] = {
        "url": url, "sha": digest(out),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    save(path, entries)
    print(f"{path}: renders.{cut} -> {url}")


def resolve(arg):
    for c in (os.path.join("input", arg), arg):
        if os.path.isdir(c):
            return c
    sys.exit(f"no such video directory: input/{arg}")


def self_test():
    import tempfile
    proj = tempfile.mkdtemp()
    d = os.path.join(proj, "input", "clip")
    os.makedirs(d)
    os.makedirs(os.path.join(proj, "output"))
    open(os.path.join(d, "clip.mp4"), "wb").write(b"not really a video")
    open(os.path.join(d, "notes.txt"), "w", encoding="utf-8").write("ignored")
    first = index(d)
    assert set(first) == {"clip.mp4"}, f"non-media indexed: {set(first)}"

    first["clip.mp4"]["source"] = "https://drive.google.com/file/d/abc"
    json.dump(first, open(os.path.join(d, INDEX), "w", encoding="utf-8"))
    open(os.path.join(d, "clip.mp4"), "wb").write(b"a different take entirely")
    second = index(d)
    assert second["clip.mp4"]["source"] == first["clip.mp4"]["source"], "source lost"
    assert second["clip.mp4"]["sha"] != first["clip.mp4"]["sha"], "recut not noticed"

    os.remove(os.path.join(d, "clip.mp4"))
    third = index(d)
    assert third["clip.mp4"]["present"] is False, "missing file dropped"
    assert third["clip.mp4"]["source"], "link to a missing file lost"

    open(os.path.join(proj, "output", "clip.ko.mp4"), "wb").write(b"a render")
    record_render(d, "ko", "https://drive.google.com/file/d/render")
    fourth = index(d)
    assert fourth[RENDERS]["ko"]["url"].endswith("/render"), "render link lost on re-index"
    assert "present" not in fourth[RENDERS], "renders treated as a material"

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=320x568:r=30:d=2",
                    "-f", "lavfi", "-i", "color=blue:s=320x568:r=30:d=3", "-filter_complex",
                    "concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p", os.path.join(d, "take1.mp4")], check=True)
    rec = index(d, sheets=True)["take1.mp4"]
    assert os.path.exists(os.path.join(d, rec["sheet"])), "no contact sheet"
    assert rec["sheet_step"] == 1 and any(abs(c - 2.0) < 0.1 for c in rec["cuts"]), rec
    mtime = os.path.getmtime(os.path.join(d, rec["sheet"]))
    index(d, sheets=True)
    assert os.path.getmtime(os.path.join(d, rec["sheet"])) == mtime, "unchanged clip re-sheeted"
    print("self-test ok")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if "--self-test" in args:
        self_test()
    elif "--render" in args:
        i = args.index("--render")
        if i != 1 or len(args) != 4:
            sys.exit("usage: index_materials.py <video> --render <cut> <url>")
        record_render(resolve(args[0]), args[2], args[3])
    elif "--all" in args:
        for name in sorted(os.listdir("input")):
            if os.path.isdir(os.path.join("input", name)):
                index(os.path.join("input", name))
    elif args:
        index(resolve(args[0]), sheets="--sheets" in args)
    else:
        sys.exit("usage: index_materials.py <video> | --all")
