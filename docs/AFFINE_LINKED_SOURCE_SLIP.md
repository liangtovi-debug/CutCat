> Subsequent reconstructed candidate (2026-10-30): [exact affine-linked through-split](AFFINE_LINKED_PAIR_SPLIT.md) supports qualified 1× v1/v2 maps with independent residual tails. Older split refusal statements below describe the earlier milestone; joint roll remains strict-v1-only. No historical pass claim transfers.

# Affine-linked source slip / 保持 J/L 边界的联合源滑移

Bounded local candidate, 2026-10-29; schema **43** and native version unchanged.
This is not a release or a Windows/macOS package claim. The headless milestone
passed 1,145 tests on its own frozen source `5123a2b4b945b5073233efa29c828820721fe62571c116f72355f7c07e2bc9ed`.
Those results do not certify the subsequent UI/media additions described here.

## Use the existing command

Pause playback and select exactly one complete same-source A/V pair. It may be
strictly aligned, or an existing affine-sync pair with different picture/audio
boundaries following a J/L edit. Choose the active **V** or **A** role, then use
**Joint source slip…** (or the existing inspector route). No new command or
shortcut is introduced; this command remains unbound by default.

The entered source In belongs to the **explicitly active role**, in exact
project-frame source units. It is not a shared origin and is not a timeline
shift. The dialog puts the active-role/common-delta summary and numeric input
first, then separately shows each role's old/new source range and fixed timeline
interval. Both source contents intentionally change; existing J/L boundaries do
not move. Preview observes source/LUT dependencies on a cancellable worker;
Apply observes them again before one atomic edit and one Undo.

新源入点以当前 V 或 A 角色为基准。音画双方以同一精确源位移平移，但保留各自
不同的源范围、时间线边界和尾部余量。已有 J/L 时序不会被对齐或取消；滑移后的
画面与声音可能变化。预览不修改项目，应用可一次撤销；取消不会产生历史记录。

## Exact rule and preserved state

For selected role `s`, requested source origin `N` and old maps `[O_i,E_i)`:

```
delta = N - O_s
new map i = [O_i + delta, E_i + delta), at the old speed r
```

This is a source-coordinate delta: **do not multiply it by speed again**.
Both affine intercepts `O_i - timeline_start_i*r` increase by `delta`. Each
independent source span and tail residual `duration_i*r - (E_i-O_i)` remains
exact. No overlap or adjacency requirement is added to a valid single pair.
The link mode stays unchanged, even if v2 geometry happens to match strict v1.

Timeline starts, durations, tracks, project duration, clip/link/media identities,
source ownership, mute/gain, fades, all visible/hidden local keys, effects,
grades and historical mix anchors stay fixed. Video source audio remains
disabled and only the audio-track member contributes that source audio.
Source and LUT files are never written. Rebuild preview/export to hear and see
the changed source selection; old full-video/PCM equality is not promised.

Both members' source bounds and rational bounds are validated on a detached
candidate. Either member's edit lock blocks a nonzero slip; unrelated tracks
need not be unlocked. Out-of-range partner origins/endpoints or denominator
explosion reject atomically, without clamping or rounding. Headless zero delta
preserves raw live values, identities, history, redo and revision; a verified
no-op receipt is still consumed once. UI requires both selected tracks unlocked.

## Freshness and interaction boundaries

Plans retain original selected role, exact request, session/revision, source
snapshot and candidate signature. Apply cannot rebase stale work. Project or
selection changes, active-role/Undo ABA, unlink/relink, locks, playback and
unrelated job start/finish invalidate it. The dialog's own Preview and Apply jobs
are explicitly tracked, rather than invalidating themselves. Local IME preedit,
other modals/popups, disabled windows and text composition gate direct Preview
and Apply as well as shortcuts; the final Apply callback checks again after its
worker returns. Preedit alone does not disable the source-input field.

The existing ordinary-source sampled identity and generated-source/LUT full
hash budgets are reused, including double observation per worker batch. These
are freshness checks, not an adversarial file seal or a filesystem lock. Pure
planning and model Apply perform no owner-thread stat/hash/probe/decode.

## Boundaries and validation claims

This source-slip operation does not perform independent A/V slip, external
recording sync, one-to-many links, topology changes, new J/L cuts, joint
speed/ripple, trim, split or roll. A later, separate
[affine paired-edge trim](AFFINE_LINKED_EDGE_TRIM.md) candidate admits v2 trim;
joint split and roll still require strict-v1 links. Central linked-edit
transaction fences remain unchanged. The evidence below belongs to this
source-slip round, not to the later trim source.

Focused model tests derive expectations from old records plus the selected
request, across exact speeds/rates, independent tails, hidden keys and locks.
Proposed actual-media tests use small procedural numbered video and scalar PCM
oracles over complete scenes, source offsets, leading/EOF silence, fixed other
tracks and historical summation. They intentionally require changed selected
content rather than old-output conservation. They cover 1× media; rational
retimed model proofs are not independent retimed DSP validation.

Full frozen-source catalog/aggregate, independent review, fresh restoration,
private-remote verification and native packages remain separate gates. Existing
or earlier passes are not rebound to new bytes.
