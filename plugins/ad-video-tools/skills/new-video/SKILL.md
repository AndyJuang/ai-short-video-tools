---
name: new-video
description: >
  Start one new video inside a video-ad project set up by init-video: name it, create
  input/<video>/, decide how it gets edited (from a plan with video-edit, or by copying a
  reference video's layout with video-style-clone), take in the plan or reference, copy or
  download the footage, stills, sound effects and music into the right folders, index them,
  write a tracked brief.md, commit, then hand off to the editing skill. Trigger on "new
  video", "add a video", "start a new video for this client", "set up the folder for the
  spring campaign video", "新影片", "開一支新影片", "新しい動画を追加". Not for setting up
  the client folder itself (init-video) or for cutting and rendering (video-edit,
  video-style-clone, which this hands off to).
argument-hint: "[video name] [plan file | reference video] [asset paths or links]"
---

Arguments: `$ARGUMENTS`. Anything given there is an answer already: don't ask it again.

# Starting a new video

One video = one directory under `input/`. This skill makes that directory and fills it the
way the editing skills expect, so that `video-edit` or `video-style-clone` starts at
"pick footage", not at "where are the files". It asks one question at a time.

## Phase 0 — Is this a project?

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py" --ready
```

Exit 2: not an init-video project. Run the `init-video` skill first, then come back.

Projects set up before this skill existed ignore `brief.md`, `storyboard.md` and the
`SOURCES.txt` licence files. Ask git which:

```bash
for p in input/x/brief.md input/x/storyboard.md assets/bgm/SOURCES.txt assets/sfx/SOURCES.txt; do
  git -c core.fsmonitor=false check-ignore -q "$p" && echo "swallowed: $p"
done
```

Add only the missing ones: `!input/**/brief.md` and `!input/**/storyboard.md` under the
`materials.json` line; for `bgm` or `sfx`, three lines after the `assets/*` exceptions:
`!assets/<dir>/`, `assets/<dir>/*`, `!assets/<dir>/SOURCES.txt`. Leave any other change
to `.gitignore` alone, and tell the user you changed `.gitignore` and why.

## Phase 1 — Name

Ask what the video is called. The answer becomes the directory, the output filename and
what everyone calls it, so turn it into a slug: lowercase ASCII, words joined with `-`,
the campaign or content rather than `video1` (`spring-sale`, `noodle-hook-15s`). Romanize
or translate a Chinese or Japanese name and keep the original as the brief's title. Show
the slug before creating anything.

If `input/<slug>/` already exists, stop: that is another cut of an existing video, not a
new one. Offer the editing skill on it instead (it reads `video.json` or `edit_plan.yaml`).

```bash
mkdir -p input/<slug>
```

## Phase 2 — How it gets edited

Ask: "Edit from a plan, or copy the layout of an existing video?"

| Answer | Skill | What it needs from this skill |
|---|---|---|
| From a plan: a script, storyboard, shot list or 企劃 the user has | `video-edit` | the plan file |
| From a plan they don't have yet | `video-edit` | the brief (Phase 3); video-edit drafts the storyboard from it |
| Like an existing video: a reference ad, a past cut | `video-style-clone` | the reference video, or a saved template |

For a saved template, list `${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/templates/*.json`
and any `templates/` folder in the project, by name and ratio. A known template needs no
reference video.

## Phase 3 — Brief

Ask what to make, defaulting rather than interrogating:

- **Ratios**: 9:16 unless they say feed (4:5) or square (1:1). Several are fine.
- **Languages**: the ones in `brand.json`; ask only if it lists more than one.
- **Length**: 15 s for a short ad unless the plan or reference says otherwise.

For "a plan they don't have yet", also ask, in one message: the product or offer, who
it's for, the one thing a viewer should remember, the call to action, and anything every
cut must show. Don't draft the storyboard here: video-edit does that from this brief, in
its own format, and shows it before converting.

Write `input/<slug>/brief.md`:

```markdown
# <title as the user said it>

- skill: video-edit | video-style-clone
- plan: <file name in this folder> | to draft from this brief
- reference: ref.mp4 | template <name>          (video-style-clone only)
- ratios: 9:16, 4:5
- languages: tc
- length: 15 s

## Brief
<product, audience, message, CTA, must-show — only when there is no plan>
```

## Phase 4 — Plan or reference

- **Plan file**: copy it into `input/<slug>/` under its own name. A storyboard table also
  goes into `storyboard.md` in that folder, as is (video-edit's `references/storyboard.md`
  explains the shape; don't reshape it here).
- **Reference video**: copy it to `input/<slug>/ref.<ext>` (keep the extension).

Both arrive the way assets do (Phase 5): copied, downloaded, gated.

## Phase 5 — Assets

Ask for three kinds, each optional, as paths, Drive links or URLs. They don't all go to the
same place:

| Kind | Goes to | Shared? |
|---|---|---|
| Footage and stills | `input/<slug>/` | this video only |
| Sound effects | `assets/sfx/`, plain names (`whoosh.wav`) | every video in the project |
| Music | `assets/bgm/` | every video in the project |

Getting each file in:

- **Local path**: copy, never move. The original is often in a synced Drive folder that
  other people use. `cp -n` (never overwrite); a name already taken gets `-2` before the
  extension, and say so.
- **Google Drive link**: if the file is in a Drive for Desktop folder on this machine,
  copy it from there. Otherwise download it with the Google Drive MCP
  (`download_file_content`). Keep the link: it goes into `materials.json` (Phase 6).
- **Other URL**: `curl -fL -o <dest> <url>`. A page instead of a file (a share page, a
  YouTube link) is not a file: ask for a direct download or a local copy instead.
- **A folder**: copy the media files in it, flat. Skip anything that isn't video, audio
  or an image.

Every media file goes through the same gate the renderers use before anything else reads
it. A file that fails is deleted, and the user hears why:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/ffsafe.py" <file> ...
```

Sound effects and music each get one tab-separated line in their folder's `SOURCES.txt`:
`file`, licence, author, source URL. A client-supplied file is
`<file>\tclient-supplied\t<client>\t<link or "local">`. Ask about the licence of anything
else rather than guessing it.

- **No music**: say video-edit or video-style-clone will offer OpenTracks tracks as links
  for them to download. Don't fetch music from anywhere.
- **No sound effects, and the cut is for SNS**: offer the CC0 starter set
  (`uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/sfx.py" assets/sfx`); it writes
  its own `SOURCES.txt` lines.

## Phase 6 — Record and commit

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/scripts/index_materials.py" <slug>
```

Then fill in `source` in `input/<slug>/materials.json` for every file that came from a Drive
link or URL: it is the only trace git keeps of footage it never holds. The script never
overwrites `source`, so this survives re-indexing.

Commit the text, not the media:

```bash
git -c core.fsmonitor=false add .gitignore input/<slug> assets
git -c core.fsmonitor=false commit -m "new video: <slug>"
```

`git add` picks up only what `.gitignore` lets through: `brief.md`, `materials.json`,
`storyboard.md` and the `SOURCES.txt` files (and any new logo in `assets/`). Run
`git status --short` before committing, and stop if a media file shows up.

## Phase 7 — Hand off

Report in a short table: the slug, the skill, and each file with where it went. Then run
the editing skill on it right away, saying which:

- `video-edit` with the plan in `input/<slug>/`, or with `brief.md` when there is no plan.
- `video-style-clone` with `<slug>`.

Both read `brief.md` and skip what it already answers.
