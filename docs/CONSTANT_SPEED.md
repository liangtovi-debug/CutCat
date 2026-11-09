# Constant-speed editing: exact timing foundation

This milestone adds one constant playback rate per clip, from 0.25× to 4×. It does not implement speed curves, reverse, optical flow, frame synthesis or stabilization. Existing caption, sticker and other voice clips keep their independent timeline anchors. Speed changes preserve the full selected source interval and start position, reject collisions rather than ripple unrelated material, and use normal undo/redo.

## Project representation

Schema 7 records reduced rational source origin, exclusive source endpoint and rate in `time_map`. Timeline duration is the ceiling of source span divided by rate; the final partial output picture frame may hold the last selected picture while audio is silence-padded to its exact output budget. Schema 1–6 projects migrate at 1×; the new format is intentionally rejected by older readers. Save a separate project copy when retaining compatibility with an older app.

Split and trim operations retain the exact fractional source phase. Bounds, denominator/numerator limits, overlap and source-length errors reject the entire edit without changing undo history. Generated speech provenance remains immutable: clip playback rate and original synthesis rate are separate concepts.

## Video selection

Ordinary integral 1× clips preserve the existing source trim/FPS behavior. Retimed or fractional-origin clips normalize the source from its timestamp origin to the project frame-rate grid, then select `floor(O + kR)` for output frame k. This is deterministic frame selection/duplication, not native-frame interpolation. The decoder streams through required source context with bounded image memory; long source offsets may require decoding earlier frames. The same owned float color/LUT, effects and compositing pipeline is reused by still preview, playback cache and export. Sampling algorithm identity and exact timing participate in cache keys.

Tests use original FFV1 RGB frame-ID fixtures, rational project rates 24000/1001 and 30000/1001, mixed native rates, verified VFR/nonzero timestamps, partial tails and repeated fractional splits. Lossless intermediates require exact pixel identity; H.264 delivery comparisons use explicit tolerances and frame IDs.

## Captions and transcription

Existing timeline captions are never silently stretched. New selected-clip transcription extracts only the chosen source interval at 16 kHz and maps source times through the inverse exact rate before one output-frame quantization. Collapsed cues, overlap and stale project revisions remain explicit errors. Selecting a media-bin item retains whole-source 1× transcription behavior. No cloud transcription or model download is introduced.

## Audio acceptance

Audio uses exact global 48 kHz sample boundaries, scaled late-stream offsets and source endpoint selection independently from ceil-rounded picture duration. Pitch-preserving processing is a local external FFmpeg capability, not a bundled engine. Its measured transient behavior and actual selected algorithm must be recorded in the completed validation report; mathematical timeline precision does not imply waveform phase identity after time stretching. Independent time-stretch operations at cuts can change transients; audition edits. Physical sound-device playback remains separate from decoded-sample acceptance.

### Implemented audio algorithm and compatibility gate

Non-1× audible clips use the fixed algorithm `rubberband-r2-short-mixed-together-48k-512pad-v1`: external FFmpeg Rubber Band R2, short window, mixed transient handling, linked stereo channels, and stages whose tempo factors stay between 0.5 and 2. Each stage uses 512 input silence samples and removes 512 output samples; bounded tail silence flushes analysis windows before the exact timeline sample budget is selected. Those constants are not asserted to fit every Rubber Band build. The offline runtime gate hashes the FFmpeg executable and verifies actual pitch, markers and selected-tail energy for the fixed configuration; incompatible builds fail explicitly before video grading. There is no automatic algorithm fallback.

A 1× clip bypasses the time-stretch algorithm. Canonical 48 kHz sample selection and global placement still apply. Tempo changes can alter waveform phase/transient shape, especially at independent cuts; this is not lossless audio. Measured acceptance applies to the synthetic fixtures and native builds recorded in validation, not a guarantee for all speech/music. Initial alternatives were rejected when stronger tests exposed shifted transient timing or pitch peaks; tests were not loosened to accept them.

The gate is offline, uses only original generated signals, and caches successful checks by executable SHA256 and fixed algorithm identity. Repeated valid renders do not rerun its signal processing. First use adds a small bounded compatibility check. An FFmpeg without the required filter or compatible behavior produces a translated application error with technical details, not a download or cloud fallback.

Official technical/license references:
- https://breakfastquay.com/rubberband/integration.html — realtime API start-padding/delay requirements; the documented short-window values motivate the tested bounded configuration, not a universal API substitute.
- https://ffmpeg.org/doxygen/7.0/af__rubberband_8c_source.html — FFmpeg wrapper behavior.
- https://breakfastquay.com/rubberband/license.html — GPL/commercial licensing choices. The application source/test archive does not redistribute FFmpeg or Rubber Band binaries. Any future bundled distribution needs its own complete licensing review; external availability is not a blanket license clearance.
