> Subsequent reconstructed candidate (2026-10-30): [exact affine-linked through-split](AFFINE_LINKED_PAIR_SPLIT.md) supports qualified 1× v1/v2 maps with independent residual tails. Older split refusal statements below describe the earlier milestone; joint roll remains strict-v1-only. No historical pass claim transfers.

> Subsequent candidate: [joint roll of exactly two adjacent linked A/V pairs](LINKED_AV_PAIR_ROLL.md) adds a separate bounded shared-cut workflow. Older unsupported-roll statements below describe their original round; no later acceptance is inferred.

# Qualified joint A/V through-split / 经验证的关联音画联合分割

## What this candidate adds

Pause playback, select **exactly one existing complete same-source linked A/V
pair**, and put the playhead strictly inside it. The existing Split at playhead
button, Edit menu and shortcut share one qualification path. Either active V/A
role can open the captured-cut dialog. Preview verifies sources; Apply repeats
that verification before one atomic edit. The new right pair becomes selected,
retaining the active V/A role. One Undo restores the original pair.

The first version deliberately accepts only **integer source In/Out, 1×,
whole-frame source spans, same source/project frame rate, at most 30 seconds of
selected source**, and fresh proven CFR/legacy-renderer equivalence. Source
metadata alone is not proof. Unknown, VFR, mixed-rate, retimed, fractional-origin,
partial-tail, over-budget, active effects/stabilization/restoration, crossing
fades and incompatible audio sample phases reject without rounding or clamping.
Existing independent clip split is unchanged; this is not an automatic unlock,
unlink, pair search, multiselect split, J/L edit, ripple or speed edit.

先暂停播放，只选择一对既有同源关联音画，将播放头置于内部，然后使用原有“在播放头处分割”。
预览与应用均在可取消后台任务中验证当前源；应用前不改工程。仅支持同帧率、整数源入出点、
1 倍速且源范围不超过 30 秒的合格素材。未知/VFR/混合帧率、变速、小数源边界或不足一帧
的源尾不会被悄悄取整。新右侧音画仍是一对，左侧保留原标识，一次撤销恢复。

## Exact candidate, ownership and history

For source `[O,E)`, timeline `[t,t+D)`, cut `c` and `k=c-t`, the candidate is:

| | Timeline | Source | Identities |
|---|---|---|---|
| Left V+A | `[t,c)` | `[O,O+k)` | original two clip IDs and original link ID |
| Right V+A | `[c,t+D)` | `[O+k,E)` | exactly two new clip IDs and one new link ID |

`E` is retained, not reconstructed. In this version fractional spans are refused
before candidate admission rather than supported by a changed renderer branch.
Both roles use the identical mapping. Sources/Media, tracks, mute/base gain,
structural audio emission, historical mix anchors and every other setting remain
unchanged except the specified key/fade changes. V remains suppressed and A
remains enabled, including user-muted A. All other clips, links, groups, captions,
stickers, markers, media bins, source ranges and project records stay fixed.

Left keys are unchanged. Right **all stored** motion and window keys (including
disabled/hidden/out-of-clip handles) shift by `-k`. All gain keys shift by
`-[S(c)-S(t)]`, where `S(f)=round_half_up(f*48000/fps)`, never `-S(c-t)`.
No keys are added/cropped. Existing key/schema bounds remain mandatory.

Outer fades retain their exact frame lengths and global sample endpoints. Only
`left.fade_out` and `right.fade_in` become zero. If an original fade crosses the
cut, the entire operation rejects: it never invokes the duration helper that
silently clamps fades.

The dedicated private transaction compares the **entire approved Project** at
both the link-edit fence and the special-source-audio creation fence. It does not
relax generic `_commit`, existing linked slip/trim authority, or independent
separated-component split. Locks, owner thread, full project validation and
rollback remain active. A successful change adds exactly one history entry.

## Picture and audio proof boundaries

### Actual picture qualification

The unchanged legacy video branch trims seconds before resetting timestamps and
normalizing FPS. Its result is not automatically equivalent to origin-normalized
frame selection, even when imported average FPS equals the project rate.

The narrow public source-worker helper checks actual per-frame CFR timestamps
and complete native-resolution RGB16 SHA-256 sequences for the **original,
left and right trim ranges**, comparing legacy and origin-normalized paths.
The union is the original selected source interval, charged once against 30s.
Missing/ambiguous timestamps, insufficient complete frames and legacy phase
mismatch reject, rather than sampling a few frames or assuming metadata parity.
Grading, geometry, compositing and audio/render compilers are unchanged. Active
unproven effect/restoration/stabilization branches remain outside this version.

