# Linked A/V real-media oracle

`tests/test_linked_av_pcm.py` covers the narrow same-source, zero-relative-offset
A/V relation and joint source-slip. It does not establish general NLE linked
editing, external recorder synchronization, J/L cuts, joint trim/split, or
phase continuity across independent retime instances.

## Inputs and independent expectations

- Synthetic FFV1 RGB16 source pictures encode frame IDs with eight binary bars
  and a three-channel ramp. Decoding the input is first checked against the
  original arrays, not an edited render.
- Stereo `pcm_f32le` source samples contain distinct source-frame-coded values
  plus independent impulses. Muxed positive and negative stream offsets are
  probed; the complete decoded source audio equals the original samples.
- The expected slipped map is built from the **pre-edit** clip's exact source
  span and speed plus the requested new source-in. It is never read back from
  the result of `slip_linked_av`.
- For 1x audio, an independent rational/sample-index oracle selects raw samples,
  inserts leading silence, and pads the exact absolute timeline sample budget.
  Every f32 sample must match exactly. Rates include 24, 24000/1001, 30000/1001,
  and 60000/1001; both signed source offsets and partial tails are covered.
- Retimed audio uses the accepted fixed Rubber Band path through the existing
  frozen schema40 mixer, then an independent scalar envelope/fade/placement/sum
  oracle. This intentionally tests existing retime semantics rather than
  reimplementing Rubber Band or claiming retimed PCM is lossless. The scalar
  comparison allows at most one ULP for the established libm gain calculation,
  never a timing, resampling, or AAC tolerance.
- Selected RGB16 segments match `floor(new_origin + k * speed)` against original
  source arrays. Full 8-bit scene output separately checks frame IDs and the
  established 6/255 color-patch bound; it is not represented as RGB16-lossless.

## Invariants checked

Link and Unlink leave real rendered RGB and the complete pre-encoder float32
mix byte-identical. Both audible and muted A members are covered; later
unmuting A does not restore disabled V audio. A cancellation-sensitive
three-contribution fixture detects historical mix-slot changes that can erase
all tiny samples even though source selections still match.

Joint slip must actually change marked source audio and numbered pictures.
The expected new map preserves the rational span, speed, and partial tail.
Whole-project comparison checks every other field, including local and hidden
audio-gain handles, visual motion/window handles, fades, anchors and unrelated
clips. Both 1x and retimed partial-tail slips exercise save/reopen and one-step
undo/redo with actual PCM; the retimed cases also recheck the original/new
numbered RGB16 source frames after undo, redo and reopen. The full-render
pre-encoder audio path is compared separately.

The suite hashes every synthetic source and checks its size and modification
time before and after edits/rendering. No extraction or modified source file
is permitted. Temporary output scenes are removed, and the test-owned
synthetic directory is cleaned at teardown.

## Focused invocation

Use the existing configured environment, with bytecode/cache output disabled
and a temporary directory outside the source tree, for example:

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen \
  ../setup-venv/bin/python -m pytest -q tests/test_linked_av_pcm.py \
  --basetemp=/tmp/cutcat-linked-av-oracle-pytest -p no:cacheprovider
```

This focused oracle is not a substitute for the aggregate suite, freshness
transaction tests, independent UI review, or native platform builds.
