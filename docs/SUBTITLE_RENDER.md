# Subtitle burn-in

Captions are burned into the same FFmpeg graph used by timeline playback,
still-frame previews, and MP4/MOV export. They are composited after clip grading,
so the Before/After grade toggle does not hide or grade the captions. A caption
past the media end extends the project and appears over black with silent audio.

## Timing and literal text

Each cue uses drawtext's zero-based frame counter with
`gte(n,start)*lt(n,start+duration)`. Cue ends are exclusive. Timing is not converted
to ASS centiseconds: one-frame cues work at 30, 30000/1001, and 60000/1001 fps.
UTF-8 text is placed in an engine-owned local file and rendered with
`expansion=none`. Braces, percent expressions, backslashes, apostrophes, and actual
line breaks remain literal. ASS, HTML, and karaoke markup are not interpreted.

Font size, fill, outline, and bottom margin use project-resolution pixels.
Multiline blocks are centered as a block, with explicit line breaks; there is no
automatic line wrapping, per-line alignment control, or safe-area fitting. Long
or very large captions may clip. This is a single, non-overlapping subtitle track.

## Local fonts and language coverage

Install a TTF/OTF/TTC/OTC font locally and choose its exact family name. The optional
font directory is searched first, then standard system/user font directories.
Linux fontconfig is used only as a local lookup optimization. No fonts or resources
are downloaded. The default family is DejaVu Sans; install it or choose a locally
available family on systems where it is absent.

FontTools inspects local family names and Unicode cmap coverage before rendering.
Missing families and missing visible code points cause a useful error, rather than
silently substituting a font or accepting tofu boxes. A selected face from a font
collection is extracted into the private render cache, preventing FFmpeg from
accidentally using collection face zero. Font stat identity participates in the
preview cache key so replacing a font refreshes playback.

For Chinese, choose an installed CJK family such as Noto Sans CJK SC. Tests in the
Linux development environment use the already installed Noto Sans CJK SC font;
no font download or redistributed font is involved. Tests first check coverage,
then decode actual Chinese rendered pixels. These tests do not establish every
language, shaping system, emoji sequence, or platform's font behavior. Complex
shaping remains dependent on the user's FFmpeg/freetype build. Bundling or sharing
fonts must respect their licenses; project files reference a family/directory and
do not embed or distribute font data.

## Requirements and verification

Requires FFmpeg built with the drawtext filter and freetype support, plus the
pinned fonttools runtime dependency. All video inputs retain the `file,pipe`
protocol allowlist and absolute local paths. Caption text is never inserted into
the filter graph. Captioned graphs use a local filter script to avoid OS command
argument limits on long subtitle timelines. Engine-owned font/text resources are deleted when the render
engine closes. Rendering an uncaptioned project follows the existing graph.

Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_caption_render.py -q`.
The suite checks decoded frame boundaries in export and cached playback, project
save/reopen, PNG frame preview, caption-only tails, grade bypass, literal text,
local Chinese glyph coverage, missing-font/glyph failures, font-replacement cache
invalidation, and special-character paths. Latin tests select installed DejaVu Sans, Arial, or Helvetica. Chinese integration
selects installed Noto Sans CJK SC, Microsoft YaHei, or PingFang SC, and is explicitly
skipped when none is installed; it is not treated as proof of Chinese support in that environment.
