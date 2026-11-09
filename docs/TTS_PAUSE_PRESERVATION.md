# Local speech pacing: pause preservation

The subtitle voice panel now exposes **Pause preservation / 停顿保留**,20–100%. It passes `silence_scale` to the pinned sherpa-onnx1.12.26 Kokoro engine. The old implicit20% behavior remains the default; higher values preserve more of eligible engine-generated quiet intervals. This does not rewrite captions, substitute pronunciations, add silence at every punctuation mark, or provide an emotion/pitch control.

## Native behavior and safe limits
The official tagged [Kokoro implementation](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/v1.12.26/sherpa-onnx/csrc/offline-tts-kokoro-impl.h) calls GeneratedAudio.ScaleSilence. Its [audio implementation](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/v1.12.26/sherpa-onnx/csrc/offline-tts.cc) detects quiet intervals rather than semantic word boundaries. Do not assume every punctuation mark corresponds to a pause. Values above1 are deliberately rejected: this implementation copies a scaled prefix from existing samples, not arbitrarily synthesized extra silence. No manual hard-chunking was introduced.

The same immutable config drives auditions and saved takes. Changing the setting invalidates only the owned audition. Saved provenance records the value; old projects default to0.2. Project schema11 prevents older binaries silently interpreting new metadata. Original subtitle text and timing remain unchanged; full-take overflow still requires an explicit decision.

## Same-text comparison
Real CPU inference, under the existing Linux network-denial harness, used Kokoro v1.1 voice30 `zf_047` and the exact first two sentences of the prior46-second passage. All words and punctuation were held constant:

- A: speed1.00, pause20%:13.374500s
- B: speed1.00, pause65%:16.227500s
- C: speed0.95, pause65%:16.822250s

B increases total duration by2.853s while preserving more detected pause samples. This is a measurable pacing change, not proof of better naturalness or pronunciation. Listen to the samples to choose a preference. Native repeated inference varies slightly; duration differences are not word alignment. An independent native callback diagnostic confirmed its concatenated batches equal returned samples. No separate manual-gap variant is exposed.

## Verification and honest history
346 frozen targeted tests passed with clean process exit. They cover safe range, direct worker propagation, schema migration, provenance, cancellation, cache invalidation, subtitle preservation, save/undo and localization. Earlier two failures were missing Chinese translations and were corrected before the final frozen run. A first test command named a nonexistent localization file and ran no tests.

Actual Chinese Qt workflow verified a65% take, full overflow acceptance, unchanged caption text, undo/redo and save/reopen. The exact screenshot is from Linux offscreen Qt. An initial research A/B run incorrectly resolved an older editable worker checkout; it was rejected and retained separately. Delivered A/B/C runs explicitly verified the child import path and use the restored final source.

No Windows/macOS native validation or subjective listening certification is claimed. Audited official runtime/model test caches remain outside the source package; redistribution is still uncleared. No automatic downloader, cloud inference, repository push, paid service or CI run was added.

Actual preview/export rendering also passed: the full saved16.228625s take occupies487 timeline frames; preview and export decoded PCM are identical. The render fixture explicitly selects the installed Noto Sans CJK SC font after the default DejaVu font correctly refused missing Chinese glyphs. The reset environment's already-pinned Pillow13.0.0/regex2026.9.29 dependencies were restored from official PyPI before that render. Initial failed setup attempts remain in the evidence logs. These are local checks, not physical desktop or speaker tests.
