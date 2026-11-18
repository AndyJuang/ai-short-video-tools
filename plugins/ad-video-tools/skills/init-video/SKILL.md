---
name: init-video
description: >
  Set up a video-ad project before any editing happens: scaffold the project folder
  (input/, output/, assets/, git, a .gitignore that keeps media out), write brand.json
  (palette, per-language logos, font overrides, tone rules), place the logo and font
  files, and verify the environment so a broken font or a logo with a baked-in
  background surfaces now instead of six phases later. Also sets up the optional API keys
  video-edit uses: ElevenLabs (transcription, narration) and Gemini (generated stills and clips).
  Trigger on "set up a video project", "init video", "new client for video ads", "define
  the brand guide for video", "add our logos and fonts", "set up the ElevenLabs / Gemini key", "影片專案設定", "動画案件の初期設定". Not for measuring a
  reference, writing copy or building a cut — that is video-style-clone, or video-edit
  for a cut from a plan or storyboard.
argument-hint: "[project folder] [notes: brand guide path, site URL, languages...]"
---

Arguments: `$ARGUMENTS` — a leading path is the project folder: `cd` there (create it only if the user asked) before Phase 0. The rest is context for the interview.

# Setting up a video-ad project

One project folder = one client = one `brand.json` = one git repo. Every video the client
ever gets is a directory under `input/`; `video-style-clone` then reads the brand and needs
no per-client edits anywhere.

The point of doing this first is timing. A logo whose transparency is a baked white
rectangle, or a Hangul face that is not installed, fails at render time — after the
reference is measured, the copy is written and the shot list is picked. Both are visible in
thirty seconds here.

## Phase 0 — Where

The commands in this plugin's skills are bash. They run as written on macOS and Linux, and
on Windows in the Git Bash shell Claude Code uses there; keep the quotes around
`${CLAUDE_PLUGIN_ROOT}`, since a Windows home folder often has a space in it. The
scripts run under `uv run`, which installs each one's dependencies on every OS.
`check_env.py` prints which OS it found and the install command for anything
missing on that OS.

Never scaffold inside a plugin or marketplace repo (this one included). If cwd is inside
either, stop and ask the user to create and `cd` into a client folder.

If cwd already holds a `brand.json`, offer to update it instead of re-scaffolding, and read
it before asking anything the file already answers.

```bash
mkdir -p input output assets
touch input/.gitkeep output/.gitkeep assets/.gitkeep   # empty dirs survive a clone
cp "${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/assets/brand.example.json" ./brand.json
cp "${CLAUDE_PLUGIN_ROOT}/skills/init-video/assets/project.gitignore" ./.gitignore
[ -e .env ] || { cp "${CLAUDE_PLUGIN_ROOT}/skills/init-video/assets/project.env" ./.env; chmod 600 .env; }
git init -q 2>/dev/null
uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py" --trust
```

`--trust` adds this folder to `~/.ad-video-tools/projects.txt`, the list of projects whose `.env` the
scripts will read keys from. A repo someone else made is never on it, so its `.env` is
ignored even if it looks like ours. A project set up before this list existed needs
`--trust` run once from its root; until then the scripts print that they're ignoring its `.env`.

`.env` holds the optional API keys as `{Placeholder}` lines. The user fills them in
later, in Phases 3b, 3c and 3d. It comes after `.gitignore`, which ignores it, so the scaffold
commit can't pick it up. An existing `.env` is never overwritten.

| Path | Holds | Git |
|---|---|---|
| `brand.json` | palette, logos, font overrides, tone rules — the whole client | tracked |
| `.env` | this project's API keys, `{Placeholder}` until filled in | ignored |
| `input/<video>/` | one directory per video: its materials **and** its `video.json` | params tracked, media ignored |
| `input/<video>/materials.json` | what each material was: duration, size, hash, Drive link | tracked |
| `output/<video>.<lang>.mp4` | the renders, named after the input directory | ignored |
| `assets/` | logo PNGs (tracked) and music beds (ignored) | mixed |
| `build/` | render scratch, one dir per video per language | ignored |
| `templates/` | layouts measured for *this* client, if any | tracked |

