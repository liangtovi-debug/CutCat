# Local subtitle speech and voice browser (experimental)

The optional adapter runs `sherpa-onnx==1.12.26` and matching
`sherpa-onnx-core==1.12.26` on CPU. Install these explicitly into the application's
Python environment. The running app never downloads models, runtimes or packages.
No model, optional runtime, eSpeak data, dictionaries or lexicons are included in
source/delivery ZIPs or default frozen builds.

## Supported, explicitly versioned packs

Only the full-precision official packs pinned below are supported. Kokoro uses
verified archives; Melo uses individually verified files at a fixed revision.
Separate int8 variants have different fingerprints and are not accepted.

- **Kokoro v1.0:** the existing 53 exposed IDs 0–52 are preserved, including 28
  English-origin voices and eight Chinese-origin voices. The actual pinned bank
  has 54 entries; the final `em_santa` entry is not exposed by this compatibility
  catalog. Upstream's older documentation lists 53, newer samples list 54.
- **Kokoro v1.1:** all 103 documented IDs 0–102 are exposed: three English-origin
  voices and 100 Chinese-origin voices. The exact upstream names include gaps in
  their numeric suffixes. For example, ID 11 is `zf_017`, not `zf_009`.
- **Melo Chinese-English (`melo-zh_en-v1`, experimental):** one embedded voice,
  application voice ID `0`, at 44.1 kHz. Explicitly select this pack and verify
  its existing local files to opt in. It does not replace either Kokoro pack or
  change the legacy default. Listening quality is not certified; actual Qt
  acceptance and final integration test status are pending in this snapshot.
- The listed adapters provide English and Chinese text frontends. **Origin is not a
  language restriction.** Selecting “English text” must not hide Chinese-origin
  voices, or vice versa. This is engine support, not a guarantee that every
  voice/language pairing has equal accent, quality or intelligibility.
- These are model voice identities. No celebrity imitation, voice cloning,
  speaker personality, profession, age or emotion categories are claimed.

Official sources:

