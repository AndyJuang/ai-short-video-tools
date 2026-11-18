#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["librosa>=0.10"]
# ///
"""The beat grid of an audio bed, for snapping cuts and timing push-ins.

    uv run beats.py bed.mp3 [--start S] [--every N]   # print {bpm, beats, bars}
    uv run beats.py bed.mp3 --expr 0.08               # print between(t,…)+… for a -vf
    uv run beats.py --self-test

Times are seconds into the bed. `--start` is where the cut starts using it
(video.json's music_in), and analysis covers 60 s from there — librosa is slow on
long beds and no ad runs longer. `bars` is every 4th beat from the phase with the
strongest onsets: librosa tracks beats, not downbeats, so a bar line is a good
guess on four-on-the-floor and a wrong one on anything syncopated.

Never fails a build: no beat, no file, no librosa — it prints an empty grid and
`--expr` prints 0, so the cut renders unsynced instead of not at all.

build.py and measure.py call grid() below, which runs this file under `uv run`
so librosa never becomes their dependency.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.abspath(__file__)
WINDOW = 60.0
EMPTY = {"bpm": 0, "beats": [], "bars": []}
NOTE = "bars = every 4th beat from the strongest-onset phase, not detected downbeats"


def analyse(path, start=0.0):
    import librosa
    import numpy as np
    # ffmpeg decodes, not librosa: soundfile cannot open mp4/mov/m4a, which is
    # what footage and most delivered beds are.
    sr = 22050
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(WINDOW),
                          "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    y = np.frombuffer(pcm, dtype=np.float32)
    if not len(y):
        return dict(EMPTY)
    env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr)
    if len(frames) < 2:
        return dict(EMPTY)
    # The tracker's grid lags the hits by 20-50 ms; pull each beat back onto the
    # attack of the onset just before it, which is where an editor would cut.
    # Looking back, not to the nearest: a hit's tail is an onset too.
    onsets = librosa.onset.onset_detect(onset_envelope=env, sr=sr, backtrack=True, units="time")
    times, hit = librosa.frames_to_time(frames, sr=sr), 0
    for i, t in enumerate(times):
        near = onsets[(onsets > t - 0.08) & (onsets < t + 0.02)]
        if len(near):
            times[i], hit = near[np.abs(near - t).argmin()], hit + 1
    # The tracker always returns a tempo, even for a drone or a voice. If most
    # of its beats have no hit under them, there is no beat to cut to.
    if hit < len(times) / 2:
        return dict(EMPTY)
    times = times + start
    phase = max(range(min(4, len(frames))), key=lambda p: env[frames[p::4]].sum())
    return {"bpm": round(float(np.atleast_1d(tempo)[0]), 1),
            "beats": [round(float(t), 3) for t in times],
            "bars": [round(float(t), 3) for t in times[phase::4]],
            "note": NOTE}


def expr(times, window, var="t"):
    """The spec's flash/pulse switch: 1 inside [t, t+window) of any grid point."""
    return "+".join(f"between({var},{t:.3f},{t + window:.3f})" for t in times) or "0"


def grid(path, start=0.0):
    """Analyse in a uv-managed env (librosa), or in this one if it has librosa."""
    cmd = (["uv", "run", "--quiet", "--script", HERE] if shutil.which("uv")
           else [sys.executable, HERE])
    p = subprocess.run(cmd + [path, "--start", str(start)], capture_output=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(p.stdout)
    except ValueError:
        print(f"! beats.py: {p.stderr.strip()[-300:] or 'no output'}", file=sys.stderr)
        return dict(EMPTY)


def self_test():
    import tempfile
    d = tempfile.mkdtemp(prefix="advideo-beats-")
    click, silent = os.path.join(d, "click.wav"), os.path.join(d, "silent.wav")
    drone = os.path.join(d, "drone.wav")
    for out, src in ((click, "sine=f=1000:r=48000:d=10,volume='if(lt(mod(t\\,0.5)\\,0.05)\\,1\\,0)':eval=frame"),
                     (silent, "anullsrc=r=48000:cl=mono:d=5"),
                     (drone, "sine=f=440:r=48000:d=6")):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, out], check=True)
    g = analyse(click)
    assert g["bpm"] in (60, 120, 240) or abs(g["bpm"] - 120) < 3, f"bpm {g['bpm']}"
    off = max(abs(t * 2 - round(t * 2)) / 2 for t in g["beats"])
    assert off < 1 / 30, f"beat {off * 1000:.0f} ms off the 0.5 s grid"
    assert g["bars"] == g["beats"][g["beats"].index(g["bars"][0])::4], "bars not every 4th beat"
    shifted = analyse(click, start=2.0)
    assert min(shifted["beats"]) >= 2.0, "--start not applied to times"
    assert analyse(silent)["beats"] == [], "beats found in silence"
    assert analyse(drone)["beats"] == [], "beats found in a steady tone"
    assert expr([], 0.08) == "0" and expr([1], 0.1) == "between(t,1.000,1.100)"
    print(f"self-test ok  ({g['bpm']} bpm, {len(g['beats'])} beats, worst {off * 1000:.0f} ms)")


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(usage="beats.py <audio> [--start S] [--every N] [--expr WINDOW] | --self-test")
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--every", type=int, default=1, help="keep every Nth beat")
    ap.add_argument("--expr", type=float, metavar="WINDOW",
                    help="print an ffmpeg between() sum, WINDOW seconds per beat")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.audio:
        ap.error("an audio file or --self-test")
    try:
        g = analyse(a.audio, a.start)
    except Exception as e:                    # zero-beat safety: render unchanged
        print(f"no beat grid for {a.audio}: {e}", file=sys.stderr)
        g = dict(EMPTY)
    g["beats"] = g["beats"][::max(1, a.every)]
    print(expr(g["beats"], a.expr) if a.expr else json.dumps(g))


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1:])
