# Next milestone: rational constant speed, then stabilization

Design only, inspected against the current bilingual/LUT editor on 2026-10-15.
No speed or stabilization implementation, native validation, or delivery is
claimed by this document. Keep the two features independently testable.

## What changes together

- `lumen/model.py`: `Clip`, `Project.validate/from_dict/to_dict`,
  `Editor.split_clip/trim_clip/move_clip/update_clip` currently assume that one
  timeline frame consumes one project-frame unit of source time.
- `lumen/render.py`: `_graded_segment` trims and re-zeroes source timestamps;
  `_compile` separately trims original audio using `Media.audio_offset`.
  `preview` and `frame` already reuse the export compiler. Preserve that design.
- `lumen/color_media.py:grade_segment` pipes selected RGB48 frames through the
  owned float grader. Its input filter chain, cache identity and exact output
  frame count must reflect the new mapping; color math need not change.
- `lumen/ui.py:_build_inspector/refresh_inspector/apply_timing` currently read and
  write source-in with an integer spin box. Do not round a fractional source-in
  merely because the inspector was refreshed or another timing field changed.
- `lumen/ui_captions.py:segments_to_captions/transcribe_selected` currently map
  source ASR cues at 1x. `caption_render.caption_filters`,
  `overlay_render.evaluate_overlay/animation_frame`, and generated speech have
  different existing anchors, described below.

## Exact model and schema 7

Use integer project frames for timeline positions and durations, and rational
*project-frame units* for source positions. Source units are not native source
frame indices. All editing arithmetic uses `fractions.Fraction`; convert to
codec timestamps only at the render boundary.

Recommended immutable `ClipTimeMap` has three reduced positive-denominator
pairs: source origin O, exclusive source endpoint E, and speed R. In JSON use
explicit `source_in_num/den`, `source_out_num/den`, and `speed_num/den` inside a
`time_map` object. Source numerators are nonnegative; E > O; speed is positive.
Do not retain a second authoritative legacy `source_in` field in schema 7.
A distinct endpoint is intentional: it preserves the entire selected source
span when the new timeline duration is not an integer number of frames.

For clip-local output frame k and rational project rate F:

- source position: `O + k*R`; source seconds: `(O + k*R)/F`
- output duration: `D = ceil((E-O)/R)`, at least one frame
- selected source interval: `[O, E)`
- the last output frame may have a partial source interval. Its picture holds
  through the remainder of that output frame; audio is padded with silence.
  This does not authorize reading source samples beyond E.
- equivalent duration invariant: `(D-1)*R < E-O <= D*R`.

Keep `Clip.duration` as the validated integer output duration, derived by editing
commands, because existing timeline drawing, overlaps, fades and gates use it.
This redundancy is checked, never silently repaired on load.

Bound the first release to `1/8 <= R <= 8`, reduced speed numerator/denominator
at most 1,000,000, source denominator at most 1,000,000, and source numerator at
most 2^53-1. Reject bools, floats, zero/negative denominators, oversized integers,
noncanonical serialized pairs, NaN and infinities. Parse UI decimals as decimal
strings, then reduce; reject unrepresentable edits rather than approximating.
Repeated operations that exceed a bound fail atomically. Check practical render
length/cache budgets separately; arithmetic bounds are not resource budgets.

Load versions 1–6 as `O=old source_in`, `E=old source_in+old duration`, `R=1`.
Their existing integer duration, color, effects, captions, speech provenance and
paths remain unchanged. Accept 1–7 explicitly when bumping `VERSION`; the current
version tuple would otherwise omit version 6. New saves write version 7; old
readers should reject it, not silently play altered timing at 1x. Reject legacy
and new timing fields together. Add explicit pair serialization instead of
assuming `asdict`/JSON can encode `Fraction`. Audit both render fingerprints.

### Shared API, preferably `lumen/timing.py`

Pure bounded helpers, independent of Qt and FFmpeg:

- `validate_time_map(mapping)` and canonical constructors
- `source_position(mapping, local_frame) -> Fraction`
- `source_interval(mapping) -> (Fraction, Fraction)`
- `source_to_local(mapping, source_position) -> Fraction`
- `duration_frames(mapping) -> int`
- `seconds_at_source(mapping, local_frame, project_fps) -> Fraction`
- `round_half_up(Fraction) -> int` and `sample_boundary(frame, fps, rate)`
- `atempo_factors(rate) -> tuple[Fraction, ...]`

Validate against the **exclusive endpoint**, not `O+D` or only the first/last
sample: `0 <= O < E <= Project.media_frames(media)`. Retain the current
conservative floor for ordinary media. Generated speech retains its existing
ceil-to-frame source extent, with only that pre-existing last partial frame
represented by silence; speed must not amplify that allowance into arbitrary
extra source frames. Re-probe and repeat bounds checks in `_compile`, including
when a source is replaced by a shorter file. Describe this as compatibility
with the current duration policy, not exact knowledge of native packet ends.

## Editing semantics

- Speed edit: preserve O and E, derive new D with ceil, preserve timeline start,
  clamp fades to D, and validate the entire transaction. No implicit ripple.
  If the longer clip overlaps another item on its lane, reject with a clear
  error; do not move unrelated clips. Show the resulting duration before apply.
