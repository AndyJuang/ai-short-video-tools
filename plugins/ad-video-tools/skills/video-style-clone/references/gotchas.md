# ffmpeg and font gotchas

Failures this workflow hits, and why. Check here before debugging an ffmpeg
error from scratch.

## ffmpeg

### A looped still image without `-t` produces an endless file

`-loop 1 -i overlay.png` never ends. Paired with `overlay`, the encode can keep
running long after the footage is exhausted, quietly writing gigabytes. The
symptom is an encode that "is just slow" while the output file grows past any
plausible size.

Put `-t <duration>` on every looped image input, and `-t <duration>` on the
output as a second guard. Cheap, and it makes the intended length explicit.

### `zoompan` x/y expressions use `zoom`, not `z`

```
zoompan=z='min(1.0+0.0009*on,1.10)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'
```

`z` sets the zoom but is not readable inside `x`/`y`. Writing `iw/z/2` fails with
`Undefined constant or missing '(' in 'z/2)'`. `on` is the output frame number,
which is what you want to drive the ramp.

### `zoompan` stair-steps unless you upscale first

zoompan crops on integer pixel boundaries, so a slow push-in on a source at its
native size jumps rather than glides. Scale 2x before it and let zoompan render
back down:

```
crop=1080:608:0:460,scale=2160:1216,zoompan=...:s=1080x608
```

### Scale cannot vary with time; overlay position can

There is no time-varying `scale`, so a caption cannot literally zoom in. An
overlay's `x`/`y` do accept `t`, so animate position and alpha instead:

```
[cap]fade=t=in:st=0:d=0.3:alpha=1[c];
[bg][c]overlay=x=0:y='40-40*min(t/0.45,1)'
```

A fade plus a short rise reads as a pop-in and costs nothing.

### `drawtext` is the wrong tool for styled captions

It cannot do a per-line font size, a second-line indent, a stroke *and* a shadow
together, or a size that changes over time. Render the caption in PIL and overlay
it as a PNG. You get full control and the result is reproducible.

### Build shots as intermediates, not one filter graph

Ten shots with crops, zooms and captions in a single `filter_complex` is
unreadable and undebuggable. One mp4 per shot, then concat, then a final pass for
chrome and audio. Caption timing becomes relative to its own shot. Intermediates
in a scratch directory are free.

### Keep the audio bed continuous

Taking audio from each shot's source in-point chops the ambience at every cut.
Pull one continuous stretch from the source instead, independent of the picture.
The final pass `loudnorm`s it to the fixed -14 LUFS output spec, whatever the
reference measured.

### Useful one-liners

```bash
# scene cuts, in seconds (pts_time in the showinfo lines)
ffmpeg -hide_banner -i in.mp4 -an -vf "select='gt(scene,0.25)',showinfo" -f null - 2>&1 | grep -o 'pts_time:[0-9.]*'

# contact sheet (build.py writes one per cut as build/<video>/<cut>/sheet.jpg)
ffmpeg -i in.mp4 -vf "fps=1,scale=250:-1,tile=6x5" -frames:v 1 sheet.jpg

# loudness
ffmpeg -hide_banner -i in.mp4 -af volumedetect -f null - 2>&1 | grep volume
```

## Fonts

### Two different failures wear the same name

"Wrong font for the language" is two problems, and only one of them is visible:

| Failure | Looks like | Caught by |
|---|---|---|
| Missing glyphs | tofu boxes, or gaps | the build script's preflight |
| Wrong variant shapes | plausible but subtly foreign characters | nothing — only your eye |

**Missing glyphs** happen with single-language faces. Hiragino Sans is Japanese;
point it at Traditional Chinese and it drops TC-only characters — 飯, 顯, 錄, 轉,
處 come out as boxes. A Latin face pointed at any CJK copy drops everything.

**Wrong shapes** happen with Noto Sans CJK, and are the sneakier one. The `tc`,
`sc`, `jp` and `kr` cuts are one pan-CJK glyph set with different *defaults* —
`NotoSansCJKtc-Black.otf` will happily render Hangul and Japanese kana with no
missing characters at all. Nothing errors. The kanji just have the wrong regional
shapes, and a native reader sees it immediately. Pick the cut by the copy's
language even though the wrong one "works".

### Detecting tofu without fontTools

fontTools is not always installed. Render the character and compare it to two
known-bad stamps — the `.notdef` box and a blank:

