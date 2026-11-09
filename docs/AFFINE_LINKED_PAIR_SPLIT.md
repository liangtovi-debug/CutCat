# Exact affine-linked A/V through-split / 保留 J/L 外边缘的联合分割

New reconstructed candidate, 2026-10-30, based on recovered accepted private
source `aa57c9bd2e6de051c3edf97592d37745476765fe`. This is **not** the lost
October 29 candidate and inherits none of its test results. Schema 43,
renderer/audio compilers, shared strict-v1 roll fence and dependencies are unchanged.
No remote integration, native Windows/macOS package or competitor parity is claimed.

## Operation

Pause playback, select exactly one complete existing same-source V/A pair, and
place the playhead strictly inside **both** members. Existing Split at playhead
opens the captured Preview/Apply dialog. Each stage rechecks sources on a
cancellable worker; Apply performs one exact-candidate transaction and selects
the new right pair, retaining the active V/A role. One Undo restores both originals.

Strict-v1 and affine-v2 1× maps are eligible, including fractional source origins
and unequal residual tails. The two original outer timeline edges stay fixed:
this splits through a J/L pair, not an audio-only roll, resync, ripple or edge reset.
Both links retain their explicit original alignment mode, even when current
geometry could fit v1. Independent split cannot bypass linked-edit protection.

先暂停播放，只选择一对已有的同源关联音画，将播放头置于音画双方的内部。
左右各形成一对关联音画；原有 J/L 外边缘、小数源边界和不同的不足一帧源尾
均保留，不强制对齐。预览和应用分别校验源文件，应用一次提交，一次撤销恢复。

## Exact own-role contract

For each role `i`, source `[O_i,E_i)`, timeline `[t_i,t_i+D_i)`, cut `c`, and
`k_i=c-t_i`, set `B_i=O_i+k_i`:

| Record | Timeline | Source |
|---|---|---|
| Left | `[t_i,c)` | `[O_i,B_i)` |
| Right | `[c,t_i+D_i)` | `[B_i,E_i)` |

Affine equality `O_V-t_V == O_A-t_A` implies `B_V == B_A`; it does **not**
justify copying V's old source endpoint, duration, key delta or residual tail
to A. Each right tail `D_i-(E_i-O_i)` remains exact; left tails are zero.
The outer start/end differences and total coverage are not changed.

Left keys stay fixed. Every stored right motion/window key shifts by `-k_i`,
including hidden or disabled keys. Gain keys, including hidden V gain keys,
shift by `-[S(c)-S(t_i)]`, `S(f)=round_half_up(f*48000/fps)`, independently for
each role. Neither `S(c-t_i)` nor the partner's delta is equivalent in general.
Outer fades keep their exact lengths; inner fades become zero. Crossing fades,
key bounds, either-track locks and ambiguous/changed historical mix order refuse
atomically without clamping, UUID retry, anchor rewriting or history loss.
Source-audio ownership remains V suppressed/A enabled, including user-muted A.

## Bounded conservation proof

Only 1× is admitted: independent retime instances cannot promise unchanged PCM.
The existing exact sample-index/leading-silence/padding proof partitions A at
`S(c)` and checks user-muted potential audio too. Fractional endpoints do not
bypass this proof. A one-sample incompatible cut is rejected, not rounded.

At 1× the exact-map renderer samples `floor(O)+k`; its complete frame envelope
is `[floor(O),floor(O)+D)`, **not** `[int(O),int(E))`. The worker proves actual
CFR for this envelope. For old/left/right records it requires legacy/global
native RGB16 equivalence only where the actual renderer uses legacy
trim-before-fps. An integer-origin partial-tail original can create a legacy
left half, so this branch must be checked even though the original is exact-map.
Unused legacy discrepancies do not block fractional-origin all-exact maps.
For exact-map cases full origin-normalized original and half frame hashes must
partition exactly, with explicit frame-count checks.

The source envelope covering **both** members is bounded to 30 seconds; an
extended audio-only J/L tail cannot evade admission. Actual video source FPS
must equal project FPS. VFR/missing complete frames, wrong current audio offset,
active effects/stabilization/restoration, stale dependencies or failed pixel
proof refuse. Both Preview and Apply retain one shared 120-second deadline,
source-identity binding and cancellation checks. Source observation is sampled
identity, not a filesystem lock or a zero-window TOCTOU claim.

## UI and verification scope

The dialog uses `local_owner_window` rather than shadowing `QWidget.window()`.
A fresh focused Qt IME event reproduced the old TypeError; regression checks
must exercise the real focus/event path, not merely search for the renamed field.
Existing selection/playhead/role/revision ABA, window closing, cancel and late
result guards remain. Captured own-role timeline/source intervals explain the
unequal boundaries in English and Chinese.

New model/worker, actual-media and offscreen UI tests are separate scopes.
Model receipts and mocked decoder/UI proofs do not establish real media or
native behavior. Actual media uses generated tagged RGB16 and original stereo
samples, independent full-scene/PCM expectations, negative controls, source
fingerprints and save/Undo/Redo. Lossy AAC is not asserted byte-identical to
lossless PCM. Run logs and frozen identities, not this document, establish
which tests executed; focused passes are not full-suite acceptance.
