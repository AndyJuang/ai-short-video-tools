#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6"]
# ///
"""Generate the stills and clips an edit plan asks for: Gemini images, Veo video.

    uv run generate.py <edit_plan.yaml> <asset_dir> [--scene N] [--cap USD]         # estimate only, no API call
    uv run generate.py <edit_plan.yaml> <asset_dir> [--scene N] [--cap USD] --yes   # generate, after the user OKs the price
    uv run generate.py --doctor       # is GEMINI_API_KEY valid and the model reachable? (free)
    uv run generate.py --self-test    # offline, stubbed generation

A timeline scene with `generate: {brief, prompt}` gets a 9:16 image at its
`asset_file` (e.g. gen/3.jpg), which render_plan.py then renders like any still.
Without --yes nothing is sent: it prints each scene and the estimated cost, and
exits. An image whose file exists and was made from the same prompt and model is
skipped, so editing one prompt re-bills one image.

`generate: {kind: video, prompt}` gets a 9:16 clip instead (asset_file gen/3.mp4), Veo,
4/6/8 s: `seconds`, else the shortest that covers the scene. `from: gen/2.jpg` animates
that still (approve the still first; it is far cheaper to iterate). Veo always adds
sound; it is stripped on write, so the clip is picture only like any b-roll.
--cap refuses a run whose estimate is over that many dollars, even with --yes.

Revising: change `prompt` to start over, or add `edit: "make the umbrella red"` to
change the current image and keep the rest of it — the image is sent back with the
instruction. Edits stack on whatever is on disk; leaving or deleting `edit` after it
ran costs nothing. Every overwrite first keeps the old image (and its stamp) as
gen/3.v1.jpg, v2, … — copy one back over gen/3.jpg (and .json) to roll back.

Recurring people: a plan-level `characters: {A: {ref: refs/A.jpg (or a list), desc}}` and
`cast: [A]` on a still's `generate` block send A's reference image(s) first, with the
instruction that A is the same person. `ref: [paths]` sends extra images without a name.
With `edit` set, the references go in front of the current image: the way to fix a face
that drifted without redrawing the scene. A changed reference file re-runs the scene.

Key: GEMINI_API_KEY, found the way transcribe.py finds its key: the project's .env
(the git root of the folder it runs from, that file only), else the environment, else the global
~/.ad-video-tools/.env; the key or an op:// reference.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transcribe import find_key, load_key  # noqa: E402
from tts import confined  # noqa: E402

API = "https://generativelanguage.googleapis.com/v1beta"
MODEL = "gemini-3.1-flash-image"
SIZE = "2K"          # 1536x2752 at 9:16, above the 1080x1920 frame
# Cached price table, USD per output image, standard paid tier (no free tier for image
# output). Source: ai.google.dev/gemini-api/docs/pricing, checked PRICES_AT. Don't look
# prices up per run; refresh this table when MODEL or SIZE changes or Google reprices.
PRICES_AT = "2026-10-13"
KEEP = ("Keep everything else in the image exactly the same, preserving the original style, "
        "lighting, and composition.")    # Google's own wording for a targeted edit
PRICES = {
    "gemini-3.1-flash-image": {"0.5K": 0.045, "1K": 0.067, "2K": 0.101, "4K": 0.151},
    "gemini-3-pro-image": {"1K": 0.134, "2K": 0.134, "4K": 0.24},
}
VIDEO_MODEL = "veo-3.1-lite-generate-preview"
VIDEO_RES = "720p"   # Lite: 720p any length, 1080p 8 s only; 720x1280 is upscaled to the frame
# USD per second of video, audio included (always generated), same source and date.
VIDEO_PRICES = {
    "veo-3.1-lite-generate-preview": {"720p": 0.05, "1080p": 0.08},
    "veo-3.1-fast-generate-preview": {"720p": 0.10, "1080p": 0.12, "4k": 0.30},
    "veo-3.1-generate-preview": {"720p": 0.40, "1080p": 0.40, "4k": 0.60},
}


def first_image(node):
    """base64 data of the first {type: image, data} block anywhere in the response.
    The docs show it under steps[].content[], the SDK reference under outputs[]."""
    if isinstance(node, dict):
        if node.get("type") == "image" and node.get("data"):
            return node["data"]
        node = list(node.values())
    if isinstance(node, list):
        for v in node:
            found = first_image(v)
            if found:
                return found
    return None


def mime_of(path):
    return "image/png" if path.lower().endswith(".png") else "image/jpeg"


def imagine(prompt, key, image=None, refs=()):
    """prompt -> jpeg bytes; with `image` (jpeg bytes), prompt is an edit of that image.
    `refs` [(bytes, mime)] go before it: identity references come first."""
    if image is not None or refs:
        blob = lambda data, mime: {"type": "image", "mime_type": mime, "data": base64.b64encode(data).decode()}  # noqa: E731
        prompt = ([{"type": "text", "text": f"{prompt} {KEEP}" if image is not None else prompt}]
                  + [blob(*r) for r in refs] + ([blob(image, "image/jpeg")] if image is not None else []))
    body = {"model": MODEL, "input": prompt, "store": False,
            "response_format": {"type": "image", "mime_type": "image/jpeg",
                                "aspect_ratio": "9:16", "image_size": SIZE}}
    req = urllib.request.Request(f"{API}/interactions", json.dumps(body).encode(),
                                 {"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=300))
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini {e.code}: {e.read().decode(errors='replace')[:300]}")
    data = first_image(resp)
    if not data:
        sys.exit(f"Gemini returned no image (a refused prompt?): {json.dumps(resp, ensure_ascii=False)[:300]}")
    return base64.b64decode(data)


def call(url, key, body=None, raw=False):
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=300)
    except urllib.error.HTTPError as e:
        sys.exit(f"Gemini {e.code}: {e.read().decode(errors='replace')[:300]}")
    return r.read() if raw else json.load(r)


def film(prompt, key, seconds, image=None, mime="image/jpeg", fetch=call, every=10, limit=900):
    """prompt (+ a still to animate) -> mp4 bytes. Veo is a long-running operation: submit,
    poll until done, download (Google keeps the file 2 days)."""
    inst = {"prompt": prompt}
    if image is not None:
        # predictLongRunning wants Vertex-style bytes; the docs' inlineData is refused (400) by Lite
        inst["image"] = {"bytesBase64Encoded": base64.b64encode(image).decode(), "mimeType": mime}
    op = fetch(f"{API}/models/{VIDEO_MODEL}:predictLongRunning", key,
               {"instances": [inst], "parameters": {"aspectRatio": "9:16", "durationSeconds": seconds,
                                                    "resolution": VIDEO_RES}})
    t0 = time.time()
    while not op.get("done"):
        if time.time() - t0 > limit:
            sys.exit(f"Veo still running after {limit}s: {op.get('name')} — re-run later to try again")
        time.sleep(every)
        op = fetch(f"{API}/{op['name']}", key)
    if op.get("error"):
        sys.exit(f"Veo failed: {json.dumps(op['error'], ensure_ascii=False)[:300]}")
    samples = ((op.get("response") or {}).get("generateVideoResponse") or {}).get("generatedSamples") or []
    if not samples:
        sys.exit(f"Veo returned no video (a filtered prompt?): {json.dumps(op.get('response'), ensure_ascii=False)[:300]}")
    return fetch(samples[0]["video"]["uri"], key, raw=True)


def clip_seconds(dur):
    """Veo makes 4, 6 or 8 s: the shortest that covers the scene; only 720p allows < 8."""
    if VIDEO_RES != "720p":
        return 8
    return next((s for s in (4, 6, 8) if s >= dur - 0.01), 8)


def silent(data, out):
    """Write Veo's mp4 without its audio track (Veo always adds sound). The download goes to
    a fresh temp file outside the asset folder (mkstemp creates it, O_EXCL), so nothing
    planted next to `out` can redirect the write; `out` itself is already confined."""
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-map", "0:v", "-c", "copy", out],
                       check=True)
    finally:
        os.remove(tmp)


def same(old, stamp):
    """Only the keys this version writes decide: an extra key in the saved stamp
    (an older field, a hand note) must not re-bill a finished image."""
    return all(old.get(k) == v for k, v in stamp.items())


def references(plan, g, asset_dir, n):
    """(paths, identity note) for a still: each `cast` character's reference image(s) from the
    plan's `characters` block, then any `ref` paths. The note goes into the prompt, so the
    model is told whose face each reference is, not left to guess."""
    chars, paths, notes = plan.get("characters") or {}, [], []
    for name in g.get("cast") or []:
        c = chars.get(name)
        if not c or not c.get("ref"):
            sys.exit(f"scene {n}: cast {name!r} has no characters.{name}.ref in the plan")
        refs = c["ref"] if isinstance(c["ref"], list) else [c["ref"]]
        paths += refs
        notes.append(f"{name} ({c.get('desc', '').strip()}) is the person in reference image"
                     f"{'s' if len(refs) > 1 else ''} {', '.join(str(len(paths) - len(refs) + k + 1) for k in range(len(refs)))}")
    extra = g.get("ref") or []
    paths += extra if isinstance(extra, list) else [extra]
    if not paths:
        return [], ""
    out = []
    for p in paths:
        full = confined(asset_dir, p, f"scene {n} reference")
        if not os.path.isfile(full):
            sys.exit(f"scene {n}: reference {p} doesn't exist — make or crop it first")
        out.append(full)
    note = ("The first images are references; the last is the scene to change." if g.get("edit") else
            "The images are references.")
    note += (" " + "; ".join(notes) + "." if notes else "") + \
        (" Every person shown must be exactly the same person as in their reference: same face, eyes, nose, "
         "jaw, skin tone, build, hair and clothing. Do not copy the references' framing or background.")
    return out, note


def pending(plan_path, asset_dir, scene=None):
    """[{n, out, side, text, stamp, kind: image|edit|video, seconds, src, refs}] still to make."""
    plan = yaml.safe_load(open(plan_path, encoding="utf-8"))
    todo = []
    for sc in plan.get("timeline") or []:
        g, n = sc.get("generate"), sc.get("scene")
        if not g or (scene is not None and n != scene):
            continue
        if not g.get("prompt"):
            sys.exit(f"scene {n}: generate.prompt is empty — write it from generate.brief first")
        if not sc.get("asset_file"):
            sys.exit(f"scene {n}: set asset_file (e.g. gen/{n}.jpg) — the image is written there")
        out = confined(asset_dir, sc["asset_file"], f"scene {n}")
        side = confined(asset_dir, out + ".json", f"scene {n} stamp")   # a planted symlink is refused too
        old = json.load(open(side, encoding="utf-8")) if os.path.exists(out) and os.path.exists(side) else {}
        item = {"n": n, "out": out, "side": side, "text": g["prompt"], "seconds": None, "src": None, "refs": []}
        if (g.get("kind") or "image") not in ("image", "video"):
            sys.exit(f"scene {n}: generate.kind is image or video, not {g['kind']!r}")
        if g.get("kind") == "video":
            if not out.lower().endswith(".mp4"):
                sys.exit(f"scene {n}: a generated clip's asset_file ends in .mp4 (e.g. gen/{n}.mp4)")
            if g.get("cast") or g.get("ref"):
                sys.exit(f"scene {n}: cast/ref are for stills — put the cast on the still this clip "
                         f"animates (from:)")
            if g.get("edit"):
                sys.exit(f"scene {n}: edit is for stills — for a clip change the prompt, or edit the "
                         f"still it animates (from:)")
            secs = g.get("seconds") or clip_seconds(float(sc.get("end_sec", 0)) - float(sc.get("start_sec", 0)))
            if secs not in (4, 6, 8) or (VIDEO_RES != "720p" and secs != 8):
                sys.exit(f"scene {n}: generate.seconds is 4, 6 or 8 (8 only above 720p), not {secs!r}")
            stamp = {"prompt": g["prompt"], "model": VIDEO_MODEL, "aspect": "9:16", "res": VIDEO_RES,
                     "seconds": secs}
            if g.get("from"):
                src = confined(asset_dir, g["from"], f"scene {n} from")
                if not os.path.isfile(src):
                    sys.exit(f"scene {n}: generate.from {g['from']} doesn't exist — make the still first")
                stamp["from"] = hashlib.sha256(open(src, "rb").read()).hexdigest()[:16]   # an edited still re-films
                item["src"] = src
            if not same(old, stamp):
                todo.append({**item, "stamp": stamp, "kind": "video", "seconds": secs})
            continue
        stamp = {"prompt": g["prompt"], "model": MODEL, "aspect": "9:16", "size": SIZE}
        made_from_prompt = same(old, stamp)
        edit = g.get("edit")
        refs, note = references(plan, g, asset_dir, n)
        # Hashed references live beside `edit` in the stamp, not in the prompt part: adding a
        # cast to an image made without one is a change to make (one image), but must not
        # read as "the prompt changed" and block an edit that brings the face in.
        rk = {"refs": hashlib.sha256(b"".join(open(p, "rb").read() for p in refs)).hexdigest()[:16]} if refs else {}
        if made_from_prompt and (not edit or old.get("edit") == edit) and all(old.get(k) == v for k, v in rk.items()):
            continue                  # up to date; an applied edit stays applied when `edit` is removed
        item["refs"] = refs
        if edit:
            if not os.path.exists(out):
                sys.exit(f"scene {n}: generate.edit set but there is no {sc['asset_file']} to edit — "
                         f"remove edit to generate it from the prompt first")
            if old and not made_from_prompt:
                sys.exit(f"scene {n}: generate.prompt changed and edit is set — remove edit to start over "
                         f"from the new prompt, or restore the prompt to edit the current image")
            todo.append({**item, "text": f"{note} {edit}".strip(), "stamp": {**stamp, "edit": edit, **rk},
                         "kind": "edit"})
        else:
            todo.append({**item, "text": f"{note} {g['prompt']}".strip(), "stamp": {**stamp, **rk}, "kind": "image"})
    return todo


def cost(it):
    if it["kind"] == "video":
        per = VIDEO_PRICES.get(VIDEO_MODEL, {}).get(VIDEO_RES)
        if per is None:
            sys.exit(f"no cached price for {VIDEO_MODEL} {VIDEO_RES} — add it to VIDEO_PRICES")
        return per * it["seconds"]
    per = PRICES.get(MODEL, {}).get(SIZE)
    if per is None:
        sys.exit(f"no cached price for {MODEL} {SIZE} — add it to PRICES from the Gemini pricing page")
    return per


def generate(plan_path, asset_dir, scene=None, yes=False, make=None, shoot=None, cap=None):
    """Prints the estimate; with yes, generates. Returns how many assets were made.
    make(text, image) -> image bytes and shoot(text, seconds, image, mime) -> mp4 bytes are
    the providers, stubbed by the self-test."""
    todo = pending(plan_path, asset_dir, scene)
    total = 0.0
    for it in todo:
        c = cost(it)
        total += c
        what = (f"{it['seconds']}s clip" + (f" of {os.path.basename(it['src'])}" if it["src"] else "")
                if it["kind"] == "video" else "edit" if it["kind"] == "edit" else "image")
        what += f" +{len(it['refs'])} ref" if it["refs"] else ""
        print(f"  scene {it['n']}  {os.path.relpath(it['out'], os.path.realpath(asset_dir))}  "
              f"{what} ${c:.2f}  {it['text'][:60]}")
    clips = sum(it["kind"] == "video" for it in todo)
    print(f"{len(todo) - clips} image(s) x {MODEL} {SIZE}, {clips} clip(s) x {VIDEO_MODEL} {VIDEO_RES}"
          f"  ≈ ${total:.2f}  (prices as of {PRICES_AT})")
    if not todo:
        return 0
    if cap is not None and total > cap:
        sys.exit(f"estimate ${total:.2f} is over the ${cap:.2f} cap — nothing sent; drop scenes "
                 f"(--scene) or raise --cap")
    if not yes:
        print("estimate only — nothing sent. Rerun with --yes once the price is approved.")
        return 0
    key = lambda: load_key("GEMINI_API_KEY", "3c")   # noqa: E731 — only a real provider needs it
    make = make or (lambda text, image=None, refs=(): imagine(text, key(), image, refs))
    shoot = shoot or (lambda text, seconds, image=None, mime=None: film(text, key(), seconds, image, mime))
    for it in todo:
        out, side = it["out"], it["side"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if it["kind"] == "video":
            src = open(it["src"], "rb").read() if it["src"] else None
            mime = "image/png" if (it["src"] or "").lower().endswith(".png") else "image/jpeg"
            data = shoot(it["text"], it["seconds"], src, mime)
        else:
            data = make(it["text"], open(out, "rb").read() if it["kind"] == "edit" else None,
                        [(open(p, "rb").read(), mime_of(p)) for p in it["refs"]])
        if os.path.exists(out):
            keep_version(asset_dir, out, side)
        if it["kind"] == "video":
            silent(data, out)
        else:
            open(out, "wb").write(data)
        json.dump(it["stamp"], open(side, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  {dict(image='made', edit='edited', video='filmed')[it['kind']]} scene {it['n']}", flush=True)
    return len(todo)


def keep_version(asset_dir, out, side):
    """Copy out (and its stamp) to the next free <stem>.v<k><ext> before it is overwritten."""
    stem, ext = os.path.splitext(out)
    k = 1
    while os.path.exists(f"{stem}.v{k}{ext}"):
        k += 1
    v = confined(asset_dir, f"{stem}.v{k}{ext}", "kept version")
    shutil.copyfile(out, v)
    if os.path.exists(side):
        shutil.copyfile(side, confined(asset_dir, v + ".json", "kept version stamp"))
    return v


def self_test():
    d = tempfile.mkdtemp(prefix="generate-test-")
    calls, sent_refs = [], []

    def make(text, image=None, refs=()):
        calls.append((text, image))
        sent_refs.append(list(refs))
        return f"img:{text}".encode()

    plan = {"timeline": [
        {"scene": 1, "asset_file": "talk.mp4"},
        {"scene": 2, "asset_file": "gen/2.jpg", "generate": {"brief": "summer beach", "prompt": "a beach, 9:16"}},
        {"scene": 3, "asset_file": "gen/3.jpg", "generate": {"brief": "sunscreen", "prompt": "a towel, 9:16"}}]}
    pp = os.path.join(d, "edit_plan.yaml")
    save = lambda: yaml.safe_dump(plan, open(pp, "w", encoding="utf-8"), allow_unicode=True)  # noqa: E731
    save()
    assert generate(pp, d, make=make) == 0 and not calls, "estimate called the provider"
    assert not os.path.exists(os.path.join(d, "gen")), "estimate wrote files"
    assert generate(pp, d, yes=True, make=make) == 2 and len(calls) == 2
    assert os.path.exists(os.path.join(d, "gen", "3.jpg.json"))
    assert generate(pp, d, yes=True, make=make) == 0, "unchanged prompts re-generated"
    side2 = os.path.join(d, "gen", "2.jpg.json")
    json.dump({**json.load(open(side2)), "note": "hand-added"}, open(side2, "w"))
    assert generate(pp, d, yes=True, make=make) == 0, "an extra stamp key re-billed"
    plan["timeline"][1]["generate"]["prompt"] = "a rocky beach, 9:16"
    save()
    assert generate(pp, d, yes=True, make=make) == 1, "edited prompt not re-generated"
    plan["timeline"][2]["generate"]["prompt"] = "a blue towel, 9:16"
    plan["timeline"][1]["generate"]["prompt"] = "a sandy beach, 9:16"
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 1 and calls[-1][0] == "a blue towel, 9:16", "--scene ignored"
    g3, img3 = plan["timeline"][2]["generate"], os.path.join(d, "gen", "3.jpg")
    g3["edit"] = "make it red"
    save()
    assert generate(pp, d, scene=3, make=make) == 0 and len(calls) == 4, "edit estimate called the provider"
    assert generate(pp, d, scene=3, yes=True, make=make) == 1
    assert calls[-1] == ("make it red", b"img:a blue towel, 9:16"), "edit not sent with the current image"
    assert open(img3, "rb").read() == b"img:make it red"
    assert open(os.path.join(d, "gen", "3.v1.jpg"), "rb").read() == b"img:a towel, 9:16", "regenerated image not kept"
    assert open(os.path.join(d, "gen", "3.v2.jpg"), "rb").read() == b"img:a blue towel, 9:16", "edited image not kept"
    assert json.load(open(os.path.join(d, "gen", "3.v2.jpg.json")))["prompt"] == "a blue towel, 9:16", "stamp not kept"
    assert generate(pp, d, scene=3, yes=True, make=make) == 0, "applied edit re-billed"
    del g3["edit"]
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 0, "removing an applied edit re-generated"
    g3["edit"] = "add stripes"
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 1 and calls[-1][1] == b"img:make it red", "edits don't stack"
    assert open(os.path.join(d, "gen", "3.v3.jpg"), "rb").read() == b"img:make it red"
    g3["prompt"] = "a green towel, 9:16"
    save()
    try:
        generate(pp, d, scene=3, make=make)
        raise AssertionError("prompt change with edit set accepted")
    except SystemExit as e:
        assert "prompt changed" in str(e), e
    del g3["edit"]
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 1 and calls[-1] == ("a green towel, 9:16", None)
    assert open(os.path.join(d, "gen", "3.v4.jpg"), "rb").read() == b"img:add stripes", "regeneration lost the edit"
    # A cast brings its reference in. Adding it to an image made without one, with `edit`
    # set, edits that image (the joke01 face fix) rather than refusing "prompt changed".
    os.makedirs(os.path.join(d, "refs"))
    open(os.path.join(d, "refs", "A.png"), "wb").write(b"face-A")
    plan["characters"] = {"A": {"ref": "refs/A.png", "desc": "round face, thick straight brows"}}
    g3.update(cast=["A"], edit="same man as the reference")
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 1 and calls[-1][1] == b"img:a green towel, 9:16"
    assert sent_refs[-1] == [(b"face-A", "image/png")], sent_refs[-1]
    assert "A (round face, thick straight brows) is the person in reference image 1" in calls[-1][0], calls[-1][0]
    assert calls[-1][0].endswith("same man as the reference"), calls[-1][0]
    assert generate(pp, d, scene=3, yes=True, make=make) == 0, "an applied cast re-billed"
    open(os.path.join(d, "refs", "A.png"), "wb").write(b"face-A2")
    assert generate(pp, d, scene=3, yes=True, make=make) == 1, "a changed reference did not re-run"
    del g3["edit"], g3["cast"]
    save()
    assert generate(pp, d, scene=3, yes=True, make=make) == 0, "removing an applied cast re-generated"
    plan["timeline"][1]["generate"]["cast"] = ["B"]
    save()
    try:
        generate(pp, d, scene=2, make=make)
        raise AssertionError("unknown cast accepted")
    except SystemExit as e:
        assert "characters.B.ref" in str(e), e
    del plan["timeline"][1]["generate"]["cast"]
    for bad, msg in (({"asset_file": "../x.jpg"}, "outside"), ({"asset_file": None}, "set asset_file"),
                     ({"generate": {"prompt": "p", "ref": "refs/none.png"}}, "doesn't exist"),
                     ({"generate": {"prompt": "p", "ref": "../../x.png"}}, "outside"),
                     ({"generate": {"prompt": ""}}, "prompt is empty"),
                     ({"generate": {"prompt": "p", "edit": "x"}}, "to edit")):
        sc = {"scene": 9, "asset_file": "gen/9.jpg", "generate": {"prompt": "p"}, **bad}
        plan["timeline"].append(sc)
        save()
        try:
            generate(pp, d, scene=9, make=make)
            raise AssertionError(f"{bad} accepted")
        except SystemExit as e:
            assert msg in str(e), e
        plan["timeline"].pop()
    shots = []

    def shoot(text, seconds, image=None, mime=None):   # a real mp4 with sound, like Veo's
        shots.append((text, seconds, image, mime))
        clip = os.path.join(d, "veo.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s=72x128:r=24:d={seconds}",
                        "-f", "lavfi", "-i", f"sine=d={seconds}", "-shortest", clip], check=True)
        return open(clip, "rb").read()

    def streams(path):
        return subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                               path], capture_output=True, text=True).stdout.split()

    save()                                             # the bad-input loop left scene 9 on disk
    generate(pp, d, yes=True, make=make)               # stills up to date, so only clips are pending
    still = open(os.path.join(d, "gen", "2.jpg"), "rb").read()
    plan["timeline"] += [
        {"scene": 5, "start_sec": 0, "end_sec": 5, "asset_file": "gen/5.mp4",
         "generate": {"kind": "video", "prompt": "waves at dusk"}},
        {"scene": 6, "start_sec": 5, "end_sec": 8, "asset_file": "gen/6.mp4",
         "generate": {"kind": "video", "prompt": "slow push in", "from": "gen/2.jpg"}}]
    save()
    assert generate(pp, d, make=make, shoot=shoot) == 0 and not shots, "clip estimate called Veo"
    try:                                               # 6 s + 4 s at $0.05 = $0.50
        generate(pp, d, yes=True, make=make, shoot=shoot, cap=0.40)
        raise AssertionError("over-cap run went ahead")
    except SystemExit as e:
        assert "over the $0.40 cap" in str(e) and not shots, e
    assert generate(pp, d, yes=True, make=make, shoot=shoot) == 2
    assert [(t, sec) for t, sec, _, _ in shots] == [("waves at dusk", 6), ("slow push in", 4)], shots
    assert shots[1][2] == still and shots[1][3] == "image/jpeg", "the still was not sent to animate"
    assert streams(os.path.join(d, "gen", "5.mp4")) == ["video"], "Veo's audio was kept"
    bait = os.path.join(d, "bait.txt")                 # nothing written beside `out` but `out` itself
    open(bait, "w").write("keep")
    os.symlink(bait, os.path.join(d, "gen", "7.mp4.part.mp4"))
    silent(shoot("x", 4), os.path.join(d, "gen", "7.mp4"))
    assert open(bait).read() == "keep" and streams(os.path.join(d, "gen", "7.mp4")) == ["video"]
    assert generate(pp, d, yes=True, make=make, shoot=shoot) == 0, "unchanged clips re-filmed"
    open(os.path.join(d, "gen", "2.jpg"), "wb").write(b"img:edited still")
    assert generate(pp, d, yes=True, make=make, shoot=shoot) == 1 and shots[-1][2] == b"img:edited still", \
        "an edited still did not re-film its clip"
    assert os.path.exists(os.path.join(d, "gen", "6.v1.mp4")), "the old clip was not kept"
    for bad, msg in (({"asset_file": "gen/9.jpg"}, ".mp4"), ({"generate": {"kind": "video", "prompt": "p", "edit": "x"}}, "for stills"),
                     ({"generate": {"kind": "video", "prompt": "p", "seconds": 5}}, "4, 6 or 8"),
                     ({"generate": {"kind": "video", "prompt": "p", "from": "gen/none.jpg"}}, "doesn't exist"),
                     ({"generate": {"kind": "gif", "prompt": "p"}}, "image or video"),
                     ({"generate": {"kind": "video", "prompt": "p", "cast": ["A"]}}, "for stills")):
        plan["timeline"].append({"scene": 9, "asset_file": "gen/9.mp4", "generate": {"kind": "video", "prompt": "p"}, **bad})
        save()
        try:
            generate(pp, d, scene=9, make=make, shoot=shoot)
            raise AssertionError(f"{bad} accepted")
        except SystemExit as e:
            assert msg in str(e), e
        plan["timeline"].pop()
    save()
    sent = []                                          # the long-running operation: submit, poll, download

    def fetch(url, key, body=None, raw=False):
        sent.append((url, body))
        if raw:
            return b"mp4 bytes"
        if body is not None:
            return {"name": "operations/op1", "done": False}
        return ({"name": "operations/op1", "done": False} if len(sent) < 3 else
                {"done": True, "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://v/1"}}]}}})
    assert film("p", "k", 4, image=b"jpg", fetch=fetch, every=0) == b"mp4 bytes"
    assert [u for u, _ in sent] == [f"{API}/models/{VIDEO_MODEL}:predictLongRunning", f"{API}/operations/op1",
                                    f"{API}/operations/op1", "https://v/1"], sent
    req = sent[0][1]
    assert req["parameters"] == {"aspectRatio": "9:16", "durationSeconds": 4, "resolution": VIDEO_RES}, req
    assert req["instances"][0]["image"] == {"bytesBase64Encoded": base64.b64encode(b"jpg").decode(),
                                            "mimeType": "image/jpeg"}, req
    for done, msg in (({"done": True, "error": {"message": "x"}}, "Veo failed"), ({"done": True, "response": {}}, "no video")):
        try:
            film("p", "k", 4, fetch=lambda url, key, body=None, raw=False: done, every=0)
            raise AssertionError(msg)
        except SystemExit as e:
            assert msg in str(e), e
    assert (clip_seconds(3.0), clip_seconds(4.0), clip_seconds(5.5), clip_seconds(12)) == (4, 4, 6, 8)
    victim = os.path.join(d, "victim.txt")             # a symlinked stamp must not be followed out
    open(victim, "w").write("keep")
    os.makedirs(os.path.join(d, "assets", "gen"))
    os.symlink(victim, os.path.join(d, "assets", "gen", "2.jpg.json"))
    try:
        generate(pp, os.path.join(d, "assets"), scene=2, yes=True, make=make)
        raise AssertionError("symlinked stamp followed")
    except SystemExit as e:
        assert "outside" in str(e), e
    assert open(victim).read() == "keep"
    for shape in ({"steps": [{"type": "model_output", "content": [{"type": "text", "text": "x"},
                                                                  {"type": "image", "data": "QQ=="}]}]},
                  {"outputs": [{"type": "image", "data": "QQ==", "mime_type": "image/jpeg"}]}):
        assert first_image(shape) == "QQ==", shape
    assert first_image({"outputs": [{"type": "text", "text": "refused"}]}) is None
    print(f"self-test ok  ({d})")


def main():
    ap = argparse.ArgumentParser(usage=__doc__.split("\n\n")[1])
    ap.add_argument("plan", nargs="?")
    ap.add_argument("assets", nargs="?")
    ap.add_argument("--scene", type=int)
    ap.add_argument("--yes", action="store_true", help="generate (after the user approved the estimate)")
    ap.add_argument("--cap", type=float, help="refuse if the estimate is over this many USD")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    if a.doctor:          # model lookups prove key and models without generating (no free tier for either)
        key = load_key("GEMINI_API_KEY", "3c")
        for m in (MODEL, VIDEO_MODEL):
            call(f"{API}/models/{m}", key)
        return print(f"GEMINI_API_KEY: ok, from {find_key('GEMINI_API_KEY')[1]} ({MODEL}, {VIDEO_MODEL} reachable)")
    if not (a.plan and a.assets):
        ap.error("an edit plan and its asset directory, --doctor or --self-test")
    generate(a.plan, a.assets, a.scene, a.yes, cap=a.cap)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    main()