- Split at local integer k: left `[O,O+kR)`, right `[O+kR,E)`; right timeline
  start advances by k, durations are k and D-k. Preserve the rational boundary
  exactly. Existing fade policy remains: no new inner fade, clamp outer fades.
- Source trim: accept an exact O/E pair and derive D. A timeline-duration edit
  explicitly chooses `E=O+D*R` and therefore consumes a possibly different
  amount of source. Validate it before committing; never silently clamp to EOF.
- Left-edge trim by k timeline frames advances O by kR; moving a whole clip
  changes start/track only. Explicit absolute source trim and edge trim should
  be separate commands, not overloaded ambiguous integer arguments.
- Route timing through these commands rather than permitting inconsistent
  `update_clip` assignments. Undo/redo restores the complete map and D together.

## One video mapping and one audio mapping

### Video

`_graded_segment` owns source-time selection; `_compile` must not apply speed a
second time. Keep the existing path for 1x clips with integral full-frame bounds
so legacy frame-selection behavior remains unchanged. For other maps, retain
source timestamp phase: subtract the **requested exact source origin**, not the
first frame that survives trim. The latter changes pictures at fractional splits.

A bounded first implementation may decode from the normalized source origin,
retain the predecessor context needed by frame sampling, translate timestamps
by `-O/F`, divide by R, then apply the rational project `fps` filter with explicit
rounding/start policy and cap output at exactly D frames. Exclude source picture
samples at/after E; extend the last selected picture only into its permitted
partial final output frame. Do not use early source trim plus a fresh STARTPTS
as a substitute for phase preservation. Set a sufficiently precise time base
before retiming; avoid quantizing fractional requests to coarse container ticks.

The acceptance oracle is split-versus-unsplit frame identity on lossless source
frame-ID fixtures, including non-source-frame-aligned O and cuts. If a proposed
FFmpeg chain cannot meet that oracle, introduce an explicit deterministic
sampler; do not label the mapping frame-exact based only on a setpts expression.
VFR/mixed-rate handling remains deterministic frame selection, without optical
flow or invented intermediate frames. Bound decoder output to D and verify
frame count in `grade_segment`; avoid decoding the unused tail after selection.

Include the canonical map, frame-selection algorithm version and partial-tail
policy in `_graded_segment` and preview fingerprints. The final geometry, owned
float color/LUT evaluator, clip effects, compositing, stickers and captions retain
their current order. Preview and export use the same temporal graph and differ
only in the existing delivery codec settings.

### Audio

Let `a=O/F`, `b=E/F`, `L=D/F`, and `A=Media.audio_offset` relative to video origin.
Trim original audio to `[max(0,a-A), b-A)` when that interval is nonempty.
Reset trimmed audio timestamps, normalize sample format/rate, and apply
pitch-preserving `atempo` factors whose product is R. The leading output silence
is `max(0,A-a)/R`, not the current unscaled offset. Then pad/trim to the exact
output sample budget, apply gain and clip-local fades in output-frame units,
and place the clip at its timeline start. An absent audio interval stays silent.

