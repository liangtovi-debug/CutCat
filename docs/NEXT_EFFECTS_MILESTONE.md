# Proposed next local milestone: effects and stickers

This is a bounded plan, not implemented capability. Existing color, captions,
ASR and TTS stay covered by regression tests. No external asset library is needed.

## Deliverable

- Timed overlay objects with independent frame start/duration, source identity,
  z-order, visibility, and undo/redo/save/reopen.
- Static PNG stickers plus explicit local PNG-frame animation packs. Begin with
  a few original procedural assets whose source generator is included. Avoid
  third-party art, trademarks, remote asset catalogs and silent downloads.
- Position, uniform scale and opacity keyframes with linear interpolation at
  integer project frames. No tracking, motion estimation or arbitrary plugins.
- Two genuine ordered effects: Gaussian blur and vignette, with numeric controls,
  per-effect bypass and undo. Parameter meanings follow the actual operator.
- One compilation path for preview and export. Named order: clip correction and
  clip effects, track composite, stickers, then captions. Preserve audio unchanged.
- Simple Add sticker / Effects inspector with advanced keyframes disclosed only
  when requested. Visible order and supported controls, no placeholder panels.

## Safety and acceptance

Local asset manifests must use contained relative paths, reject symlinks/path
traversal, bound dimensions/frame count/total decoded size and fail clearly for
missing or malformed assets. Avoid archive extraction in the first version.
Cache identity includes asset hashes and all animation/effect parameters.

Test exact start/end frame gates at 24/30/30000/1001 fps, alpha compositing,
keyframe interpolation, effect order/bypass, animation looping, save/reopen,
undo/redo, missing/tampered assets, cancellation and output protection. Export
real original static + animated sticker demos and compare decoded preview/export
pixels with codec tolerance. Retain every existing audio, color, caption, ASR and
TTS test. Native Windows/macOS still require their own execution evidence.

## Explicit boundaries

This establishes an extensible local overlay/effect model, not CapCut asset or
feature parity. No asset marketplace, generative-video model, automatic tracking,
complex masks, speed ramp, GPU real-time compositor or commercial pack licensing
is implied. Original generated asset source and attribution should travel with
the source ZIP. Model and repository publication decisions remain separate.