The split is the whole design: git holds every decision — the edit, the copy deck, the
material index — and not one byte of video. A clone of the repo is a complete record of
what was made and from what, and re-renders as soon as the material is pulled back from
Drive.

Logo paths in `brand.json` resolve relative to the project root, so `assets/logo.png` is a
valid value and keeps the root clean. Font overrides are filenames, looked up in the OS's
font folders, so a licensed brand face is normally installed (a project-relative path also
works — see below):

| OS | Install a font by | It lands in |
|---|---|---|
| macOS | double-click → Install, or copy the file | `~/Library/Fonts` |
| Windows | right-click → Install (not "Install for all users") | `%LOCALAPPDATA%\Microsoft\Windows\Fonts` |
| Linux | copy the file, then `fc-cache -f` | `~/.local/share/fonts` |

System font folders (`/Library/Fonts`, `C:\Windows\Fonts`, `/usr/share/fonts`) are
searched too. A path relative to the project root also works, for a face the client
licensed for this project only.

Commit the scaffold before the interview, so the brand answers land as their own commit:

```bash
git add -A && git commit -qm "scaffold video project"
```

Beyond this first commit, git is the user's. Commit when a video's params or its material
index changes and say so in one line; do not commit renders, and do not commit on every
build unless asked.

## Phase 1 — Interview

In the user's language, in two small batches. Skip anything they already said. Every key is
optional and falls back to the template default, so an unanswered question costs a
fallback, not a failure — say which fallback it took rather than inventing an answer.

**Brand**

- Palette: `accent`, `panel`, `ink`, `surface`, `white`, `muted` as hex. If the client
  supplied a brand guide PDF or a site, read the colours off it rather than asking for six
  hex codes. Ask which one is the primary button/pill colour — that is `accent`.
- Logos: one file per language plus a `default`. Ask whether a knocked-out (white) version
  exists; without one the bar has to stay white, and that is a layout decision, not a
  preference.
- Fonts: does the brand license its own faces, or do the bundled Noto faces stand in? Only
  ask per language that is actually in scope.
- Languages in scope now, and likely later. Cheap to list, expensive to retrofit.

**Voice** — this block is for whoever drafts the copy, not for the build script:

- `register`: person, tense, punctuation habits ("second person, present tense, no
  exclamation marks").
- `avoid`: claims the brand or its regulator bars — prices, medical claims, superlatives,
  competitor names. Treated as binding at copy time.
- `must_carry`: what every cut has to contain — brand name in the URL line, a legal line, a
  campaign hashtag.

Write the answers into `brand.json`, keeping the example's shape. Drop the `_comment` key
and any language block that is not in scope.

## Phase 2 — Place the files

Logos into `assets/`, brand fonts into the user font folder for the OS (table above),
music beds into `assets/`.

For SNS cuts, ask whether the client has a sound-effect set; if so, put it in `assets/sfx/`
with plain names (`whoosh.wav`, `pop.wav`, `ding.wav`). If not, fetch the CC0 starter set
(whoosh, pop, ding, click; public domain, no key):

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/sfx.py" assets/sfx
```

It never overwrites a file already there, and writes `assets/sfx/SOURCES.txt` (licence,
author, URL per file): keep it with the project. A specific sound (a knife, a pour) comes
from Freesound, CC0 only, with Phase 3d's key: `sfx.py assets/sfx --freesound "knife chop"
--as chop`. Pull effects from nowhere else: Pixabay's are not CC0 and can't be fetched by
script, so the user downloads those and adds the licence to `SOURCES.txt` by hand. Say which
starter sounds you placed, and that they should be listened to before a client cut.

If a font has to be fetched, ask first and say which one shipped — see the font section of
`${CLAUDE_PLUGIN_ROOT}/skills/video-style-clone/references/gotchas.md` for the Noto Sans KR
Black case, which no OS covers. The Noto CJK faces themselves are not installed by default
anywhere except some Linux desktops; `check_env.py` names the files it looked for.

## Phase 3 — Verify

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py"
```

It checks ffmpeg/ffprobe/Pillow; that the scaffold exists and git ignores media while still
tracking `video.json`, `materials.json` and `brand.json` (asked of `git check-ignore`, not
of the file's text, because a stray `*.json` line swallows the params); that `brand.json`
parses and names keys something reads; that each logo exists with real transparency and
enough height for the 120px bar; and that each language's heavy and body faces are
installed and can draw that language's script. Pass languages as arguments to check only
some, or `--ready` to check the scaffold alone (exit 2 means "not set up").

Reading the result:

| Result | Means |
|---|---|
| FAIL | the build will stop or render tofu. Fix before writing copy. |
| FAIL on .gitignore | media or `.env` (with its API keys) is about to be committed, or the params are invisible to git. Each defeats the point of the repo. |
| warn on alpha | the logo carries a baked background — it only sits right over chrome of that same colour. Ask for a transparent export, or plan the bar around it. |
| warn on a missing palette key | the template default ships instead. Fine, but say so. |
| warn on the Korean heavy face | the system's bold Hangul face (SD Gothic Bold on macOS, Malgun Gothic Bold on Windows) stands in and reads lighter than the rest of the campaign. |
| `--` on ELEVENLABS_API_KEY / GEMINI_API_KEY | optional, not a problem. Mention both in the report as "optional, not set up" and offer Phase 3b (speech) and 3c (generated stills). |

Report the failures and the fallbacks in one table. A silent fallback is the thing this
whole phase exists to prevent.

## The key file (used by Phases 3b, 3c and 3d)

Never ask for a key. Don't ask the user to paste it, don't read it back, and don't
`cat`, `grep` or echo the file. The user opens it and types the key in. You
only show the steps and run the `--doctor` check afterwards. Show the steps for the
user's OS only (you know the platform), one step at a time.

**Where the key goes.** The project's own `.env`, at the project root: the folder this
skill set up, beside `brand.json` and `.git`. The scaffold's `.gitignore` already ignores
it. Each client's work then bills that client's key. The scripts read only that one file:
the `.env` in the nearest folder at or above the working directory that holds `.git`, so
run them from inside the project. A `.env` anywhere else, such as inside `input/` among
client files, is ignored on purpose. So is the `.env` of a project that isn't on the trusted list
(Phase 0's `--trust`), and a `.env` that git tracks: a committed one came with someone else's repo, not from the user. On macOS and Linux the file must also be private to the user (`chmod 600`, which the scaffold sets): a world-readable `.env` usually came in a download, so the scripts skip it and print the `chmod` to run if it is the user's own. Only when there's
no project does the key go in the global `~/.ad-video-tools/.env`. That covers `/video-edit` on
a loose folder, and a project outside the home folder, for example on an external drive. A key missing from the project's `.env` falls back to an environment
variable of the same name, then to the global file; `check_env.py` and `--doctor` print where each key came from, so say it
whenever a project is running on the global key.

The scaffold already wrote `.env` with one line per key, the value a `{Placeholder}`. The
user replaces the whole `{…}`, braces included, with the key. A line still holding its
placeholder counts as not set, so an unused service needs nothing. Keep the format
`NAME=value`, with no spaces around `=` and no quotes. A second project of the same
client gets its own `.env` from its own scaffold; the user can copy the keys across.

Open it from the project root:

| OS | Command | Then |
|---|---|---|
| macOS | `open -e .env` | TextEdit opens it. Save with ⌘S and close. |
| Linux | `nano .env` | Save and exit with Ctrl+O, Enter, Ctrl+X. |
| Windows (PowerShell) | `notepad .env` | Save with Ctrl+S. Don't use "Save as": it would append `.txt` and the keys would not be found. |

If the project has no `.env`, copy the template in the way Phase 0 does, then open it as
above.

**No project (global file):** copy the same template to `~/.ad-video-tools/.env`. On Windows
the path is `$env:USERPROFILE\.ad-video-tools\.env`. Create the folder first: `mkdir -p
~/.ad-video-tools`, or `New-Item -ItemType Directory -Force "$env:USERPROFILE\.ad-video-tools"` on
Windows. On macOS and Linux, `chmod 600` the file. Then open it as above.

Before the user types anything into a project `.env`, run `git check-ignore .env` from
the project root. An init-video project passes. Any other repo, an ACOS workspace for
example, may not; then add `.env` to its `.gitignore` first.

**1Password:** if the team keeps the key there, the line can be a reference instead,
`NAME=op://<vault>/<item>/<field>`. This needs the `op` CLI, signed in.

## Phase 3b — Speech (only for `/video-edit`)

`/video-edit` transcribes spoken footage (ElevenLabs Scribe) and voices a plan's narration
(ElevenLabs text-to-speech). Neither is needed for `/video-style-clone`. Ask once whether
this client will get talking-head cuts or narrated cuts. If not, skip this phase and say it
can be done later. If `check_env.py` already found the key for this project, skip
straight to step 4.

Walk the user through it one step at a time and wait for each:

1. **Create the key.** elevenlabs.io → Developers → API Keys → Create. Enable
   **Speech to Text** and **Text to Speech**. Voices → Read is optional; without it nothing
   can list voices, so voice IDs have to be copied from the website.
2. **Store it.** Follow "The key file" above: on the `ELEVENLABS_API_KEY=` line, replace
   the `{…}` with the key (it starts `sk_`).
3. **Check it.**

   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/transcribe.py" --doctor
   ```

   This checks that the key is found and accepted. A speech-to-text-only key passes.
4. **Pick a narrator per language** in scope, and write it to `brand.json`:
   `"narrator": {"tc": "<voice_id>", "jp": "<voice_id>"}`. Then check it:

   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/tts.py" --doctor --lang tc
   ```

   This voices 「測試」 once, two characters of credit. ElevenLabs' error names the fix:

   | Error | Fix |
   |---|---|
   | 401 `missing_permissions` | The key lacks Text to Speech. Edit the key (step 1). |
   | 402 `paid_plan_required` | Free plans can't use Voice Library voices. Upgrade, or use a default voice. |
   | 404 `voice_not_found` | The ID is wrong or that default voice was retired. Copy it again from the site. |

   **The free plan only reaches the default voices, and they are English natives.** In a
   live test George (`JBFqnCBsd6RMkjVDRZzb`) read 補擦 so that Scribe heard 不擦. That is
   fine for checking timing, but not for client delivery. Native Mandarin or Japanese
   narration needs a paid plan (a Voice Library voice) or a cloned voice. Say this before
   the user picks; it is a budget question, not a setting.

`check_env.py` reports where it found the key, offline. Speech is optional, so it is never a FAIL; it warns only when `brand.json`
names a narrator but no key exists.

## Phase 3c — Generated stills and clips (only for `/video-edit`)

When a plan needs a shot nobody filmed, `/video-edit` can generate a 9:16 still with
Gemini (`gemini-3.1-flash-image`, about US$0.10 an image or revision) or a short Veo clip
(Veo 3.1 Lite, about US$0.05 a second, US$0.20–0.40 a clip). There is no free tier;
nothing is generated until the user approves the estimate. Ask once whether the client allows AI-generated imagery in its ads at
all; if not, or not yet, skip this phase. Like the speech key, it goes in the project's
`.env`.

1. **Create the key.** aistudio.google.com → Get API key → Create. Image output needs
   billing enabled on that Google Cloud project.
2. **Store it.** Follow "The key file" above: on the `GEMINI_API_KEY=` line, replace the
   `{…}` with the key.
3. **Check it.** Free — it looks the model up, it does not generate:

   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/generate.py" --doctor
   ```

`check_env.py` reports whether the key is found; never a FAIL.

## Phase 3d — Freesound (only when a cut needs a specific sound effect)

The starter set (Phase 2) needs no key. A sound it doesn't cover comes from Freesound's CC0
sounds, which needs a free key. Skip this until a plan asks for such a sound.

1. **Create the key.** Sign in at freesound.org, then freesound.org/apiv2/apply → fill in
   the form → copy the **Client secret/Api key**.
2. **Store it.** Follow "The key file" above: on the `FREESOUND_API_KEY=` line, replace the
   `{…}` with the key.
3. **Check it.** Free — one search:

   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/sfx.py" --doctor
   ```

## Phase 4 — Hand off

Say what is set and what is still `TBD` (including speech and generated stills and clips, if Phase 3b or 3c was skipped or a narrator is missing), then point at `/video-style-clone` with what it
still needs: a name for the video, a reference (or the name of a saved template), the
footage or stills, and the target languages. If the user has a plan or storyboard and raw
footage rather than a reference ad, point at `/video-edit` instead: the plan and the
footage folder.
