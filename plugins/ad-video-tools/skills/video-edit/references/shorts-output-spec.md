# Vertical shorts — output spec (normative, shared)

The delivery constants every render path must agree on (Path C, the local ffmpeg
render, is the one in this plugin; see the README for ACOS paths). **Do not restate
these numbers in a SKILL.md** — point at this file instead.

## Frame and container

| Item | Value |
|---|---|
| Resolution / aspect | 1080×1920, 9:16 |
| Frame rate | 30 fps, constant |
| Video codec | H.264, MP4, `yuv420p` |
| Pixel aspect | square — always `setsar=1`; a non-square SAR shows as pillarboxing on import |
| Audio codec | AAC |

Bitrate/quality differs by purpose, not by path:

| Purpose | Setting |
|---|---|
| Draft for review (Path B default) | VBR 1-pass ≈10 Mbps, AAC 256 kbps — favour encode speed |
| Deliverable (Path C default) | crf 18, AAC 192 kbps |

## Loudness

| Item | Value |
|---|---|
| Integrated | **−14 LUFS** (short-form platform norm) |
| True peak | **−1.5 dBTP** |
| Where applied | master bus, final stage, after the mix is summed |

A vertical profile may override the true-peak target (e.g. a louder −1.0 dBTP
for feed ads). An override is legitimate but must be recorded in the render's
`warnings` so the deviation is visible at review. Integrated −14 LUFS is not
overridable.

## Safe areas

Coordinates are for a 1080×1920 frame; percentages are authoritative if a
platform changes its UI.

| Zone | Value | Reason |
|---|---|---|
| Bottom UI zone | y > 1632 (**85% H**) | caption/CTA/handle overlay — **hard limit, no key text ever** |
| Right UI rail | x > 930 (right **150px**) | like/comment/share column on TikTok and Reels |
| Left / right text margin | ≥ 60px | also applies at peak scale of any pop-in or zoom animation |
| Top margin | ≥ 220px | platform header and profile chrome |

**Default caption anchor: caption block bottom edge at 78% H.** A vertical
profile may move the anchor down to at most 83% H. Nothing may cross the 85%
hard limit — including a caption's stroke, drop shadow, or its transient size
during a pop-in. Verify at peak scale, not at rest.

## Captions

| Item | Value |
|---|---|
| Font | Noto Sans CJK TC — **Black** for punchy short-form captions. Some Noto CJK TC distributions omit the Bold weight, so Black is the portable choice; resolve fonts by search path and fail with the name to install rather than silently substituting |
| Fill / stroke | `#FFFFFF` on a dark stroke; stroke width ≈ 11.5% of glyph size |
| Alignment | horizontally centred unless a profile says otherwise |
| Language | Traditional Chinese output must contain no kana and no Japanese-only kanji forms |

Per-vertical styling — accent colour, emphasis scale, background band, pop-in,
role presets — is a **profile** concern, not a spec concern. This file fixes
only what all paths must share.

## Subtitle sidecar

Every path emits an SRT alongside the video, cues matching the on-screen caption
timings one-for-one:

```
{n}
{HH:MM:SS,mmm} --> {HH:MM:SS,mmm}
{caption text}
```

Path B writes `exports/{content_id}.srt`; Path C writes it beside its output.
Editors reuse it for platform-side captions.

## Degradation rule

Shared with `premiere-mapping.md` and binding on every path: **rendering never
fails over vocabulary or over a missing optional asset.** An unknown enum value,
an absent SFX file, or an unsupported effect takes the documented fallback and
appends a `warnings` entry naming what was substituted. Only a missing *required*
asset fails a job.