```python
def _stamp(face, ch, size=40):
    im = Image.new("L", (size * 2, size * 2), 0)
    ImageDraw.Draw(im).text((2, 2), ch, font=font(face, size), fill=255)
    return im.tobytes()

def missing_glyphs(face, text):
    tofu, blank = _stamp(face, "\ue000"), _stamp(face, " ")
    return [c for c in dict.fromkeys(text)
            if not c.isspace() and _stamp(face, c) in (tofu, blank)]
```

A missing glyph renders either as the box or as nothing, so both comparisons are
needed. Run it over the whole deck before encoding — a translated cut that ships
with tofu is invisible until someone watches the file.

### Faces by language

| Copy language | Heavy face | Notes |
|---|---|---|
| Japanese | `NotoSansCJKjp-Black.otf` or `ヒラギノ角ゴシック W8.ttc` | |
| Traditional Chinese | `NotoSansCJKtc-Black.otf` | |
| Simplified Chinese | `NotoSansCJKsc-Black.otf` | |
| Korean | `NotoSansKR-Black.otf` | **not installed by default** — see below |

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/skills/init-video/scripts/check_env.py"   # what each language resolves to, on this OS
```

`build.py` looks each face up by filename in the OS's font folders — on macOS
`~/Library/Fonts`, `/Library/Fonts`, `/System/Library/Fonts`; on Windows the
per-user `%LOCALAPPDATA%\Microsoft\Windows\Fonts` and then `C:\Windows\Fonts`;
on Linux `~/.local/share/fonts`, `~/.fonts`, `/usr/local/share/fonts`,
`/usr/share/fonts`. Linux distros package Noto CJK as one collection,
`NotoSansCJK-Black.ttc`, with the regions at indexes 0 jp, 1 ko, 2 sc, 3 tc; the
build takes that when the per-language OTF is not there.

### No OS ships a heavy Hangul face

The system Korean families top out at **Bold**: Apple SD Gothic Neo on macOS
(`AppleSDGothicNeo.ttc`, index 6 — there is no Black), Malgun Gothic on Windows
(`malgunbd.ttf`). Ad layouts built around a Black weight look limp in Bold, and
the difference is obvious next to a Chinese or Japanese cut of the same ad.

Noto Sans KR Black is a 5MB download under the SIL Open Font License,
`https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/KR/NotoSansKR-Black.otf`.
Into the user font folder:

```bash
URL=https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/KR/NotoSansKR-Black.otf
curl -fsSL -o ~/Library/Fonts/NotoSansKR-Black.otf "$URL"                                    # macOS
curl -fsSL -o "$LOCALAPPDATA/Microsoft/Windows/Fonts/NotoSansKR-Black.otf" "$URL"             # Windows (Git Bash)
mkdir -p ~/.local/share/fonts && curl -fsSL -o ~/.local/share/fonts/NotoSansKR-Black.otf "$URL" && fc-cache -f   # Linux
```

On Windows that makes the file visible to the build but not to other apps;
right-click → Install on the downloaded file registers it properly.

Ask before downloading it. The build script falls back to the system bold with a
warning when it is absent, so a missing font degrades rather than crashes — but
say which one shipped. On Linux there is no system fallback unless the Noto CJK
collection is installed (`fonts-noto-cjk` / `fonts-noto-cjk-extra` on Debian and
Ubuntu, `google-noto-sans-cjk-fonts` on Fedora).

The medium slot matters much less; SD Gothic Medium or Malgun Gothic next to Noto
Black is invisible at the 30px a URL line uses.

### Windows: text and paths

Two failures that only show up on Windows, both handled in the scripts — worth
knowing if you write a one-off next to them:

- Python's `open()` and subprocess pipes default to the ANSI code page (cp950,
  cp932), so a `video.json` with CJK copy fails to load or comes out garbled.
  Every script opens text as UTF-8 and switches its own output to UTF-8.
- ffmpeg reads `C:\...` badly in two places: the concat list (backslashes are
  escapes, so the list is written with forward slashes) and any filtergraph
  (`movie='C:\clip.mp4'` — the drive colon splits the option, so `measure.py`
  detects cuts through `-i` and `showinfo` instead).

### Common punctuation glyphs are missing from CJK faces

U+25B6 ▶ is absent from Hiragino W8 and most Noto CJK cuts, so a CTA reading
"Details ▶" renders with a tofu box. Draw the marker instead — an arrow is a
polygon, a chevron is two lines:

```python
d.polygon([(x, y - 15), (x, y + 15), (x + 21, y)], fill=colour)             # arrow
d.line([(x, y - 15), (x + 9, y), (x, y + 15)], fill=colour, width=6,
       joint="curve")                                                       # chevron
