# ad-video-tools

Rebuild an ad in an existing video's exact layout — with your footage, your copy,
your language, your brand.

```
/init-video <folder>          set the client up once — scaffold, git, brand, logos, fonts, CC0 sound effects, optional ElevenLabs/Gemini/Freesound keys
/new-video                    start each video — name it, take in the plan or reference, copy footage, sfx and music into place
/video-style-clone <folder>   then clone a reference and build
/video-edit <plan> <folder>   or cut a regular video from a plan, picking the footage
```

## What it does

Point it at a reference video ad and raw footage (or a folder of photos). It
measures the reference to the pixel — band boundaries, pill and button boxes,
row pitch, cut rhythm, loudness — then builds that layout with your material. The
result should sit next to the reference and look like it came out of the same
template.

| | |
|---|---|
| **One folder per client** | `input/<video>/` holds a video's materials and its `video.json`; the render mirrors the name at `output/<video>.<lang>.mp4`. Git tracks the decisions, never the media. |
| **Templates** | Measured layouts are saved as JSON and reused. Two ship with the plugin: `single-offer-9x16` and `feature-list-4x5`. Measure a new reference once and it joins them. |
| **Multilingual** | One edit, many cuts. `build.py spring-sale ko` swaps copy, type face and logo; timings, push-ins and audio stay identical. A preflight refuses to render text the face cannot draw. |
| **Brand guides** | `brand.json` carries palette, per-language logos, font overrides and tone rules. Swapping clients is one file, not a rewrite. |
| **Slideshows** | A folder of images instead of footage builds a slideshow — ken-burns moves, same chrome. Omit `shots` for one shot per still, or name the file per shot. Supply music or it ships silent, explicitly. |
| **Beat sync** | `sync` in `video.json` snaps cuts to the bed's beats or bars, steps the push-ins on the beat, and can flash or punch on each hit. `beats.py` finds the grid (librosa, via `uv`); a bed with no beat builds unsynced. `measure.py` reports the reference's BPM. |
| **Batch** | `--all` builds every language; `--ratio 9x16,4x5,1x1` retargets a template (bar and panel fixed, video window absorbs the difference); `--stale` rebuilds whatever is older than its params. Every cut gets a contact sheet. |
| **Delivery** | Renders go to the client's Drive folder; `index_materials.py <video> --render <cut> <url>` records the link and hash in `materials.json`, so the repo knows where each cut went. |

## Editing from a plan (`/video-edit`)

Hand it a plan — an ACOS `content_id`, a script, a storyboard, a client 企劃 — and a
folder of unlabelled footage and stills. `index_materials.py <folder> --sheets`
draws a timestamped contact sheet per clip; Claude reads every one, picks a file
and in-point per scene, writes an edit plan (`references/edit-plan.md`) and shows a picks sheet for approval (`--auto` skips the
wait; a scene with no fitting material always stops to ask). `render_plan.py`
then renders it full-frame 1080×1920: captions in the hook/body/cta/info_card
presets with emphasis in the accent colour, hard cuts or fades, punch-in or
ken-burns, BGM/VO/SFX mixed, two-pass loudnorm to −14 LUFS, plus an `.srt` and a
`.qc.json` (loudness, safe area, warnings). Spoken scenes are
picked and captioned from an ElevenLabs Scribe transcript. Original dialogue can be the
voice track: `voiceover.cuts` joins source ranges, snapped to word edges, into one track
with room tone in the pauses. `--check` transcribes the render and flags stray, clipped or
dropped words against the plan, cut by cut. Brand chrome is a logo overlay and an optional `kind: card`
end card drawn from brand.json; a scene can carry several captions.
`pace: sns` paces an organic post instead of an ad: unvoiced cuts snap to the BGM's
beat, stills punch in, hooks pop. Captions can type on or pop in (`fx`); stills can
whip-pan or shake; a scene can jump-cut between source ranges, change speed, and carry
several timed sound effects. `sfx.py` fetches a CC0 starter set (whoosh, pop, ding, click)
from a pinned public-domain repo, or a named CC0 sound from Freesound, and records each
file's licence and source in `SOURCES.txt`. BGM is the user's own track or, by default,
one they download from [OpenTracks](https://opentracks.com/) (free, commercial use, no
credit), logged in `assets/bgm/SOURCES.txt`.
A storyboard — video-storyboard's cut table, any other table, storyboard frames, or one
drafted from a brief — becomes an edit plan with `storyboard.py`: timings, captions,
narration lines and the end card, with footage left to pick or every cut a generated still.
A gap the user agrees to fill can be a Gemini-generated 9:16 still or a Veo clip,
text-to-video or animating an approved still (`generate.py`):
it prints an estimate from a cached price table and generates only with `--yes`, after
the price is approved. `edit:` revises the current image in place of a fresh one, and every
overwrite keeps the old file as `gen/N.vK.jpg`. A plan's `characters` block with `cast:` per
scene sends each person's reference image, so a recurring face stays the same person.

