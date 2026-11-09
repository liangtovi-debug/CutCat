# Local TTS revalidation: dependency, privacy and license audit

Audit date: 2026-10-17 (UTC). Scope: the existing Linux x86-64 adapter,
CPython 3.12, `sherpa-onnx==1.12.26`, `sherpa-onnx-core==1.12.26`, and the
full-precision official Kokoro v1.1 Chinese/English pack. This is a pre-download,
source-and-metadata review, not a binary execution or listening result.

## Decision and limits

The pinned official wheels and model archive may be fetched for the local,
test-only experiment using original synthetic text. Verify the hashes below
before installation or extraction, and run inference through the existing
`scripts/run_network_denied.py` child-process harness. Keep dependencies, weights,
lexicons and dictionaries outside source and delivery bundles.

This review does not clear redistribution of the combined runtime/model pack,
establish native Windows/macOS behavior, or certify an unrestricted application
as telemetry-free. No private user media is required. No private repository
upload, CI run, account creation, payment, or external inference service is part
of this experiment. Only public upstream source/metadata was consulted during
the audit; no dependency archive was downloaded or executed by this audit.

## Exact model identity

The [official model documentation](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html)
links the following full-precision archive and documents 103 speakers, Chinese
and English frontends, and a 24,000 Hz sample rate:

- Archive: `kokoro-multi-lang-v1_1.tar.bz2`
- URL: https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_1.tar.bz2
- Size: `364816464` bytes
- SHA-256: `a3f4c73d043860e3fd2e5b06f36795eb81de0fc8e8de6df703245edddd87dbad`
- GitHub release asset ID: `265069737`
- Asset created: `2025-06-18T11:56:45Z`; updated: `2025-06-18T11:56:57Z`

The digest and size were independently read from the public
[official release metadata](https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/tts-models),
and the digest matches `lumen/voice_catalog.py`. The release is mutable, so a
matching filename is insufficient; stop if the downloaded bytes differ.

The existing catalog additionally records these extracted-file fingerprints:

- `model.onnx`: `acc4adc175b9d9986106cd20060329673ad5a2e12ef3c557d2d3745b694f8b38`
- `voices.bin`: `e64a5a581d8c2a350d848f51c3121657cd83aa07ed6109172177345874a7244c`

Those two fingerprints were read from the repository, not recomputed in this
pre-download audit. Recompute them after safe extraction. Do not substitute the
separate int8 archive. The complete pack also supplies local tokens, lexicons,
eSpeak data, dictionaries and FSTs required by the adapter.

## Exact Python runtime artifacts

Use these artifacts for the reviewed CPython 3.12/Linux x86-64 environment. Both
were published on 2026-02-24. PyPI supplies these SHA-256 values; its release
details report that the files were not uploaded using Trusted Publishing. A
checksum provides byte identity, not a separate signature or security guarantee.