```

### Auto-fit the font size to a max width

Copy gets rewritten and translated constantly, and a size that fit the first
draft overflows the second:

```python
def fitted(draw, face, text, size, max_w):
    while size > 8 and draw.textlength(text, font=font(face, size)) > max_w:
        size -= 1
    return font(face, size)
```

This is what lets one layout serve every language — but watch the result. A
translation that fits only by dropping 30% of the size has not fitted, it has
shrunk; cut a word instead.

### Anchor headlines by glyph top, not text origin

`draw.text((x, y))` puts `y` at the top of the font's box, which sits well above
the glyphs and varies by face. Measured layouts record where the *ink* starts, so
subtract the bbox offset or a Korean line lands lower than the Chinese one it is
supposed to match:

```python
top = MEASURED_TOP - draw.textbbox((0, 0), line, font=f)[1]
```

### Trim logo artwork to its ink before scaling

Supplied logos carry uneven transparent padding, so centring the file centres the
padding rather than the mark:

```python
logo = Image.open(path).convert("RGBA")
logo = logo.crop(logo.getchannel("A").getbbox())
```

Also check the alpha actually exists — a "transparent" PNG with a white
background only looks right over white chrome, and will show a box elsewhere.

### A localized logo decides the bar colour and the URL line

Brands ship per-language lockups, and they are not interchangeable. A dark logo
cannot go on the brand-coloured bar the reference uses with its knocked-out white
one; keep the bar white and say so. The localized logo is also the authority on
the brand's descriptor in that language — take the URL line from it rather than
inventing a translation.

## Beat sync

### The first synced build is slow, once

`beats.py` declares librosa in its PEP 723 header and `build.py` runs it with `uv
run`, so the first synced build installs librosa and numba into uv's cache — tens
of seconds, with no output. Later builds reuse the env, and the grid itself is
cached per bed in `build/<video>/beats.json`. Without `uv` it runs under the same
Python as the build, which then needs librosa installed.

### librosa reads 60 seconds, from `music_in`

Analysis is slow on long beds and no ad runs past a minute, so only the window
the cut uses is decoded. A bed shorter than the cut loops in the mix, but the grid
does not loop with it. ffmpeg does the decoding, not librosa: soundfile cannot
open mp4, mov or m4a, which is what footage and most delivered tracks are.

### The tracker's beats lag the hits

`beat_track` returns a tempo grid that sits 20–50 ms behind the actual attacks,
enough to see on a hard cut. `beats.py` pulls each beat back onto the onset just
before it. It looks back, not to the nearest, because a hit's tail is an onset too.

### Every track has a tempo

The tracker returns a BPM for speech, ambience and a steady tone. If most of its
beats have no onset under them, `beats.py` returns an empty grid, and `--expr`
prints `0`, so that bed renders unsynced instead of cutting to invented beats.

## Slideshows

### Stills have no audio, so a slideshow is silent unless you give it a bed

The video path pulls a continuous ambience bed from the source. Images have
nothing. Either supply a music file or accept silence, but decide explicitly —
an ad that is silent by accident looks broken.

Loop a short bed to the full duration with `-stream_loop -1` before the input,
and keep the `-t` guard:

```bash
ffmpeg ... -stream_loop -1 -ss 0 -t "$TOTAL" -i music.m4a ...
```

When there is no bed, drop the audio map entirely rather than encoding silence.

### Cover-fit stills before zoompan, or they letterbox

A photo will not share the video window's aspect. Scale to cover, crop to the
window, and only then do the 2x upscale that keeps the push-in smooth:

```
scale=1080:1180:force_original_aspect_ratio=increase,crop=1080:1180,
scale=2160:2360,zoompan=...
```

`force_original_aspect_ratio=decrease` letterboxes instead — the opposite of what
an ad wants.

### A still with a ken-burns move is not a static slide

The same alternating push-in/pull-out the footage path uses gives stills enough
motion that hard cuts read fine, which means a slideshow needs no crossfade
machinery to look finished. Reach for `xfade` only if someone asks; it changes
the assembly from concat to a filter chain and is not free.
