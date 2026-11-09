# Local multi-voice TTS integration revalidation — 2026-10-17

This is a Linux source-validation milestone. Existing production TTS/UI code is
unchanged from the caption-layout baseline. New work adds reproducible actual
model acceptance and workflow boundary tests, not new voice identities or a
claim that every available voice has been auditioned.

## Runtime, model and privacy boundary

Explicit test setup fetched official PyPI sherpa-onnx/core 1.12.26 wheels and the
official full-precision Kokoro v1.1 pack. Downloaded wheel/archive hashes matched
the audited pins. The bundled native ONNX Runtime has the expected 1.23.2 version
string. Model and bank hashes, all pack assets and original text are retained in
synthesis provenance. See [source/license audit](LOCAL_TTS_REVALIDATION_AUDIT.md).

Acquisition was network-enabled; actual inference and Qt workflows ran afterward
under `scripts/run_network_denied.py`, with successful socket negative controls
and inherited descriptors closed. This is a Linux development boundary, not an
unrestricted Windows/macOS telemetry certification. Windows ORT telemetry remains
an explicit release gate. No user media was used. No app model downloader, cloud
inference, GitHub push, CI or payment was involved.

Models, runtime wheels, eSpeak data and dictionaries are test-cache inputs and are
excluded from source/evidence ZIPs. Their redistribution is not cleared merely by
a top-level Apache label. Evidence contains original test text and generated audio.

## Actual exercised workflow

The same four real v1.1 voice IDs were sampled in English and Chinese:

- 0: `af_maple`
- 2: `bf_vale`
- 3: `zf_001`
- 58: `zm_009`

The installed catalog has 103 entries; this milestone samples four, not all103.
Origin labels are upstream metadata, not exclusive language capabilities or
invented character/personality categories. Different PCM does not establish
perceptually distinct quality or human preference.

English and Simplified Chinese UI runs each verify:

1. Explicit component setup and real pack/runtime verification.
2. Eight complete local auditions, replay/stop and unchanged project state.
3. Real worker cancellation/reaping and cleanup, duplicate-start protection,
   then successful retry. Missing-pack errors remain visible.
4. A new full saved take, explicit decline/accept when it exceeds its caption,
   normal editable audio-lane insertion, undo/redo and immutable provenance.
5. Save/reopen and moving the project folder with relative generated-media paths.
6. Actual Qt decoded preview, exported audio/video, shared processing graph,
   exact compiled offset/sample budget and comparison with the original waveform.

The acceptance caption is deliberately one frame long to force the overflow
question. The audio continues in full after that caption ends. Its black video
background is a synthetic audio-only test project, not a picture-quality demo.
No physical audio device is present; screenshots and decoded waveform checks do
not certify speakers, device playback or subjective voice quality.

A separate initial Chinese probe said “你好，这是剪辑猫的本地语音测试。”
Local whisper-small recovered all characters, with only comma-style variation.
That single secondary machine check is not a general intelligibility score.

## Reproduction and evidence

Install explicitly audited optional dependencies into the development Python
before running. The runner never installs or downloads:

```sh
QT_QPA_PLATFORM=offscreen python scripts/run_network_denied.py -- \
  python scripts/verify_tts_setup_integration.py \
  --model-pack /existing/kokoro-multi-lang-v1_1 \
  --output-dir /new/acceptance-output --ui-language zh-CN \
  --font-family 'Noto Sans CJK SC'
```

The output must be fresh. Reports preserve partial failures, source/runtime/pack
hashes, process results, actual generated WAVs, decoded waveform measurements,
projects and screenshots. Final test counts and hashes are recorded in the
separate validation evidence archive. Earlier provisional reports were followed
by a runner-only correction to exact waveform bucket start positions and stronger
provenance assertions; final workflows were rerun on the frozen corrected script.

The prior full source baseline passed 2613 tests with two documented skips.
This milestone uses focused affected tests and real inference acceptance; it does
not relabel the prior full run as a fresh all-suite pass. Existing native packages
remain unchanged. Optional TTS runtimes/models are still absent from those packs.
