# Clip volume keyframes / 片段音量关键帧

## Scope and competitor comparison / 范围与竞品对照

Premiere's [audio Properties controls](https://helpx.adobe.com/premiere/desktop/add-audio-effects/basic-audio-editing/edit-audio-using-the-properties-panel.html)
and Final Cut Pro's [volume keyframes](https://support.apple.com/en-my/guide/final-cut-pro/ver717f5914/mac)
establish the comparison target: manually vary a clip's level over time, not
only apply one scalar gain and head/tail fades. This round adds that bounded
manual capability. It does **not** claim equivalent paid-editor audio workflows.

剪辑猫本轮补齐有界的**手动片段音量关键帧**：音频检查器中打开“片段音量关键帧”，输入准确采样点及 dB 偏移，添加／替换、移动、删除或清空。不是轨道／总线自动化、录制自动化、自动 ducking、响度骑乘、曲线贝塞尔手柄、插件宿主或实时音频引擎。源波形仍是**处理前波形**，不能据此判定处理后峰值或响度。修改后需重建预览或导出；响度分析结果会变为过期。

## Exact model / 精确模型

- Schema **41** appends `Clip.audio_gain_keyframes`; positional compatibility of
  older Clip fields is preserved. Each key is exactly
  `{"sample": integer, "offset_db": number, "interpolation": "linear" | "hold"}`.
- At most **128** keys, strictly increasing and unique. No implicit sorting,
  clamping, duplicate removal, or conversion from video-frame positions on load.
- `sample` is a signed integer in **[-2^40, 2^40]**. `offset_db` is finite and
  bounded to **[-96, +24] dB**. Booleans are not numbers. Integers for offset and
  signed zero canonicalize to float / positive zero. Each key is immutable.
- Only clips with nonempty envelopes get the **2^40 output-sample budget**
  limit; historical empty-envelope timeline ranges do not change.
- Clock: 48,000 Hz **OUTPUT clip-local samples**, not source samples or video
  frames. Define `S(f) = sample_boundary(f, project_fps, 48000)`. Local `n=0`
  is global `S(clip.start)`; the visible interval is
  `[0, S(clip.end) - S(clip.start))`.
- Every point applies at its exact sample. A point's outgoing `linear` segment
  is linear in **dB**, not amplitude; `hold` lasts until the next point. At the
  next point, that next point's value applies. Outside the range, endpoint
  values hold. Empty keys mean zero offset.
- The existing `gain_db` remains the base gain. The displayed combined value
  is **base + envelope offset** before fades. There is **no automatic limiting**;
  overload may remain and must be measured and addressed deliberately.

隐藏关键点仍保存并参与插值：负采样点与片段尾部之外的点都不会被自动裁掉。检查器的关键点列表可选择这些点。准确时间输入使用有符号整数文本而非 32 位 spin box；毫秒仅作显示，不回写成舍入采样点。

## Production signal path / 生产信号路径

One shared `AudioMixBuilder` compiles preview, export, and audio analysis:

1. Existing source selection, normalization to 48 kHz stereo float32, constant
   retiming, late-offset leading silence, padding and exact output trim.
2. Unchanged legacy scalar `volume` base gain.
3. New per-sample `aeval` envelope, with explicit float32 stereo boundaries on
   both sides of its double-precision evaluator.
4. Existing clip fades, exact global sample placement, historical-slot sum,
   and master gain.

Leading silence occupies the envelope clock. `AudioMixAnchor` controls only
summation order, never the curve's time origin. Mute, disabled source audio,
no audio stream, or a compiled fully silent clip still skips the audio input;
an envelope cannot enable or unmute it.

`volume:eval=frame` is **not** used. The [FFmpeg aeval filter](https://ffmpeg.org/ffmpeg-filters.html#aeval)
evaluates each sample. Numeric coordinates/differences stay exactly representable
in its double sample counter; envelope coefficients use 17-digit serialization.
The balanced decision tree needs at most eight comparisons for 128 keys;
expression size is capped at 32,768 characters. Only trusted numeric literals
and two enumerated interpolation modes reach the expression compiler. Large
envelope graphs use the existing graph-file transport, rather than risking
Windows command-line length limits. Existing render/analysis cancellation and
timeouts remain in force.

**Empty envelope: no new filter and no new format conversion.** Frozen schema40
graph and float32 PCM comparisons are byte-exact. Preview and analysis cache
identities include the complete envelope plus
`clip-local-48k-linear-db-hold-aeval-f32-balanced-v1`.

## Edit semantics / 编辑语义

| Edit | Envelope behavior |
|---|---|
| Whole move, copy | All local samples remain unchanged; existing global sample-phase guards remain. Copy retains its existing rule clearing the historical audio-mix anchor. |
| Slip, source-range change, speed change | Keys remain in output-local sample time; they do not scale with the source or speed. |
| Right trim | Retain every key unchanged, including hidden handles. |
| Left trim, right clip of roll | Subtract `S(new.start)-S(old.start)` from every key. |
| Ordinary split, right fragment | Subtract `S(cut)-S(old.start)` from every key. Left fragment retains keys. |
| Separate source audio | Copy the full envelope to the new audio clip; retained video remains disabled. |
| Strict reattach | Adopt the audio clip's **current** envelope, replacing the retained video's inactive old envelope. |
| Undo/redo/save/load | Preserve all keys, modes and hidden handles. |

Any shift outside the signed bound rejects atomically. Retimed splits can
create independent Rubber Band instances: envelope coordinates are consistent,
but **lossless PCM or transient phase continuity is not promised**. Existing
special-state restrictions are not relaxed.

首版源插入／源覆盖对需切割的带包络片段明确拒绝整个事务；源覆盖还保守拒绝完全覆盖的受影响包络片段，避免错误保留区段。源插入的整段平移仍可通过现有生产 local-audio contract。可以先清空相应包络再执行，但软件不会擅自清空或降级。

## UI and verification / 操作与验证

Timeline markers represent key **time**, not processed waveform level. Dragging
shows a ghost without changing the project, and release commits one undo step.
Left/Right moves by one sample (Shift: ten); key repeat is one gesture. Escape,
focus loss, closure, changed selection/session and stale state cancel gestures.
Explicit exact-sample entry reaches hidden and long-duration coordinates.
Closing discards uncommitted text, not edits already applied. The dialog states
this distinction and displays saved base/offset/combined gain.

Focused tests independently capture the pre-change schema40 signal before the
new envelope, then calculate per-sample dB, float32 rounding, fades, placement,
sum and master gain. New nonconstant arithmetic permits at most one float32 ULP
for platform libm differences, **not** a temporal tolerance. Step positions,
block-partition invariance, empty-envelope graph/PCM, recombination PCM and
unchanged numbered picture frames use exact comparisons. Coverage includes
fractional FPS, positive/negative source offset, EOF silence, retiming, 128
keys, extreme signed handles, strict schema/edit rollback, cache invalidation,
and repeated/interrupted UI interactions.

Local performance measurements are evidence for this environment only. They
are not real-time playback or Windows/macOS package acceptance. No dependency
updates or new native build claims are part of this source change.