### PCM and mixing

At 1×, each half's active interval is represented as global output bounds plus
its source-sample offset. Matching that tuple against the original over each
half proves actual source indices, leading silence and padding, including finite
source EOF. Sample budgets partition exactly at `S(c)`. This is checked even for
user-muted A. Fresh source headers must reproduce the renderer's exact stored
`audio_offset`; the source viewer's existing 1µs tolerance alone is not accepted
as a rounding-boundary proof.

Both halves retain the original A contribution's relative position against
**all unchanged potentially audible contributions**, including user-muted ones,
using the full historical-anchor/start/id sort key. UUID-dependent ties reject
before IDs can affect admission. Plan IDs are generated once; no random retry or
anchor rewriting rescues an incompatible order. Suppressed V never becomes a
second audio contribution.

The tests compare original complete pre-encoder stereo PCM and original rendered
pictures before/after the edit. These are separate real-media gates, not claims
inferred from key equality or mocked worker tests. Lossy delivery codecs are not
claimed byte-identical; native decoder/platform results require their own gates.

## Freshness, cancellation and resource contract

Planning/qualification/commit perform no source file probing, hashing or decode
on the GUI thread. Preview and Apply each perform fresh dependency observation,
complete picture proof, final source revalidation and final dependency observation
under **one shared 120s deadline** (including dependency reads and backend-lock
waiting). Existing source-viewer memory/output/dimension/FPS limits and dependency
read budgets remain. There is no full-media copy, external service or source write.

The proof's actual path/state/sampled hash must match the dependency batch before
and after proof. Ordinary identity is bounded sampled change detection, **not** a
filesystem lock, adversarial full-file seal or zero-window TOCTOU guarantee.
Generated/LUT identities follow existing stricter full-hash contracts where valid.

Plans bind session, editor revision, original full-project signature, selected
pair, active role and captured cut; the dialog also guards selection/seek ABA,
window/project replacement, jobs, text/IME focus, cancellation and closure. Late
success cannot apply after expiry. Raising or retrying the still-current dialog reuses its captured IDs; closing
then reopening creates a new captured plan. A consumed successful plan cannot
replay after revision changes.

## Official comparison (checked 2026-10-27)

- [Final Cut Pro — Cut clips in two](https://support.apple.com/guide/final-cut-pro/cut-clips-in-two-ver4e30479/mac)
  describes through edits. This candidate fills one narrow paired through-split
  gap, not its full blade/range/multiple-selection toolset.
- [Premiere — Link audio and video clips](https://helpx.adobe.com/premiere/desktop/add-audio-effects/basic-audio-editing/link-audio-and-video-clips.html)
  describes treating linked components as an editing unit. CutCat remains a
  strict same-Media 1:1 relationship rather than arbitrary components.
- [Final Cut Pro — Create split edits](https://support.apple.com/guide/final-cut-pro/create-split-edits-ver1632d82c/mac)
  and [Premiere — Perform J-cuts and L-cuts](https://helpx.adobe.com/premiere/desktop/edit-projects/trim-clips/perform-j-cuts-and-l-cuts.html)
  describe different picture/sound edit points. That remains a different,
  unimplemented linked-timing capability, not something this through-split adds.
- [CapCut's split-scene tool](https://www.capcut.com/tools/split-scene) markets
  synchronized splitting. It does not establish exact detached-linked-pair,
  hidden-key, sample-lattice or historical-mix semantics for comparison here.

No paid-tier exclusivity, full paid-editor parity, completion percentage or
performance ranking is asserted. After this candidate's evidence closes, compare
again before choosing independent linked timing/J/L or further qualified-split
coverage. Joint roll/speed/ripple, multiple audio partners, external sync and
source patching remain separate contracts.

## Baseline and gates

Accepted local baseline: 1018 files, schema42, tree identity
`3bcb8e73ab42242e6dad3e5bf8b637b974597d405a8b2ad48510ff065f3f8d8c`.
This candidate changes no schema, renderer/audio compiler or runtime dependency.
Source edits, focused unit/mocked UI results, actual media proof, independent
review, frozen full gate and fresh extraction are distinct stages. Consult the
matching evidence receipts for which stages actually ran. Source existence is
not acceptance, remote/main/CI success, or Windows/macOS test-package delivery.
