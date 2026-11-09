# Local Mandarin backend evaluation

Review and validation snapshot: 2026-10-19 UTC. Scope: a test-only alternative to Kokoro
using original synthetic Mandarin text and the existing Linux CPU runtime.
An optional experimental adapter is now integrated. Actual Qt acceptance and
final regression results are recorded in [LOCAL_MELO_INTEGRATION.md](LOCAL_MELO_INTEGRATION.md). No user recording, voice
cloning, external inference service, CI run or new runtime dependency is part
of this evaluation. Model/data files remain outside the source tree and
delivery bundles.

## Decision and measured evidence

**Melo Chinese/English is a viable bounded local inference experiment. Mandarin
naturalness and production suitability remain unverified.** The roughly 170 MB
graph is not a low-memory guarantee: a completed 300-character slow-speed test
peaked near 3.35 GiB RSS. Matcha Baker has a
non-commercial dataset restriction; CosyVoice2/3 require a substantially larger
runtime and a separately resolved stock-voice/prompt-audio strategy.

The initial raw-baseline measurements below come from `melo-batch0.json`,
`melo-batch1.json`, their logs, and `evaluate_native.py` in the sibling evaluation
directory. They are individual runs, not controlled repeated benchmarks.

- Runtime: `sherpa-onnx==1.12.26`, existing audited core/ONNX Runtime bundle,
  `provider=cpu`, two threads; no new Torch or G2P dependency.
- Input: “请先整理视频素材，再检查字幕和声音。转场保持自然，画面清晰稳定，确认完成后再导出。”
- Both runs: `sid=0`, speed and silence scale `1`, `noise_scale=0.667`,
  `noise_scale_w=0.8`, `length_scale=1`, no FSTs, no text pre-normalization,
  debug logging enabled. The graph internally selects its recorded speaker ID.
- `max_num_sentences=0`: load 4.203 s; generate 5.854 s; 313,856 samples at
  44,100 Hz, duration 7.117 s; RTF 0.823; one callback; peak RSS 501,668 KiB.
- `max_num_sentences=1`: load 5.294 s; generate 7.057 s; 343,552 samples at
  44,100 Hz, duration 7.790 s; RTF 0.906; five callbacks; peak RSS 419,824 KiB.
- Both generated finite, nonempty mono PCM16 WAVs with zero detected clipped
  float samples. These checks establish waveform generation, not correct
  pronunciation, pleasant prosody or superiority to Kokoro.
- The raw baseline log reconstructs the complete 41-character input, including
  punctuation, from 39 lexicon matches: `和声` and `转场` each group two
  characters. There are no OOV warnings. This establishes frontend coverage of
  that passage, not correct phrase interpretation or audible pronunciation.
- Baseline WAV SHA-256:
  `38fa3d52c9574020664475451bea56572973d4d89ed7c0b84ed5448d95abe13d`.
  Batch-1 WAV SHA-256:
  `bebeebd2a84e5eb00379c1f74c0115f304bf3d479a6ef684bbfbba13b81f0be2`.

RTF is synthesis time divided by generated duration; it excludes model loading.
No random-seed control was recorded, so differences between these runs must not
be attributed exclusively to batching. Native Windows/macOS execution and
listening acceptance were not established by these measurements.

### Completed probes and duration comparison

`melo-batch0-probes.json` and its completed log record a **different
configuration**: `date.fst` followed by `number.fst`, speed `1`, silence scale
`1`, and `max_num_sentences=0`. All five cases generated nonempty WAVs with zero
detected clipped float samples. The native normalization trace is retained:

- `他说：“检查字幕……再导出！”`: fullwidth colon `：` and curly quotes `“`/`”`
  were ignored as OOV; the ellipsis and exclamation mark were retained. This is
  punctuation loss, not proof of skipped spoken words.
- `请读嗧这个字。`: `嗧` was ignored as OOV. This loses a requested Han character
  and is a semantic-content failure, distinct from omitted punctuation.
