# Explicit preview color conversion

## Scope and diagnosis

This change is at the decoded preview display boundary. It does not change source interpretation, grade arithmetic, proxy encoding, ProRes export, project schema26, or the reference still's immutable display-pixel contract.

The pinned Qt 6.8.3 CPU YUV conversion helper uses fixed limited-range BT.601 coefficients. On the actual Linux/offscreen H.264 test, the mapped YUV samples match independent FFmpeg decoding byte-for-byte, while Qt's RGB exactly matches those fixed coefficients despite BT.709/video-range metadata. The prior saturated fixture differs by up to 10/255 from independent BT.709 RGB. This identifies a CPU conversion defect, not an encoded proxy or reference-copy defect. It does not establish that every native Qt/GPU path behaves identically.

## Conversion contract

Supported YUV formats are YUV420P, YV12, NV12, NV21, YUV422P, UYVY and YUYV. Supported packed RGB(A) formats are copied by documented byte layout, preserving premultiplication where applicable.

The application reads the frame's explicit color matrix and range before expanding YUV to encoded SDR RGB. Supported 8-bit planar and semiplanar formats use their actual plane layout and per-plane row stride, excluding padding. BT.601 and BT.709 coefficients are distinct. YUV also requires an explicit BT.601 or BT.709 SDR transfer tag; no transfer-function conversion is performed. Limited-range Y uses 16–235 and Cb/Cr use 16–240 centered at 128; full-range uses 0–255 with chroma centered at 128. RGB is rounded and clipped once at the final 8-bit display boundary. This is not scene-linear processing, transfer-function conversion, ICC monitor compensation, gamut mapping or HDR tone mapping.

Subsampled chroma uses deterministic nearest-neighbor reconstruction. This intentionally differs from other decoders' filtered chroma at sharp color edges. Numerical matrix/range comparisons use an independently configured FFmpeg nearest-neighbor reference; ordinary decoder comparisons report patch interiors separately. Neither identical FFmpeg defaults nor physical-monitor equivalence is claimed.

Already-RGB backend frames use the existing RGB path without an additional matrix or range correction. Their prior backend conversion is not independently certified; the viewer/scopes disclosure explicitly says so. Unsupported format, unreadable planes, unknown YUV matrix/range/transfer or unsupported HDR metadata must fail rather than silently assume BT.709 or apply a global BT.601→BT.709 correction to already-clipped RGB.

## Ownership, memory and freshness

Conversion is synchronous on the existing GUI-affine frame receiver. The mapped frame is unmapped in a finally block and the returned image owns its pixels independently of the decoder. Work is chunked by rows instead of allocating full-frame floating-point RGB planes. Explicit dimension, pixel-count and mapped-buffer limits bound allocations. There is no subprocess per frame, extra decoder thread, retained frame queue or new file cache.

On this test CPU, isolated mapped-frame conversion medians were approximately 5.8 ms at 720p, 12.1 ms at 1080p and 48.3 ms at 4K (4K p95 59.5 ms). These are conversion-only measurements, not whole-GUI playback rates. The 4K path does not meet a 30fps conversion budget here; native hardware and high-resolution playback performance remain unverified. Peak process RSS in isolated runs was about 69/79/129 MB respectively, including Python/Qt and input/output buffers.

All existing generation, revision, source URL, timestamps, pending seek and monotonic acceptance guards run before conversion. Conversion failure blanks current pixels and scopes, disables capture, and displays an unavailable-color notice; it preserves the separate frozen reference. A valid later frame recovers normally. Scope measurements consume exactly the same accepted image as the viewer. Reference capture owns an immutable copy of that image and never consumes the composite comparison surface.

The queued native Qt seek fix remains unchanged. Direct synchronous Python calls to QMediaPlayer.setPosition are not reintroduced.

## Tests and remaining gaps

Analytical tests cover matrix/range, black/white endpoints, neutrals, saturated values, chroma reconstruction, padded/odd layouts, mapped lifetime and failure cases. On this Qt backend, full-range H.264 can be remapped to YUV420P with an undefined matrix. Those actual frames are rejected; this is not claimed as validated full-range H.264 playback. Analytical mapped full-range tests establish conversion math separately. The application renderer emits tagged limited-range Rec.709 proxies.

Actual H.264 tests cover both 30 and 30000/1001 fps, native mapped planes, independent RGB conversion and frame ownership. UI tests cover failed conversion, recovery, stale signals, frozen-reference separation, scopes, bilingual layouts and high DPI. Full and re-extracted package results are reported only after completed validation.

This is Linux/offscreen source validation, not Windows/macOS packaging or a calibrated-monitor claim. Native hardware decoder formats and performance still require native testing. Real white balance/chromatic adaptation, scene-linear/HDR monitoring, persistent grade galleries and automatic shot matching remain separate work.

## Competitor comparison

Resolve's official Color page describes input/timeline/output color transforms, ACES and professional scopes. CutCat's narrower improvement fixes a demonstrated SDR display conversion error and prevents unsupported conversion from masquerading as verified color. It is not equivalent to Resolve Color Management, ACES, HDR output or calibrated external monitoring. The next useful color step is independently validated white balance/color adaptation rather than calling existing warm/cool RGB offsets Kelvin white balance.

## Primary sources

- Qt 6.8.3 CPU conversion implementation: https://raw.githubusercontent.com/qt/qtmultimedia/v6.8.3/src/multimedia/video/qvideoframeconversionhelper.cpp
- Qt QVideoFrame mapping and ownership: https://doc.qt.io/qt-6.8/qvideoframe.html
- Qt format/plane/matrix/range enums: https://doc.qt.io/qt-6.8/qvideoframeformat.html
- FFmpeg scaling and point reconstruction: https://ffmpeg.org/ffmpeg-scaler.html
- FFmpeg matrix/range API: https://ffmpeg.org/doxygen/7.1/group__libsws.html
- Resolve Color/Professional Color Management: https://www.blackmagicdesign.com/products/davinciresolve/color

Checked 2026-10-23. No third-party video, paid API, user-media upload or public repository is involved.
