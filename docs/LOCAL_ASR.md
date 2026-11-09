# Local speech recognition

Lumen can use a **user-selected, trusted whisper.cpp CLI executable** and a
**user-selected Whisper GGML model file**. Nothing is downloaded by the app.
No recording, audio extraction, transcript, or media file is uploaded. Runtime
selection is executable-code selection: obtain it from the official project and
check its provenance. A malicious selected executable can act with your account's
permissions; Lumen is not a security sandbox for that executable.

## 2026-10-17 local revalidation

The new component-setup workflow has been exercised with real CPU inference,
editable captions, save/reopen and captioned preview/export. See
[the dated acceptance record](LOCAL_ASR_SETUP_VALIDATION.md) for exact source,
model, fixture, process and quality evidence. The tested Linux child chain now
runs under a restrictive socket/io_uring syscall filter. This is a development
harness, not a sandbox added to the Windows/macOS application.

ASR runtime launch now removes inherited GGML/WHISPER/LLAMA/model-hub overrides
and uses a private working directory. The selected executable, libraries in its
installation directory, compiled-in backend locations and OS loader environment
remain trusted. This targeted hardening does not certify arbitrary runtimes.

## Setup and use

1. Install FFmpeg separately and put it on PATH (or set `LUMEN_FFMPEG`).
2. Obtain/build whisper.cpp from https://github.com/ggml-org/whisper.cpp . The
   integration is verified against **v1.8.2**, commit
   `4979e04f5dcaccb36057e059bbaed8a2f5288315`.
3. Obtain a compatible GGML Whisper model following that project's documentation.
   Use a multilingual model such as **small** for English and Chinese. Models
   ending in `.en` are English-only. Other sizes/quantizations are not validated by
   this prototype's inference test.
4. Select the executable (`whisper-cli` / `whisper-cli.exe`) and model explicitly
   in the caption import workflow. Choose `auto`, `en`, or `zh` as appropriate.
   The runtime's required shared libraries must remain installed alongside it.
5. Review the generated text and timing before export. Speech recognition can
   omit speech, hallucinate words, and mishandle silence, music, accents, and
   synthetic speech. This does not provide speaker diarization or word alignment.

## Adapter contract

`lumen.asr.ASRConfig(runtime_path, model_path, language='auto', threads=4)`;
`transcribe(media_path, config, progress=None, cancel=None)` returns
`TranscriptSegment(start_seconds, end_seconds, text, language)` values.

Times use the source media video origin (or audio origin for audio-only media),
not the destination timeline. FFprobe measures the first audio/video start
timestamps. Positive audio offset inserts silence; negative audio offset trims
audio before the video origin. Real FFmpeg shifted-source regressions cover both.
The UI handles placement, clip trim/rate mapping, and caption-track changes.
The adapter preserves original language (no translation flag). It supports
whisper.cpp `-oj` JSON and divides `offsets.from/to` by **1000**, because CLI JSON
uses milliseconds. Internal Whisper timestamps use different units and are not
read. Invalid/missing/nonfinite/reversed/out-of-order timing data is rejected.

FFmpeg extracts the first audio stream to a private temporary 16 kHz mono
PCM signed-16-bit WAV. It uses the `file,pipe` protocol whitelist, `-nostdin`,
and `-n`; process execution uses argument arrays, no shell. Input and adjacent
files are never overwritten. Extraction, JSON and diagnostics are cleaned up
on success, errors, or cancellation. Diagnostics use temporary files rather
than unbounded in-memory process pipes. Cancellation kills and reaps the active
process at the next 100 ms polling check. Normal process failures become
`ASRError`; cancellation becomes `ASRCancelled`.

Progress callbacks receive float values in [0, 1]: 0 (starting), .15 (decoded),
.95 (recognition complete), 1 (parsed). This is stage progress, not a real-time
percentage or completion estimate. The inference stage can take minutes on CPU.

## Licensing and reproducibility

The app does **not** redistribute whisper.cpp, model weights, or test TTS code.
Runtime, model, TTS dependencies and build assets live outside the app tree in
`/workspace/shared/lumen-model-test-cache`. Small synthetic WAV fixtures and
auditable results are included in `artifacts/asr-validation/`; these are not
user media or executable dependencies.