- The decimal/date case became `这个镜头长三点一四秒，今天是二零二六年十月十九日。`.
  The identifier case became `请导出MP四，分辨率一千零八十p，版本v一.二点三。`.
  Neither case logged OOVs; this does **not** establish correct pronunciation
  or appropriate reading of MP4, resolution and version identifiers.
- The 123-character repeated-passage case produced 20.561 s in 10.730 s,
  with one callback. Peak RSS for the complete five-case process was
  756,100 KiB. These are individual measurements, not latency guarantees.

For the same original 41-character passage, the completed **no-FST** native
comparison in `melo-batch0-speed0.5.json` and `kokoro-batch1.json` is:

- Melo: speed `0.5`, silence scale `1`, one native block; 585,216 samples at
  44.1 kHz = **13.2702 s**; generation 6.074 s.
- Kokoro `zf_001`: speed `1`, silence scale `1`, five native blocks; 325,800
  samples at 24 kHz = **13.575 s**; generation 10.064 s.

These backend-native speed controls are not calibrated to the same units.
Similar total duration is a listening aid, not evidence of equivalent prosody
or a controlled backend-only comparison. Original native WAVs were not
resampled or time-warped. `make_comparison.py` converts the playback MP3/MP4 to
44.1 kHz and adds 1.25 s padding after each take; that encoding-stage sample-rate
conversion does not adjust speaking speed. The optional speed-`0.55` Melo trial
produced 12.2601 s and is not used in the final comparison.

### Completed boundary test and current adapter

`melo-batch0-limit.json` records a completed **no-FST**, speed-`1`, silence-`1`,
single-block test of exactly 300 Unicode characters: 2,145,280 samples at
44.1 kHz = 48.6458 s; generation 57.8244 s (RTF 1.189); one callback; zero
detected clipped samples; peak RSS **1,770,896 KiB**.

The same no-FST, 300-character case at speed `0.5` has now completed:
`melo-batch0-limit-speed0.5.json` and the log record 3,963,392 samples at
44.1 kHz = **89.8728 s**, generation **166.6023 s** (RTF 1.854), one callback,
zero detected clipped samples, and peak RSS **3,508,976 KiB (about 3.35 GiB)**.
The approximately 170 MB model-file size must not be marketed as low inference
memory. These repeated-and-truncated passage runs are resource samples, not a
worst-case bound or validation of every text/control combination.

The experimental `melo-zh_en-v1` descriptor in
[`voice_catalog.py`](../lumen/voice_catalog.py) now requires five individually
hash-pinned files: `model.onnx`, `tokens.txt`, `lexicon.txt`, `date.fst`, and
`number.fst`. Its FST order is **date then number**, matching the completed
probe configuration, not the no-FST baseline/comparison/boundary measurements.
[`tts_worker.py`](../lumen/tts_worker.py) uses CPU with two threads,
`max_num_sentences=0`, the evaluated VITS noise settings and `debug=False`.
[`tts.py`](../lumen/tts.py) rejects input over 300 Unicode characters before
asset work or native worker construction; it does not silently split or
truncate text. The single-block choice is prosody-relevant and can consume
substantial memory. Core adapter presence is not actual Qt acceptance, a
passed final test suite, or redistribution clearance.

## Exact Melo assets and execution path

