# CutCat offline face-runtime audit

Reviewed 2026-10-16. Read-only official documentation/source review; no model/package installation, model download, execution, CI, push, or user-media transfer. Source inspection is not a native binary network test.

## Decision

OpenCV 4.x DNN + YuNet is a credible local face detector, but its five points cannot supply a safe skin-only beauty mask. Do not enable automatic skin beautification from an elliptical/five-point mask. ONNX Runtime is a possible inference engine for a future rights-cleared dense model; engine licensing does not clear model weights or training-data restrictions.

## Exact existing optional pin: ONNX Runtime 1.22.1

- [Pinned privacy specification](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/docs/Privacy.md): official telemetry is implemented only on Windows in this release, using platform TraceLogging. The document describes possible OS-managed sending to Microsoft subject to user consent. This is not evidence of a bundled cross-platform HTTP uploader, nor evidence that user media is uploaded.
- [Pinned POSIX environment](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/onnxruntime/core/platform/posix/env.cc), lines 543–553, returns a plain Telemetry member. [Base telemetry implementation](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/onnxruntime/core/platform/telemetry.cc) reports disabled and implements event methods as no-ops. This supports no ORT platform telemetry on macOS/Linux for this version, within the inspected scope.
- [Pinned Python binding](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/onnxruntime/python/onnxruntime_pybind_state.cc), lines 1447–1452, exposes disable_telemetry_events() and directly invokes the platform provider's DisableTelemetryEvents().
- [Pinned Windows implementation](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/onnxruntime/core/platform/windows/telemetry.cc): enabled_ starts true, constructor registers an ETW provider, DisableTelemetryEvents() sets enabled_ false, and event emitters check this flag. Registration itself is distinct from an event or network transmission. The ETW enable callback also writes enabled_; therefore do not claim a one-time Python call permanently defeats every external trace-session change.
- ORT_DISABLE_TELEMETRY is not documented in 1.22.1 Privacy.md and is not read in the audited Windows/base telemetry implementations. It must not be claimed as a verified 1.22.1 control. The supported control found here is disable_telemetry_events(). Call it immediately after import and before constructing sessions or initiating inference, if retaining official Windows binaries.
- [Pinned Environment initialization](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/onnxruntime/core/session/environment.cc), lines 293–295, invokes LogProcessInfo(). The Python source has a platform Env static initializer. This review does not prove every import/initialization path is event-free before Python can call the opt-out. Do not convert the supported API into a strict zero-event guarantee without native testing.
- [Pinned CMake options](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.22.1/cmake/CMakeLists.txt), line 116, defines onnxruntime_USE_TELEMETRY default OFF. A controlled source build with this explicitly OFF is the stronger route to investigate for a strict requirement; verify final generated build config and binary behavior. The newer --no_telemetry build-driver flag has not been verified for this old version.

### Do not apply current documentation retroactively

[Current main Privacy.md](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md) describes newer native cross-platform 1DS telemetry, a pre-initialization ORT_DISABLE_TELEMETRY=1 opt-out, and --no_telemetry builds. It warns that API disabling can occur after an initialization event, and that the environment switch does not affect legacy Windows TraceLogging. Those claims describe current source and must not be assigned blindly to CutCat's 1.22.1 pin. Any upgrade requires a new exact-version audit.

## OpenCV YuNet pathway

- [Zoo YuNet README](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md) explicitly assigns MIT to all files in its directory and traces the model to ShiqiYu/libfacedetection.train commit a61a428929148171b488f024b5d6774f93cdbc13. [Directory license](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE). Preserve notices and pin model bytes/checksum before packaging; model not retrieved here.
- Choose face_detection_yunet_2023mar.onnx for an OpenCV 4.x prototype, explicitly selecting DNN_BACKEND_OPENCV and DNN_TARGET_CPU. The current Zoo default has changed to a dynamic-shape 2026 export for OpenCV 5.x's ORT engine; avoid accidentally broadening the runtime through a latest-version upgrade.
- [Official wrapper](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/yunet.py) loads a supplied model path and calls detect(image). Use a packaged local path, no download helper. This wrapper inspection alone does not certify all native dependencies as telemetry-free.
- [Detector API](https://docs.opencv.org/4.13.0/df/d20/classcv_1_1FaceDetectorYN.html): five points represent eyes, nose and mouth corners, with bounding box and score. No eye/lip outlines, hair, occlusion, or skin segmentation. Dense landmarks alone would still not identify hair and occlusions reliably.
- [OpenCV-Python 4.12.0.88 files](https://pypi.org/project/opencv-python/4.12.0.88/) list Python-3.12-compatible abi3 Windows x64 and macOS 13+ Intel/ARM64 wheels. [Tag 88 setup.py](https://raw.githubusercontent.com/opencv/opencv-python/88/setup.py) declares NumPy >=2,<2.3 for Python >=3.9, admitting the app's NumPy 2.2.4. Metadata fit is not a tested app bundle. Prefer one headless cv2 distribution if the app owns its GUI; never install multiple cv2 package flavors together.

## Dense face model licensing gap

- [yakhyo/face-parsing](https://github.com/yakhyo/face-parsing) offers BiSeNet ONNX export/inference, MIT project code, and publisher-trained CelebAMask-HQ weights. [Dataset publisher agreement](https://github.com/switchablenorms/CelebAMask-HQ) restricts commercial use and derived data. MIT code does not resolve pretrained weight distribution rights; obtain explicit clearance or use independently rights-cleared training data/model. This is a licensing uncertainty, not a categorical legal determination about all derived weights.
- [InsightFace publisher policy](https://github.com/deepinsight/insightface#license) likewise distinguishes MIT code from noncommercial-research training data and pretrained models, including manually and automatically downloaded models. It is not a ready commercial licensing substitute.
- No distribution-cleared dense skin segmentation model was established by this bounded review. Do not add a third-party ONNX conversion merely because its repository says MIT; trace original weights, training source, model grant, and conversion provenance.

## Next validation gate

Keep beauty opt-in/disabled by default and fail closed when uncertain. Detector diagnostics and manually reviewable masks can progress separately. Before shipping automatic skin masks, require rights-cleared weight provenance, pinned checksums, both-OS cold-start/import/inference native network/ETW validation, packaging smoke tests, and mask-quality checks covering glasses, facial hair, fringe, hands, profiles, low light, varied skin tones, movement and temporal flicker. CPU-only, local models, no remote custom operators or model downloads keep the initial scope bounded.
