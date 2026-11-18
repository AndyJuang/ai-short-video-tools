#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10"]
# ///
"""Measure a reference video's layout and edit rhythm, and profile the source
footage that has to fill it.

    uv run measure.py REFERENCE.mp4 [SOURCE.mp4] --out build/<video>/measure

Writes contact sheets and a full-res reference frame to the output directory,
and prints:
  - format of each file (size, aspect, duration, fps, audio)
  - flat-colour chrome bands vs. the moving video window in the reference
  - the reference's scene cuts, as shot durations
  - static burned-in regions in the source, and the safe crop band between them
  - loudness of both, so the rebuild's mix can match
  - the reference's tempo, so a bed can be picked to match it (beats.py)

Needs ffmpeg/ffprobe on PATH and Pillow.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import beats  # noqa: E402  runs librosa in its own uv env
from ffsafe import media  # noqa: E402  refuse playlist/list files posing as media


def sh(args):
    p = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
    return p.stdout.strip()


def probe(path):
    out = sh(["ffprobe", "-v", "error", "-print_format", "json",
              "-show_format", "-show_streams", media(path)])
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    return {
        "w": v["width"], "h": v["height"],
        "fps": round(num / den, 3) if den else 0,
        "dur": float(d["format"]["duration"]),
        "audio": f"{a['codec_name']} {a['sample_rate']}Hz {a['channels']}ch" if a else "none",
    }


def aspect(w, h):
    from math import gcd
    g = gcd(w, h)
    return f"{w // g}:{h // g}"


def contact_sheet(path, dst, dur, cols=6, rows=5):
    """One tile per second-ish, sized to cover the whole clip."""
    step = max(1, round(dur / (cols * rows)))
    sh(["ffmpeg", "-v", "error", "-y", "-i", media(path),
        "-vf", f"fps=1/{step},scale=250:-1,tile={cols}x{rows}",
        "-frames:v", "1", dst])
    return step


def scene_cuts(path, threshold=0.25):
    """Through -i and showinfo, not a movie= source: a filtergraph cannot hold a
    Windows path, whose drive colon reads as an option separator."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", media(path), "-an",
                        "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                       capture_output=True, encoding="utf-8", errors="replace")
    return [float(t) for t in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def loudness(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", media(path),
                        "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, encoding="utf-8", errors="replace")
    vals = {}
    for line in p.stderr.splitlines():
        for key in ("mean_volume", "max_volume"):
            if key in line:
                vals[key] = line.split(":")[-1].strip()
    return vals


def bands(frame_path):
    """Rows where both side edges hold the same flat colour are chrome; rows
    where they differ are picture. Returns [(y0, y1, 'chrome #rrggbb'|'video')].
    """
    im = Image.open(frame_path).convert("RGB")
    px, w, h = im.load(), im.width, im.height
    kinds = []
    for y in range(h):
        left, right = px[3, y], px[w - 4, y]
        kinds.append(left if left == right else None)
    runs, start = [], 0
    for y in range(1, h + 1):
        same = y < h and kinds[y] == kinds[start]
        if not same:
            k = kinds[start]
            label = "chrome #%02x%02x%02x" % k if k else "video"
            if y - start >= 8:                     # ignore 1px seams
                runs.append((start, y - 1, label))
            start = y
    # merge adjacent runs carrying the same label
    merged = []
    for r in runs:
        if merged and merged[-1][2] == r[2]:
            merged[-1] = (merged[-1][0], r[1], r[2])
        else:
            merged.append(list(r))
    return [tuple(m) for m in merged]


def static_regions(path, dur, samples=8, grid=24):
    """Pixels that never change across the clip are burned-in graphics.

    Samples frames spread over the clip, reduces each to a coarse grid, and
    reports grid rows/cells whose values stay put. Returns (rows, cells) where
    rows is a list of (y0, y1) pixel bands that contain frozen content.
    """
    frames, tmpdir = [], tempfile.mkdtemp(prefix="vsc-static-")
    for i in range(samples):
        t = dur * (i + 0.5) / samples
        tmp = os.path.join(tmpdir, f"{i}.png")
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", media(path),
            "-frames:v", "1", "-vf", f"scale={grid * 2}:{grid * 2}", tmp])
        if os.path.exists(tmp):
            frames.append(Image.open(tmp).convert("L").load())
    if len(frames) < 3:
        return [], 0
    n = grid * 2
    frozen = [[True] * n for _ in range(n)]
    for y in range(n):
        for x in range(n):
            vals = [f[x, y] for f in frames]
            if max(vals) - min(vals) > 12:
                frozen[y][x] = False
    shutil.rmtree(tmpdir, ignore_errors=True)
    # a grid row counts as overlay if a meaningful share of it never moves
    rows, run = [], None
    for y in range(n):
        share = sum(frozen[y]) / n
        hit = share > 0.30
        if hit and run is None:
            run = y
        elif not hit and run is not None:
            rows.append((run, y - 1))
            run = None
    if run is not None:
        rows.append((run, n - 1))
    return rows, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("source", nargs="?")
    ap.add_argument("--out", default=None)   # default: a fresh private temp folder, not a fixed /tmp name
    args = ap.parse_args()
    args.out = args.out or tempfile.mkdtemp(prefix="vsc-")

    os.makedirs(args.out, exist_ok=True)
    ref = probe(args.reference)

    print(f"\n=== REFERENCE  {os.path.basename(args.reference)}")
    print(f"  {ref['w']}x{ref['h']} ({aspect(ref['w'], ref['h'])})  "
          f"{ref['dur']:.2f}s  {ref['fps']}fps  audio: {ref['audio']}")
    print(f"  loudness: {loudness(args.reference)}")
    if ref["audio"] != "none":
        g = beats.grid(args.reference)
        print(f"  tempo: {g['bpm']} bpm" if g["beats"] else "  tempo: no clear beat")

    frame = os.path.join(args.out, "ref_frame.png")
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{ref['dur'] * 0.6:.2f}",
        "-i", media(args.reference), "-frames:v", "1", frame])
    sheet = os.path.join(args.out, "ref_sheet.jpg")
    step = contact_sheet(args.reference, sheet, ref["dur"])
    print(f"  frame  -> {frame}")
    print(f"  sheet  -> {sheet}   ({step}s per tile)")

    print("\n  layout bands (y0..y1, height):")
    for y0, y1, label in bands(frame):
        print(f"    {y0:5d}..{y1:<5d} {y1 - y0 + 1:5d}px  {label}")

    cuts = scene_cuts(args.reference)
    if cuts:
        marks = [0.0] + cuts + [ref["dur"]]
        durs = [round(b - a, 2) for a, b in zip(marks, marks[1:])]
        print(f"\n  scene cuts at: {', '.join(f'{c:.2f}' for c in cuts)}")
        print(f"  shot lengths : {durs}")
        print(f"  {len(durs)} shots, mean {sum(durs) / len(durs):.2f}s")

    if not args.source:
        return

    src = probe(args.source)
    print(f"\n=== SOURCE  {os.path.basename(args.source)}")
    print(f"  {src['w']}x{src['h']} ({aspect(src['w'], src['h'])})  "
          f"{src['dur']:.2f}s  {src['fps']}fps  audio: {src['audio']}")
    print(f"  loudness: {loudness(args.source)}")

    ssheet = os.path.join(args.out, "src_sheet.jpg")
    step = contact_sheet(args.source, ssheet, src["dur"])
    print(f"  sheet  -> {ssheet}   ({step}s per tile)")

    rows, n = static_regions(args.source, src["dur"])
    if rows:
        print("\n  static (burned-in) bands — your crop must avoid these:")
        for a, b in rows:
            y0, y1 = round(a * src["h"] / n), round((b + 1) * src["h"] / n)
            print(f"    y {y0}..{y1}")
        # widest gap between frozen bands
        edges = [0] + [v for a, b in rows
                       for v in (round(a * src['h'] / n),
                                 round((b + 1) * src['h'] / n))] + [src["h"]]
        gaps = [(edges[i], edges[i + 1]) for i in range(0, len(edges) - 1, 2)]
        best = max(gaps, key=lambda g: g[1] - g[0], default=None)
        if best:
            print(f"  widest clear band: y {best[0]}..{best[1]} "
                  f"({best[1] - best[0]}px tall)")
        print("  NOTE: this finds frozen pixels, so an overlay whose content "
              "changes\n        (a step counter, a timecode) will NOT appear "
              "here. Confirm against a\n        full-res frame before locking "
              "the crop.")
    else:
        print("\n  no burned-in overlays detected")

    vid = [b for b in bands(frame) if b[2] == "video"]
    if vid:
        y0, y1, _ = max(vid, key=lambda b: b[1] - b[0])
        win_h = y1 - y0 + 1
        print(f"\n  reference video window: {ref['w']}x{win_h} "
              f"(ratio {ref['w'] / win_h:.3f} — 16:9 is 1.778, 4:3 is 1.333)")
        print("  band edges can carry a soft gradient; confirm the seam on the "
              "frame\n  before committing the window height")
        need = round(src["w"] * win_h / ref["w"])
        print(f"  filling it from this source needs a {src['w']}x{need} crop "
              f"— {100 - round(need / src['h'] * 100)}% of the frame height is lost")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except StopIteration:
        sys.exit("could not find a video stream — is the input a video file?")
