---
name: video-style-clone
description: Reverse-engineer the layout and edit style of a reference video ad — a template with a logo bar, an info panel, burned-in captions, timed cuts — measure it to the pixel, save it as a reusable template, and rebuild it with different footage, copy, language or brand. Use this whenever someone points at an existing video and wants one like it, or hands over a reference MP4 with raw footage or a folder of photos. Also for translated cuts of an ad already built ("make a Korean version"), re-skinning with a new logo, palette or brand guide, slideshow ads from stills, resizing between 4:5 / 9:16 / 1:1, and any ad work where the layout must match a template exactly rather than approximately.
argument-hint: "[project folder] [reference video, footage/stills, target language]"
---

# Cloning a video ad's style

Someone has a reference video whose format works — a PDP ad, a listing video, a
social cut — and wants the same thing with different footage, different copy, or
a different language. The value you add is fidelity: the rebuild should sit next
to the reference and look like it came out of the same template, not like a
homage to it.

The failure mode to avoid is eyeballing. A layout that is "about right" reads as
sloppy the moment someone puts the two files side by side. Every band boundary,
pill, chip and button in the reference sits at an exact pixel. Measure them once,
save them, and never measure that layout again.

## How a project is laid out

Work happens in a client folder, one video per directory under `input/`:

```
brand.json                     the client: palette, logos, faces, voice
input/spring-sale/
    video.json                 the edit — template, shots, copy, captions   [git]
    materials.json             what the materials were, and where they came from   [git]
    ref.mp4  clip.mov  ...     the materials themselves                     [ignored]
output/spring-sale.tc.mp4      the render, mirroring the input name         [ignored]
output/spring-sale.ko.mp4
```

Everything that is a decision is text, tracked in git. Everything that is a file
from a camera or a render farm is ignored. That is what makes a second language,
a re-cut six months later, or a handover to someone else cheap.

## Phase 0 — Is the project ready?

The user names a folder. Check it before anything else:

```bash
cd <the folder> && uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py" --ready
```

Exit 2 means it is not set up: run the `init-video` skill here and now — scaffold,
brand interview, verification — then come back. Do not improvise a half-scaffold;
the layout above is what `build.py` expects.

Then pick the video's name. It is the input directory, the output filename and the
thing everyone will refer to it by afterwards, so use a slug of the campaign, not
`video1`. An existing directory under `input/` means another cut of something
already built — read its `video.json` first. The exception is a directory with a
`brief.md` and no `video.json`: the new-video skill set it up. Its name is the slug, and
the brief gives the reference (`ref.*` in the folder) or template, the ratios and the
languages; don't ask those again.

## Phase 1 — Is this layout already known?

Measured layouts live as JSON, in the project's `templates/` (this client's) and in
the plugin's (the ones that ship). Look before you measure:

```bash
ls templates/ "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/templates/"
uv run python -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['summary']);print(d['notes'])" \
  "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/templates/single-offer-9x16.json"
```

If the reference is one of these — or the user names a template, or is asking for
another cut of something you already built — set `template` in `video.json` and
skip to Phase 3. A template carries the canvas, the band boundaries, every panel
element box, and a `notes` field recording the judgement calls made when it was
measured. Read `notes`; it is where the non-obvious decisions are.

If it is a new layout, measure it (Phase 2), then save it (Phase 2b) so the next
job starts here instead.

## Phase 2 — Measure the reference

Put the reference in the video's own directory (`input/<video>/ref.mp4`) — it is
material like any other, and the index records where it came from. Run the
analyzer on the reference and the source before you plan anything:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/measure.py" \
  input/<video>/ref.mp4 input/<video>/clip.mov --out build/<video>/measure
```

It writes to the output directory and prints a summary:

| Output | What it tells you |
|---|---|
| `ref_sheet.jpg`, `src_sheet.jpg` | contact sheets — the whole edit at a glance |
| `ref_frame.png` | one full-res frame to crop and inspect |
| band table | exact y-ranges of flat-colour chrome vs. the moving video window |
| scene cuts | the reference's cut rhythm, in seconds |
| static regions | burned-in overlays in the **source** that your crop must dodge |
| loudness | mean/max dB of both — for reference only; every build is normalised to -14 LUFS |
| tempo | the reference's BPM, so the bed you pick can match its pace |

Read the contact sheets as images. They answer, in one look: how many shots, what
the captions look like, where text sits, what the panel contains.

Then take `ref_frame.png` apart. The analyzer prints the top-level bands; this
gets you every element inside a panel — headline, pill, rule, list rows, button:

```python
from PIL import Image
im = Image.open("ref_frame.png").convert("RGB"); px = im.load()
PANEL_Y = 752                        # the panel's top edge: take it from the band table above
bg = px[3, PANEL_Y + 10]             # the panel's flat fill colour, just inside it
prev = False
for y in range(PANEL_Y, im.height):
    xs = [x for x in range(im.width) if px[x, y] != bg]
    if bool(xs) != prev:
        print(("start" if xs else "end"), y, (min(xs), max(xs)) if xs else "")
        prev = bool(xs)