### ACOS, briefly

Some names in `/video-edit` come from **ACOS** (AI Content Operating System), applemint's
in-house short-video pipeline: research → plan → script and storyboard → edit → render →
performance review. It lives in a separate plugin, content-tools, which is not part of
this repo. You don't need it.

- **`content_id`**: ACOS's ID for one video (e.g. `2026-07_summer-sale_v1`). Here it only
  names the output file; any string works.
- **Edit plan**: the YAML timeline (scenes, captions, music, narration) that
  `render_plan.py` renders. ACOS defined the format; `references/edit-plan.md` documents it.
- **Path A / B / C**: ACOS's three ways to render an edit plan: by hand in CapCut (A), on a
  Premiere render queue (B), or locally with ffmpeg (C). This plugin is Path C. A and B
  are not included.
- **An ACOS workspace** (a `content/` folder) is one more place `/video-edit` can find a plan
  and footage. Without one, hand it any plan file and a folder.

## A project

```
brand.json                    palette, logos, faces, voice                [tracked]
.env                          API keys, {Placeholder} until filled in     [ignored]
templates/                    layouts measured for this client            [tracked]
input/spring-sale/
    video.json                template, shots, copy deck, captions        [tracked]
    materials.json            duration, size, hash, Drive link per file   [tracked]
    ref.mp4  clip.mov         the materials                               [ignored]
output/spring-sale.tc.mp4     the render                                  [ignored]
output/spring-sale.tc.1x1.mp4 a retargeted ratio                          [ignored]
build/spring-sale/tc/         scratch and sheet.jpg, the contact sheet    [ignored]
```

Media is too big for git and already lives in Drive, so `materials.json` is the
committed record of what was used — including the link, and including files
someone has since deleted locally. A clone of the repo is a complete account of
what was made and from what, and re-renders once the material is pulled back.

`/init-video` writes the `.gitignore` that draws that line and makes the first
commit; after that the repo is yours. The environment check asks `git check-ignore`
whether media is really excluded and the params really are not — a stray `*.json`
line silently defeats the whole arrangement.

## Requirements

macOS, Windows or Linux.