- whisper.cpp v1.8.2 source: https://github.com/ggml-org/whisper.cpp/tree/v1.8.2
  (commit above), MIT license, https://github.com/ggml-org/whisper.cpp/blob/v1.8.2/LICENSE .
- Multilingual `ggml-small.bin`: downloaded with the checked-out project's
  `sh ./models/download-ggml-model.sh small`, which resolves to
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin .
  SHA-256: `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
  Source weights are OpenAI Whisper, whose code and model weights are MIT licensed:
  https://github.com/openai/whisper#license and https://github.com/openai/whisper/blob/main/LICENSE .
  The download URL's `main` is mutable; the hash identifies the exact tested bytes.
- Build tooling: PyPI CMake 3.31.6, GNU C/C++ 14.2. CPU build, no GPU requirement.
- Synthetic fixture generation: PyPI `espeakng-loader==0.2.4` provides local
  eSpeak NG library/voice data; development cache only, never an application
  dependency. Original English and Chinese fixture texts are shown below.
  eSpeak NG is GPL-3.0-or-later: https://github.com/espeak-ng/espeak-ng .
  The generated test sentences contain no user content and were not uploaded.

For reproducible verification, see `artifacts/asr-validation/verification.md`
and run `scripts/verify_local_asr.py` with explicit runtime, model and fixture
paths. That helper never downloads anything. The manifest records source,
license and hashes. The test cache additionally retains generation/build logs.

## Earlier milestone verification (historical fixtures)

Adapter tests cover milliseconds, Unicode text, malformed output, finite and
ordered time ranges, config validation, safe argument passing, PCM settings,
source preservation, temporary cleanup, process failure diagnostics, and actual
running-process cancellation. Run `python -m pytest -q tests/test_asr.py`.

Actual CPU inference completed with the real v1.8.2 CLI and multilingual small
model, four CPU threads. The final run took 5.63 s for English and 6.85 s for
Chinese. These are short synthetic smoke tests, **not accuracy benchmarks**.

English reference and hypothesis match exactly:

> Hello world. This is a local speech recognition test. We are editing a short video today.

Returned segment: 0.00–6.50 s, language `en` (fixture duration 6.486 s).
Fixture SHA-256: `0fdb5961969a5330b555fd101ac496f708305198e89e6139c1afcc848691aedb`.

Chinese reference:

> 你好，欢迎使用视频编辑器。今天我们测试本地语音识别。这个视频有中文字幕。

Actual hypothesis (language `zh`, fixture duration 11.429 s):

- 0.00–4.16 s: 你好 歡迎 使用 視頻編輯器
- 4.16–8.32 s: 今天我們則是本地語音視頻
- 8.32–11.72 s: 這個視頻有中文字幕

The first and last sentences are recognizable in traditional characters; the
middle sentence is misrecognized. End time slightly extends beyond the input
sample. Review/edit is required; do not present this as perfect Chinese accuracy.
Fixture SHA-256: `11334d363ee705e1b6662f499e604e505ce4005f559ceb2bc6542587982ff566`.
Compiled test executable SHA-256:
`f28b30ad6493b271667eb88e7838b3c6ec823316f0ad6b0c0731bdef35a7092f`.

Additional actual-runtime checks: cancelling a running whisper inference killed
and reaped the process (total adapter time about 0.52 s); an invalid model failed
with a useful bad-magic diagnostic. The focused suite includes positive/negative
stream-start shifts using real FFmpeg plus a JSON size-limit regression.


Historical isolation result from the earlier environment (superseded only for the newly dated Linux test-harness runs above): network-isolation verification was **not available in that execution environment**:
network namespace creation is denied, and a fresh process traced with
`strace -f -e trace=network` fails at `PTRACE_TRACEME` / `PTRACE_SETOPTIONS` with
`Operation not permitted` before inference starts. No restriction was bypassed.
The successful inference runs used already-local files; the adapter code has no
network client or downloader and restricts FFmpeg/FFprobe input protocols. This
is code-path evidence, not proof from an isolated/network-traced runtime. An
OS-level offline run remains a release validation step on supported desktops.

Default `auto` language detection was also tested against both real fixtures;
it correctly returned `en` and `zh` and the same hypotheses and time ranges.