```

Scan a row through the middle of each element for its left and right edges too —
a centred pill's width is set by its text, but a two-up button row is not, and
guessing the gutter is how the second button ends up 20px off.

Row pitch in a list is worth computing rather than guessing: take (last row top −
first row top) / (n − 1). In one reference that was 54.25px — fractional, and not
something anyone picks by eye.

### Phase 2b — Save what you measured

This is the step that makes the next job cheap. Write the numbers to
`templates/<name>.json` in the project — it is tracked, so the layout ships with
the client's repo — and it becomes a predefined template:

```json
{
  "name": "panelkind-ratio",
  "measured_from": null,
  "summary": "one line: what this layout is and what is in the panel",
  "canvas": {"w": 1080, "h": 1920, "fps": 30},
  "bands":  {"bar_h": 186, "vid_y": 186, "vid_h": 1180, "panel_y": 1366},
  "bar":    {"fill": "white", "logo_h": 120, "logo_max_w": 760},
  "panel":  {"kind": "offer", "fill": "surface", "...": "element boxes"},
  "caption": {"x": 70, "y": 60, "sizes": [66, 86], "indent": 26, "stroke": 11},
  "notes": "judgement calls, omitted elements, retarget decisions"
}
```

Rules that keep templates reusable:

- **Colours are palette keys, not hex.** `"fill": "surface"` resolves against the
  brand (Phase 4). A hardcoded `#C2185B` makes the template single-client.
- **`kind` selects a renderer** in `scripts/build.py`. Two exist: `offer` (pill,
  two-tone headline, one CTA, rule, URL) and `list` (headline, badge, ruled
  label, chip rows, CTA). If a new reference genuinely needs a third, say so and
  ask: a `render_<kind>_panel` beside them is a change to the shared plugin, not
  to this job. Don't bend an existing renderer to fit.
- **Put the omissions in `notes`.** The `single-offer-9x16` template drops the
  reference's date line and collapses two CTAs into one. Someone comparing the
  build to the reference will notice; `notes` is where they find out it was
  deliberate.

A layout that is genuinely generic — nothing client-specific in it — is worth
offering back to the plugin's `templates/`. Ask before doing that; it is a change
to a shared repo, not to the client's.

## Phase 3 — Take in the materials