| | macOS | Windows | Linux |
|---|---|---|---|
| `ffmpeg` / `ffprobe` on PATH | `brew install ffmpeg-full` (found automatically; plain `ffmpeg` lacks HDR tone mapping and .jxl) | `winget install Gyan.FFmpeg` | `sudo apt install ffmpeg` |
| `uv` (installs each script's Python deps) | `brew install uv` | `winget install astral-sh.uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| user font folder | `~/Library/Fonts` | `%LOCALAPPDATA%\Microsoft\Windows\Fonts` | `~/.local/share/fonts` |

A CJK-capable heavy font is needed for CJK copy: the Noto Sans CJK Black/Medium set,
as per-language OTFs or (Linux) the `NotoSansCJK-*.ttc` collection. No OS ships a
heavy Hangul face, so Korean wants `NotoSansKR-Black.otf`; without it the build
falls back to the system bold (SD Gothic on macOS, Malgun Gothic on Windows) and
says so. `check_env.py` reports what it found and the install command, per OS.
On Windows, Claude Code runs the skills' commands in Git Bash.

## Layout

```
skills/init-video/
  SKILL.md                     scaffold, brand interview, verification
  assets/project.gitignore     the media/params line, copied into the project
  assets/project.env           the optional API keys as {Placeholder} lines, copied into the project
  scripts/check_env.py         ffmpeg, Pillow, scaffold, gitignore, logo alpha, font glyphs

skills/new-video/
  SKILL.md                     one new input/<video>/: name, edit path, brief.md, plan or reference, assets in, index, commit, hand off

skills/video-edit/
  SKILL.md                     plan → picks → edit plan → render, phase by phase
  scripts/transcribe.py        clip → words, caption-sized cues, srt (ElevenLabs Scribe, ELEVENLABS_API_KEY)
  scripts/tts.py               voiceover.lines → vo/<id>.mp3 (ElevenLabs TTS, brand.json narrator)
  scripts/generate.py          scene generate.prompt → gen/<scene>.jpg (Gemini) or .mp4 (Veo, audio stripped); estimate first, --yes to spend, --cap
  scripts/render_plan.py       edit plan → mp4 + srt + qc.json, voiced scenes timed to their lines; pace: sns, caption fx, sfx; --picks sheet; --check speech diff
  scripts/storyboard.py        storyboard table → edit_plan.yaml (timings, captions, narration lines, end card, picks or --generate)
  scripts/sfx.py               CC0 sound effects → assets/sfx/<name>.wav + SOURCES.txt (pinned starter set; Freesound with FREESOUND_API_KEY)
  references/edit-plan.md     the edit plan format render_plan.py reads
  references/storyboard.md     the table shape, and how a storyboard from anywhere (or none) gets into it
  references/shorts-output-spec.md   the shared delivery constants

skills/video-style-clone/
  SKILL.md                     the workflow, phase by phase
  templates/*.json             measured layouts
  assets/video.example.json    copy into input/<video>/ as video.json
  assets/brand.example.json    copied into the project as brand.json by init-video
  references/gotchas.md        ffmpeg and font failures, and why
  scripts/measure.py           the reference analyzer
  scripts/index_materials.py   writes input/<video>/materials.json
  scripts/beats.py             beat grid of a bed, for `sync` (librosa via uv)
  scripts/build.py             the builder — nothing in it is edited per job
  scripts/fixture.py           synthetic project behind `build.py --self-test`
```

`build.py` runs from the project root and takes the video's name:
`uv run .../scripts/build.py spring-sale ko`, or `spring-sale --all`,
`--ratio 4x5`, `--stale`. It finds `templates/` on its own — project-local
first, then the installed plugin. Override with `AD_VIDEO_TEMPLATES`.
`build.py --self-test` renders a generated footage cut in two languages and two
ratios, a generated slideshow, a beat-snapped slideshow over a click track, then a
stale rebuild, and checks size, length, audio, panel placement, that the snapped
cuts land on the clicks, and which outputs moved; run it after touching the
builder. `beats.py --self-test` checks the grid on clicks, silence and a drone.
`render_plan.py --self-test` renders a three-scene plan (talking clip, 60 fps
silent b-roll, still) and checks size, frame rate, real-speed playback, loudness,
safe area, SRT cues, warnings, and that a missing asset fails before rendering; then beat
snapping, typed and popped captions, motion presets, sfx lists, jump cuts and speed.
`storyboard.py --self-test` checks timecodes, memo splitting into caption and narration, the
camera map, the end card, generate blocks, that a table without timecodes is refused, and
that a planted edit_plan.yaml symlink is refused.
`sfx.py --self-test` checks that the starter set is pinned, peak-normalised and recorded,
that an existing file is kept, and that a non-CC0 Freesound result is refused.
`generate.py --self-test` checks that the estimate sends nothing, that unchanged prompts
are not re-billed, `--scene`, `--cap`, edits and versions, Veo's submit-poll-download and
audio stripping, re-filming when the animated still changes, and that a path outside the
asset folder is refused.

## Install

Claude Code:

```
/plugin marketplace add applemint-ltd/applemint-marketplace
/plugin install ad-video-tools@applemint-marketplace
```

Claude Desktop:

Download `ad-video-tools.plugin` and drop it into a Claude Desktop chat, then press
Install on the preview. To build the file from this repo: `plugins/ad-video-tools/package.sh` → `plugins/ad-video-tools/dist/ad-video-tools.plugin`.

## License

MIT — see [LICENSE](../../LICENSE).
