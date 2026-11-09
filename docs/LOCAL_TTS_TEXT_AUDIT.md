# CutCat punctuation and sentence-pause comparison

This is a local diagnostic experiment, not a production default change.
Original text and captions are unchanged. Speaker is v1.1 zf_001 (sid 3),
speed 1.0 and silence_scale 0.65 throughout.

## Verified adapter path

`lumen/tts.py::_synthesize` serializes `text=text` into its request JSON.
`lumen/tts_worker.py::main` reads that JSON and calls
`engine.generate(data['text'], sid=..., speed=...)` without punctuation removal.
The previous actual generation provenance retains the complete punctuated text.
MP3/AAC encoding takes place only after PCM speech generation.

## Pinned upstream source

- [Chinese frontend](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/kokoro-multi-lang-lexicon.cc#L69-L130): punctuation normalization, Chinese-span splitting, length-based merge.
- [Punctuation token path](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/kokoro-multi-lang-lexicon.cc#L277-L327): isolated punctuation becomes a model token.
- [Kokoro Generate](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts-kokoro-impl.h#L230-L276): independent frontend sequences processed with batch size 1 and concatenated; no fixed inserted inter-sequence silence.
- [Kokoro Process](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts-kokoro-impl.h#L373-L391): silence scaling after inference.
- [ScaleSilence](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts.cc#L33-L87): qualifying low-amplitude runs are shortened when scale <1. The setting does not define linguistic sentence boundaries.

These mechanisms alone do not prove the cause of every audible defect.
Specific runtime evidence is recorded in generation.log and comparison.json.

## Comparison

A: the same full paragraph through the existing engine configuration.
B: two complete sentences supplied separately; preserve every output sample,
insert exactly 13,200 zero-valued PCM samples (550 ms at 24 kHz) between them.
This is 550 ms of *additional* silence, not a claim that total audible pause
is exactly 550 ms. Neither sentence output is truncated or crossfaded.

This cannot repair within-sentence pronunciation, accent or prosody. It is not
a proof of improved naturalness. User listening is needed before any default
or editor behavior change. Model, dictionaries and runtime are not bundled.

## Actual runtime findings (2026-10-19)

The exact pinned model/runtime were restored and checksum-verified. Inference
ran under the existing Linux child-process network-denial harness; its socket
negative controls passed. This is not a Windows/macOS privacy certification.

The whole paragraph produced five final token sequences and five callbacks,
corresponding to the five punctuation-delimited clauses. The two separate
sentence calls produced two plus three sequences. Punctuation tokens were
present; outer sentence splitting did not change this internal clause policy.
The new whole-paragraph baseline is a fresh synthesis, not a byte-identical
copy of the morning sample: the recorded WAV hashes differ and durations differ
by one sample. No assertion of deterministic bit identity is made.

The B join retains both complete sentence outputs, adds exactly 13,200 zero
samples and joins at zero-valued endpoints. There is no sample truncation or
new join discontinuity. This does not prove every model-generated syllable is
correct; token and waveform checks do not replace phonetic listening.

Additional actual probes:
- Decimal 3.14 normalizes to 三点一四 in the tested sentence.
- Chinese date 2026年10月19日 normalizes to 二零二六年十月十九日.
- MP4 / 1080p / v1.2.3 are rewritten to MP四 / 一千零八十p / v一.二点三.
  This is inappropriate or ambiguous for some identifiers.
- Adjacent quoted punctuation produces phonemes corresponding to English
  “colon” and “exclamation”; doubled ellipsis supplies no ellipsis token.
- 嗧 is omitted with Ignore OOV, while the engine still returns speech.
- 〇/㐀/𠮷 enters non-Chinese handling and logs missing phonemes while returning
  speech. No claim is made that these characters are all pronounced correctly.

The known omission warnings are unconditional in the pinned native runtime.
The production fix rejects such results before final publication and gives a
localized, non-sensitive message. Expected startup Unknown token diagnostics
are not blanket-rejected. Actual final-adapter checks confirm valid speech is
saved, both known omission cases are refused with no output, and cancellation
leaves no output or temporary request directory. This detects reported loss,
not every possible silent omission or incorrect pronunciation.

Known risky quoted punctuation and mixed identifiers now receive an explicit
EN/ZH preflight review with No as default. Continuing passes the original text
unchanged. This warning is a mitigation, not a corrected pronunciation engine.
No phonemes, subtitles, punctuation, or numbers are silently rewritten by CutCat.

Independent review also found a separate exact-frame timing defect: float
2.3333333333333335 seconds (56,000 samples at 24 kHz) previously rounded to
71 timeline frames instead of70 at30fps. New speech stores sample_count and
uses exact rational timing; legacy projects retain their existing timing.
Schema12 rejects unsupported old readers rather than discarding the new data.

There is no exposed Kokoro option in this version to replace its fixed batching
policy. We do not patch its phonemes or replace the frontend in this milestone.
Accent, within-clause prosody, robust identifier pronunciation and full automatic
speech-content verification remain unresolved. No default pause change or
automatic sentence-chunking behavior is introduced.

## Validation scope

One frozen combined local regression completed with 496 passed, 1 skipped
(native Windows drive semantics), exit0. All196 Python-file hashes were
identical before and after that run. Coverage included TTS, speech model/render,
caption migration, model paths, localization, UI review/cancellation and synthetic
FFmpeg/AAC end-boundary tests. This was not a fresh full application suite.

A subsequent independent review found that relinking a moved new speech WAV
also receives rounded ffprobe duration. A narrow follow-up preserves exact
speech duration only after verifying replacement bytes against provenance;
changed replacements are rejected. Its final scoped checks are recorded in the
accompanying test logs rather than retroactively counted in the496-pass run.

No Windows/macOS native build or physical-device playback was run. Generated
MP3 and H.264/AAC MP4 were fully decoded locally, every comparison segment was
checked non-silent, and actual Chinese labels were visually inspected.

Final relink-targeted verification:81 passed,46 deselected,10.79s. Independent read-only review found no remaining blockers in these changes.