Everything the video is built from goes in `input/<video>/`. Then index it:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/index_materials.py" <video>
```

`materials.json` is the only committed trace of files git never holds: duration,
resolution, size, a content hash, and a `source` field for the Drive link. Fill
`source` in — from what the user pasted, or by asking. The index is regenerated
safely: `source` and any key you add survive, and a file that disappears is kept
and marked `"present": false` rather than dropped, because the link to a file
someone deleted locally is exactly what you will want later.

Re-run it whenever material is added or replaced. A changed hash on the same
filename means someone swapped a take — worth a word to the user, because the
shot list's in-points are now pointing at a different edit.

Then read the material:

**Are they videos or stills?** A directory of images, or an explicit ask for a
slideshow, means slides mode — leave `src` unset (it defaults to the video's own
directory) and the build detects it. Stills get the same ken-burns push-in as
footage, so the result moves; what it does not have is audio, so a slideshow needs
`music` or it ships silent. Decide which, and say which — a silently silent ad is
a bug report waiting to happen. For a bed, ask whether the user already has one or
wants one from OpenTracks — the default source; video-edit's "Music" bullet has the
rules. Footage plays over its own sound unless `music`
is set, which replaces it. `music_in` skips a bed's intro (seconds, default 0). Footage with no audio track is caught at build
time and reported, not guessed at.

**Aspect.** A 9:16 source dropped into a 16:9 window loses two thirds of the
frame. That is often still the right call — the reference layout *is* the style —
but say so plainly rather than silently cropping. If the loss is severe, offer
the alternative (a taller window, a shorter panel) and let the user choose.

**Burned-in graphics.** Source footage frequently carries overlays the client
baked in: counters, watermarks, minimaps, lower thirds. The analyzer's static-
region report finds them by looking for pixels that never change across the clip.
Set `crop_band` to the clear gap between them and the script derives the crop
rect — the widest window-aspect rect that fits, centred. When the band is
shorter than the window the crop loses width and scales up, which is softer but
keeps the watermark out. That gap is usually the binding constraint on framing,
not taste.

Pick shots off the contact sheet by timestamp, one per beat of the reference's
rhythm. Favour shots where the subject is legible inside the crop band.

**On the beat.** A music bed can carry the edit's timing. `sync` in `video.json`:

| Key | Values | Does |
|---|---|---|
| `snap` | `"beat"`, `"bar"`, `null` | moves each cut to the next beat (or bar) at or after where you put it, never leaving a shot under `min_shot` (default 0.8 s). The build prints every shot's before→after, so say what moved |
| `kenburns` | `"drift"`, `"beat"` | `beat` takes the same 10% push-in in one notch per beat instead of a steady drift |
| `pulse` | `null`, `"flash"`, `"zoom"` | a white flash or a 4% punch on the video window on every beat. Loud; off unless asked for |

Write the shot list at the lengths you want, then add `snap` — cuts only ever get
later, by up to one beat (or one bar), so a shot near the end of its footage can
run out, and the build warns when one does. The grid comes from `beats.py`
(librosa, in its own `uv` env) and is cached at `build/<video>/beats.json` against
the bed's hash. `bar` is every fourth beat from a guessed phase — librosa finds
beats, not downbeats — so check a bar-snapped cut by ear. A bed with no clear
beat (speech, ambience, a drone) builds as if `sync` were unset, and says so.

## Phase 4 — Read the brand

`brand.json` at the project root is already written (init-video did it). Read it
before drafting a word:

| Key | What it drives |
|---|---|
| `palette` | every colour the template names (`accent`, `panel`, `ink`, `surface`, `white`, `muted`) |
| `logo` | per-language logo files, with a `default` |
| `fonts` | per-language face overrides when the brand licenses its own |
| `voice` | register, things to avoid, things every cut must carry |

`voice` is for you, not the script: read `avoid` and treat it as binding. If a
brand bars health claims and the template has a five-point feature list, the list
gets filled with something else or the template loses a row.

Logos decide the bar colour. A reference with a brand-coloured bar and a
knocked-out white logo cannot take a dark logo on the same bar — keep the bar
white and say you did.

## Phase 5 — Write video.json

One file is the whole job: the edit, the copy deck, every language.

```bash
cp "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/assets/video.example.json" input/<video>/video.json
```

Reference ads carry claims: prices, distances, amenities, guarantees. Those
belong to the reference's advertiser, not to yours. Write copy that is either
supplied by the user or grounded in what the footage visibly shows — a station
name on a sign, a count that appears on screen, the time of day. Do not invent
prices, walk times, turn-by-turn directions or amenities to fill the template,
and do not carry the reference's numbers across.

Key the deck by language from the start, even when only one language is wanted
today — retrofitting the second one costs more than leaving the door open. What
stays shared is the edit (`shots`, the framing, the audio); what varies
is the copy, the face and the logo. Each language writes its own output file, so
cuts never overwrite each other.

Translating is not substituting. Three things that bite:

- **Length.** CJK is compact; Hangul and Latin are not. `fitted()` shrinks text
  to the box, so an over-long line does not overflow — it goes quietly small and
  looks weak. If a translated headline drops more than ~15% below the original
  size, cut a word instead of accepting it, and tell the user what you cut.
- **The brand lockup.** A descriptor that works in the source language may be
  opaque in the target. Ask for the in-market version rather than inventing one,
  and take it from the localized logo when there is one.
- **The face.** Pick it by the copy's language, and run the build — the script
  refuses to render text its face cannot draw. See the font section in
  `references/gotchas.md`; the failure it *cannot* catch is the one that matters
  most.

## Phase 6 — Build

From the project root:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/build.py" <video> [lang]
```

Nothing in the script is edited per job. What it does, in order:

1. **The layout comes from the template JSON**, the brand from `brand.json`, the
   edit from `video.json`. One measurement, one source of truth, no forked script.
2. **A preflight refuses to build** with a face that cannot draw the copy, or a
   caption count that does not match the shot count. Both are failures you would
   otherwise only find by watching the output.
