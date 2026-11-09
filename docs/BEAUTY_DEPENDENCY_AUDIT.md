# CutCat FaceLandmarker dependency evidence ledger

Audit date: 2026-10-16 UTC. Scope: source-backed research for a local-only beauty prototype using Python 3.12. Production constraints supplied for this audit: NumPy 2.2.4 and PySide6 6.8.3.

## Decision

Do not install or execute a MediaPipe runtime in production on this evidence. The verified-offline runtime gate remains unresolved. Continue with an explicitly supplied-landmark prototype until an authorized runtime can be built or isolated and tested.

MediaPipe 0.10.21 is a possible later test-only candidate because it has Python 3.12 wheels across the required desktop families, including Intel macOS. It requires NumPy below 2 and therefore cannot be added to the stated production dependency set unchanged. A separate environment with a fully resolved and pinned dependency set would be necessary. NumPy 1.26.4 is a proposed test pin, not an installed or tested stack in this audit.

No package, model, fixture image, or video was downloaded to the workspace. No installation, import, inference, binary inspection, build, CI run, source-code edit, or network-traffic capture was performed. Only this requested evidence document was written.

## Runtime availability and dependency constraints

### General support

Google's Python setup page lists Windows, Mac, and Linux and Python 3.9 or later. This establishes documented platform support, not successful installation or execution on a particular machine. Source inspected on 2026-10-16; page reports last update 2026-08-17.

- https://developers.google.com/edge/mediapipe/solutions/setup_python

### MediaPipe 0.10.21

Release date: 2025-02-06. Official PyPI metadata publishes CPython 3.12 wheels for Windows x86-64, Linux x86-64 with glibc 2.28 or later, macOS 11 or later x86-64, and macOS 11 or later universal2. No Linux ARM64 or Windows ARM64 wheel is listed for this release.

Requires-Dist includes `numpy<2`, `protobuf<5,>=4.25.3`, `attrs>=19.1.0`, `flatbuffers>=2.0`, `sounddevice>=0.4.4`, plus `absl-py`, `jax`, `jaxlib`, `matplotlib`, `opencv-contrib-python`, and `sentencepiece`. Transitive versions were not resolved. A simple MediaPipe pin is not a complete reproducible environment.

Published SHA-256 values for CPython 3.12 wheels, copied from PyPI metadata and not independently recalculated:

- `mediapipe-0.10.21-cp312-cp312-win_amd64.whl`: `d07b5e69308411161286ea4e48be2ea3e9ab62745143fcb0cb4acced0517e341`
- `mediapipe-0.10.21-cp312-cp312-manylinux_2_28_x86_64.whl`: `956eb1ebc275c629e61b085b2cab89c3a5b9e93bad1bb107348d98dafb5a4bb5`
- `mediapipe-0.10.21-cp312-cp312-macosx_11_0_x86_64.whl`: `96bf0dafa9851c74b1f930090193f23c257ccf4bb1bdebbd2ca3a02397011c0e`
- `mediapipe-0.10.21-cp312-cp312-macosx_11_0_universal2.whl`: `a65508f8c0e28a73f519c8a717f95949b9d5b2fb3bf4a632b181cdb71224d62e`

Sources inspected on 2026-10-16:

- https://pypi.org/project/mediapipe/0.10.21/
- https://pypi.org/pypi/mediapipe/0.10.21/json

### MediaPipe 1.1.1

Release date: 2026-10-14. Official PyPI metadata lists Python 3.12 among classifiers and publishes Python 3 wheels for Windows x86-64/ARM64, Linux x86-64/ARM64 with glibc 2.28 or later, and macOS 11 or later ARM64. No Intel macOS wheel is listed.

Requires-Dist lists NumPy without a version bound, along with `absl-py~=2.3`, `certifi`, `sounddevice~=0.5`, `flatbuffers~=25.9`, `opencv-contrib-python`, and `matplotlib`. The absence of a NumPy upper bound is metadata-level compatibility only. NumPy 2.2.4 ABI compatibility, PySide6 coexistence, packaging, and inference were not tested.

- https://pypi.org/project/mediapipe/1.1.1/
- https://pypi.org/pypi/mediapipe/1.1.1/json

### MediaPipe 0.10.32

Release date: 2026-01-22. Metadata also lists NumPy without a version bound, with Windows x86-64, Linux x86-64/glibc 2.28+, and macOS 11+/ARM64 wheels. No Intel macOS wheel is listed. Its older publication date and absence of a privacy paragraph in its release metadata do not establish telemetry-free operation.

- https://pypi.org/project/mediapipe/0.10.32/
- https://pypi.org/pypi/mediapipe/0.10.32/json

## Telemetry and offline evidence

The official 1.1.1 README contains a privacy notice dated 2026-06-05: input processing occurs on-device, while performance and utilization metrics are sent to Google. It also describes the app developer's consent responsibility. Local inference therefore does not by itself establish zero network egress.

- https://raw.githubusercontent.com/google-ai-edge/mediapipe/v1.1.1/README.md

In official repository issue 6291, the collaborator reply dated 2026-07-06 states that there is no official API to disable collection. It identifies building the SDK from source or blocking host access as alternatives. The issue author's observation that 0.10.21 did not show connections is not treated here as an independent verification of the wheel.

- https://github.com/google-ai-edge/mediapipe/issues/6291

The following v0.10.21 implementation files were inspected as a bounded source-path review:

