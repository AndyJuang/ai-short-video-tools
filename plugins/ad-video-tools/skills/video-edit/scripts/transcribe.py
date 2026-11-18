#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["opencc-python-reimplemented>=0.1.7"]
# ///
"""Transcribe a clip with ElevenLabs Scribe and cut the words into caption-sized cues.

    uv run transcribe.py <media> [--lang tc|jp|en|...] [--out DIR]
    uv run transcribe.py --doctor       # is the key reachable?
    uv run transcribe.py --self-test    # offline, stubbed response

Writes next to <media> (or into --out):
    <stem>.transcript.json   a list (not {words: ...}) of {text, start, end, speaker}, one per
                             word, space or punctuation mark, as Scribe returned it
    <stem>.cues.json         [{start, end, text, speaker}], one per caption
    <stem>.srt               the cues as subtitles
All times are seconds in <media>. Use cues, not raw words, for caption text.

Key: ELEVENLABS_API_KEY in the .env at the git root of the working directory (run from the
project; see project_env), else the environment,
else the global ~/.ad-video-tools/.env; either the key itself or an op://vault/item/field
reference resolved with the 1Password CLI.
--lang tc converts the cues and SRT to Taiwan Traditional (OpenCC s2twp, which works on
whole phrases), since Scribe's zho is Simplified; the raw transcript stays Simplified.
An existing .transcript.json is reused, so re-chunking never re-bills the API.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import uuid

API = "https://api.elevenlabs.io/v1/speech-to-text"
LANGS = {"tc": "zho", "sc": "zho", "jp": "jpn", "ko": "kor", "en": "eng"}   # ISO 639-3
CJK = "぀-ヿ㐀-鿿가-힯"


def resolve(val):
    """`op://vault/item/field` -> the secret, via the 1Password CLI. Anything else passes through."""
    if not val.startswith("op://"):
        return val
    try:
        r = subprocess.run(["op", "read", val], capture_output=True, text=True)
    except FileNotFoundError:
        sys.exit("key is an op:// reference but the 1Password CLI isn't installed: brew install 1password-cli")
    if r.returncode:
        sys.exit(f"op read failed: {r.stderr.strip()}\nRun `op signin` (or `eval $(op signin)`) and retry.")
    return r.stdout.strip()


def project_env():
    """<project root>/.env, or None outside a project. The root is the nearest folder holding
    .git at or above the working directory (the project the scripts are run from), below
    home. Never searched from the material folder: client and downloaded files live there,
    and a .env or .git planted among them must neither supply a key (it would receive our
    footage and prompts) nor have git run inside a repository someone else configured."""
    d, home = os.path.realpath("."), os.path.realpath(os.path.expanduser("~"))
    if os.path.commonpath([d, home]) != home:
        return None                    # outside home, e.g. /tmp or a mounted drive: no project
    while d != home:
        if os.path.exists(os.path.join(d, ".git")):
            env = os.path.join(d, ".env")
            if not os.path.isfile(env) or not private(env) or tracked(d, ".env"):
                return None
            return env
        d = os.path.dirname(d)
    return None


def private(path):
    """On macOS/Linux, a project .env counts only if it is this user's and private to them
    (chmod 600, as init-video scaffolds it). One that arrived in a download or an unzipped
    delivery is normally world-readable (644), and must not supply a key. Windows: no check."""
    if os.name == "nt":
        return True
    st = os.stat(path)
    if st.st_uid == os.getuid() and not st.st_mode & 0o077:
        return True
    print(f"! ignoring {path}: not private to you — if it is your key file, run "
          f"`chmod 600 {path}`", file=sys.stderr)
    return False


def tracked(root, path):
    """True unless git confirms `path` is untracked. A .env committed to the repo came with
    a clone, not from this user: its key (or an op:// reference reaching into their
    1Password) must not be used. The scaffold's .env is gitignored, so the user's own file
    is never tracked. Anything git can't answer (no git, not a repository, a repository
    owned by someone else) fails closed: the project .env is skipped."""
    try:
        # core.fsmonitor off: a repository's own config can name a command for git to run
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", root, "ls-files",
                            "--error-unmatch", path], capture_output=True)
        code = r.returncode
    except FileNotFoundError:
        code = None
    if code == 1:                      # --error-unmatch: not tracked
        return False
    why = ("it is committed to git, so it is not this user's key file" if code == 0 else
           "git could not confirm it is untracked (git missing, or not this user's repository)")
    print(f"! ignoring {os.path.join(root, path)}: {why}", file=sys.stderr)
    return True


def read_env(path, name):
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8-sig"):   # -sig: older Notepad writes a BOM
            k, _, v = line.strip().partition("=")
            v = v.strip().strip("\"'")
            if k == name and v and not (v.startswith("{") and v.endswith("}")):   # {Placeholder}: not filled in
                return v
    return None


def find_key(name="ELEVENLABS_API_KEY"):
    """(raw value, where it was found), or (None, None). Raw: an op:// ref stays unresolved.
    Project first, so each client's work bills that client's key; the global
    ~/.ad-video-tools/.env is the fallback for work outside a project."""
    path = project_env()
    if path and read_env(path, name):
        return read_env(path, name), path
    if os.environ.get(name):
        return os.environ[name], "environment"
    path = os.path.join(os.path.expanduser("~"), ".ad-video-tools", ".env")
    return (read_env(path, name), path) if read_env(path, name) else (None, None)


def load_key(name="ELEVENLABS_API_KEY", phase="3b"):
    val, _ = find_key(name)
    if val:
        return resolve(val)
    sys.exit(f"no {name} in the project's .env, the environment or ~/.ad-video-tools/.env — the key "
             f"or an op:// reference to it; init-video's SKILL.md (The key file, Phase {phase}) walks through it")


def scribe(audio, key, lang=None):
    """One multipart POST; returns the API's JSON. ponytail: stdlib multipart, no requests dep."""
    fields = {"model_id": "scribe_v1", "diarize": "true", "timestamps_granularity": "word",
              "tag_audio_events": "false"}
    if lang:
        fields["language_code"] = lang
    b = uuid.uuid4().hex
    body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                    for k, v in fields.items())
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             f'Content-Type: audio/wav\r\n\r\n').encode() + open(audio, "rb").read() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(API, body, {"xi-api-key": key,
                                             "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=600))
    except urllib.error.HTTPError as e:
        sys.exit(f"ElevenLabs {e.code}: {e.read().decode(errors='replace')[:300]}")


def words_of(resp):
    """Scribe returns words, spacing and audio events; keep words and spacing, drop the rest."""
    return [{"text": w["text"], "start": w["start"], "end": w["end"], "speaker": w.get("speaker_id")}
            for w in resp.get("words", []) if w.get("type") in ("word", "spacing", None)]


def should_break(chunk, nxt, gap):
    """True if the caption being built (`chunk`, a list of word dicts) should end before `nxt`.

    `gap` is the silence in seconds between the last word of `chunk` and `nxt`.
    Pause only, by decision: length/time caps were weighed and not adopted.
    """
    return gap > 0.6


def cues_of(words, brk=should_break):
    cues, chunk = [], []
    for w in words:
        if w["text"].strip() == "" and not chunk:
            continue
        if chunk and w["text"].strip():
            last = next(x for x in reversed(chunk) if x["text"].strip())
            if w["speaker"] != last["speaker"] or brk(chunk, w, w["start"] - last["end"]):
                cues.append(chunk)
                chunk = []
        chunk.append(w)
    if chunk:
        cues.append(chunk)
    out = []
    for c in cues:
        real = [x for x in c if x["text"].strip()]
        if real:
            out.append({"start": real[0]["start"], "end": real[-1]["end"],
                        "text": "".join(x["text"] for x in c).strip(), "speaker": real[0]["speaker"]})
    return out


def srt_time(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt_of(cues):
    return "\n".join(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}\n"
                     for i, c in enumerate(cues, 1))


def writable(p):
    """p, unless it is a symlink: the material folder may hold files the user didn't make."""
    if os.path.islink(p):
        sys.exit(f"{p} is a symlink — refusing to write through it")
    return p


def run(media, lang=None, out=None, resp=None):
    out = out or os.path.dirname(os.path.abspath(media))
    os.makedirs(out, exist_ok=True)
    stem = os.path.join(out, os.path.splitext(os.path.basename(media))[0])
    raw = stem + ".transcript.json"
    if os.path.exists(raw):
        words = json.load(open(raw, encoding="utf-8"))
    else:
        if resp is None:
            key = load_key()
            with tempfile.TemporaryDirectory() as tmp:     # private dir: no race on a shared /tmp
                wav = os.path.join(tmp, "audio.wav")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", media, "-vn", "-ac", "1", "-ar", "16000", wav],
                               check=True)
                resp = scribe(wav, key, LANGS.get(lang, lang))
        words = words_of(resp)
        json.dump(words, open(writable(raw), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    cues = cues_of(words)
    if lang == "tc":                      # convert whole cues, not words: s2twp phrases span words
        import opencc
        cc = opencc.OpenCC("s2twp")
        for c in cues:
            c["text"] = cc.convert(c["text"])
    json.dump(cues, open(writable(stem + ".cues.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(writable(stem + ".srt"), "w", encoding="utf-8").write(srt_of(cues))
    return stem, cues


def self_test():
    ws = [("今天", 0.0, 0.4, "s0"), ("天氣", 0.4, 0.8, "s0"), ("很好", 0.8, 1.2, "s0"),
          ("對啊", 2.5, 2.9, "s0"), ("真的", 3.0, 3.4, "s1"), ("很好", 3.4, 3.8, "s1")]
    resp = {"words": [{"text": t, "start": a, "end": b, "type": "word", "speaker_id": s} for t, a, b, s in ws]}
    d = tempfile.mkdtemp(prefix="transcribe-test-")
    stem, cues = run(os.path.join(d, "clip.mp4"), resp=resp)
    assert [c["text"] for c in cues] == ["今天天氣很好", "對啊", "真的很好"], cues   # gap, then speaker change
    assert cues[0]["start"] == 0.0 and cues[0]["end"] == 1.2, cues[0]
    assert "00:00:02,500 --> 00:00:02,900" in open(stem + ".srt", encoding="utf-8").read()
    assert os.path.exists(stem + ".transcript.json"), "raw words not cached"
    assert run(os.path.join(d, "clip.mp4"), resp=None)[1] == cues, "cache not reused"
    sc = {"words": [{"text": t, "start": a, "end": a + 0.4, "type": "word", "speaker_id": "s0"}
                    for t, a in (("软", 0.0), ("件", 0.4), ("视频", 0.8))]}
    assert run(os.path.join(d, "sc.mp4"), "tc", resp=sc)[1][0]["text"] == "軟體影片", "tc not converted"
    assert run(os.path.join(d, "sc.mp4"), "sc")[1][0]["text"] == "软件视频", "sc must stay Simplified"
    home_d = os.path.join(d, "home")
    proj = os.path.join(home_d, "client")
    os.makedirs(os.path.join(proj, "input", "v1"))
    subprocess.run(["git", "-C", proj, "init", "-q"], check=True)
    os.makedirs(os.path.join(home_d, ".ad-video-tools"))
    open(os.path.join(home_d, ".ad-video-tools", ".env"), "w").write("K=global\nG=global\n")
    open(os.path.join(home_d, ".env"), "w").write("K=above-the-project\n")
    home, os.environ["HOME"] = os.environ.get("HOME"), home_d
    os.environ.pop("K", None)
    cwd = os.getcwd()

    def key_from(folder, name):          # the scripts look from the folder they run in
        os.chdir(folder)
        return find_key(name)
    try:
        start = os.path.join(proj, "input", "v1")
        glob = ("global", os.path.join(home_d, ".ad-video-tools", ".env"))
        assert key_from(start, "K") == glob, "read a .env other than the project root's"
        open(os.path.join(start, ".env"), "w").write("K=planted\n")
        assert key_from(proj, "K") == glob, "read a .env planted in a material folder"
        open(os.path.join(proj, ".env"), "w").write("\ufeffK=project\r\nG=\nP={P API key}\n")
        if os.name != "nt":
            os.chmod(os.path.join(proj, ".env"), 0o644)
            assert key_from(start, "K") == glob, "trusted a world-readable .env"
            os.chmod(os.path.join(proj, ".env"), 0o600)
        assert key_from(start, "K") == ("project", os.path.join(os.path.realpath(proj), ".env")), "project .env not used"
        assert key_from(start, "G")[0] == "global", "empty project line should fall back"
        assert key_from(start, "P") == (None, None), "a {Placeholder} was taken for a key"
        assert key_from(start, "X") == (None, None)
        outside = tempfile.mkdtemp(prefix="outside-home-")    # not under the fake home
        os.makedirs(os.path.join(outside, ".git"))
        open(os.path.join(outside, ".env"), "w").write("K=outside\n")
        assert key_from(outside, "K") == glob, "read a .env outside home"
        fake = os.path.join(home_d, "fake")               # .git that git can't read: fail closed
        os.makedirs(os.path.join(fake, ".git"))
        open(os.path.join(fake, ".env"), "w").write("K=fake\n")
        os.chmod(os.path.join(fake, ".env"), 0o600)   # private, so the next check is what decides
        assert key_from(fake, "K") == glob, "trusted a .env git could not vouch for"
        cloned = os.path.join(home_d, "cloned")           # a repo that ships its own .env
        os.makedirs(cloned)
        open(os.path.join(cloned, ".env"), "w").write("K=op://Private/bank/password\n")
        os.chmod(os.path.join(cloned, ".env"), 0o600)   # private, so the next check is what decides
        g = ["git", "-C", cloned, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g[:3] + ["init", "-q"], check=True)
        subprocess.run(g + ["add", ".env"], check=True)
        subprocess.run(g + ["commit", "-qm", "x"], check=True)
        assert key_from(cloned, "K") == glob, "used a .env committed to the repo"
        pwned = os.path.join(d, "pwned")                   # a repo config that names a command
        subprocess.run(g[:3] + ["config", "core.fsmonitor", f"touch {pwned}; false"], check=True)
        key_from(cloned, "K")
        assert not os.path.exists(pwned), "git ran a command from the repository's own config"
    finally:
        os.chdir(cwd)
        os.environ["HOME"] = home
    print("self-test ok")


def main():
    ap = argparse.ArgumentParser(usage=__doc__.split("\n\n")[1])
    ap.add_argument("media", nargs="?")
    ap.add_argument("--lang")
    ap.add_argument("--out")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.doctor:
        try:
            urllib.request.urlopen(urllib.request.Request(
                "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": load_key()}), timeout=20)
        except urllib.error.HTTPError as e:
            # a speech-to-text-only key is valid but lacks user_read: that is a pass
            if "missing_permissions" not in e.read().decode(errors="replace"):
                sys.exit(f"ELEVENLABS_API_KEY rejected: HTTP {e.code}")
        return print(f"ELEVENLABS_API_KEY: ok, from {find_key()[1]}")
    if not a.media:
        ap.error("a media file, --doctor or --self-test")
    stem, cues = run(a.media, a.lang, a.out)
    print(f"{len(cues)} cues -> {stem}.cues.json / .srt")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):      # Windows pipes default to the ANSI code page
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
