# Joint linked A/V roll / 两对关联音画联合滚动剪辑

## Workflow and bounded scope

Select **exactly two complete existing strict linked A/V pairs**, touching at
one cut. Both video roles must share a video track, and both audio roles an
audio track. The existing Clip inspector's roll section shows all four roles,
their IDs, source intervals and durations. Enter the **absolute integer timeline
cut**, review the draft, then Apply joint roll edit. Either role in either pair
may remain active. Other parameter panels do not turn a four-role selection into
an implicit batch parameter edit. Cancel resets the draft without history.

This changes four existing clips and one shared cut, not links or identities.
The pairs can use different Media records and different constant speeds; each
pair must still have internally identical complete source/timeline maps. No
extra selected clip is silently discarded. Missing partners, nonadjacency,
locks, collisions, source limits, non-fitting fades or key/rational bounds reject
the whole request. It is not J/L editing, source sync, ripple or linked speed.

准确选中两对完整且相邻的关联音画，在原「滚动相邻接点」检查器输入新的绝对时间线帧。
四个角色共同预览、一次提交、一次撤销；其他片段、字幕、贴纸、标记及素材组织固定。
不创建新 ID，不更改关联、轨道或音频所有权。取消或回到原接点不产生历史记录。

## Exact contract

For old cut `C0`, requested integer cut `C` and `d = C-C0`:

| Pair | New timeline | New exact source interval |
|---|---|---|
| Left | `[tL, C)` | `[OL, EL + d*rL)` |
| Right | `[C, endR)` | `[OR + d*rR, ER)` |

Both roles receive their pair's identical map. Preserve each exact partial-tail
residue `D*r-(E-O)`: no source endpoint is reconstructed from rounded duration.
Left local keys remain unchanged. **Every stored** right motion/window key,
including hidden, disabled and out-of-range handles, shifts by `-d`. Right gain
keys shift by `-[S(C)-S(C0)]` with 48 kHz half-up sample boundaries, **not** by
`-S(d)`. Existing key-schema restrictions still apply to audio roles.

Fades retain their frame lengths and stay anchored to their clip edges. Reject
if a fade cannot fit; never silently clamp. Source-audio suppression on V and
emission on A stay unchanged, including user mute, gain and historical mix
anchors. Full `audio_mix_order_key` relative order is checked for **both** A roles
against every potentially audible contributor, including muted clips. Keeping
anchor fields alone is insufficient; order crossings reject rather than rewrite
IDs or anchors.

One detached final Project is validated before one private exact-candidate
commit. This is never two trim calls or temporary unlink/relink. The generic
commit and qualified-split-only structural-audio fences remain unchanged.
No-op preserves live objects, revision and redo. Undo/Redo restores all four
records atomically. Preview validates a copy, not live canonicalizable fields.

## Audio and UI limits

New source samples, edge-anchored fades and output budgets intentionally change.
Retimed clips use the established bounded renderer and may restart processing
with different transients. Exact gain-key rebasing does **not** prove retained
PCM identity or waveform continuity. Rebuild preview/export to hear the result;
this inspector does no live audition, source hashing, probing or decoding.

The draft captures editor/session/revision, project identity and full value,
selection generation/IDs and active role. Project replacement, role/selection
changes and Undo/Redo ABA invalidate it. Apply rederives the current candidate;
a stale draft is never silently rebased. The Qt cut control retains its existing
signed-integer maximum. EN/ZH and offscreen DPR evidence is not native Windows
or macOS acceptance.

## Official comparison and next gap (checked 2026-10-28)

[Final Cut Pro's roll guide](https://support.apple.com/guide/final-cut-pro/ver1632d9ae/mac)
describes simultaneous adjacent-edge edits retaining combined and project
duration and leaving unrelated clips fixed.
[DaVinci Resolve's editing guide](https://www.blackmagicdesign.com/products/davinciresolve/edit/)
distinguishes roll's equal extension/shortening from ripple's downstream movement.
This candidate closes the narrow shared-cut workflow for two strict same-source
A/V pairs; it does not establish full trim-tool or paid-product parity.

[Final Cut Pro's split-edit guide](https://support.apple.com/guide/final-cut-pro/create-split-edits-ver1632d82c/mac)
describes separate audio/video edit points while clips remain linked. Independent
A/V boundaries/J/L cuts remain a separate next relationship gap, alongside
external sync, multiple audio partners, linked ripple/speed and source patching.
These public feature descriptions do not prove paid-tier exclusivity, speed,
usability or full feature equivalence. No competitor executable was tested.

## Evidence boundaries

Base: accepted R5 split source, schema42, 1,028 files, identity
`dd2501d3462f7b21c6ac6f90932e7d4a41f1e08185d94e883fb77e46945f5bda`.
Schema, renderer/audio compiler and dependencies are unchanged. Model, real
media, UI, source/global localization guards, actual whole collection, independent
frozen aggregate and fresh extraction are separate gates; see round receipts
for executed results, not overlapping counts summed as unique coverage.

Media oracles derive requested maps from the **old project plus request**, not
returned candidates or production timing helpers. Unit-speed PCM is checked
independently; retime checks disclose the separately established renderer
semantics. Both video and audio must be consumed in a lossless mux: the older
[roll harness note](ROLL_EDIT.md#acceptance-and-scope) explains why an audio-only
nullsink graph can truncate, without justifying a renderer change.

This document is a candidate contract, not a fresh acceptance claim or a native
package delivery. No user media is uploaded and no private repository is made
public. Native builds and remote integration remain separately owned gates.
