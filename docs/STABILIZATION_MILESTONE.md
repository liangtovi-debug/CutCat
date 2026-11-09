# Local stabilization: bounded implementation

Status: both native CI jobs and re-extracted packaged runtime acceptance passed on d3c77edef4974649bda99a1a1f784a943066f592. Windows x64: 1,922 passed / seven POSIX-only skips. macOS 15 arm64: 1,928 passed / one Windows-drive-only skip. Each platform passed 414 real Qt layout states and actual bilingual stabilization UI/render checks. Linux: 1,928 passed / one Windows-only skip. No CJK test was skipped. See [the final validation record](STABILIZATION_NATIVE_VALIDATION.md).

## Pipeline and controls

Stabilization is an optional, undoable per-picture-clip operation in Edit. Old project schemas 1–7 migrate with it disabled; schema 8 stores enabled, strength (0–1), smoothing radius (1–120 source-grid frames), maximum crop zoom (1–1.5), and Crop / Black border policy. Splits copy settings independently. Changing playback speed does not restart analysis or its sampling phase. Independent captions, speech clips, and stickers retain their timeline times.

A source snapshot is hashed and analyzed locally through the external FFmpeg/libvidstab runtime. Two low-resolution, 8-bit proxy passes estimate rigid translation and rotation. These proxy pixels never enter the delivered picture. An owned float32 RGB bilinear sampler applies the correction to decoded RGB48-derived float pixels, after declared YUV matrix/range decoding and active-picture sizing, but before the owned input-transfer/gamut normalization and creative grade. Interpolation is therefore in the source-encoded RGB domain, not linear light. Letterbox padding follows the warp. The existing grade and export graph are shared by preview and export. This is not a claim of floating-point sensor decoding or HDR validation.

The source-origin project-FPS grid is analyzed once; a clip samples floor(source origin + output frame × exact rational speed). Analysis includes the whole bounded source, so trimmed and split clips use the same context. Source-wide crop uses one static zoom across all shots to avoid zoom jumps. Exceeding the configured cap fails explicitly: reduce strength, increase the cap, or select Black. No automatic strength reduction is hidden. Black mode can expose uncovered edges. Disabled or zero-strength stabilization preserves the existing picture path exactly.

Raw libvidstab debug rows are interpreted before its smoothing/zoom. A runtime synthetic test checks the convention. Independent texture/residual/cut heuristics reject unreliable motion; correction tapers toward identity near those boundaries. Source-wide static crop still applies at rejected rows. Moving subjects, repeated textures, flashes and transitions can fool these heuristics. This first implementation does not repair rolling shutter, parallax, motion blur, or severe occlusion.

## Bounds, caching, and privacy

All work runs locally, without model downloads or network fallback. Requires external FFmpeg with vidstabdetect and vidstabtransform; no FFmpeg executable is bundled. Filters and a real compatibility fixture are checked. Runtime and source hashes, geometry, FPS and algorithm settings key analysis. Original source content is rehashed before cache reuse; owned immutable snapshots also feed associated audio during compilation. Cancellation and failures do not publish partial cache entries. Temporary snapshots/cache are removed on engine close.

Initial whole-source limits: 600 seconds, 18,000 project-FPS frames, and 16 GiB source bytes. Full source copying/hashing and analysis can be costly even for a short trim; adequate local temporary disk is required. Proxies are at most 640×480. The float picture warp is CPU-based and not promised real-time at HD/4K. Estimated motion may differ across FFmpeg/libvidstab versions/platforms; shared preview/export within one cached analysis remains consistent.

FFmpeg/libvidstab stays an externally installed dependency. Existing native test providers are pinned in scripts/provision_ffmpeg.py; their licensing is not converted into permission to redistribute binaries. No new model or third-party media is bundled.

## Acceptance coverage

- Numerical SE(2), combined rotation/translation, strength/identity, half-pixel proxy mapping, bounded crop and float out-of-range/ramp tests.
- Real synthetic translation/rotation, low-texture/cut handling, source mutation/cache corruption, cancellation/timeout and geometry tests.
- Real picture split/retime equivalence, preview/export agreement, audio/timeline invariance and source integrity.
- Schema migration, undo, bilingual UI, narrow inspector layout, full prior regressions.
- Windows and macOS frozen-app runtime smoke and actual bilingual screenshots.

## Sources inspected

- FFmpeg 7.1 vidstab wrapper: https://ffmpeg.org/doxygen/7.1/vf__vidstabtransform_8c_source.html
- FFmpeg supported proxy pixel formats: https://raw.githubusercontent.com/FFmpeg/FFmpeg/n7.1/libavfilter/vidstabutils.c
- libvidstab 1.1.2 motion conversion: https://raw.githubusercontent.com/georgmartius/vid.stab/v1.1.2/src/localmotion2transform.c
- Transform convention: https://raw.githubusercontent.com/georgmartius/vid.stab/v1.1.2/src/transformtype.c

Source inspection plus actual synthetic tests, rather than filter names alone, determine the supported convention.

## 中文使用说明

在时间线选中画面片段，打开「编辑」中的「画面稳定」，启用后调整强度、平滑范围及边缘处理，再点击应用。可撤销、复位或关闭，并保留源素材。此版本分析整段源文件，不只是所选片段：最长 600 秒、最多 18,000 个工程帧，单个源文件最多 16 GiB；有效画面需至少 64×64。分析和变换使用本机 CPU，并需要临时磁盘空间。首次预览会先分析，长素材可能明显等待；可取消。

「裁切」在整个源文件上采用同一个固定放大比例。若所需比例超过上限，会明确报错；可降低强度、提高上限或选择「黑边」。低纹理、遮挡、闪光及大主体运动可能导致识别失误，务必查看原图对比与导出结果。该功能暂不修复果冻效应、运动模糊或视差。变速、字幕、配音和贴纸的独立时间位置不因启用防抖而改变。

Known pre-existing timing limitation observed in acceptance fixtures: some containers round duration metadata to milliseconds; the current media-range validator can conservatively expose one fewer final frame at fractional FPS (for example 51 of 52). This milestone preserves exact sampling within the accepted selected range but does not claim to fix source-duration discovery. A future import/indexing change should establish the decodable last-frame boundary rather than infer it solely from rounded duration metadata.