- Python model path/buffer configuration: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/python/core/base_options.py
- Python/native TaskRunner entry points: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/python/core/pybind/task_runner.cc
- Native graph initialization and processing: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/cc/core/task_runner.cc
- TaskRunner build dependencies: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/cc/core/BUILD
- Local file handling with open/read/mmap: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/cc/core/external_file_handler.cc
- Model resource creation: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/cc/core/model_resources.cc
- FaceLandmarker graph and bundle members: https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/mediapipe/tasks/cc/vision/face_landmarker/face_landmarker_graph.cc

No uploader was visible in the inspected call chain. This does not certify the complete transitive source tree, build configuration, or PyPI native binaries. In particular, no binary-to-source attestation, reproducible build comparison, symbol scan, or runtime traffic test was performed. A Python-level socket patch would not provide a sufficient guarantee against native networking.

No supported and verified zero-egress execution environment has been established by this audit. No runtime is approved as fully offline on these findings.

## Model identity and licensing evidence

### Versioned official model reference

The official Google AI Edge sample references this exact model location:

https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

This is the float16 version-1 reference, preferable for reproducibility to the mutable `latest` reference. Its presence in the official sample was verified; model bytes and current object headers were not retrieved.

- https://github.com/google-ai-edge/mediapipe-samples-web/blob/main/src/tasks/face-landmarker.ts

**Bundle SHA-256: unknown and not independently verified.** No authoritative published checksum or signed artifact manifest was located. The attempted public storage-metadata lookup returned an error and supplied no usable metadata. No third-party checksum is adopted in this ledger. A later authorized acquisition must calculate and retain the hash from the official bytes.

### License evidence

The MediaPipe v0.10.21 repository license is Apache License 2.0:

- https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.21/LICENSE

The official FaceLandmarker guide links three model cards, each explicitly stating Apache License 2.0:

- BlazeFace short-range, card dated 2021-06-09: https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20(Short%20Range).pdf
- FaceMesh V2, card dated 2022-09-15: https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf
- Blendshape V2, card dated 2022-11-11: https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf
- Guide, reporting last update 2026-10-09: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker

Official repository issue 6355 asks about the exact float16/version-1 FaceLandmarker bundle, embedded weights, and geometry metadata. The collaborator's 2026-10-05 reply states that source and models are Apache-2.0 unless otherwise noted, points to the model cards, and provides no further artifact-specific provenance. This is additional publisher-level licensing evidence, but not a byte-bound manifest or complete redistribution audit.

- https://github.com/google-ai-edge/mediapipe/issues/6355

### Remaining component-level gaps

The inspected graph expects `face_detector.tflite`, `face_landmarks_detector.tflite`, `face_blendshapes.tflite`, and, when facial transformation output is requested, `geometry_pipeline_metadata_landmarks.binarypb`. These are source expectations, not an inspected inventory of downloaded model bytes.

The guide's bundle table lists a 192 by 192 detector input; the linked BlazeFace short-range card describes a 128 by 128 input. This discrepancy is real in the inspected documents. Exact detector-to-card mapping and geometry-metadata provenance remain unresolved. No artifact-specific license/NOTICE inventory or training-data rights audit has been completed.

FaceMesh output is landmark geometry. Neither a face oval nor a polygon with eye/lip exclusions is verified skin segmentation. Occlusion, hair, background leakage, temporal behavior, and geometry accuracy require separate validation.

## Bounded natural-face fixture proposal

Scikit-image's `astronaut.png` is a 512 by 512 RGB portrait. Its upstream documentation identifies the NASA Great Images source and states public-domain status. This avoids an unlicensed model-demo image, but does not remove all publicity or endorsement considerations.

Provenance sources inspected on 2026-10-16:

- https://scikit-image.org/docs/0.25.x/api/skimage.data.html
- https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/_fetchers.py
- Original source referenced by the upstream fetcher: https://flic.kr/p/r9qvLn

Versioned fixture location derived from the upstream fetcher's documented path construction; bytes were not fetched:

https://github.com/scikit-image/scikit-image/raw/v0.25.2/skimage/data/astronaut.png

The official scikit-image registry publishes this SHA-256:

`88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5`

- https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/_registry.py

NASA's media guidance, reporting last update 2026-08-13, warns that identifiable-person commercial use can implicate publicity/privacy rights and that promotional uses must not imply endorsement. Recommendation: confine the fixture and altered outputs to private local test evidence; exclude them from installers, public product demos, and marketing. This is a cautious test-scope recommendation, not legal clearance for redistribution.

- https://www.nasa.gov/nasa-brand-center/images-and-media/

Suggested future test: one still plus a short deterministic affine-motion sequence, with no identity recognition. Label the latter synthetic motion derived from a still. It can test landmark integration and temporal plumbing; it cannot establish performance on natural head turns, expression changes, real occlusions, multiple people, or a representative range of skin tones.

## Unperformed gates before integration

1. Establish an authorized no-egress execution mechanism or an auditable source build. Do not infer offline behavior from model-local paths or older version numbers.
2. Resolve and hash-pin all transitive dependencies in a separate test environment, without downgrading production NumPy.
3. Acquire the versioned model from the official source only when authorized; record SHA-256, byte count, response metadata, component inventory, and applicable notices.
4. Acquire and verify only the bounded fixture; retain provenance and test-only restrictions.
5. Test import, construction, inference, closure, and delayed runtime behavior under enforced no-egress conditions. Record attempted outbound activity, including native code.
6. Run separate Windows and macOS execution/packaging checks. Wheel availability is not a passing platform test.

The research stops here. No unverified download, installation, inference, or production integration is implied by this document.
