# Close one captured interior gap / 关闭一个捕获的内部空隙

**No real clip is deleted.** Explicitly choose a target track, capture the playhead, preview the one gap between its real neighboring clips, and Apply once. Optional other clip tracks, captions and stickers can follow. Schema **38**, renderers, audio compilers, dependencies and old ripple policies are unchanged.

**单次操作，不是删除所有空隙。** 本轮补齐“关闭明确目标轨上一个内部空隙”的有界工作流；不引入假空隙片段、不删除真实片段、不隐式删除跟随项、不把四条独立轨道伪装成磁性时间线或持久同步锁。

## Workflow / 工作流

1. Open **Timeline → Close one gap…**. Select an explicit target track. This is not the media-library import destination and does not infer a track from the clip selection.
2. Seek the timeline into an interior gap, then **Capture playhead**. The target is frozen: later playback/seek changes do not retarget it. Changing track or capturing again clears the preview.
3. Target track is mandatory. Select any additional clip tracks and independently choose captions/stickers. All optional participants start unchecked each time.
4. **Preview** shows captured track/frame, left/right clip identities, `[A,B)`, duration, **deleted clips 0**, and every fixed/moving item. This is plain text; names and IDs cannot inject markup. Preview never changes the project.
5. **Apply** moves eligible whole items in one transaction and one Undo. Cancel, Escape or Close changes nothing. Busy state temporarily disables Capture/Preview/Apply without changing a valid capture or plan. Newer dialogs, changed projects/locks/media/revision, Undo/Redo ABA and New/Open attempts invalidate the old context; a canceled file chooser does not revive it. Repeated Apply cannot apply the gap twice.

未勾选项保持整条记录不变，可能与移动项失去同步。字幕跟随不是 TTS 音频自动跟随；视频跟随也不建立新的音画链接。应用后仍通过现有构建预览/导出流程查看结果。

## A gap has real neighbor identity

On the validated target track, sort real clips by start time. Adjacent clips `L` and `R` define a valid gap only when:

```
A = L.end < B = R.start
A <= captured_playhead < B
D = B - A
```

`P=A` is inside the gap; `P=B` is inside the right clip and rejects. Empty tracks, leading/trailing empty timeline, touching clips, and playheads inside clips reject. Other tracks/captions/stickers never shorten the gap or produce a “shortest common gap.” Other gaps remain unchanged in length.

The immutable plan binds track, captured P, L/R IDs, boundaries, session/revision, frame rate, full project signature, canonical participation choices, all impacts and candidate signature. Apply rediscovers the same neighbors and reconstructs the candidate. Replacing a neighbor with another ID at identical bounds is not the same gap.

## Whole-item classification

| Item | Result |
|---|---|
| Unchecked category/track | Complete record stays fixed |
| Participating item with `end <= A` | Fixed |
| Participating item with `start >= B` | `start -= D`; every other field unchanged |
| Participating item intersecting `[A,B)` | Entire operation rejects, including an item wholly contained in the gap |

Every original clip, caption and sticker ID/count remains. Source bounds, retime maps, effects, grades, transforms, local keyframes, fades, caption language/text/style and sticker asset identity are preserved. There is no trimming, splitting, stretching or range Extract.

Moving locked tracks, grouped clips and any moving video-transition endpoint reject. No automatic unlock/ungroup/transition removal occurs. Fixed locked followers, fixed groups and fixed transitions remain allowed. All changed collections commit together; a final guard failure restores project/history/redo. Total duration is the maximum end across all collections, so a fixed anchor can keep the old endpoint.

## Audio proof: same-track common shift also matters

Each moved clip uses the complete existing local production audio contract, including timing, tempo, source bounds, leading silence, gain, output budget and fades; only final placement delay may differ. This requires no decoding or subprocess and imposes no source-cut/CFR/30-second limit on translating a whole existing clip.

For **every shifted audible contribution, even in target-only mode**, gap close additionally requires:

```
ΔS = sample_boundary(A) - sample_boundary(B)
new.start_sample - old.start_sample == ΔS
```

Do not use `-sample_boundary(D)`. At **30000/1001 fps**, closing `[1,2)` requires **−1601 samples**. Right neighbor `[2,7)` shifts −1601, while a later same-track `[8,13)` shifts **−1602**. Both retain an 8008-sample local contract, but the latter breaks the one-splice promise, so the operation rejects. No source or fade field is silently adjusted to force acceptance. Muted/no-audio/compiled-silent contributions introduce no audible common-shift constraint; the existing conservative local proof remains.

This stricter gap-only promise does **not** change accepted legacy same-track ripple behavior or explicit ripple/Insert participation policy. Local proof and real full-mix PCM tests are separate evidence; we do not infer mix equality from metadata alone.

## Official-editor comparison / 本轮竞品对照

Official pages checked **2026-10-25**. This is documentation comparison, not a competitor hands-on benchmark or a claim of paid-tier exclusivity.

- Adobe distinguishes deleting clips, leaving gaps and ripple deletion. CutCat's new action addresses one explicitly identified existing gap without deleting a real clip; multiselection ripple remains separate. [Premiere deletion](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/remove-clips-from-a-sequence.html)
- Adobe's Sync Lock controls participating tracks across insert and ripple operations. CutCat's checkboxes apply only to this invocation, not a saved synchronization policy. [Premiere Sync Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/sync-lock-to-prevent-changes.html)
- Final Cut Pro has actual gap clips and connected-item ownership. CutCat deliberately does not emulate those semantics with a temporary dummy clip or implicit follower deletion. [Final Cut Pro deletion/gaps](https://support.apple.com/en-ca/guide/final-cut-pro/verc5d34129/mac)
- Premiere Elements 2026 documents both individual-gap removal and Delete All Gaps. This round implements only CutCat's conservative single-interior-gap subset, not all-gaps cleanup, gap hover affordances or export gap warnings. [Elements gap tools](https://helpx.adobe.com/br/premiere-elements/desktop/editing-clips/delete-all-gaps.html)

Remaining priorities include persistent synchronization policy, linked A/V/component ownership and source patching, broader ripple trim, all-gaps review, realtime performance and native package validation. No capability family or paid-editor parity is declared complete.

## Verification boundary

Focused model/UI and actual numbered-frame/whole-mix PCM tests are followed by independent review, final frozen-source aggregate and fresh extraction checks. These must bind the same source digest before acceptance. Small synthetic Linux/offscreen evidence does not validate Windows/macOS installation, arbitrary media compatibility, realtime playback or remote-main/CI status. This round makes no remote publication or native package delivery claim.