The [official sherpa model documentation](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/vits.html#vits-melo-tts-zh-en-chinese-english-1-speaker)
identifies one fixed Chinese/English voice and a 44.1 kHz sample rate. This test
used the maintainer's converted model repository at revision
`a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`, not an unpinned latest download:

- [Pinned model repository](https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/tree/a0d5c6a264c0ef92d70d8661d8cc502d79627cd6)
- `model.onnx`: 170,429,550 bytes; published LFS SHA-256
  `bf30582eb1b012250a35b1a4a80e7dfbcf8485e7bb9de0d95efbbeef0e4ad86d`.
- Required frontend files: `lexicon.txt` (6,837,671 bytes) and `tokens.txt`
  (655 bytes). Local SHA-256 values respectively:
  `7236884b02435ac5d10cf69b4be40a61b45aa676b5300f0e412f185748fee528` and
  `d18664a7e12bd7ea1022ddaf951e534e136815016c5a809d6b64156bffb4369d`.
- The acquisition script checked size and published LFS hashes, or Git blob
  IDs for ordinary files. [MELO_MODEL_MANIFEST.json](MELO_MODEL_MANIFEST.json)
  is an unchanged copy of `verified-model-manifest.json` and retains local
  SHA-256 values for all nine fetched files, including LICENSE/README and the four
  FSTs. Date/number are now required by the experimental adapter; phone and
  new_heteronym remain unused. A locally calculated SHA-256 is not an upstream
  publication.
- Inspected model metadata confirms version `2`, `jieba=1`, `comment=melo`,
  one speaker, sample rate `44100`, and `license=MIT license`.

The alternative GitHub archive `vits-melo-tts-zh_en.tar.bz2` has official asset
ID `203769960`, size `167006755` and `digest=null` in the
[release API](https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/tts-models).
No published archive SHA-256 was found; this evaluation acquired pinned HF
files instead.

In the pinned [VITS implementation](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts-vits-impl.h),
`InitFrontend()` selects `MeloTtsLexicon` and reads explicit local paths. Its
constructor needs tokens and lexicon; the archived `dict/` directory is not used
by this path. The [graph loader](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts-vits-model.cc)
loads the local ONNX bytes and requires Melo metadata version at least `2`.
No separate vocoder or Python tokenizer download is required for this path.

## Frontend and prosody limitations

The [v1.12.26 Melo frontend](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/melo-tts-lexicon.cc)
maps `，。！？` to corresponding token aliases and adds an alias for `、`.
`ConvertTextToTokenIds()` computes normalized punctuation into `s` but splits
`text`; the completed colon/curly-quote probe above confirms those characters
are omitted. Other unsupported punctuation, including semicolons, still needs
coverage. Its OOV warning is unconditional even when debug logging is disabled;
debug logs additionally contain the complete input text.

`max_num_sentences` changes native inference boundaries: `1` processes each
recognized punctuation-delimited segment separately; `0` processes all segments
together. `Process()` concatenates token/tonal sequences, and silence scaling is
applied to its output. No explicit inter-batch silence is inserted here. Treat
batching as a prosody-relevant setting, not merely a memory optimization.

The [official ONNX export](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/scripts/melo-tts/export-onnx.py)
replaces BERT and ja-BERT features with zero tensors and uses a generated fixed
lexicon. Upstream PyTorch demo quality cannot certify this conversion. Unknown
English words also have limited lexicon/character fallback behavior.

## License boundary

- Runtime code: [sherpa-onnx 1.12.26 Apache-2.0](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/LICENSE).
  Preserve the existing core/dependency audit in
  [LOCAL_TTS_REVALIDATION_AUDIT.md](LOCAL_TTS_REVALIDATION_AUDIT.md); switching the
  model does not change bundled-library licenses or distribution obligations.
- Melo code is MIT. [Original Chinese weights at revision af5d207](https://huggingface.co/myshell-ai/MeloTTS-Chinese/blob/af5d207a364ea4208c6f589c89f57f88414bdd16/README.md)
  declare MIT, and the [pinned converted repository LICENSE](https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/blob/a0d5c6a264c0ef92d70d8661d8cc502d79627cd6/LICENSE)
  contains MyShell's MIT notice. Retain applicable notices.
- Export code derives the lexicon from pypinyin character/phrase data and Melo's
  English dictionary. [pypinyin](https://github.com/mozillazg/python-pinyin),
  [pinyin-data](https://github.com/mozillazg/pinyin-data/blob/master/LICENSE) and
  [phrase-pinyin-data](https://github.com/mozillazg/phrase-pinyin-data/blob/master/LICENSE)
  declare MIT. [Melo's cmudict.rep](https://github.com/myshell-ai/MeloTTS/blob/main/melo/text/cmudict.rep)
  includes CMU's 1998 unrestricted research/commercial-use notice. These links
  establish upstream declarations, not a reconstructed exact export dependency
  lockfile or complete audit of every underlying dictionary source.
- **The exact build provenance and redistribution terms of the supplied
  `phone.fst`, `date.fst`, `number.fst`, and `new_heteronym.fst` remain unresolved
  at asset level.** A top-level MIT file is not blanket clearance for every
  incorporated asset. The raw baselines, native duration comparison and
  completed 300-character boundary measurements loaded no FSTs. The completed
  probes loaded date then number, and the experimental adapter now requires
  that same pair. Loading or hash-pinning them for private tests does not
  resolve their redistribution terms. Phone and new_heteronym were not loaded.

Local private evaluation can proceed under these controls. This note is not
commercial redistribution clearance or a legal opinion; model/runtime/data
files must remain excluded from source and delivery bundles pending review.

## Privacy and platforms

The completed baseline, probe, speed-comparison and both boundary logs confirm the
network-denial filter loaded and its IPv4, IPv6, Unix-socket and io_uring
negative controls were denied before execution.
This demonstrates successful inference under the existing process-local
network restriction, not packet tracing or absence of attempted telemetry.
Acquisition was a separate network-enabled operation using public model URLs.

The selected native loaders use local files, with no explicit auto-download
call in the reviewed path. Missing data should fail rather than trigger a
download. The [version-specific ONNX Runtime 1.23.2 privacy note](https://github.com/microsoft/onnxruntime/blob/v1.23.2/docs/Privacy.md)
documents Windows telemetry; do not transfer Linux isolation evidence to an
unrestricted Windows build. `debug=False` is not a telemetry switch.

[Official sherpa installation documentation](https://k2-fsa.github.io/sherpa/onnx/python/install.html)
describes CPU Linux, Windows and macOS artifacts. This is platform availability,
not validation of CutCat packaging, CPU latency or privacy on those platforms.

## Bounded alternatives and next gates

- **Matcha Baker:** the [official model page](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/matcha.html)
  lists one Mandarin female voice, a roughly 72 MiB acoustic graph and 51 MiB
  Vocos graph, and explicitly warns that its training dataset is
  non-commercial-only. Do not select it as a cleared shipping voice. Its
  suggested [BSC-LT Vocos checkpoint](https://huggingface.co/BSC-LT/vocos-mel-22khz)
  declares Apache-2.0; that does not resolve the Baker restriction.
- **CosyVoice2/3:** [code revision 074ca6d](https://github.com/QwenAudio/CosyVoice/tree/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc),
  [CV2 revision eec1ae6](https://huggingface.co/FunAudioLLM/CosyVoice2-0.5B/tree/eec1ae6c79877dbd9379285cf8789c9e0879293d)
  and [CV3 revision 29e01c4](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/tree/29e01c4e8d000f4bcd70751be16fa94bf3d85a18)
  declare Apache-2.0. Full weight repositories were approximately 4.86/9.75 GB
  when reviewed. CPU fallback exists, but Torch/Torchaudio, Transformers, ONNX
  Runtime and supporting dependencies are required. Default Linux requirements
  also include CUDA/TensorRT/DeepSpeed; optional ttsfrd is documented as a Linux
  x86-64 Python-3.10 wheel. This is not a small validated three-platform package.
- The pinned CosyVoice `AutoModel()` downloads through ModelScope when a local
  directory is absent. Complete local provisioning and a cold, network-denied
  test would be required. CV2/3 examples use prompt audio; named stock-speaker
  SFT examples concern the older 300M-SFT model. Demo-audio voice rights were
  not established for shipping. No user-voice cloning is proposed. CosyVoice3's
  naturalness claims are upstream claims, not measured results here.

Next gates: listen to the completed same-text comparison and probe outputs;
assess pronunciation, prosody, punctuation loss and identifier normalization;
extend polyphone and unsupported-punctuation coverage; assess the completed Qt acceptance results and measured memory/latency cost
(see LOCAL_MELO_INTEGRATION.md for the exact final verification scope).
Preserve the no-FST baselines alongside the date-then-number probe evidence and
record normalized text. Do not silently load every available FST or replace
punctuation to make a test pass. Complete native Windows/macOS checks and
asset/runtime redistribution review before any production or bundled-voice
decision.
