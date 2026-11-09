# Actual local ASR setup acceptance — 2026-10-17

## What was exercised

This source-only milestone connects the new local component setup dialog to the
existing real whisper.cpp workflow: explicit local runtime/model paths → metadata
checks → apply → actual recognition → editable subtitle track → edit/undo/redo →
save/reopen → SRT → decoded Qt preview and burned-in H.264/AAC export. A nonzero
15-frame timeline offset verifies source-to-timeline placement. The runner uses
original synthetic speech, not user media. It automates file choosers and observes
real child processes; it does not replace inference, rendering or model output.

No GitHub push, CI, paid API, model upload or native Windows/macOS rebuild occurred.
All optional runtime/model/generator assets remain outside the source archive.
Beauty experiments are unchanged and still do not implement production face inference.

## Runtime and source provenance

- Existing supported whisper.cpp v1.8.2 pin: `4979e04f5dcaccb36057e059bbaed8a2f5288315`.
  This deliberately retains the current adapter's tested JSON contract; it is not
  a claim to use the newest upstream release (v1.9.4 was visible during research).
- Official source: https://github.com/ggml-org/whisper.cpp/tree/v1.8.2 .
  Source archive SHA-256: `ec8e33f4c838ed459ad0a23da255c0262f150a6f54839a17e504ddf1495305d5`.
- CPU CLI built from that source using CMake3.31.6/GNU C++14.2; only `whisper-cli`
  target built. Whisper/GGML static libraries; RPC, CURL, GPU backends, CoreML,
  OpenVINO and FFmpeg-linked decoding disabled. Standard OS shared libraries remain.
  `GGML_NATIVE=OFF` does not establish compatibility with every older CPU.
