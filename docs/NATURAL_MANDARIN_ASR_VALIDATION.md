# Natural Mandarin ASR diagnostic — 2026-10-17

## Fixed fixture selection

All three original Dataset Samples on the AISHELL-3 authors' page were selected
before recognition, in displayed order. Total duration11.564s,39reference Chinese
characters. These are human recordings, not the separately labeled synthesized
examples on that page. This is a tiny clean read-speech diagnostic, not a
representative Mandarin benchmark or independent held-out model evaluation.
Possible training-data overlap is unknown.

Official license and sources:
- https://www.openslr.org/93/ (speech+transcripts, Apache-2.0, linked author page)
- https://sos1sos2sixteen.github.io/aishell3v2/ (original recordings and exact labels)
- https://www.apache.org/licenses/LICENSE-2.0

Audio remains in development test cache and is excluded from deliverable archives.
References, original-file hashes, raw adapter outputs and scores are retained.
No user media was used. No reference text was passed to the recognizer.

## Comparison policy

Existing verified whisper.cpp v1.8.2 CPU runtime; multilingual base and small
models; explicit zh versus auto;4threads; identical adapter PCM16kHz extraction.
No model fallback, training, reference prompting or selective failure removal.
Run under the repaired Linux child-only syscall-denial harness. This is not a
universal hostile-code sandbox or native Windows/macOS offline certification.

Report raw character error rate and separately NFC/casefold normalized CER with
punctuation/whitespace removed. Simplified/Traditional characters remain distinct;
this intentionally avoids a hidden transliteration that could conceal errors.
CER can exceed100% because insertions count. Sample-weighted averages should not
replace pooled edit-distance divided by pooled reference-character count.

## Results

All12cases completed successfully; all manifest/runtime/model/audio hashes were unchanged.
Base auto/zh: raw and punctuation-normalized CER14/39=35.90%; runtime28.24/14.71s.
Small auto/zh: raw CER11/39=28.21%; punctuation-normalized9/39=23.08%; runtime71.24/34.45s.
These runtimes include extraction/recognizer launch on this Linux cloud CPU, not
customer hardware latency guarantees. No repeated randomized timing benchmark.

A separately labeled fixed OpenCC1.4.2 `t2s` diagnostic applies the same predefined
conversion to reference and hypothesis before normalization: base8/39=20.51%,
small3/39=7.69%. It does not replace the raw score or rewrite application captions.
Traditional/Simplified spelling inflated raw mismatch; genuine recognition errors
remain. In raw1 small produced 施放 instead of 释放; raw3 used 湘絲 instead of 相思.
Base also emitted RUSTA for 如此大 in raw2. All outputs and scores are preserved.

The3samples show the pipeline can recognize natural Mandarin substantially better
than the earlier robotic synthetic fixture suggested. They do not demonstrate
broad Mandarin quality or Wink/competitor parity. Small+explicitzh is a reasonable
setting to try for known Mandarin, with manual review and the observed CPU cost.
No decoding change was justified by this tiny set; app defaults and raw text were
left intact. Natural conversation, accents, noise, longer passages and timing
alignment remain untested here.

## Tool and validation limits

135 targeted tests passed (including60 new runner tests), not a full-suite rerun.
An independent reviewer checked3,969 string pairs against a separate edit-distance
implementation and reviewed denominator/failure handling. Summaries count only
successful runs; coverage and errors remain visible. Here all12succeeded.
Cancellation is cooperative through the existing adapter and direct-process
kill/reap. Ctrl-C outside the execution loop may leave an incomplete report.

The actual run began with the initial runner; subsequent changes only rejected
URL/UNC paths and nonfinite JSON and clarified deadline wording. Inference,
scoring, normalization and adapter code were unchanged. The initial runner hash
was reconstructed from that known edit, not recorded at run start:
f92fb5d7e186987bd34595cdfa8c012ebc977d1d18fa411de884fe95645b80d8.
Final tested runner hash:
05cafafba3630499d582462f2ae99e2a59cf91f35ec08bceb9c5396116f59cbe.
Do not claim the live run used the later preflight revision.

OpenCC was installed from its official PyPI package into testcache only; Apache2.0.
Official project: https://github.com/BYVoid/OpenCC ; package https://pypi.org/project/OpenCC/ .
WheelSHA256 d90a8b76ea5d1f425a4f2eb16114cb33abd29a73c6a4ec367361c61b1c059a10.
The supplemental scorer ran under the same syscall-denial harness. No OpenCC
binary/dictionary, audio, model or runtime is bundled in source/evidence archives.

No GitHub push, CI/payment or native Windows/macOS build occurred. Existing beauty
experiments and production editor code were preserved.

