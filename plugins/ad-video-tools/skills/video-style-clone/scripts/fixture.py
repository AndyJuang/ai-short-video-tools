#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["Pillow>=10"]
# ///
"""A throwaway project that exercises build.py end to end.

    uv run fixture.py [DIR]       # write the project into DIR (default: a temp dir)
    uv run build.py --self-test   # write it, build both videos, check the outputs

Three videos: `clip` is footage on the offer template, `slides` is three stills
on the list template with a click-track bed and no shot list, and `beat` is the
same stills cut off the beat with every `sync` option on. Colour clips from
lavfi, stills from PIL — nothing is downloaded, nothing is a real ad. The click
track ticks every 0.5 s, so a snapped cut has a known grid to land on.
Faces come from the installed Noto CJK TC set, same as any real tc cut.
"""
import json
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build.py")


def sh(*args):
    subprocess.run(args, check=True, capture_output=True)


def make(proj):
    for d in ("assets", "output"):
        os.makedirs(os.path.join(proj, d), exist_ok=True)
    for v in ("clip", "slides", "beat"):
        os.makedirs(os.path.join(proj, "input", v), exist_ok=True)

    logo = Image.new("RGBA", (600, 200), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rounded_rectangle([20, 40, 580, 160], radius=40, fill="#C2185B")
    logo.save(os.path.join(proj, "assets", "logo.png"))
    json.dump({"logo": {"default": "assets/logo.png"}},
              open(os.path.join(proj, "brand.json"), "w", encoding="utf-8"))

    clip = os.path.join(proj, "input", "clip")
    sh("ffmpeg", "-v", "error", "-y",
       "-f", "lavfi", "-i", "color=c=0x3366cc:s=640x360:r=30:d=6",
       "-f", "lavfi", "-i", "sine=f=440:r=48000:d=6",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
       os.path.join(clip, "clip.mp4"))
    json.dump({"template": "single-offer-9x16", "src": "clip.mp4",
               "shots": [[0.5, 2.0], [3.0, 2.0]],
               "copy": {"tc": {"pill": "限時", "head": [["夏季", "ink"], ["特賣", "accent"]],
                               "cta": "立即選購", "url": "BRAND.TW"},
                        "jp": {"pill": "期間限定", "head": [["夏の", "ink"], ["セール", "accent"]],
                               "cta": "今すぐ購入", "url": "BRAND.JP"}},
               "captions": {"tc": [["第一鏡", "夏天來了"], ["第二鏡", "全館八折"]],
                            "jp": [["一枚目", "夏が来た"], ["二枚目", "全品二割引"]]}},
              open(os.path.join(clip, "video.json"), "w", encoding="utf-8"), ensure_ascii=False)

    slides = os.path.join(proj, "input", "slides")
    for i, c in enumerate(("#E91E63", "#2C2C2C", "#8A8084")):
        Image.new("RGB", (800, 600), c).save(os.path.join(slides, f"still{i}.png"))
    sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi",
       "-i", "sine=f=1000:r=48000:d=10,volume='if(lt(mod(t\\,0.5)\\,0.05)\\,1\\,0)':eval=frame",
       os.path.join(slides, "click.wav"))
    json.dump({"template": "feature-list-4x5", "music": "click.wav",
               "copy": {"tc": {"headline": "三大特色", "badge": "新品", "label": "特色",
                               "points": [["1", "第一點"], ["2", "第二點"], ["3", "第三點"]],
                               "cta": "了解更多"}},
               "captions": {"tc": [["一", "第一張"], ["二", "第二張"], ["三", "第三張"]]}},
              open(os.path.join(slides, "video.json"), "w", encoding="utf-8"), ensure_ascii=False)

    beat = os.path.join(proj, "input", "beat")
    par = json.load(open(os.path.join(slides, "video.json"), encoding="utf-8"))
    for f in ("still0.png", "still1.png", "still2.png", "click.wav"):
        os.link(os.path.join(slides, f), os.path.join(beat, f))
    par.update(shots=[["still0.png", 2.2], ["still1.png", 2.7], ["still2.png", 1.9]],
               sync={"snap": "beat", "kenburns": "beat", "pulse": "flash"})
    json.dump(par, open(os.path.join(beat, "video.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return proj


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", path],
        capture_output=True, encoding="utf-8", errors="replace", check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return (v["width"], v["height"], float(info["format"]["duration"]),
            any(s["codec_type"] == "audio" for s in info["streams"]))


def self_test():
    proj = make(tempfile.mkdtemp(prefix="advideo-fixture-"))

    def build(*args):
        p = subprocess.run([sys.executable, BUILD, *args], cwd=proj,
                           capture_output=True, encoding="utf-8", errors="replace")
        assert p.returncode == 0, f"build {' '.join(args)}: {p.stderr or p.stdout}"

    def check(cut, w, h, secs):
        video, sub = cut.split(".", 1)
        out = os.path.join(proj, "output", f"{cut}.mp4")
        pw, ph, dur, audio = probe(out)
        assert (pw, ph) == (w, h), f"{cut}: {pw}x{ph}, wanted {w}x{h}"
        assert abs(dur - secs) < 0.15, f"{cut}: {dur:.2f}s, wanted {secs}s"
        assert audio, f"{cut}: no audio stream"
        sheet = os.path.join(proj, "build", video, sub, "sheet.jpg")
        assert os.path.exists(sheet), f"{cut}: no contact sheet"
        print(f"  ok  {cut}: {pw}x{ph} {dur:.2f}s")
        return out

    build("clip", "--all")                       # every language, one command
    outs = [check("clip.tc", 1080, 1920, 4.0), check("clip.jp", 1080, 1920, 4.0)]
    build("clip", "--ratio", "4x5")              # retarget: 9x16 template -> 4x5 canvas
    outs.append(check("clip.tc.4x5", 1080, 1350, 4.0))
    # The panel must have moved up with the canvas: 1366 - (1920 - 1350) = 796.
    chrome = Image.open(os.path.join(proj, "build", "clip", "tc.4x5", "chrome.png"))
    assert chrome.getpixel((5, 794))[3] == 0 and chrome.getpixel((5, 798))[3] == 255, \
        "retargeted panel did not shift with the canvas"
    build("slides")
    slides = check("slides.tc", 1080, 1350, 9.0)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", slides, "-af", "ebur128",
                          "-f", "null", "-"], capture_output=True, encoding="utf-8").stderr
    lufs = float(err[err.rfind("Summary:"):].split("I:")[1].split("LUFS")[0])
    assert abs(lufs + 14) < 1, f"slides.tc at {lufs} LUFS, wanted -14 ±1 (two-pass loudnorm)"
    print(f"  ok  slides.tc loudness {lufs} LUFS")

    # Cuts at 2.2 / 4.9 / 6.8 s move to the next click: ~2.5 / 5.0 / 7.0.
    build("beat")
    check("beat.tc", 1080, 1350, 7.0)
    fps, cut = 30, 0.0
    for i in range(3):
        cut += probe(os.path.join(proj, "build", "beat", "tc", f"shot{i:02d}.mp4"))[2]
        assert abs(cut * 2 - round(cut * 2)) / 2 <= 1 / fps, f"cut {i + 1} at {cut:.3f}s, off the beat"
    print("  ok  beat: cuts on the 0.5 s grid")

    # --stale: touch clip's params, and only clip's three cuts rebuild.
    before = {o: os.path.getmtime(o) for o in outs + [slides]}
    bump = max(before.values()) + 1
    os.utime(os.path.join(proj, "input", "clip", "video.json"), (bump, bump))
    build("--stale")
    for o in outs:
        assert os.path.getmtime(o) > before[o], f"--stale skipped {os.path.basename(o)}"
    assert os.path.getmtime(slides) == before[slides], "--stale rebuilt a fresh output"
    print("  ok  --stale rebuilt 3, left 1")
    print(f"self-test ok  ({proj})")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    print(make(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="advideo-fixture-")))