Each atempo factor must be in `[0.5,2]`; repeated 2 or 1/2 factors plus one
residual cover the proposed speed range. Bypass the chain at 1x. Current FFmpeg
accepts larger factors, but documents sample skipping above 2, so do not pass
8 directly. Runtime capability checks and real audio smoke tests must be added
to `scripts/provision_ffmpeg.py` and its tests. Missing support is an error, not a
silent sample-rate/pitch-changing fallback. See [FFmpeg atempo documentation](https://ffmpeg.org/ffmpeg-filters.html#atempo).

Use rational global sample boundaries: `B(n)=round_half_up(n*48000/F)`, start at
B(start), and emit `B(start+D)-B(start)` samples. This avoids accumulated rounding
drift at 30000/1001 or 24000/1001; timeline silence and final trim share that
budget. Account explicitly for tempo-filter startup/tail behavior and AAC priming
in tests. At tempo changes, transient shape is not sample-identical to original
PCM; do not promise lossless audio or phase-continuous results across independent
atempo instances. Adjacent contiguous same-rate spans can share a tempo pass to
avoid unnecessary split-boundary transients.

## Anchors: do not silently retime unrelated content

- Existing captions/SRT remain absolute timeline-frame items. Clip speed, split,
  trim or move does not alter them. Display this alongside speed controls.
- New ASR on a selected retimed clip intersects source intervals with `[O,E)`,
  applies `(source-O)/R`, then quantizes both endpoints once with half-up rounding
  and clamps to `[0,D]`. Preserve shared boundaries; discard/report collapsed
  cues and reject overlaps. Do not round to source frames before dividing by R.
  Media-bin ASR stays 1x. Keep the current stale-revision rejection.
- Existing generated-speech media and `SpeechProvenance` remain immutable. Its
  recorded synthesis speed is separate from clip playback speed. A deliberate
  speech-clip speed edit uses the audio map, preserves its selected full source
  interval and pads its final partial output frame. Caption edits never change
  the take. TTS audition playback remains separate.
- Stickers remain independent absolute timeline items. Their keyframes and
  animation cadence are overlay-relative project frames, unaffected by clip
  retiming. `evaluate_overlay` and `animation_frame` should not change.
- Current grades and clip-effect amounts are static; there are no clip parameter
  keyframes to migrate. Future clip keyframes must declare source-anchored or
  clip-local-output-anchored timing before implementation. Do not infer links
  from proximity or IDs. Stabilization transforms will be source-anchored.

## Acceptance gates

Extend `tests/test_model.py`, `test_render.py`, `test_asr.py`, `test_ai_ui.py`,
`test_speech_model.py`, `test_speech_render.py`, and `test_overlay_render.py`, or
add focused `test_timing.py`, `test_speed_render.py`, and `test_speed_ui.py`.

1. Version 1–6 migration/roundtrip and 1x decoded picture/PCM regressions; strict
   rejection of bad pairs, duplicate timing representations and overflow.
2. At 24, 30, 30000/1001 and 24000/1001 fps, test rates 1/8, 1/2, 2/3, 1, 3/2,
   2 and 8. Split repeatedly at odd frames; source endpoints must be exactly
   adjacent, spans conserved, total output frame count unchanged. Example:
   O=5, E=50, R=3/2, D=30; split k=7 gives boundary 31/2 and lengths 7/23.
3. A non-divisible endpoint case preserves all selected source content:
   O=0, E=31, R=2 gives D=16, with only a half-frame source interval on the last
   output frame. Check audio tail and declared picture hold; no over-read.
4. Trim both edges, move lanes, same-lane overlap failure, source-bound failure,
   undo/redo, reopen, fractional inspector refresh and applying unrelated fields
   preserve exact maps. Failed edits leave history and project unchanged.
5. Frame-ID source at 24/60 fps and VFR, nonzero PTS, fractional source origin,
   and positive/negative audio offsets: compare split/unsplit picture selection,
   clip start/end gates, gaps, and both preview/export against the same oracle.
6. 440 Hz/1 kHz tones retain pitch (set a numeric FFT tolerance), known click/flash
   markers retain timing (target within one output frame, no drift), plus silent,
   muted, audio-only, embedded audio, gain and output-time fades. A long rational-
   rate fixture checks markers near start/middle/end and exact decoded PCM budget
   before AAC; separately verify container duration/priming tolerance after AAC.
7. Full generated-speech last sample survives speed changes; provenance/hash
   checks still run on preview hits. ASR cue boundaries use inverse mapping;
   pre-existing captions, sticker keyframes and TTS audition results are unchanged.
8. Changed speed/endpoint invalidates caches; source replacement revalidates
   bounds; cancellation/timeout reap processes and remove partial files. Preserve
   output/input/alias protection and local `file,pipe` protocol restrictions.
9. Full existing `python -m pytest` plus real Windows x64/macOS arm64 frozen-app
   edit/save/reopen/preview/export checks. Existing native results do not validate
   this proposed feature. Add bilingual labels/errors and their coverage tests.

## Stabilization: subsequent real two-pass milestone

Use an explicit enabled stabilization object, not another generic blur amount.
Require actual analysis with `vidstabdetect` and application with
`vidstabtransform`, or a separately measured owned implementation. FFmpeg's
filters require libvidstab; availability on this Linux host was observed, but the
pinned Windows/macOS packages must each be tested before exposing the feature.
See [FFmpeg stabilization documentation](https://ffmpeg.org/ffmpeg-filters.html#vidstabdetect).

Analyze source-anchored frames before speed sampling and before creative grading,
using identical orientation/SAR, pixel geometry, sample sequence and time base
for analysis and application. Use bounded source windows with smoothing handles;
record their exact origins/frame identities. Adjacent splits must not restart
motion analysis or shift transform indices. VFR handling must be specified,
never assume a transform row indexes arbitrary project frames.

Cache analysis by content identity, source window/handles, geometry, algorithm
and FFmpeg/libvidstab versions, and analysis settings. Exclude clip position,
creative grade and playback rate if the analysis sequence truly stays unchanged.
Keep apply settings (smoothing, interpolation, crop/zoom policy) in the rendered
segment key. A reusable transform must be verified against its source, published
atomically, bounded in size, and invalidated on relink/content change. Store
settings/provenance in the project; treat cached analysis as regenerable data.

Expose the loss of field of view: explicit static crop/zoom or visible border
policy, maximum zoom, and smoothing window in a defined time domain. Do not
silently upscale without limit or claim recovery of motion blur, rolling shutter,
parallax, tracking or optical-flow quality. Preserve SDR precision and color tags
through any new intermediate; libvidstab pixel-format negotiation needs a real
high-bit-depth regression before release.

Acceptance: synthetic known camera translation/rotation plus a static baseline;
measure reduced residual motion and crop bound, check split continuity, settings
invalidation, cancellation/cleanup, bypass identity, unchanged audio, and identical
preview/export transform use. Do not show a functioning stabilization toggle
until analysis and application both succeed on supported native builds.
