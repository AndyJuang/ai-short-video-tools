# Storyboard → edit plan

`scripts/storyboard.py` turns a storyboard table into `edit_plan.yaml`. It reads one
shape, so every way a storyboard can arrive ends in that shape first.

## The table

```markdown
フォーマット：縦型 9:16            ← optional header; a ratio here goes to export_checklist
| カット | タイムコード | ショット | カメラムーブ | 映像・シーン説明 | テキスト案／ナレ案 |
|---|---|---|---|---|---|
| 01 | 0–1.7s | Wide | Static | 早晨的廚房，桌上一個空碗 | テキスト：「你也這樣嗎？」／ナレ：「每天早上都好趕」 |
| 02 | 1.7–3.3s | MCU | Push-in | 手打開產品 | 只要三分鐘 |
| 09 | 13.3–15.0s | Wide | Final hold | ブランドロゴ + CTA | 立即預約 |
```

Columns are read by position; header words and language don't matter. A row starts with
the cut number (`01`, `C1`). Timecodes are `a–b` seconds (`-`, `~` and `〜` work too).
A cut count and length other than 9 × 1.7 s are fine.

- **Camera move** → `animation`: Static / Final hold → `none`; Push-in, Pull-back, Pan,
  Tilt, Tracking → `ken_burns`; Zoom-in → `zoom_in_punch`. Anything else → `none`, warned.
  On footage this is a starting point: a clip that already moves wants `none`.
- **Memo** → caption. Labelled parts split: `テキスト／字幕／字卡／Text:` is the caption,
  `ナレ／旁白／口白／VO:` is a `voiceover.lines` entry for `tts.py`. Unlabelled is all caption.
  A forced line break inside a caption is `<br>` (or `\n`) in the cell; otherwise the
  renderer wraps long captions itself.
- **Last cut naming a logo or CTA** → `kind: card` with the memo as its `cta` caption
  (brand.json `card` fills the rest).
- **Picture** → the scene's `brief`: what Phase 3 picks footage for.

## Where the storyboard comes from

| It arrived as | Do |
|---|---|
| video-storyboard's output (table in the chat or a file) | Save the table as `storyboard.md` in the material folder, as is. If its GPT Image prompt block is there, save it in the same file: its panel lines become the `generate` prompts. |
| A table in a doc, sheet, PDF or slides | Copy it into the shape above, one row per cut, in `storyboard.md`. |
| Storyboard frames as images | Read each frame; write one row per frame (picture = what the frame shows, memo = its text). Timecodes from the frames, else split the stated length evenly. |
| Only a brief, no storyboard | Draft the table yourself in the shape above, from the brief and brand.json (`voice`, palette), and show it before converting. Hook done by 3 s; last cut logo + CTA; picture column filmable or generatable, no on-screen text in it (text belongs in the memo). If video-storyboard is installed, offer it for a 9Types-structured ad instead. |

## Convert

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/storyboard.py" <folder>/storyboard.md            # footage to pick
uv run "${CLAUDE_PLUGIN_ROOT}/skills/video-edit/scripts/storyboard.py" <folder>/storyboard.md --generate # every cut a generated still
```

It never overwrites an existing `edit_plan.yaml` (`--out` to write a variation beside it).

- **Footage:** each non-card scene has `asset_file: null`; Phase 2–3 fill `asset_file` and
  `source_in_sec` from the scene's `brief`. `render_plan.py` refuses to render while any is null.
- **`--generate`:** each scene gets `gen/NN.jpg` and `generate: {brief, prompt?}`. Panel lines
  written for a storyboard *sheet* are a start, not a finished prompt: rewrite each to Phase 4's
  still rules (9:16 photographic, the named absences, no text, no product, no real people) and
  add `cast` for recurring people. Then the price step as usual — nothing is generated here.
  Only if the client allows AI imagery; mixing (footage for the product, generated for mood) is
  normal: drop `generate` from the scenes you pick footage for.

Then Phase 4 onwards as for any plan: `tts.py` for the narration lines, a `bgm`, `pace: sns`
for an organic post (storyboards for paid ads leave it off).
