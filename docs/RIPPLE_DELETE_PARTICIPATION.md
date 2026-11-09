# Ripple-delete preservation and explicit participants / 波纹删除保护与显式参与项

**One committed selected clip, one delete, one Undo.** The existing **Ripple delete · this track** action remains immediate and same-track. It now rejects negative translations that change the survivor's actual local audio compilation. A separate **Ripple delete · choose participants…** preview lets this operation include additional clip tracks, captions and stickers. Schema **38**, renderers, audio compilers and dependencies are unchanged.

**本轮先修复旧操作，再增加有界参与预览。** 只删除当前已提交的单个选中片段，不读取尚未应用的检查器时间值。默认仅选中本轨；每次重新打开对话框都重新选择，工程不保存同步策略。未勾选项保持原位置，可能与参与项失去同步。字幕跟随不等于生成语音跟随；视频跟随也不制造音画链接。

## Why the old action needed a safety tightening

At **30000/1001 fps**, deleting `[0,1)` moves a later `[2,3)` clip to `[1,2)`. The renderer's half-up 48 kHz boundaries change its local output budget from **1602 to 1601 samples**. A five-frame survivor retains **8008** samples but a one-frame fade-in changes **1602 to 1601** samples. Both defects were reproduced with actual PCM before repair. Unchanged source bounds and duration metadata alone did not preserve sound.

The shared `clip_audio_translation.py` proof compares the complete production `AudioTiming` plus `AudioMixBuilder` clip fragment. It strips **only the final timeline placement delay**, retaining source selection, tempo filters, leading silence, output budget, gain and fades. Source Insert reuses the extracted unchanged contract. Incompatible moves now fail atomically; the historical ripple action is intentionally safer, not universally compatibility-identical.

Whole retimed/complex picture clips remain supported when the audio proof succeeds. No source-cut restrictions, CFR/30-second selected-source limit or downstream full-source decode is added just to move an existing whole clip. The old action retains valid whole downstream transition-pair translation. Ordinary non-ripple deletion is unaffected.

## Explicit participation workflow

1. Select exactly one committed timeline clip. Multi-selection does not offer this action.
2. Open **Ripple delete · choose participants…**. Its track is mandatory; other tracks, captions and stickers start unchecked.
3. Choose participants, then **Preview**. Review the deleted interval and every item's fixed/moved/deleted disposition. No project data has changed.
4. **Apply** performs one atomic edit. Cancel/Close/Escape is a no-op. Editing choices invalidates the preview. A changed selection, project/session, revision, relink, lock, Undo/Redo ABA or newer dialog cannot use an obsolete preview. New/Open attempts dismiss it, including a canceled Open chooser.
5. Undo/Redo, save/reopen and recovery preserve all resulting clip/caption/sticker records together.

This path is deliberately more conservative than the legacy action about transitions: **any touched transition endpoint rejects**, including an otherwise movable downstream pair. Groups must be explicitly ungrouped first; changed locked tracks reject. There is no automatic unlock, ungroup or relationship deletion. A checked locked follower with no changed items is not a mutation.

## Exact interval rules

Let the selected committed clip occupy `[A,B)` and `D = B−A > 0`.

| Item | Action |
|---|---|
| The selected clip ID | Delete exactly this ID |
| Participating survivor with `end <= A` | Fixed |
| Participating survivor with `start >= B` | Whole-item translation by `−D` |
| Participating survivor with any intersection with `[A,B)` | **Reject the entire operation**, including wholly contained items, either-edge crossings, and intervals spanning both edges |
| Unchecked item | Complete record remains unchanged, even when intersecting the deleted interval |

There is no follower splitting, trimming, stretching, overlap deletion or generalized range Extract. Gaps outside the selected interval remain. Surviving source ranges, speeds, effects, grade, transforms, local keyframes, fades, caption text/style and sticker assets remain unchanged. The project endpoint is recomputed from **all** surviving collections: fixed anchors can keep the original total duration, so it is not simply `old duration − D`.

## Cross-participant sample synchronization

Every moved clip passes the complete local proof. When any additional track/category participates, every moved audible contribution also must share the exact cut-boundary displacement:

```
ΔS = sample_boundary(A) − sample_boundary(B)
new.start_sample − old.start_sample == ΔS
```

**Do not use `−sample_boundary(D)`.** At 29.97 fps, deleting `[1,2)` gives `ΔS = −1601`, whereas `−S(1) = −1602`. Five-frame followers starting at 2 and 3 both keep 8008 local samples but move −1601 and −1602 samples respectively. Participation mode rejects this otherwise locally equivalent one-sample relative drift. Muted/no-audio/compiled-silent contributions do not invent common audible displacement requirements; the existing conservative local contract is retained.

Unchecked items are deliberately fixed, so synchronization with them is not promised. Whole-mix PCM regression tests are separate from local equivalence checks; floating-point summation order is not assumed to prove mix identity.

## Plans, atomicity and bounds

The immutable plan binds selected identity/bounds, session, revision, project/rate/candidate signatures, canonical track choices, independent caption/sticker flags and full item impacts. Choices remain bound even when their collections are empty. Apply reconstructs the candidate and compares all records before one central transaction. A late guard failure restores clips, captions, overlays, history and redo. Repeated Apply is stale after success. Preview/application are owner-thread operations with no I/O, source probing or decoding.

Existing project validation and range limits remain; there is no clamping, rounding, schema migration, renderer branch or new dependency. This operation does not validate external source bytes or promise compatibility with arbitrary damaged media; normal preview/export validation remains responsible for its sources.

## Official comparison and remaining gap

Checked **2026-10-25** against official documentation, not a hands-on benchmark or a paid-tier entitlement claim.

- Premiere documents ripple deletion of selected clips separately from ordinary deletion; it can operate on multiple selected clips. CutCat now has this **single-clip** explicit-participation subset, not arbitrary multiselect ripple or automatic removal of all gaps. [Adobe deletion](https://helpx.adobe.com/ca/premiere/desktop/edit-projects/change-clip-sequence/remove-clips-from-a-sequence.html)
- Premiere Sync Lock determines which tracks participate in Insert, ripple delete and ripple trim. CutCat's choices apply only to the current operation; they are not persistent Sync Lock or a general ripple-edit policy. [Adobe Sync Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/sync-lock-to-prevent-changes.html)
- Final Cut Pro's connected items can be deleted with their parent clip. CutCat has no equivalent connected-ownership model and deliberately never deletes follower items implicitly. [Apple deletion rules](https://support.apple.com/en-ca/guide/final-cut-pro/verc5d34129/mac)

A next bounded candidate is **one explicitly captured interior target-track gap**: reuse negative translation without deleting a clip, require stale-gap identity/bounds, and do not infer “all gaps” or shortest cross-track gap. General ripple trim, persistent Sync Lock, linked A/V ownership, component patching and native package validation remain separate gaps. No paid-editor capability family is declared complete.

## Acceptance boundary

Actual small synthetic PCM/frame oracles, negative-phase refusals, signed offsets/fades/retime, complete stereo mix, exact boundaries, all participant subsets, stale/tampered plans, old transition-pair compatibility, save/recovery and interrupted bilingual UI are separate checks. Acceptance requires matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts. This local round does not itself claim remote-main publication, CI success or Windows/macOS packages.
