# Generated speech: timeline and provenance

Generated speech is an ordinary local audio source with an additional immutable
synthesis record. This record stores the original synthesis text, engine and
version, model/voice/asset SHA-256 hashes, voice identifier, speed, sample rate,
UTC generation time, originating caption identifier and frame range, and the
SHA-256 hash of the completed audio file. These facts describe the synthesis
that actually happened. Editing or deleting the caption afterward does not
rewrite this record or regenerate the audio. Generate a new voice clip when
changed text should be spoken.

The record is saved in the project JSON and restored on reopening. Audio files
remain external assets: retain them alongside the project or at their recorded
locations. The record is provenance metadata, not a digital signature or proof
of authorship. It is inspectable/editable as project data by someone with access
to the project file.

## Full-duration audio

Generated audio duration is rounded **up** to project frames so an automatically
inserted full-length voice clip includes the final partial frame. For example,
1.013 seconds at 30 fps occupies 31 frames (1.033333 seconds). The renderer keeps
the full source audio and pads the remaining fraction of a frame with silence.
Regular imported media retains its existing floor-to-frame behavior.

Preview playback and export use the same timeline compiler and audio chain.
Re-probing the audio at render time preserves its synthesis record and the
associated round-up rule. Ordinary explicit trims, fades, mute and gain still
apply; the full-source guarantee concerns an untrimmed, unmuted generated clip,
not an intentionally shortened or silenced clip. Output uses lossy AAC, so
sample-level identity and a perfectly sharp boundary after encoding are not
promised.

## Changed-source protection

Before preview or export, the renderer hashes each unique generated source used
on the timeline once and compares it with the original output hash. A valid WAV
with altered samples is rejected just like other changed content. A cached
preview also requires this check. Restore the original file or generate speech
again to proceed; the renderer never silently adopts changed bytes under the
old synthesis record. Files not used on the timeline are not hashed for render.

Rendering does not overwrite an existing output or input source. Cancellation
removes partial render output and leaves generated source files intact.

## Verification scope

`tests/test_speech_render.py` uses synthetic 24 kHz and 48 kHz PCM WAVs, not a
downloaded speech model. Its 1.013-second signal has an ending tone entirely after the first
second. Both actual FFmpeg preview and export must retain that tone, use a
31-frame timeline, reject a tampered source, and render again after restoration.
The tests also cover save/reopen, unique-source hashing and cancellation/source
protection. These are media-integration checks; they do not establish speech
naturalness, pronunciation accuracy, language quality or actual model inference.
