# Edit plan — the format render_plan.py reads

One YAML file per video. `/video-edit` writes it in Phase 4; `render_plan.py` renders it.
The format comes from ACOS (see the README); this is the subset the local ffmpeg
render (Path C) uses, plus this plugin's extensions.

```yaml
content_id: 2026-07_summer-sale_v1   # names the output: <content_id>_v<n>.mp4
total_duration_sec: 28
render:
  mode: ffmpeg
timeline:
  - scene: 1
    start_sec: 0.0
    end_sec: 3.0
    asset_file: "clip01.mp4"      # relative to the material folder; a still works too
    source_in_sec: 1.5            # optional: in-point in the source
    beat: "hook"                  # optional, advisory: hook | fact | info_card | turn | payoff | cta
    caption:
      text: "Burned every summer?"
      style: "hook"               # hook | body | cta | info_card (bold_white_outline = body)
      emphasis: ["Burned"]        # optional: drawn larger in the brand accent colour
      start_sec: 0.5
      end_sec: 3.0
    transition_in: "hard_cut"     # hard_cut | fade
    animation: "zoom_in_punch"    # zoom_in_punch | ken_burns | pan_fast_l | pan_fast_r | shake | none
    sfx: "whoosh.wav"             # a file in the material folder, or "none"
  - scene: 2
    start_sec: 3.0
    end_sec: 8.0
    asset_file: "broll.mov"
    caption: {text: "...", style: "body", start_sec: 3.5, end_sec: 7.5}
    transition_in: "fade"
    animation: "none"
    sfx: "none"
bgm:                              # optional: omit for a VO-only or ambience-only mix
  mood: "upbeat_summer"
  asset_file: "music.mp3"
voiceover:                        # optional: only when narration is a separate track
  asset_file: "voiceover.mp3"
  lines:                          # optional: per-line narration, voiced by tts.py
    - id: "s01a"
      scene: 1
      text: "Burned every summer?"
      asset_file: "vo/s01a.wav"   # optional; default vo/<id>.mp3
```

`render`, `bgm`, `voiceover`, `source_in_sec`, `beat`, `caption.emphasis` and
`voiceover.lines` are optional. An unknown `caption.style` or `animation` takes a
fallback and is listed in the QC report's `warnings`. A missing `asset_file` fails
before anything renders.

With `voiceover.lines`, run `tts.py` on the plan first. Each voiced scene then lasts as
long as its lines, so those scenes' durations in the timeline are estimates; QC's
`retimed` lists what moved. Without lines, `voiceover.asset_file` is mixed in as
recorded, so time the scenes to it yourself.

Caption styling and export settings are not plan fields: they live in
`shorts-output-spec.md` and brand.json.

The extensions this plugin adds on top (`cues`, `captions: [...]`, `kind: card`,
`voiceover.cuts`, `pace: sns`, `sync`, caption `fx`, `generate`) are described in
SKILL.md Phase 4 and at the top of `render_plan.py`.
