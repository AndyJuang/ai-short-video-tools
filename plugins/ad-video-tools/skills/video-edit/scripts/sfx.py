#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fetch CC0 sound effects into a project's sfx folder, with a record of where each came from.

    uv run sfx.py <sfx_dir>                                  # the starter set: whoosh, pop, ding, click
    uv run sfx.py <sfx_dir> --freesound "knife chop" --as chop   # best-rated CC0 match on Freesound
    uv run sfx.py --doctor                                   # is FREESOUND_API_KEY valid? (free)
    uv run sfx.py --self-test                                # offline

Sources, CC0 only (public domain: no credit owed, commercial use fine):
  * the starter set comes from github.com/lavenderdotpet/CC0-Public-Domain-Sounds, pinned to
    one commit, so the same names always fetch the same sounds. No key.
  * --freesound searches freesound.org with license:"Creative Commons 0" and takes the
    best-rated match under 4 s (its HQ mp3 preview: originals need OAuth). Needs
    FREESOUND_API_KEY, found the way transcribe.py finds its key (init-video Phase 3d).
Pixabay is not a source: its API has no audio, its pages refuse scripts, and its sounds are
under the Pixabay Content License, not CC0. Download those by hand and note the licence.

Each sound lands as <sfx_dir>/<name>.wav, 48 kHz stereo, peaks at -6 dBFS so a set mixes
evenly, plus a line in <sfx_dir>/SOURCES.txt (file, licence, author, source URL). A name
already there is left alone: the folder may hold the client's own licensed set.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import find_key, load_key, writable  # noqa: E402

REPO = "lavenderdotpet/CC0-Public-Domain-Sounds"
SHA = "f2b6264f9ab89fabc266914c3654685d68c5a39b"
STARTER = {   # name: (path in REPO, author). Picked from spectrograms; listen before a client cut.
    "whoosh": ("MMRetroArcadeSoundsPack1_0_5/Misc/ogg/Swipe5.ogg", "The Motion Monkey"),
    "pop": ("BB_2HTC Samples Vol 4/Drums/2022-10-19 Frozen Shores - Pop.wav", "BB_2HTC"),
    "ding": ("100-CC0-SFX/bell_01.ogg", "100-CC0-SFX"),
    "click": ("kenney_interfacesounds/Audio/click_001.ogg", "Kenney"),
}
FREESOUND = "https://freesound.org/apiv2/search/text/"
PEAK = -6.0   # dBFS


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "ad-video-tools-sfx", **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.HTTPError as e:
        sys.exit(f"{e.code} from {url.split('?')[0]}: {e.read().decode(errors='replace')[:200]}")


