#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Gate for material files before ffmpeg or ffprobe opens them.

ffmpeg picks the demuxer from a file's content, not its name. A "clip.mp4" from a
client or a download can be an HLS playlist or a concat list, and then ffmpeg reads
whatever files or URLs it names into the render. media(path) probes the file with
local files only and passes it on only if it is a plain media container or image.

    from ffsafe import media
    subprocess.run(["ffmpeg", "-i", media(path), ...])

Only material goes through this: files the scripts write themselves (concat lists,
caption PNGs, intermediate masters) and lavfi sources are not gated.

    uv run ffsafe.py --self-test
"""
import functools
import os
import subprocess
import sys

# Homebrew's `ffmpeg` leaves out zimg (zscale, needed to tone-map HDR) and libjxl. The
# keg-only `ffmpeg-full` has both; put it first on PATH so every ffmpeg/ffprobe this
# process starts is that one. Every script that runs ffmpeg imports this module.
for _keg in ("/opt/homebrew/opt/ffmpeg-full/bin", "/usr/local/opt/ffmpeg-full/bin",
             "/home/linuxbrew/.linuxbrew/opt/ffmpeg-full/bin"):
    if os.path.isdir(_keg):
        os.environ["PATH"] = _keg + os.pathsep + os.environ.get("PATH", "")
        break

# Demuxer names as ffprobe reports them (format=format_name, split on commas).
ALLOWED = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi", "mpegts",
           "mpeg", "flv", "asf", "wav", "w64", "aiff", "mp3", "aac", "flac", "ogg", "caf",
           "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "bmp_pipe", "tiff_pipe", "gif",
           "jpegxl_pipe"}

# PQ (HDR10, HDR photos) and HLG (iPhone video). Fed straight into yuv420p they come out
# washed out with clipped highlights, so they go through TONEMAP first.
HDR = {"smpte2084", "arib-std-b67"}
# Linearize, tone-map to BT.709, and land in YUV with a BT.709 matrix: a later scale from
# RGB would pick swscale's BT.601 default and tint the frame. 4:4:4 so odd photo sizes
# (1600x1093) still work.
# npl=203: BT.2408 reference white; matched Apple's SDR export on a PQ photo and a Pixel HLG
# clip within 1 Y. ponytail: one curve for all sources, peaks land ~5% dim; per-source npl if needed
TONEMAP = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv444p,")


@functools.cache
def check(path):
    """None if ffprobe reads path as plain media, else the reason it doesn't."""
    r = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file",
                        "-show_entries", "format=format_name", "-of", "csv=p=0", path],
                       capture_output=True, encoding="utf-8", errors="replace")
    names = set(r.stdout.strip().strip('"').split(",")) - {""}
    if r.returncode or not names & ALLOWED:
        return ",".join(sorted(names)) or (r.stderr.strip().splitlines() or ["unreadable"])[-1]
    return None


def media(path):
    """path, if ffprobe reads it as plain media; otherwise exit with the reason."""
    why = check(path)
    if why:
        sys.exit(f"{path}: not plain media ({why}) — a playlist or list file could make "
                 f"ffmpeg read other files into the render; refusing it")
    return path


@functools.cache
def sdr(path):
    """Filter-chain prefix that tone-maps path's video to SDR if it is HDR, else ''."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=color_transfer", "-of", "csv=p=0", path],
                       capture_output=True, encoding="utf-8", errors="replace")
    # first field only: phone video carries rotation side data, which csv appends as ",…"
    return TONEMAP if r.stdout.split(",")[0].strip() in HDR else ""


def inside(path, roots, what):
    """path, if it resolves (symlinks followed) inside one of roots — the material folder or
    the project — else exit. A plan or video.json may reach into the project's assets/
    (sound effects, music), but not elsewhere on the machine, such as ~/.ssh or another
    client's project."""
    p = os.path.realpath(path)
    for r in filter(None, roots):
        r = os.path.realpath(r)
        try:
            if os.path.commonpath([r, p]) == r:
                return path
        except ValueError:                 # Windows: different drives
            pass
    sys.exit(f"{what}: {path} is outside the material folder and the project — refusing to read it")


def self_test():
    import tempfile
    d = tempfile.mkdtemp(prefix="ffsafe-test-")
    ok = os.path.join(d, "ok.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1", ok], check=True)
    assert media(ok) == ok
    still = os.path.join(d, "still.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1", "-frames:v", "1", still],
                   check=True)
    assert media(still) == still
    assert sdr(ok) == "", "SDR source tone-mapped"
    flat, hlg = os.path.join(d, "hlg_flat.mp4"), os.path.join(d, "hlg.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1",
                    "-vf", "setparams=color_trc=arib-std-b67", "-c:v", "libx264", flat], check=True)
    # a phone clip: HLG plus rotation side data, which makes ffprobe's csv end in a comma
    # -display_rotation is ffmpeg 7+; 6.x (Ubuntu CI) still turns the old rotate tag into side data
    if subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", flat,
                       "-c", "copy", hlg]).returncode:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", flat, "-c", "copy",
                        "-metadata:s:v:0", "rotate=90", hlg], check=True)
    assert sdr(hlg) == TONEMAP, "HLG phone clip not tone-mapped"
    for name, body in (("list.mp4", "ffconcat version 1.0\nfile 'ok.mp4'\n"),
                       ("playlist.mp4", "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nok.mp4\n#EXT-X-ENDLIST\n")):
        p = os.path.join(d, name)
        open(p, "w").write(body)
        try:
            media(p)
        except SystemExit:
            continue
        raise AssertionError(f"{name} was accepted")
    proj, mat = os.path.join(d, "proj"), os.path.join(d, "proj", "input", "v")
    os.makedirs(os.path.join(proj, "assets"))
    os.makedirs(mat)
    roots = (mat, proj)
    assert inside(os.path.join(mat, "a.mp4"), roots, "t")
    assert inside(os.path.join(mat, "..", "..", "assets", "s.wav"), roots, "t"), "project assets refused"
    for bad in (os.path.join(mat, "..", "..", "..", "ok.mp4"), "/etc/hosts", os.path.join(d, "proj2", "x.mp4")):
        try:
            inside(bad, roots, "t")
        except SystemExit:
            continue
        raise AssertionError(f"{bad} was accepted")
    os.symlink(ok, os.path.join(mat, "link.mp4"))          # a link out of the project counts as outside
    try:
        inside(os.path.join(mat, "link.mp4"), roots, "t")
        raise AssertionError("a symlink out of the project was accepted")
    except SystemExit:
        pass
    assert inside(os.path.join(mat, "x.mp4"), (mat, None), "t"), "no project: material folder alone"
    print(f"self-test ok  ({d})")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        for p in sys.argv[1:]:
            print(media(p))
