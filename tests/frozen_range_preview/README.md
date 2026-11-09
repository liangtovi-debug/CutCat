# Accepted full-render oracle for range playback

`accepted-lumen-python.zip` contains only the 217 top-level `lumen/*.py` source
files from private accepted commit `f91f80ac101ea8fed59fa44497a019faa375f50f`,
tree `0034e800278d018bf3ea935b47edcd244102f272`. It contains no user media, assets,
credentials, Git metadata or third-party dependencies. The deterministic ZIP and
each member are SHA-256 pinned in `manifest.json`; the oracle script separately
pins the archive hash in source. Do not regenerate these pins from candidate code.

The full original schema43 renderer runs in a fresh Python process whose
application path is this ZIP. It writes **all** final pre-encoding YUV420 frames
and the full 48 kHz stereo float32 mix. Only the calling test slices the completed
reference buffers. Every loaded `lumen` module must originate in this archive.
This avoids circular expected values and makes a fresh source extraction usable
without an external historical checkout. Synthetic files and fonts are supplied
by each test and the existing pinned CI fixture; their licenses stay unchanged.

The twelve scene/rate cases (six variants × 24/29.97 fps) are kept in three
two-variant pytest modules so the whole-module shard runner does not receive
one approximately 15-minute indivisible module. Each case keeps all four
nonzero/edge windows. Separate tests cover legacy non-project-rate/VFR ingress,
44.1 kHz resampling, production 2/4-thread main-picture motion, global scalar
flags, source/tail isolation, empty picture/audio participation and omission
sensitivity. Splitting test files does not reduce the case matrix or alter CI.
