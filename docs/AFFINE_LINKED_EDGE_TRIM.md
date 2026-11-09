> Subsequent reconstructed candidate (2026-10-30): [exact affine-linked through-split](AFFINE_LINKED_PAIR_SPLIT.md) supports qualified 1× v1/v2 maps with independent residual tails. Older split refusal statements below describe the earlier milestone; joint roll remains strict-v1-only. No historical pass claim transfers.

# Affine-linked paired-edge trim / 保留 J/L 偏移的配对边缘修剪

Bounded development candidate, 2026-10-29. Schema **43**, native version,
renderer/audio compilers and dependencies are unchanged. The baseline is private
main `43022004f40fb6d8fede527121a6f16c52fd2b9f`, 1,054 source files, identity
`733a21df7198d071103c74f94565d249291c1bfcf357a537ddfa1b5cb04e1c2e`.
This document is not a release, full-source acceptance or native-package claim.

## Use the existing timeline gesture

Select exactly one complete same-source linked V/A pair. Drag the left or right
edge of either member: both corresponding edges move by the same signed integer
**project-timeline frame delta**, preserving their existing J/L offsets. This
works for strict-v1 and affine-v2 pairs; it does not align unequal edges to one
absolute cut. Both tracks must be editable. Extra clips, multiple pairs or
Shift-selection do not silently become a single-pair trim.

Both outlines preview without editing the project. The compact label starts
with canonical **V duration, A duration and common signed delta**, regardless
of the active role or clip order. Full tooltip/accessibility text explains offset
preservation and edge-anchored fades; long painted labels intentionally elide.
Release revalidates and commits once. Escape, focus/window loss, hide, lost mouse
button and stale selection/role/editor/project/revision/membership/zoom contexts
cancel the disposable gesture. Existing shortcut/text/IME/modal policies remain;
this adds no dialog, command, shortcut, asynchronous job or universal playback
lock to the timeline gesture.

只选中一对完整的关联音画，再拖动任一成员的对应边缘。双方采用同一个时间线帧
位移，各自保留原有 J/L 起止偏移与时长差，不会强行对齐。预览分别显示 V、A
时长和共同位移；释放时一次提交、一次撤销。取消、无位移或拒绝均不新增历史记录。

## Exact own-role contract

For each role `i`, old timeline `[t_i,t_i+D_i)`, source map `[O_i,E_i)` and
common exact rational speed `r`, apply integer delta `d` independently:

| Edge | New timeline | New source map |
|---|---|---|
| Left | `[t_i+d,t_i+D_i)` | `[O_i+d*r,E_i)` |
| Right | `[t_i,t_i+D_i+d)` | `[O_i,E_i+d*r)` |

The common affine intercept `O_i-t_i*r`, each independent residual tail
`D_i*r-(E_i-O_i)`, A-minus-V start/end offsets and duration difference remain
unchanged. Never reconstruct a source endpoint from rounded duration. Each
role's bounds are checked; trim does not invent a shared-overlap requirement.
V2 stays explicitly v2 even when current geometry happens to match v1.

At 1×, V `[12,167/4)` at timeline `[5,35)` and A `[10,87/2)` at `[3,37)` have
independent tails `1/4` and `1/2`. Left `d=+1` gives V `[13,167/4)` at `[6,35)`
and A `[11,87/2)` at `[4,37)`, preserving both tails and offsets.

Left trim shifts every stored motion/window key by `-d`, including hidden keys.
Each role's gain keys shift by its own `-[S(t_i+d)-S(t_i)]`, with
`S(f)=round_half_up(f*48000/fps)`. At `30000/1001` fps, `d=1`, V start 5 and A
start 3 yield **1602 versus 1601 samples**. Neither `S(d)` nor the V-role delta
may substitute for the A-role delta. Right trim leaves local key coordinates
unchanged. Existing schema restrictions on role-specific keys still apply.

Clip/link/media IDs, tracks, source-audio ownership, historical mixing anchors,
clip order and unrelated objects stay fixed. Video source audio stays suppressed.
Detached qualification preserves live raw values, identities and history on
refusal/no-op; model preview is owner-thread guarded. Valid model zero delta
also remains a no-op on locked tracks, although locked tracks cannot start the
UI gesture. Real edits use the existing candidate fence and one transaction.

## Audio changes and refusal boundaries

Fade lengths stay unchanged in frames and follow the edited edge. A fade that
cannot fit rejects the whole operation; it is not clamped. Source bounds,
positive durations, rational/key bounds, partner collisions and either affected
track lock are authoritative. Left trim rejects a changed historical relative
mixing order, including muted-but-potentially-audible contributions, rather than
rewriting anchors or IDs. There is no independent-role bypass or temporary
unlink/relink workaround.

Trimming changes selected content and fades. Retimed processing may restart with
different transients: exact rebasing does **not** promise unchanged retained PCM,
lossless AAC or DSP phase continuity. Rebuild preview/export to hear the result.
The timeline source waveform remains approximate pre-gain/pre-fade information.

This is separate from [source slip](AFFINE_LINKED_SOURCE_SLIP.md), which keeps
timeline boundaries fixed, and [audio-only J/L roll](LINKED_AUDIO_CUT_ROLL.md),
which keeps picture clips fixed. V2 **split and joint roll remain refused** by
the unchanged strict-v1 admission helper. No new topology, schema, resync,
external audio synchronization, multi-partner, joint speed or ripple is added.

## Evidence scope, not a combined acceptance total

Focused milestones passed on their own frozen source identities: model 1,071
(`0fbe707f…`), whole selected UI 381 (`86676e6f…`), corrected DPR 6 (`368f07ad…`),
and strict-plus-affine actual media 36 (`6843dbb1…`). These are not an outcome
union for the later documentation source. Existing 16 legacy trim roles retain
their node IDs but intentionally test successful v2 trim/no-op instead of the
former refusal; split/joint-roll refusals and old strict coverage remain.

EN/ZH offscreen captures cover normal/narrow widths and DPR 1/1.5/2. Requested
height 390 differs from the actual minimum height 414; corrected reports and all
132 PNG headers verify actual geometry. Compact V/A values and the common delta
are visible; full explanations may require accessibility/tooltip text. These
are Linux offscreen checks, not native Windows/macOS interaction validation.

Ten new media cases supplement all 26 existing strict trim cases. Independently
decoded tagged RGB16 inputs and original stereo samples determine full-scene
new-state expectations, not old-output conservation. Scalar PCM keeps its 2-ULP
limit; retime keeps the accepted fixed local DSP plus independent envelopes at
1 ULP, not a new independent Rubber Band implementation. Gain-free cancellation
is exact. Source integrity, gaps, outer duration, hidden keys, mute/unmute,
preview/export/cache and history are covered. The helper's short secondary audio
stream is **not exhausted by these new cases**; new actual-EOF coverage is not
claimed. Existing broader EOF tests remain separate.

Actual catalog reconciliation, a clean full-source/fresh-restoration gate,
independent review, exact private-remote verification and native packages remain
separate. No previous pass is rebound to changed bytes.