def to_wav(raw, dest):
    """Any audio -> 48 kHz stereo wav, gained so its peak sits at PEAK."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(raw)
    try:
        fmt = "aformat=sample_rates=48000:channel_layouts=stereo"   # upmix first: it drops mono 3 dB
        log = subprocess.run(["ffmpeg", "-i", f.name, "-af", f"{fmt},volumedetect", "-f", "null", "-"],
                             capture_output=True, text=True).stderr
        m = re.search(r"max_volume: (-?[\d.]+) dB", log)
        if not m:
            sys.exit(f"not audio ffmpeg can read: {os.path.basename(dest)}")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f.name, "-af", f"{fmt},volume={PEAK - float(m[1]):.2f}dB", dest],
                       check=True)
    finally:
        os.unlink(f.name)


def save(sfx_dir, name, raw, author, url):
    dest = writable(os.path.join(sfx_dir, f"{name}.wav"))
    to_wav(raw, dest)
    with open(writable(os.path.join(sfx_dir, "SOURCES.txt")), "a", encoding="utf-8") as fh:
        fh.write(f"{name}.wav\tCC0 1.0\t{author}\t{url}\n")
    print(f"  {name}.wav  ← {author}, {url}")


def starter(sfx_dir, get=fetch):
    for name, (path, author) in STARTER.items():
        if os.path.lexists(os.path.join(sfx_dir, f"{name}.wav")):
            print(f"  {name}.wav  already there, kept")
            continue
        raw = get(f"https://raw.githubusercontent.com/{REPO}/{SHA}/{urllib.parse.quote(path)}")
        save(sfx_dir, name, raw, author, f"https://github.com/{REPO}/blob/{SHA}/{urllib.parse.quote(path)}")


def search(query, key, get=fetch):
    """The best-rated CC0 sound under 4 s for `query`, or None."""
    q = urllib.parse.urlencode({"query": query, "filter": 'license:"Creative Commons 0" duration:[0 TO 4]',
                                "sort": "rating_desc", "page_size": 1,
                                "fields": "id,name,username,license,url,previews,duration"})
    hits = json.loads(get(f"{FREESOUND}?{q}", {"Authorization": f"Token {key}"}))["results"]
    return hits[0] if hits else None


def freesound(sfx_dir, query, name, key, get=fetch):
    if os.path.lexists(os.path.join(sfx_dir, f"{name}.wav")):
        return print(f"  {name}.wav  already there, kept — pick another --as")
    hit = search(query, key, get)
    if not hit:
        sys.exit(f"no CC0 sound under 4 s on Freesound for {query!r} — try other words")
    if "publicdomain/zero" not in hit["license"]:   # the filter should guarantee it; don't trust it blind
        sys.exit(f"Freesound returned a non-CC0 sound ({hit['license']}) — not saved")
    save(sfx_dir, name, get(hit["previews"]["preview-hq-mp3"]), hit["username"], hit["url"])


def self_test():
    import shutil
    d = tempfile.mkdtemp(prefix="sfx-test-")
    tone = os.path.join(d, "t.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=880:d=0.3",
                    "-af", "volume=-20dB", tone], check=True)
    raw = open(tone, "rb").read()
    open(os.path.join(d, "pop.wav"), "wb").write(b"client's own")
    seen = []
    starter(d, lambda url, h=None: seen.append(url) or raw)
    assert open(os.path.join(d, "pop.wav"), "rb").read() == b"client's own", "overwrote an existing sound"
    assert len(seen) == len(STARTER) - 1 and all(SHA in u for u in seen), seen
    log = subprocess.run(["ffmpeg", "-i", os.path.join(d, "whoosh.wav"), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    assert abs(float(re.search(r"max_volume: (-?[\d.]+)", log)[1]) - PEAK) < 0.6, log[-300:]
    rows = open(os.path.join(d, "SOURCES.txt"), encoding="utf-8").read().splitlines()
    assert len(rows) == 3 and all(r.split("\t")[1] == "CC0 1.0" for r in rows), rows
    print("  ok  starter set: pinned, peak-normalised, recorded, existing file kept")

    hit = {"username": "someone", "url": "https://freesound.org/s/1/", "previews": {"preview-hq-mp3": "P"},
           "license": "http://creativecommons.org/publicdomain/zero/1.0/"}
    def api(url, h=None):
        if url == "P":
            return raw
        assert h == {"Authorization": "Token K"} and "Creative+Commons+0" in url, (url, h)
        return json.dumps({"results": [hit]}).encode()
    freesound(d, "chop", "chop", "K", api)
    assert "freesound.org/s/1/" in open(os.path.join(d, "SOURCES.txt")).read()
    hit["license"] = "http://creativecommons.org/licenses/by/4.0/"
    try:
        freesound(d, "chop", "chop2", "K", api)
        raise AssertionError("saved a CC-BY sound")
    except SystemExit as e:
        assert "non-CC0" in str(e) and not os.path.exists(os.path.join(d, "chop2.wav"))
    print("  ok  freesound: CC0 filter sent, non-CC0 result refused")
    shutil.rmtree(d)
    print("self-test ok")


def cli(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sfx_dir", nargs="?")
    ap.add_argument("--freesound", metavar="QUERY")
    ap.add_argument("--as", dest="name", help="file name for a --freesound sound (default: the query)")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.doctor:
        search("whoosh", load_key("FREESOUND_API_KEY", "3d"))
        return print(f"FREESOUND_API_KEY: ok, from {find_key('FREESOUND_API_KEY')[1]}")
    if not a.sfx_dir:
        ap.error("an sfx folder, --doctor or --self-test")
    os.makedirs(a.sfx_dir, exist_ok=True)
    if a.freesound:
        name = a.name or re.sub(r"\W+", "_", a.freesound.strip()).lower()
        return freesound(a.sfx_dir, a.freesound, name, load_key("FREESOUND_API_KEY", "3d"))
    starter(a.sfx_dir)


if __name__ == "__main__":
    cli(sys.argv[1:])
