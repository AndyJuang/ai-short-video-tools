---
name: video-edit
description: Cut a video from a plan — read a planning document or storyboard and a folder of raw footage and stills, pick the best take and in-point for every scene, write an ACOS edit plan, and render a finished 9:16 short (or 4:5 / 1:1 feed cut) with captions, SRT and a QC report (ACOS Path C). Use this when someone hands over a script, shot list, storyboard, ACOS content_id or 企劃 alongside a pile of material and asks to "edit this", "cut this together", "pick the footage", "make the video from this plan", "剪這支", or when the content-orchestrator production stage runs with render.mode ffmpeg. Not for cloning a reference ad's layout — that is video-style-clone.
argument-hint: "<plan file or ACOS content_id> <material folder> [--auto]"
---

# Editing a video from its plan

The plan says what each scene should show and say; the folder holds whatever the
shoot, the client and the stock library produced, usually unlabelled. The work
you add is the editor's eye: watching everything, choosing the take that actually
carries each scene, and placing the in-point where the action starts, not where
the camera started rolling.

Picking uses both picture and speech. Contact sheets show what a take looks like;
`transcribe.py` gives you what is said and when, so a scene whose VO or caption is a
spoken line is matched on the words, with the in-point at the cue's start.

`/video-edit <plan or content_id> <folder> [--auto]`

## Phase 0 — Ready?

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py" --render
```

Tools and caption faces only — the workspace does not have to be an init-video
project. Fix what fails before reading anything.

**Where the material lives.** Three cases, decided once here:

| Workspace | Material folder (sheets, `materials.json`, picks) | Plan | Render |
|---|---|---|---|
| ACOS (`content/`) | `content/assets/{content_id}/raw/` | `content/production/` | beside the plan |
| init-video project (`check_env.py --ready` exits 0) | `input/<video>/` — copy footage given elsewhere into it first | `input/<video>/edit_plan.yaml` (tracked) | `output/<video>_v<n>.mp4` |
| any other folder | that folder | beside the material | beside the plan |

In an init-video project pick the `<video>` slug the way video-style-clone Phase 0
does, and pass that name (not a path) to `index_materials.py`.

## Phase 1 — Find the plan

**ACOS** (a `content_id`, or a workspace with `content/` — only with applemint's
content-tools plugin, see the README): the plan is
`content/scripts_storyboard/{content_id}_script.md` plus
`content/assets/{content_id}/asset_manifest.yaml`, and the material is in
`content/assets/{content_id}/raw/`.

**Anything else:** read whatever plan is in or next to the folder — markdown,
PDF, docx, slides, a video-storyboard 9-cut table, storyboard frames as images.
Ask only if there are two plausible plans.

**A storyboard** (a cut table, storyboard frames, or only a brief to storyboard from): follow
`references/storyboard.md`. It gets the table into one shape as `storyboard.md`, and
`storyboard.py` converts it to `edit_plan.yaml` — timings, captions, narration lines, end card,
and either footage left to pick or `--generate` blocks. Works without the video-storyboard
plugin. Phases 2–3 then fill in the footage.

Either way, list the scenes back as a table: scene, duration, what it must show,
on-screen text, VO. When a duration is missing, estimate it from the VO (about
4 Traditional Chinese characters per second).

## Phase 2 — Log the material

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/index_materials.py" <folder|video> --sheets
```