1. [sherpa-onnx 1.12.26 release files](https://pypi.org/project/sherpa-onnx/1.12.26/#files)
   - `sherpa_onnx-1.12.26-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`
   - SHA-256: `becef26a846da675b96ce78dbfcd9904b93604f0077c496facc206228a19ef94`
2. [sherpa-onnx-core 1.12.26 release files](https://pypi.org/project/sherpa-onnx-core/1.12.26/#files)
   - `sherpa_onnx_core-1.12.26-py3-none-manylinux2014_x86_64.whl`
   - SHA-256: `7b11f1f7db36d489bb698b57207f0389512a739a69741a821d0067548c1e5624`

The [tagged setup source](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/setup.py)
pins the split core package to the same `1.12.26` version. Use the prebuilt wheels
rather than falling back to a source build that can fetch build dependencies.
Do not silently upgrade either package or add a latest `onnxruntime` package.
The adapter separately uses NumPy; test tools such as `onnx==1.18.0` are not
included in the two-artifact audit above and must be inventoried separately if
installed for additional validation.

## Inference privacy and download behavior

Local inspection of `lumen/tts.py` and `lumen/tts_worker.py` found explicit local
asset paths and no network or automatic package/model download calls. The
worker uses `OfflineTts`, `provider='cpu'`, and `debug=False`. Missing or mismatched
runtime/model files produce an error. The parent stores input text in a temporary
local request and in returned provenance; saved project provenance therefore
contains spoken text and should be handled accordingly.

The [tagged Python initializer](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/python/sherpa_onnx/__init__.py)
imports the native API and local modules. The audited
[native Kokoro model implementation](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/sherpa-onnx/csrc/offline-tts-kokoro-model.cc)
reads the configured model/bank into memory and creates an ONNX Runtime session.
It does not explicitly call `DisableTelemetryEvents`; `debug=False` is not a
telemetry control. These observations concern the selected API path, not every
CLI, example, package feature, or transitive source file.

The [v1.12.26 Linux x86-64 build recipe](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/cmake/onnxruntime-linux-x86_64.cmake)
pins ONNX Runtime `1.23.2`. Its dependency archive is
`onnxruntime-linux-x64-glibc2_17-Release-1.23.2.zip`, with SHA-256
`77ea3532dfdd8d5c66918429f7eacd80c1fea834941a14746adf3109f8e7b830`.
This is build provenance, not an instruction to fetch another runtime. Inspect
the fetched core wheel's native libraries before running to confirm the actual
bundle is consistent with that recipe.

The [version-specific ONNX Runtime 1.23.2 privacy document](https://github.com/microsoft/onnxruntime/blob/v1.23.2/docs/Privacy.md)
says telemetry is implemented only for Windows in that version and is enabled
by default in official Windows builds. Its
[generic telemetry provider](https://github.com/microsoft/onnxruntime/blob/v1.23.2/onnxruntime/core/platform/telemetry.cc)
returns disabled and has empty event methods. This supports the expected Linux
behavior but is not independent proof of the installed binary. The
[current upstream privacy document](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md)
now describes cross-platform telemetry; do not transfer the old version's
privacy expectation to newer versions.

For this experiment, the reviewed `scripts/run_network_denied.py` is an additional
process-local control. Before executing the test it rejects socket-backed
standard streams, closes inherited descriptors above 2, loads a libseccomp filter
denying socket creation/use and `io_uring`, and checks IPv4, IPv6 and Unix-socket
negative controls. Failure prevents execution. Its `sendto` rule returns
`ENOTSOCK` without executing the syscall, allowing a local PulseAudio pipe
fallback without permitting sockets. Confirm the harness's successful control
message for each run. This bounds normal network egress from the reviewed child
chain; it is not a general adversarial sandbox or a claim of packet tracing.

Downloading wheels/models is a separate network-enabled acquisition step;
inference must not need those download channels. Neither a successful isolated
test nor the filter's presence proves that unrestricted packaged releases on
other operating systems have no network behavior.

## License boundary: local testing versus distribution

- [Kokoro v1.1-zh upstream model card](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)
  declares Apache-2.0. [sherpa-onnx v1.12.26](https://github.com/k2-fsa/sherpa-onnx/blob/v1.12.26/LICENSE)
  is Apache-2.0. Preserve applicable license, copyright and NOTICE information
  when redistributing covered components; modified files need appropriate
  change notices.
- [ONNX Runtime 1.23.2](https://github.com/microsoft/onnxruntime/blob/v1.23.2/LICENSE)
  is MIT-licensed and requires retaining its copyright and permission notice
  for distributed copies/substantial portions.
- [eSpeak NG's license statement](https://github.com/espeak-ng/espeak-ng/blob/master/README.md#license-information)
  identifies GPL-3.0-or-later. Its [GPL text](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING)
  permits running the unmodified program; redistributing covered software/data
  is a separate matter involving notices and corresponding-source obligations
  as applicable. Output does not automatically become GPL merely because the
  program generated it.
- The [official pack construction workflow](https://github.com/k2-fsa/sherpa-onnx/blob/master/.github/workflows/export-kokoro.yaml)
  incorporates eSpeak data, a separate cppjieba dictionary archive, generated
  lexicons, and Chinese FST assets. Its copied top-level Apache LICENSE does not
  establish blanket rights over every incorporated asset. Exact asset provenance,
  incorporated dependencies and linkage/source obligations remain distribution
  review items.

Conclusion: local, non-redistributed synthetic-text validation can proceed under
the above controls. Keep runtime/model/data archives and extracted dependencies
out of delivered application/source bundles. Only original test text, generated
test audio, hashes, measurements and narrowly scoped validation reports may be
used as test evidence; do not treat this audit as commercial redistribution
clearance or a legal opinion.