- Runtime SHA-256: `96eb0072e5a2a33ce4cb67cf84b4cfa8723f9a1986b9a6796e4f44c841a4d345`.
- Official model repository revision: `5359861c739e955e79d9a303bcbc70fb988958b1` at
  https://huggingface.co/ggerganov/whisper.cpp . Checksums and exact sizes were
  verified before loading:
  - multilingual base:147,951,465 bytes,
    `60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe`.
  - multilingual small:487,601,967 bytes,
    `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
- Runtime and Whisper weights are MIT licensed per their official projects:
  https://github.com/ggml-org/whisper.cpp/blob/v1.8.2/LICENSE and
  https://github.com/openai/whisper#license . No runtime or weights are redistributed here.
- Synthetic audio was generated locally using PyPI `espeakng-loader==0.2.4`,
  eSpeak NG1.52. Its GPL-3.0-or-later implementation/data remain test-only and are
  not bundled with CutCat. Original sentence text and WAV hashes are retained in
  the evidence fixture manifest. This is not a new CutCat TTS engine.

## Privacy evidence and boundaries

Source audit found v1.8.2 dynamically scans the executable directory and working
directory for GGML backends and honors `GGML_BACKEND_PATH`, even when building
with `GGML_BACKEND_DL=OFF`. CutCat ASR now starts its selected runtime from a
private temporary directory and removes inherited GGML/WHISPER/LLAMA/model-hub
overrides. Model-hub offline flags and bounded thread variables are set. Loader
variables, PATH, executable-directory libraries and compiled backend paths still
require trust; these changes are not an application security sandbox.

`scripts/run_network_denied.py` is a separate **Linux development harness**. It
closes actual inherited descriptors above2 via `/proc/self/fd`, verifies closure,
then installs a child-only libseccomp filter denying the listed socket and
io_uring syscalls before `exec`. IPv4/IPv6/Unix socket negative controls must fail
with EPERM; failures to inspect descriptors, load the filter or deny a required
syscall abort execution. Descendants inherit the filter.
All denied operations return EPERM except `sendto`, which returns ENOTSOCK while
still skipping the syscall. PulseAudio calls `send()` on its ordinary wake-up
pipe and falls back to `write()` only for ENOTSOCK. Returning EPERM broke that
pipe fallback and could deadlock Qt startup. No ALLOW rule, socket, socketpair,
local daemon connection, or network endpoint was added by the repair. A high socket FD above a
lowered soft limit is explicitly covered by regression tests. No system-wide
network/security setting is changed.

The defensible result is: **inference and the editor workflow succeeded with
those network syscalls denied in the tested Linux child chain**. This does not
prove zero attempted networking, all possible IPC/file-backed egress restrictions,
or Windows/macOS offline acceptance. The application itself does not install this
filter on customer machines. Later user-selected runtimes remain trusted code.

## Quality findings: errors are preserved

Original English reference:
“Hello world. This is a local speech recognition test. We are editing a short video today.”

Original Mandarin reference:
“你好，欢迎使用视频编辑器。今天我们测试本地语音识别。这个视频有中文字幕。”

The base model made English word errors, misdetected the synthetic Mandarin as
Thai with automatic language selection, and produced an unrelated generic Chinese
sentence with explicit `zh`. These failures are retained in the evidence. There
was no hidden model fallback. The small model was then selected explicitly.

Small matched this English reference exactly. Small recognized Mandarin as
Chinese but still produced substantial wrong words, including “晚宴是勇士” and
“偏低於音識偏”. Original recognition is retained separately from subsequent
explicit editing. **This is a functional smoke test, not a Mandarin accuracy
benchmark or evidence of production-quality transcription.** Synthetic eSpeak
speech is unusually robotic; natural speech, accents, noise and long recordings
need separate openly licensed/user-authorized tests. No result is silently corrected
or presented as accurate. Review and correction remain necessary.

The demonstration's English line breaks are an explicit text edit to prevent
long single-line captions from clipping. This does not add automatic wrapping.
The source's current caption renderer still requires appropriate line breaks/style.

## Functional and regression results

Affected regression: **243 passed in 45.74 s**, followed by **7 guard tests passed** (overlapping coverage) (2026-10-17). This covers ASR,
retiming/caption mapping, SRT/rendering, shared task management, setup/UI/i18n,
the real-workflow runner's assertions and the Linux network-denial harness.
It is not a new complete-suite run; earlier whole-suite counts remain historical.

The final Chinese-interface workflow passed local path setup, deliberately invalid
model error recovery, direct-process cancellation (114 ms observed), duplicate-job
prevention, successful retry, caption import/edit undo and redo, exact project and
SRT round trips, decoded Qt preview inside a caption, and 312-frame H.264/AAC export.
Preview/export used the same normalized graph SHA256
`7f0a36e47d8cc598d91406d190993c61723084e8f4915a7fc217e1ff04c7779b`.
The original fixture and model/runtime inputs were unchanged. Actual Qt screenshots
show readable Chinese controls and caption glyphs with the explicitly installed
local test font. The black picture is intentional: these fixtures contain audio
only. Raw engine diagnostics remain in English inside the translated error shell.

Evidence directories are `artifacts/asr-setup-acceptance-zh-ui-final` and
`artifacts/asr-setup-acceptance-en-small-reviewed`; these generated outputs are excluded
from the source ZIP and collected separately. Source contains a reproducible
runner, not models, executables, font binaries or generated results.

Independent read-only review checked the runtime loader boundary and the network
test harness. The high inherited descriptor case is covered: socket descriptors
above a lowered soft file-descriptor limit are also closed before exec.
The isolated beauty experiments were preserved and are still not automatic
production face inference. No GitHub push, paid build, or new native package was
performed. All timing numbers are observations on the cloud Linux CPU fixture,
not desktop latency guarantees. Actual cancellation verifies the direct whisper
process is killed/reaped; it does not promise process-tree containment.

## Repeated-startup diagnosis and bounded recovery

A subsequent repeat with the final runner stalled before the first setup screenshot
or inference launch after Qt/PulseAudio reported denied local socket operations.
It was interrupted and recorded separately; a bounded retry with a private writable
XDG runtime directory also timed out (exit137 after120s plus10s kill grace).
These failures are retained. Static inspection of installed libpulse and its upstream
`pa_write` implementation identified the pipe fallback described above. A minimal
QApplication → QAudioOutput → QMediaPlayer probe timed out with the old filter and
completed with the repaired sendto errno in four consecutive runs.
A direct `pa_write` probe transferred one byte through a pipe, while socket,
socketpair, connect, recvfrom and io_uring controls remained denied. Regression
also checks sendmsg denial, descendant errno inheritance, high descriptor closure
and rejection of socket-backed standard input. Physical audio-device output was not
verified. The evidence README records full workflow recovery results.

Reference: https://raw.githubusercontent.com/pulseaudio/pulseaudio/master/src/pulsecore/core-util.c
(`pa_write`; source inspected for behavior, not copied into the product).

This fixes the test harness's syscall emulation behavior, not an application
networking feature. Socket/socketpair/connect and every other previous denied
operation remain denied. It does not certify all IPC/file-backed egress boundaries
or customer-selected runtimes on Windows/macOS.

The successful reviewed English run predates the runner's added `--ui-language`
argument; its exact source fingerprint is retained in its acceptance JSON. The
successful Chinese-interface run uses the final packaged runner. No earlier run
is relabeled as having used a later source hash.

## Final recovered workflows

Both final-code workflows exited0 with the repaired guard.
- en: 185 video frames; cancel/reap 0.177s; total 55.0s; normalized preview/export graph `f68c9c10b552c0424a9a4ab89b72d2e503a06cae2656c40af60b813b768ebf8a`.
- zh: 312 video frames; cancel/reap 0.158s; total 52.7s; normalized preview/export graph `7f0a36e47d8cc598d91406d190993c61723084e8f4915a7fc217e1ff04c7779b`.

Both use the final runner and production code hashes, preserve inputs, and pass
real malformed-model recovery, cancellation/retry, editable caption import,
undo/redo, exact save/reopen and SRT, decoded preview and H.264/AAC export.
The final evidence uses `asr-setup-acceptance-en-recovered` and
`asr-setup-acceptance-zh-recovered`. Their execution-context.json records the
repaired guard SHA256. Prior startup failures remain in the evidence archive.
Chinese recognition quality limitations remain unchanged.