Then transcribe every clip that has an audio track (`build.has_audio` style check, or
just try it — a silent clip yields no cues):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/transcribe.py" <clip> --lang tc|jp|en --out <folder>/.cues
```

It needs `ELEVENLABS_API_KEY` (`--doctor` to check; if missing, walk the user through init-video's "The key file" and Phase 3b, never ask for the key). Output is `<stem>.cues.json`
(`start`/`end` in source seconds, `text`, `speaker`), `.srt` and the raw
`.transcript.json`, a plain list of word dicts (not `{words: …}`); an existing transcript is
reused, so re-running costs nothing. With `--lang tc` only the cues and SRT are converted to
Traditional; the raw words stay in Scribe's Simplified, so take caption text from cues.
Skip clips that are b-roll or music-only.

Every clip gets `.sheets/<file>.jpg`: one labelled frame per `sheet_step`
seconds, plus its scene `cuts`, in `materials.json`. Read **every** sheet and
every still as an image, and every `.cues.json` — the right take is often the file with the least useful
name. For each file note what it shows, which seconds are usable, and what is
wrong with the rest: shake, focus hunting, blown or crushed exposure, the camera
being set down, burned-in text or watermarks, a horizontal frame that loses the
subject in a 9:16 crop (output is cover-cropped to the centre).

## Phase 3 — Pick

For each scene: the file, `source_in_sec`, and one line on why. For a spoken scene,
find the cue whose text carries the scene's line (the best match wins over the prettiest
frame) and set `source_in_sec` to that cue's `start`, less ~0.2s of lead-in. Otherwise
take it to the second from the sheet, nudging past a scene cut rather than straddling
it. Then:

- Several takes of the same line: prefer the one with fewer stumbles and a cleaner
  cue, then the better frame. Say which transcript words differ from the plan.

- The usable range from the in-point must cover the scene's duration. If it
  doesn't, pick another take or say the scene will hold its last frame.
- Don't use the same range twice unless the plan repeats a shot on purpose.
- A still is fine for a product or info-card scene; give it `ken_burns` so it
  moves (`zoom_in_punch` under `pace: sns`, Phase 4).
- **No fitting material is a gap, not a guess.** List the scene, what it needs,
  and the closest thing you have, and ask. This applies under `--auto` too.
  One answer you may offer: **generate a still or a clip** (Phase 4) — for mood, place
  or b-roll only, never as the product or a real person, and only if the client allows
  AI imagery. Never choose it yourself.

## Phase 4 — Write the plan

The edit plan's format is `references/edit-plan.md` (when content-tools is installed,
its `stages/production.md` "Edit Plan" is the owner and wins), and the delivery
constants are `references/shorts-output-spec.md`. Follow them; don't restate them here.

- **ACOS:** fill `asset_manifest.yaml` — `file_name` is the real filename in
  `raw/` (don't rename client footage), `source_in_sec` the pick, `status: ready`.
  A generated still or clip gets its entry too: `source: generated`,
  `file_name: gen/<scene>.jpg` or `.mp4`, `status: ready` once the user approved it.
  Then run content-orchestrator's `production` stage with `render.mode: ffmpeg`;
  it writes `content/production/{content_id}_edit_plan.yaml` and carries
  `source_in_sec` into the timeline.
- **Spoken scenes (this skill's extension):** instead of a hand-written `caption`, set
  `cues: <path to that clip's .cues.json, relative to the folder>` on the timeline scene.
  `render_plan.py` then makes one caption per cue inside the scene's source range, timed
  to the speech, and to the SRT. An explicit `caption` on the scene wins. Other render
  paths ignore `cues`, so ACOS Path B still needs its `caption`.
