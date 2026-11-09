# 精确素材滑移 / Exact source slip (2026-10-20)

新增独立的「滑移源入点」与「应用素材滑移」控件，位于片段检查器的剪辑页。输入的是以工程帧为单位的绝对源入点，可用小数或分数，不是向前/向后移动量，也不是素材原始帧率的帧编号。

滑移只更换片段选用的素材内容，同时平移源入点和源出点。时间线起点、时长、速度、淡入淡出和精确源区间长度保持不变；变速片段末尾不足一帧的部分也不会被扩大。片段自带音频随其源区间一起变化，但独立音频片段、字幕和贴纸不会自动跟随，可能需要人工校对。

- 先选中时间线片段，输入新源入点，查看将使用的源区间，再点击应用。
- 新入点或出点超出当前已知素材范围时拒绝整个操作，不截断、不移动邻接片段。
- 只应用滑移值，不同时应用尚未确认的普通时间参数或速度值。
- 支持撤销、重做、保存重开，使用现有工程格式，不新增项目字段。
- 未改变原有「应用时间」语义。若同时改变源入点和显式输出时长，仍由当前速度计算新的源出点。

## Exact contract

For old source interval `[O,E)`, speed R and requested absolute origin N, slip writes `[N,N+(E-O))` with unchanged R. Thus `ceil((E-O)/R)`, timeline placement, fades and partial final source interval remain invariant. Whole-project validation is atomic. A no-op preserves undo/redo history. Existing renderer/cache fingerprints already include the exact timing map; stabilization keeps its source-wide motion analysis, selecting the newly requested source-grid transforms.

## Scope and limits

This is an explicit, single-clip, non-ripple source-selection operation. It is not linked-selection editing, caption relinking, ripple/roll/slide editing, speed curves, reverse, optical flow, or a new stabilization algorithm. Existing .25–4× speed and basic stabilization are retained, not newly claimed.

The known import limitation where rounded container duration can hide the final frame at fractional FPS is not changed. A safe general fix needs decoded timestamp/index evidence, especially for VFR and mixed audio/video ends; simply rounding duration up is unsafe. The slip range uses the existing conservative media bounds.

Local Linux source validation only. No new Windows/macOS native package, remote push, CI run, paid service, model inference, or physical sound-device acceptance is claimed. Runtime dependencies, FFmpeg, AI models and fonts are not bundled.

## Executed local checks

- New actual-media acceptance: 16 passed. Covers 24000/1001 and 30000/1001, frame-ID selection, fractional tails, actual preview/export, changed PCM content and exclusive audio tail, cache invalidation/undo reuse, and stabilized slip/split consistency.
- Final focused model/UI/localization checks: 57 passed. Covers boundaries, rational precision overflow, no-op redo preservation, speech provenance, independent staged inputs, locale/workspace switches, translated invalid range and actual Qt clicks.
- Existing model/editor/workspace/exact-TTS regressions: 146 passed.
- Actual Qt layout matrix: 16 states, zero geometry violations (Chinese/English, simple/professional, 1366×768 and 1920×1080, scale 1 and 1.5). Chinese/English scrolled-slip screenshots were visually inspected separately.
- Python bytecode compilation passed. Broad speed/audio/stabilization regression and fresh source-archive verification results are recorded in the accompanying validation report. Counts above overlap other suites; do not sum them as unique tests.

An independent review found a Chinese error allowlist omission during implementation. It was fixed and has a regression test. The initial broad run, started before that correction, observed the same issue; it is not presented as a final pass.
