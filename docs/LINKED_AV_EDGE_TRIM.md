> Current schema43 extension: [affine-linked paired-edge trim](AFFINE_LINKED_EDGE_TRIM.md) admits existing J/L pairs and shows separate V/A durations. The original strict-v1 contract below, including identical-interval statements, describes its historical round; the new own-role contract preserves unequal edges. No current full/native acceptance is inferred.

> Subsequent candidate: [joint roll of exactly two adjacent linked A/V pairs](LINKED_AV_PAIR_ROLL.md) adds a separate bounded shared-cut workflow. Older unsupported-roll statements below describe their original round; no later acceptance is inferred.

> Subsequent candidate: [qualified linked A/V through-split](LINKED_AV_PAIR_SPLIT.md) adds a separately worker-qualified integer1× subset. Unsupported-split statements below describe this earlier round; other limits remain. No later acceptance is inferred from this note.

# Joint non-ripple A/V edge trim / 关联音画联合边缘修剪

## Scope and interaction

Select **exactly one existing complete same-source 1:1 linked pair**. Drag either
edge of either V or A member to trim both by whole project frames. Both outlines
show the draft; the impact text identifies the joint edit, signed displacement,
new duration and edge-anchored fades. A rejected draft shows both attempted
outlines and its reason; the full reason also remains in the timeline tooltip
and accessible description. Other clips, captions, stickers, markers and links
stay fixed. Release revalidates and commits once; Escape cancels. A zero-motion
click or return to zero preserves history and redo.

Extra clips, multiple pairs and Shift-selection do not start a joint trim. Both
tracks must be editable; a collision on either track rejects both members. A
role/selection/link/edit/lock/zoom change, Undo/Redo ABA, replacement/reload,
Escape, focus/window loss, hide/close or lost mouse button cancels the draft.
There is no snapping to a different admissible edge, hidden ripple or GUI-thread
hashing, probing or decoding.

只选中一对已存在的同源关联音画，再拖动任一成员的左右边缘。两个成员一起预览、
一起提交，一次撤销；其他轨道、字幕、贴纸和标记不跟随。多余选区、多对选区不会
被悄悄缩为单对。任一轨道锁定或碰撞均整次拒绝，不自动解锁、取消关联或清除设置。

## Exact source map and local keys

For old timeline start `t`, rounded duration `D`, rational source map `[O,E)` at
speed `r`, and signed integer delta `d`:

| Edge | New timeline interval | New source map |
|---|---|---|
| Left | `[t+d, t+D)` | `[O+d*r, E)` |
| Right | `[t, t+D+d)` | `[O, E+d*r)` |

Both roles must receive identical intervals. The partial-tail residue
`D*r-(E-O)` is invariant: no endpoint is reconstructed from rounded duration.
Source limits, rational bounds, nonnegative timeline/source start, positive
length, locks, collisions and all key bounds are validated before any commit.
The existing exact-candidate transaction allows only these approved pair edits;
generic paired timing writes and temporary unlink/relink remain forbidden.

Left trim shifts **every valid stored** motion/window key by `-d`, including
hidden/disabled and out-of-range handles. Existing schema restrictions remain;
this does not permit motion keys on an audio role where they are invalid. Audio gain keys shift by `-[S(t+d)-S(t)]`, where
`S(f)=round_half_up(f*48000/fps)`, **not** by `-S(d)`. Right trim preserves all key
coordinates. Clip/link/Media IDs, tracks, speed, structural audio emission,
historical mixing anchors and all non-rebased settings remain unchanged.

## Fades and audio are intentionally not waveform-conserving

Fade durations remain unchanged in frames and anchored to the corresponding
clip edge. Left trim moves fade-in; right trim moves fade-out. The opposite-edge
fade stays globally anchored. Reject if either member's fade exceeds the new
length; never clamp it. Fractional frame phases may change a fade's sample count.

The new exact source span and output budget intentionally change. Fractional
source and timeline boundaries can round differently; retimed clips restart the
existing bounded Rubber Band processing and can have different transients.
Gain-key rebasing is exact, but it does **not** prove retained PCM identity or
phase continuity. Rebuild preview/export to hear the new result. Source waveforms
remain approximate pre-gain/pre-fade views, not processed-output evidence.

Keeping anchor fields alone is insufficient: a left trim can change the sort
order of historical audio descendants sharing an anchor. The operation treats
muted contributions as potentially audible, excludes structurally suppressed V,
and rejects a changed relative order involving the affected A role instead of
rewriting IDs or anchors. Right trim cannot change that ordering.

淡化保留原帧数并跟随对应边缘，因此修剪后的声音可能有意改变。隐藏关键点不丢失；
音量关键点按全局采样边界之差重定位，而非单独对位移取整。此能力不承诺保留段 PCM
逐样本一致，也不是无损音频裁剪、重同步或 J/L 剪辑。

## Official comparison and next gap (checked 2026-10-27)

[Adobe Premiere's linking guide](https://helpx.adobe.com/premiere/desktop/add-audio-effects/basic-audio-editing/link-audio-and-video-clips.html)
documents linked moving and trimming. This candidate closes the narrow matching
edge-trim gap for one strict same-source pair, not arbitrary linked components
or multiple audio partners.

[Final Cut Pro's split-edit guide](https://support.apple.com/en-ca/guide/final-cut-pro/ver1632d82c/mac)
describes separate audio/video edit points. CutCat still does **not** provide
independent linked timing/J/L edits, linked split, roll, speed, ripple, external
sync or source patching. These require separate ownership and overlap contracts;
next relationship work is linked split and then independent A/V timing. This is
a workflow comparison, not paid-tier exclusivity, a completion percentage,
performance ranking or native-package parity claim.

## Source and acceptance gates

Baseline: accepted linked-A/V source, schema42, 1011 files, tree identity
`5f81be5a75af37d3e4eb99123b71145eab1db46a23ef0025ebf5acd8029eec43`.
No schema, render/audio compiler or runtime dependency change is intended.
Model/UI regression tests, old-project-derived RGB/PCM/envelope oracles, historical
mix-order acceptance/refusal, save/reopen/Undo and source integrity checks are
separate evidence from frozen aggregate and fresh-extraction admission.
EN/ZH narrow/DPI offscreen checks are not Windows/macOS native validation.
No remote, main, hosted CI or native package result is asserted by this document.