3. **PIL renders the static chrome once**, as a single transparent PNG the size
   of the whole canvas, with a hole where the video window goes. One overlay, one
   place to fix a misplaced pixel.
4. **PIL renders each caption as its own PNG.** Not ffmpeg `drawtext` — it cannot
   do multi-line offsets, per-line sizes, or a stroke plus a shadow.
5. **Each shot becomes its own intermediate mp4** in `build/<video>/<lang>/`, with
   its framing, its push-in and its caption baked in. Caption timing is then
   relative to the shot, which is trivial, instead of to a global timeline.
6. **Concat, then one final pass** adds the chrome, the fades and the audio, and
   writes `output/<video>.<lang>.mp4`, plus a contact sheet at
   `build/<video>/<lang>/sheet.jpg`.

Once the first cut is right, build the rest in one go:

| Flag | Does |
|---|---|
| `<video> --all` | every language in `video.json`, each to its own output and sheet |
| `<video> --ratio 9x16,4x5,1x1` | retargets the template: bar and panel keep their measured size, the video window absorbs the difference. Output `output/<video>.<lang>.<ratio>.mp4`; the template's native ratio keeps the plain name. A template lists what it retargets to under `retarget.ratios` — anything else is refused, so add the ratio there once you have looked at the result |
| `--stale` | rebuilds every output older than its `video.json`, `brand.json` or template — after a copy fix across several videos, or a palette change. Plain mtimes: `touch` a params file to force its cuts |

Flags combine: `build.py spring-sale --all --ratio 9x16,1x1` is every language at
both ratios. A 1:1 cut of a 9:16 layout leaves a ~340px strip of footage; look at
the sheet before you promise it.

## Phase 7 — Verify, push, then commit the decisions

Read `build/<video>/<lang>/sheet.jpg` as an image — one frame per second, six
across. Check, in this order: does every caption land on a frame that supports it (a
caption saying "signs everywhere" over a blank wall means the in-point is wrong);
is every caption legible against its background; does the duration and cut count
sit near the reference's; are any glyphs rendering as tofu or in the wrong script.

Then pull one full frame at full resolution and confirm the chrome. For a second
language cut, pull the same timestamp from both files and compare them — the
chrome should differ only in the copy and the logo.

Fix `video.json` and rebuild — it is cheap, and the first pass is rarely right.

When the cut is approved, push each render to the client's Drive folder and record
where it went. The Google Drive MCP's `create_file` takes content inline, which is
fine for a JSON and not for a 40MB mp4 — so the file itself moves one of two ways:
`cp` it into the client folder if Drive for Desktop syncs it on this machine, or
ask the user to upload it. Then find it with the Drive MCP (`search_files` by
filename in the client folder), take its link, and record it:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/index_materials.py" <video> \
  --render <lang>[.<ratio>] <drive url>
```

That writes `renders: {<cut>: {url, sha, at}}` into `materials.json` — the hash
of the file as pushed, so a later rebuild that was never re-uploaded shows as a
mismatch. Python never talks to Drive; you do, and the script only writes it down.

Then commit the text: `video.json`, `materials.json`, any new template. Not the
render — it is ignored, and it rebuilds from what you committed. Say what you
committed in one line.

Report what you matched in a table against the reference, and state the
trade-offs you made: cropping losses, placeholder copy, dropped elements, a
translated line you shortened, a silent slideshow. The user can only correct what
you surface.

## Second cuts: language, brand, ratio

Once `video.json` exists, a new language, a new client or a new ratio is an edit to
one file, not a project:

| Ask | Change |
|---|---|
| another language | a `copy` + `captions` entry, then `build.py <video> <lang>` — or `--all` |
| another ratio | `build.py <video> --ratio 4x5` if the template lists it; a different `template` if the layout itself changes |
| a copy fix across many cuts | edit, then `build.py --stale` |
| stills instead of footage | drop images in `input/<video>/`, unset `src` and `shots` (or list `[image, duration]` per shot) |
| the same ad for another client | the other client's folder; copy `video.json` across and retranslate |

This is why the params file is the artifact, not the mp4. Keep the measurements in
the template, the brand in `brand.json`, the edit in `video.json`, and the second
change takes a minute instead of an hour.

## Gotchas

`references/gotchas.md` collects the ffmpeg and font failures this workflow hits
— runaway encodes, `zoompan` expression names, stair-stepping zooms, CJK and
Hangul font selection, missing glyphs, slideshow audio. Read it before debugging
an ffmpeg error by hand; most of them are already in there.