- **Narration (`voiceover.lines`):** when the plan has VO that nobody recorded, voice it:

  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/tts.py" <edit_plan.yaml> <folder> [--voice <voice_id>]
  ```

  The voice is `--voice` or `brand.json` `narrator.<lang>`; with neither, ask for one.
  Each line lands at its `asset_file`, default `vo/<id>.mp3`; an unchanged line is not
  re-billed. `render_plan.py` then lays each voiced scene's lines back to back and makes
  the scene as long as they are (+0.4 s), so durations in the plan become estimates.
  `voiceover.gap` (between lines, default 0.15 s) and `voiceover.tail` (after a scene's
  last line, default 0.4 s) change that spacing; with no music under the cut, give
  `voiceover.room_tone` (below) too, so the gaps aren't dead air.
  A voiced scene without a `caption` or `cues` gets one caption per line. The voiced
  lines replace `voiceover.asset_file` and `cuts`. Listen before the checkpoint: say what was
  mispronounced and fix it in the line's text (spell it out, add punctuation), not in timing.
- **Original dialogue (`voiceover.cuts`, this skill's extension):** when the voice is the
  source footage's own speech, not narration, cut it into one continuous track instead of
  per-line clips (separate clips with silence between them sound chopped):

  ```yaml
  voiceover:
    cuts:                                  # laid end to end
      - {src: source.mp4, in: 50.10, out: 59.60}
      - {pause: 2.0}                       # filled with room tone, never digital silence
      - {src: source.mp4, in: 79.92, out: 88.00}
    room_tone: {src: source.mp4, in: 64.0, out: 66.0}   # a quiet stretch with no speech
  ```

  Transcribe `src` first (Phase 2): each `in`/`out` that lands inside a word is moved to
  that word's edge, so a cut never keeps half a syllable. Joins get 40/60 ms fades only.
  Then render once and time scenes and captions against `qc.json` `voiceover_cuts`, which
  gives each piece's place on the track (`at`, `end`) after snapping. Pick room tone by
  listening for a stretch between lines with no breath or movement in it. Voices that
  aren't the client's need written permission before the ad runs: put that in
  `export_checklist`.
- **Generated stills (this skill's extension):** for a gap the user agreed to fill this way,
  give the scene `asset_file: gen/<scene>.jpg`, `animation: ken_burns` and
  `generate: {brief: <the plan's own words>, prompt: <yours>}`. Write the prompt from the
  brief, what the scenes either side show (so it cuts in), and `brand.json` palette and
  `voice.avoid`; always add: vertical 9:16, photographic, no text or lettering (captions are
  ours), no logos, no recognisable real people, no product shown. "No logos" alone doesn't
  hold: name the absence on each object that tends to carry one ("a plain laptop lid",
  "a solid white signboard with no stripes"), and when the script names real stores or
  brands, keep their colour schemes out of the picture too. Then price it — this
  sends nothing:

  ```bash
  uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/generate.py" <edit_plan.yaml> <folder>
  ```

  The estimate uses the price table cached in `generate.py` (`PRICES`, dated `PRICES_AT`);
  don't look prices up.
  Show the user each scene's prompt and the estimated total, and **wait for an explicit OK
  of the price, even under `--auto`**. Only then re-run with `--yes`. An image whose prompt
  is unchanged is not re-billed. Read every image before Phase 5.

  **Revising a still.** A local fix ("make the umbrella red", "remove the boat", stray
  lettering) is `edit: "<one change, said plainly>"` on the scene's `generate` block: the
  current image is sent back with it, so composition and light stay. A different picture
  is a new `prompt` (and no `edit`). Either way: `--scene N`, the estimate, the user's OK,
  `--yes` — an edit costs the same as a new image. Edits stack on the current image; a
  changed prompt with `edit` set is refused (say which one is meant). Every overwrite
  keeps the old file as `gen/N.v1.jpg`, `v2`, … with its `.json`; to roll back, copy both
  back over `gen/N.jpg`. Show the user before and after side by side.

  **The same person in several scenes.** Text alone draws a different face every time, even
  with an identical description. Generate the person's first scene, get it approved, crop
  the face (a strip of two or three crops from approved stills works better than one) to
  `refs/<name>.jpg`, and declare them once in the plan:

  ```yaml
  characters:
    A: {ref: refs/A.jpg, desc: "round face, thick straight brows, small eyes, broad nose"}
  ```

  Then every later scene with A gets `cast: [A]` in its `generate` block: A's reference
  goes first, with the instruction that the person is the same one. Name the features that
  drifted in `desc`; a general "same person" wasn't enough in testing. To fix a face that
  already drifted, keep the picture: add `cast` and `edit: "make the man the same person as
  the reference"`. `ref: [paths]` sends extra images without a name. A clip can't take a
  cast: put it on the still the clip animates.

  **Generated clips.** When a still won't carry the scene (water, crowds, a pour — real
  motion), the scene can be a Veo clip instead: `asset_file: gen/<scene>.mp4`,
  `animation: none`, and `generate: {kind: video, brief, prompt}`. The default is Veo 3.1
  Lite at 720p, 4, 6 or 8 s — the shortest that covers the scene, or `seconds:`. Prefer
  animating an approved still, `from: gen/<scene>.jpg`: the still is cheap to iterate, and
  the clip keeps its composition. Write a clip prompt as motion and camera ("slow push in,
  waves rolling in, light shimmering"), with the same fixed rules as a still. Same price
  step: the estimate lists each clip's seconds and cost, the user OKs it, then `--yes`. A
  clip takes a minute or more to render. Veo always adds sound; it is removed on write.
  Watch every clip before Phase 5: `index_materials.py <folder>/gen --sheets`, then read
  `gen/.sheets/<scene>.mp4.jpg`. A clip can't be `edit`ed:
  change its prompt, or edit the still it animates (that re-films it). If the user names a
  spend limit in the session, pass `--cap <USD>` on every run: an estimate over the cap is
  refused even with `--yes`.

  Needs `GEMINI_API_KEY`; if missing, walk the user through init-video's "The key file"
  and Phase 3c, never ask for the key.
- **Caption wrapping.** A caption too wide for one line wraps to two at a word break
  near the middle (jieba for Chinese, BudouX for Japanese, spaces elsewhere), never
  before closing punctuation. To choose the break yourself, put a newline in the text
  (`text: "你也是每年\n夏天都曬傷嗎？"`); it is kept as written.
- **Two captions on one picture.** `captions: [{text, style, emphasis, start_sec, end_sec}, …]`
  instead of `caption` (which stays the one-caption shorthand). Use it when a scene carries
  two beats; splitting the scene instead restarts its ken_burns and the picture jumps.
- **End card (this skill's extension).** The last scene can be `kind: card` with no
  `asset_file`: brand.json's accent fills the frame, with the logo, a sign-off line and a CTA
  button. Set the text once in brand.json, and override per plan on the scene:

  ```yaml
  # brand.json
  "card": {"signoff": "…the brand's sign-off line…", "cta": "LINE 立即預約", "cta_color": "#06C755"}
  # edit_plan.yaml
  - {scene: 10, start_sec: 25.2, end_sec: 27.2, kind: card, transition_in: fade,
     caption: {text: "…", style: cta}}
  ```

  `bg` overrides the accent. A logo the same colour as the card is drawn in white, and so
  are the caption's emphasis words; the top-centre logo overlay is hidden during the card.
- **SNS pacing.** An organic Reels / TikTok / Shorts post (not a paid ad) gets
  `pace: sns` at the plan's top level; ads leave it out and keep today's rhythm. Under it:
  the hook is done by 3 s; shots in the first 5 s run 0.5–1.5 s, then vary — alternate
  short and long rather than one flat length; stills default to `zoom_in_punch`, with
  `ken_burns` only for a deliberately slow beat; `pan_fast_l` / `pan_fast_r` whip across a
  still in 0.4 s (a wide shot, a list, a reveal), `shake` jolts it once (an impact, a
  punchline). These work on footage too, and outside `pace: sns`. `pace: sns` also turns on
  `sync: {snap: beat}`: each unvoiced scene's end moves onto a beat of the bgm (never
  under 0.5 s); voiced scenes keep the length their VO gives them and only shift. Set
  `sync: {snap: bar}` for slower cuts, or `snap: beat` on an ad to sync it too. No bgm or
  no beat in it renders unsnapped with a warning.
- **Jump cuts and speed.** `cuts: [{in, out}, …]` on a clip scene replaces
  `source_in_sec`: the source ranges play back to back in one scene — trim the ums and
  pauses out of a talking-head take without changing the picture's framing. They should add
  up to the scene's length (shorter holds the last frame, longer is trimmed; both warn).
  `speed: 2` plays the scene's source twice as fast (0.5–4, sound pitch kept); the scene's
  length stays as planned and it uses `length × speed` seconds of source. Use speed for
  b-roll and process shots, not for anyone speaking to camera.
- **Music.** Before writing `bgm`, ask: "Do you already have a BGM track, or should I
  pick one from OpenTracks?" Never pick for them, and never fetch music from anywhere else.
  Their own file: put it in `<project>/assets/bgm/`. OpenTracks (opentracks.com, free
  Japanese library, commercial use and ads allowed, no credit needed): suggest two or three
  mood / tempo tags that fit the plan (明るい, 速い, ポップ…), the user previews and downloads
  with the track page's download button — no scripted downloads, the terms forbid bots and
  direct file links. Put it in `assets/bgm/` and add a line to `assets/bgm/SOURCES.txt`:
  file, track page URL, composer, date. A composer's own terms override the site's; never
  register the cut with Content ID. Then `bgm: {mood, asset_file}` points at it.
- **Sound effects.** `sfx` on a scene is one file played at the scene's start, or a
  list `[{file, at}]` (`at`: seconds into the scene) for several — a whoosh on the cut, a pop
  on a caption, a ding on the price. Paths are relative to the material folder like every
  `asset_file` (init-video keeps a project set in `assets/sfx/`, reached as
  `../../assets/sfx/whoosh.wav` from `input/<video>/`). A plan path may leave the material
  folder only into the project it is rendered from; anything else is refused. Keep them sparse: one or two per scene at most.
  With no set, fetch the CC0 one (no key): `uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/sfx.py"
  <project>/assets/sfx` gives `whoosh`, `pop`, `ding` and `click`; a specific sound is
  `--freesound "<words>" --as <name>` (CC0 only, init-video Phase 3d's key). Each file is
  recorded in `SOURCES.txt` beside it. No other source: never fetch effects from elsewhere.
- **Caption entrances.** `fx` on a caption: `fade` (default, 0.15 s), `type` (one
  character per 16th note of the bgm, 0.1 s without one; done by 60% of the caption's time —
  for a line the viewer should read along with), `pop` (the whole caption lands 1.3× → 1×
  over 5 frames — a punchline or a hook). Under `pace: sns` a `hook` caption pops unless
  set otherwise. Pick one per caption; most captions should stay `fade`.
- **Anything else:** write `edit_plan.yaml` yourself in that schema, in the
  material folder from Phase 0, with `asset_file` paths relative to it. Choose
  `transition_in`, `animation` and caption `style`/`emphasis` the way
  production.md step 5 describes.

## Phase 5 — Checkpoint

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/render_plan.py" <edit_plan.yaml> <folder> --picks
```

Writes `<edit_plan>.picks.jpg`, one 9:16 frame per scene from where its cut
starts. Read it, then show it with the picks table (scene, file, in-point,
reason). Wait for the user to approve or swap; apply swaps to the plan and
re-run `--picks`.

**`--auto`:** still write and show the sheet and table, then go straight to
Phase 6 without waiting. A gap from Phase 3 still stops you.

## Phase 6 — Render

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/render_plan.py" <edit_plan.yaml> <folder>
```

`--out PATH` to name it; by default it lands where the Phase 0 table says, as
`<content_id>_v<n>.mp4` (`<video>_v<n>.mp4` in an init-video project), n the next free number, so a revision never overwrites
the cut that was reviewed. Alongside it: `.srt`, `.qc.json`, `.sheet.jpg`. A
missing timeline asset fails before anything renders.

`--ratio 4x5` or `--ratio 1x1` renders a feed cut from the same plan (named
`<content_id>_4x5_v<n>.mp4`): the footage is cover-cropped to the new canvas and
captions use feed margins, as feed has no Reels UI over the frame. Framing chosen in
Phase 3 for 9:16 shows more at the sides in 4:5, so check the sheet again. When
brand.json has a `logo` (per language, else `default`, relative to brand.json),
it sits top-centre inside the safe area for the whole cut except an end card. Music ducks about 12 dB
under any speech or clip sound and comes back up in the gaps.

Read `sheet.jpg` as an image and `qc.json` before showing the user anything:

- every caption sits over a frame that supports it, and is legible against it;
- `safe_area_ok` is true, `loudness_lufs` is about −14;
- `retimed` lists each voiced scene's planned vs. voiced length, and each snapped scene's planned vs. `snapped`; report any that moved by more than a second;
- `voiceover_cuts` (cut-list VO): every scene and caption boundary should match a piece's
  `at`/`end`;
- when the plan has `voiceover.cuts` or `voiceover.lines`, run
  `render_plan.py <plan> <folder> --check <out>.mp4`. It transcribes the render (one
  Scribe call; the transcript goes to `.check/`) and writes `speech` into its qc.json:
  `flags` with render times are stray, clipped or dropped words, so fix the cut and
  re-render; `ignored` are differences judged to be Scribe mishearing, listed so you can
  overrule. Blind spot: a word doubled across a join (…要 | 要去…) is usually transcribed
  once, so listen to joins where a cut ends on the word the next one starts with;
- every entry in `warnings` is reported, with what was substituted;
- `generated` lists the AI-generated scenes: name them to the user, and say the ad needs
  the platform's AI-content disclosure (Meta's "AI info" label) when it runs.

Fix the plan and re-render; it is cheap. In ACOS, then enter `draft_review` as
production.md's Path C describes.

## Phase 7 — Deliver

Push and record the render the way video-style-clone Phase 7 does (Drive for
Desktop copy or user upload, then `search_files` for the link). Report what you
picked and every trade-off: held frames, cropped-out subjects, scenes matched on
picture only, gaps filled with second-best material or generated stills and clips.

## What it does not do yet

No logo bar or info panel — that template chrome is video-style-clone's job; here the
logo is a plain overlay or an end card. No dissolves (`fade` dips through black). Voiced lines are
not retimed to picture: a scene follows its narration, never the reverse. Scenes over a
cut-list voiceover are timed by hand from `voiceover_cuts`, not bound to its words, and
room tone is a range you choose, not found automatically. One TTS voice per run.