- [v1.0 pack](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_0.tar.bz2)
- [v1.1 pack](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2)
- [v1.0 speaker map](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_0.html)
- [v1.1 speaker map and API example](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html)
- [General pack instructions and legacy v1.0 map](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html)
- [v1.1 export scripts](https://github.com/k2-fsa/sherpa-onnx/tree/master/scripts/kokoro/v1.1-zh)
- [Melo converted pack, fixed revision a0d5c6a](https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/tree/a0d5c6a264c0ef92d70d8661d8cc502d79627cd6)
- [Melo file URLs, sizes and SHA-256 manifest](MELO_MODEL_MANIFEST.json)

Choose the correct version and the existing extracted pack, then click **Verify
local pack and runtime**. The voice browser, audition and generation controls
remain disabled until verification succeeds. Changing the folder or version
invalidates verification and stops/clears the audition. Voice IDs and favorites
are scoped to their pack version; they are never migrated by numeric ID.

### Kokoro pack requirements

Kokoro verification pins the exact official `model.onnx` and `voices.bin` SHA-256
fingerprints in `lumen/voice_catalog.py`. The observed model metadata's complete
`speaker_names` mapping, `n_speakers`, `style_dim`, sample rate and source are
checked by the real-pack verification script. These fingerprints therefore bind
names to actual model/bank bytes, rather than trusting a folder name or count.
A future upstream asset update requires an explicit revalidation/catalog change.
The ONNX metadata `version=2` is its export format; it is not Kokoro pack v2.

The complete local Kokoro pack must contain `model.onnx`, `voices.bin`, `tokens.txt`,
English/Chinese lexicons, `espeak-ng-data`, `dict`, and Chinese phone/date/number
FSTs. The official v1.1 minimal API example omits FSTs; this adapter enables the
three real FSTs present in the verified archive for compatibility with v1.0 number,
date and phone normalization. All pack files are hashed for provenance and
checked again before publication. A digest identifies bytes; it does not grant
license rights or certify arbitrary changed dictionary content.

### Melo opt-in pack requirements

Select **Melo Chinese-English · experimental** in the pack selector, choose the
existing local folder, then use **Verify local pack and runtime**. The adapter
requires these five files, each checked against its pinned SHA-256:

- `model.onnx`
- `tokens.txt`
- `lexicon.txt`
- `date.fst`
- `number.fst`

Exact URLs, byte sizes and hashes are in [MELO_MODEL_MANIFEST.json](MELO_MODEL_MANIFEST.json),
copied unchanged from the verified acquisition record at revision
`a0d5c6a264c0ef92d70d8661d8cc502d79627cd6`. It also records LICENSE/README and
`phone.fst`/`new_heteronym.fst`; the latter two are not loaded. Retain license
notices, but do not treat the manifest or top-level MIT notice as redistribution
clearance for the normalization assets. This remains a private-test setup.

Melo does not require `voices.bin`, `espeak-ng-data`, `dict`, a separate vocoder,
Torch or Python G2P packages. Its voice is embedded in `model.onnx`. The adapter
uses **CPU with exactly two threads**, `max_num_sentences=0` (all native text
segments in one inference batch), and a **300 Unicode-character maximum** for
each audition or saved take. Longer input fails before asset access or native
worker construction; no automatic truncation or splitting occurs. Completed
no-FST 300-character runs peaked at 1,770,896 KiB RSS at speed `1` and
**3,508,976 KiB (about 3.35 GiB) at speed `0.5`**. The slower run needed
166.6023 s to generate 89.8728 s of speech. The roughly 170 MB model file and
300-character limit are not promises of low memory usage or real-time synthesis.

The app sends the original reading text unchanged and preserves it in
provenance; captions are not rewritten. Inside the native frontend, the
configured normalization order is **`date.fst` then `number.fst`**. The raw
no-FST baseline differs from this adapter configuration. If native diagnostics
report ignored OOV characters, including unsupported punctuation, the app
refuses to publish audio; change the reading text explicitly or choose another
pack. A no-OOV result still does not certify pronunciation. See the measured
probe results and unresolved asset licenses in
[LOCAL_MANDARIN_BACKEND_EVALUATION.md](LOCAL_MANDARIN_BACKEND_EVALUATION.md).

## Voice browser and auditions

The dropdown shows actual upstream names, origins and IDs. Origin and text-language
filters are separate. Favorites are local `QSettings` entries keyed by version
and ID. They do not modify the project or transmit information.

Enter audition text, or explicitly copy the current subtitle text, and choose
**Generate and play audition**. It synthesizes the complete entered text through
the actual local TTS adapter into a fresh file in the application's owned preview
cache. It never adds media, captions or timeline clips. You can replay or stop it;
canceling generation kills/reaps the isolated native worker. Errors are visible
and retry is supported. A changed voice, changed pack, replacement audition or
closing the window removes owned audition files. Playback uses a dedicated local
Qt audio player and pauses timeline playback to avoid competing audio.

**Generate voice to new WAV…** is the explicit existing saved-take action. It
reads the selected subtitle's current text field and requires a new durable WAV
outside the cache, the pack and all project sources. Earlier takes are preserved.
The existing insertion workflow keeps full audio: overlap blocks insertion, and
a voice longer than its caption requires explicit confirmation to extend beyond
that caption. There is no automatic truncation or speed adjustment.

## API and safety boundaries

`TTSConfig(model_dir, voice_id=0, speed=1.0, threads=2, pack_version='v1.0', silence_scale=0.2)`
retains old positional argument compatibility. `SUPPORTED_VOICES` remains the
legacy 53-entry v1.0 export. New code uses `voice_catalog.get_pack/get_voice`.

`synthesize(text, config, output_path, progress=None, cancel=None)` returns frozen
path/duration/sample-rate/provenance data. Output is mono signed 16-bit PCM:
24 kHz for Kokoro, 44.1 kHz for Melo. Speed 0.5–2.0 uses each backend's native
control; the same numeric value does not imply the same speaking rate across
backends. Kokoro allows 1–4 CPU threads; Melo requires exactly 2. Progress reports start and completion,
not an invented native-inference percentage. A shared resource gate permits only
one heavy local AI inference at a time, including auditions and saved takes.

The engine field records `sherpa-onnx/Kokoro-v1.0`,
`sherpa-onnx/Kokoro-v1.1`, or `sherpa-onnx/MeloTTS-melo-zh_en-v1`; voice ID is
immutable within that namespace. Melo's single voice has ID `0`; its voice
hash equals the model hash because the actual voice is embedded in that model,
not in a separate voice bank. Project
schema 11 introduced pause preservation; older projects default to 0.2. Schema 12
also records the exact generated PCM sample count. Older binaries cannot open
schema 12 projects. Provenance also captures exact input text, runtime
version, model/bank SHA-256, complete asset digest, speed, pause preservation, sample rate, output
SHA-256 and UTC timestamp. Source caption ID/timing are snapshotted on insertion.
Editing the caption later never rewrites a saved take's provenance or audio.
Project files therefore contain the spoken text.

New generated takes use the exact sample-count/sample-rate fraction when sizing
their timeline clips. This avoids a spurious extra frame at an exact frame
boundary (for example, 56,000 samples at 24 kHz are exactly 70 frames at 30 fps),
while retaining one extra frame whenever even one real sample crosses the
boundary. Existing takes without a recorded count retain their saved timing;
loading a project does not infer counts, rewrite captions, or resize old clips.
This timing correction does not alter synthesized PCM, punctuation handling,
sentence pauses, pronunciation or encoding.

Pack digest is SHA-256 of UTF-8 JSON sorted relative-path / SHA-256 pairs, compact
separators, `ensure_ascii=False`. Symlinks and special files (including FIFOs and
devices) are refused; regular-file identity/size is checked around hashing, using
nonblocking/no-follow opens where supported. Limits: 10,000 files/directories,
2 GiB pack, 100,000 text characters for Kokoro or 300 for Melo, 30 minutes native generation, 8 MiB worker
diagnostics and 512 MiB output. A limit fails visibly; it never truncates speech.
Cancellation is polled every 100 ms during native generation and between file
reads. The process is killed/reaped and incomplete outputs are removed.
Publication is an atomic non-overwriting hard link; unsupported filesystems return
an error rather than an unsafe overwrite fallback.

## Component licenses and distribution boundary

Do not interpret any pack's top-level license as a blanket license for every
component. **No commercial redistribution clearance is claimed.**

- [Kokoro-82M v1.0 weights](https://huggingface.co/hexgrad/Kokoro-82M) and
  [v1.1-zh weights](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh): upstream
  Apache-2.0 declarations; the downloaded packs carry an Apache-2.0 LICENSE.
- [Melo original Chinese weights](https://huggingface.co/myshell-ai/MeloTTS-Chinese/blob/af5d207a364ea4208c6f589c89f57f88414bdd16/README.md)
  declare MIT; the [pinned converted pack LICENSE](https://huggingface.co/csukuangfj/vits-melo-tts-zh_en/blob/a0d5c6a264c0ef92d70d8661d8cc502d79627cd6/LICENSE)
  carries MyShell's MIT notice. Exact FST build provenance and redistribution
  terms remain unresolved. Loading date/number FSTs in private tests does not
  clear them for shipping.
- [sherpa-onnx code](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE):
  Apache-2.0.
- [eSpeak NG](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING):
  GPL-3.0-or-later component used by the Kokoro path. Melo does not load its
  data; that does not remove obligations for eSpeak components present in a
  distributed runtime bundle. Preserve relevant notices and review source/data
  obligations and incorporated dependencies.
- [ONNX Runtime](https://github.com/microsoft/onnxruntime/blob/main/LICENSE): MIT;
  preserve its notices if redistributed.
- Chinese dictionaries, generated lexicons and normalization FSTs need their own
  provenance/license review. A top-level archive LICENSE does not establish
  rights for every incorporated asset.

Test-only archives, extracted weights, dictionaries, optional runtime and its
native dependencies stay outside delivery/source bundles. This is an optional
source-install experiment. Windows/macOS packaged runtime and audio-device
behavior still require native testing. A missing or mismatched runtime yields a
setup error without fetching anything.

## Reproducible validation

`python scripts/validate_voice_catalog.py /path/to/test-cache` is an explicit,
offline-only **Kokoro** verification command. It requires the already extracted
two official Kokoro packs, pinned runtime and test-only `onnx==1.18.0`. It never downloads or installs.
It checks metadata-to-catalog order, tensor shape/bank byte count, full source
hashes, all exposed voice identities with a common bilingual utterance, distinct
non-silent output signals, selected English/Chinese samples through the actual
isolated app adapter, and cancellation cleanup. Results go under the supplied
test cache; curated reports can be copied into `artifacts/voice-browser-validation`.
No human-listening or native Windows/macOS claim is implied by Linux signal tests.

Melo's completed native Linux measurements, raw baseline versus date/number
probe distinction, duration-comparison controls and 300-character resource
sample are documented in
[LOCAL_MANDARIN_BACKEND_EVALUATION.md](LOCAL_MANDARIN_BACKEND_EVALUATION.md).
These do not establish actual Qt acceptance or final integration-suite success;
both remain pending at this documentation snapshot. The optional model/data
and test audio remain outside source/delivery bundles.

Historical v1.0 evidence remains in `artifacts/tts-validation/`: English/Chinese
samples, 1.5× speed, source-license manifest, cancellation and independent local
whisper.cpp transcription. The prior English ASR words matched; Chinese ASR had a
character substitution. Those are secondary intelligibility checks, not a claim
of perfect speech. Runtime network absence was not independently packet-traced;
static adapter/worker inspection finds no network or download calls.
