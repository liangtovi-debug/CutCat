# Safe caption wrapping (local source milestone)

## Behavior

Caption burn-in now lays out long text against the output frame before generating
the shared caption layout used by preview and export. The H.264 preview/export
branch remains 8-bit; the ProRes 422 HQ branch burns existing basic captions
directly into 10-bit video only after a compatible FFmpeg passes capability
preflight. It does not reuse an 8-bit captioned preview as its export input.
See [high-bit caption boundaries](HIGH_BIT_CAPTIONS.zh-CN.md). Automatic line breaks exist only
in render-cache text files. Editable captions, ASR output, SRT exports, IDs and
frame timing are not rewritten. Existing manual LF breaks are respected.

Subtitle appearance includes a horizontal safe-margin percentage (default 5%,
range 0–20%) and a maximum line count (default 6, range 1–20). Apply appearance remains
one undoable edit. Font size, font selection, color and outline remain explicit;
the renderer never silently shrinks or truncates text. It rejects text that cannot
fit, with guidance to reduce text/font size/margins or increase the line limit.
A minimal outline allowance prevents a requested zero margin from clipping strokes.

Project schema version 10 stores the new settings. Versions 1–9 load with the new defaults without changing stored transcript text. Older v9 applications reject v10 explicitly rather than misreading the added style fields.
Long captions can consequently wrap differently from earlier versions; this is an
intentional rendering improvement, not a bit-identical old long-caption export
promise. Projects whose text already fits keep their explicit font and timing.

## Layout boundaries

The wrapper takes the larger of Pillow FreeType BASIC and RAQM-shaped advance/ink bounds and uses regex extended
grapheme clusters. It prefers word boundaries, allows long-word splitting only
between graphemes, and applies a bounded Chinese punctuation-break policy. All
source characters, including spaces, remain in the render text; inserted newlines
are the only automatic textual change. Tabs retain their source character and use
explicit four-space tab stops in rendering.

RAQM support is required; unavailable shaping fails with an actionable error rather than using a weaker fallback. Basic metrics alone were found to underestimate repeated accented letters and stacked combining marks. Font metrics and FFmpeg shaping are separate implementations. This slice targets
and measures Latin/CJK output with the verified local test fonts; it does not
claim arbitrary complex-script, color-emoji, variable-font or unusual-font shaping
parity. Missing visible glyphs still fail explicitly. Join controls and variation
selectors do not themselves require separate cmap glyphs. No fonts download in
the application and no substitute font is silently selected.

## Dependencies and platform status

Added pinned dependencies: Pillow 13.0.0 (MIT-CMU) and regex 2026.9.29
(Apache-2.0 AND CNRI-Python), from their official PyPI projects. No dependency
binaries or font files are included in the source ZIP.
- https://pypi.org/project/Pillow/
- https://pypi.org/project/regex/

Validation is local Linux source/Qt-offscreen/FFmpeg only. Windows/macOS frozen
packages were not rebuilt and cannot be claimed to include this change. Native
font/layout compatibility remains a later acceptance gate. No GitHub push, paid
build or cloud inference occurred.

## Validation

Terminal tests and actual-render evidence are recorded in the milestone evidence
archive. Existing caption rendering/frame-boundary tests and new layout/model/UI
checks must pass; pixel bounds are measured from actual decoded rendered output,
not inferred only from the wrapping function.

### Actual rendered acceptance

Eight fixed synthetic-text scenarios passed: English and Chinese in 640×360 and
360×640, manual breaks, a long word with combining marks, near-boundary repeated
accented letters, and stacked accents. Each retained the exact project/SRT text.
Actual decoded preview/export ink stayed inside the requested margins; maximum
whole-frame mean absolute difference was 0.221/255 (foreground 2.470/255), with
separate encoder presets. This is content comparison, not byte-identical encoding.
Two actual Qt offscreen editor screenshots also verify decoded playback. Overflow
was refused before publishing output. The evidence report retains source/font
hashes and raw frame bounds. These synthetic cases do not establish universal
font or complex-script compatibility.

Reproduce with an installed, glyph-complete local font and FFmpeg:

```sh
QT_QPA_PLATFORM=offscreen python scripts/verify_caption_layout.py --help
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_caption_layout*.py
```

The verified Linux font is the installed Noto Sans CJK SC face; its selected
face is extracted to a local rendering cache. Font extraction on this machine
can take roughly 28 seconds for a fresh cache. No font file is distributed here.
