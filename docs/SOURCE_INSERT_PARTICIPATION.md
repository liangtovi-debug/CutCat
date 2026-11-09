# Explicit Source Insert participation / 源插入的显式参与项

**Default behavior is unchanged: Insert moves only the destination Clip track.** This bounded addition lets a single Insert plan explicitly include other Clip tracks, captions and/or stickers. It is not persistent Sync Lock, linked A/V, source-component patching, a magnetic timeline or paid-editor parity. Schema **38**, rendering, audio compilation and dependencies are unchanged.

**默认仍为本轨插入。** 用户为这次操作勾选额外片段轨道，并分别选择字幕、贴纸是否跟随；未勾选项目明确保持原位置，可能失去同步。工程不保存“参与开关”或隐藏关系，只保存本次确认后的普通项目记录。

## Workflow

Open one media item in the Source viewer, set `[In, Out)` in whole project frames, choose **Insert** and capture the timeline playhead. The destination always participates. Check additional tracks and independently **Move captions / Move stickers**; preview before Apply. The preview lists the chosen categories and each existing item's old → new position, fixed position or destination split. A crossing follower or other unsafe condition blocks the entire operation; the error appears in the impact area as well as status.

Changing any choice invalidates the plan and outstanding worker token. Apply freshly verifies source identity/proof, then commits once. Cancel/Close, repeated Apply, source changes/relink, Undo/Redo, target/mode changes and changed project/editor/locks cannot let a late callback commit an old plan. A blocked New/Open remains blocked by the application's existing work-in-progress rule. New viewer instances start with the original destination-only policy. Choices kept visibly in an open viewer are explicit inputs to each newly previewed operation, not project-wide policy.

Captions and generated speech are independent: enabling captions does not implicitly select the speech clip's track. Including a video track also moves its embedded audio under the existing renderer; it does not create separate A/V components or duplicate source audio.

## Exact boundary rules

Let `C` be the captured insertion frame and `D = Out − In`.

| Item | Rule |
|---|---|
| Destination | Accepted Insert behavior: eligible interior clip splits into retained left/right source content; right resumes at `C+D`; whole downstream clips move `D` |
| Other participating clip tracks | `end <= C`: fixed; `start >= C`: whole clip moves `D`; `start < C < end`: **reject** |
| Participating captions/stickers | Same whole-item/fixed/crossing rules; no split, trim, stretch, content rewrite or automatic anchoring |
| Nonparticipating items | Complete records and live identities remain unchanged, including items crossing `C`; explicitly listed fixed |

Whole stickers retain asset paths/digests, relative keyframes, duration, z-order and enable state. No sticker asset or source file is rewritten. Existing asset validation/render requirements remain in force; this edit does not certify arbitrary assets or relax their format limits.

Touched clip groups, transition endpoints, edit locks and unsafe clip configurations reject the complete operation. There is no automatic unlock, ungroup, relationship deletion, music splitting, retime or settings removal. Whole moved clips keep the prior conservative clip compatibility gate, allowing fades only when their full compiled local audio is identical. Unchanged nonparticipants are not required to satisfy newly touched-clip restrictions. Selecting a locked follower track with no changed clips does not mutate it; locked destinations always reject.

## Audio: local equivalence **and** one common displacement

The old Insert gate already compares the entire local `AudioTiming` result and `AudioMixBuilder` fragment, including leading silence, source boundaries, gain and fades, stripping only final timeline placement. That is still required.

With cross-track/category participation, every shifted audible contribution must additionally move by:

```
ΔS = sample_boundary(C + D) − sample_boundary(C)
new.start_sample − old.start_sample == ΔS
```

This includes whole moved destination clips and the shifted right destination remnant. Muted/no-audio/compiled-silent contributions do not invent audible synchronization constraints; existing local-equivalence checks still apply conservatively. Per-track relationships are preserved without relying on mixed floating-point summation order. Nonparticipants are intentionally fixed, so this does not promise their synchronization with participants.

At **30000/1001 fps**, a one-frame insertion at frame 1 has `ΔS = 1601`. Five-frame clips starting at 1 and 2 both retain **8008** local samples after moving one frame, yet shift **1601** and **1602** samples respectively. Local equality alone would accept their one-sample relative drift. Participation mode rejects it. The historical destination-only default retains its prior compatibility behavior; it is not silently tightened into cross-track policy.

No follower is rounded independently and the legacy compiler is never modified to force acceptance. Choose another duration/position or intentionally leave the track/category fixed.

## Atomicity, persistence and proof limits

The frozen plan includes session/revision/project and candidate signatures, rate, source selection, destination, canonical participating tracks, independent caption/sticker flags and complete item impacts. Choice intent is signature-bound even if a selected track/category is currently empty. Apply reconstructs and compares the plan before one central commit of **clips, captions and overlays together**. A final guard failure restores every collection and history. Success is one Undo/Redo; save/reopen/recovery contain all resulting ordinary records, with no schema change.

The accepted proof/cache design is retained:

- Selected and actually split source intervals: merged total **30 seconds** maximum, with actual CFR/frame-selection and exact PCM evidence.
- Whole moved clips: current source identity/container checks and exact local compiler/common-shift checks, **not full downstream decoding**. Picture-only followers of an audio destination need no audio proof interval; they still undergo fresh identity/container checking.
- **120 seconds** per operation, including waiting/cleanup; **30 minutes cumulative** cold from-origin PCM scan endpoints.
- **48 MiB** shared session cache/audition/pending storage; 30-second audition and the existing sparse-window/count limits. Warm reuse still rechecks identities.
- Embedded short-audio EOF requires the accepted actual-clean-EOF evidence; independent audio does not pad a falsely claimed tail. No input seeking, partial result fallback, proxy substitution or resource-limit bypass.

See [Insert compatibility](SOURCE_MARKS_INSERT.md), [exact source-audio cache](EXACT_SOURCE_AUDIO_CACHE.md), and [named source ranges](SAVED_SOURCE_RANGES.md).

## Official comparison and next gap

Checked **2026-10-25**, using current official documentation; not a hands-on competitor benchmark or paid-tier entitlement claim.

- Adobe distinguishes **Sync Lock**, which controls which tracks participate in operations such as Insert/ripple editing, from full Track Lock. This motivates the explicit participation gap addressed here. CutCat's controls are deliberately **per Insert**, not persistent per-track switches and not a general ripple-edit system. [Premiere Sync Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/sync-lock-to-prevent-changes.html)
- **Source Patching** controls source destinations and Adobe describes distinct Insert/Overwrite placement. CutCat still inserts one selected source take on one destination track; follower choices do not independently route video/audio source components. [Premiere Source Patching](https://helpx.adobe.com/premiere/desktop/edit-projects/intro-to-editing/add-media-to-the-timeline-using-source-patching.html)

Next distinct design gap: explicit source-component ownership and safe A/V detach/link/restore. A naïve cloned audio clip plus video mute would duplicate embedded audio when unmuted; any future implementation needs ownership separate from mute and linking, exact offset/timing preservation, group/lock/trim/Insert rules and an explicit mix-order policy. Persistent Sync Lock and generic follower splitting remain separate work. No broader capability family is marked complete.

## Verification boundary

Focused model/lifecycle tests and actual per-track frame/PCM oracles cover participation subsets, fixed-byte records, destination splitting, follower crossings, offsets/fades/AAC EOF, common-phase refusal, one-step history/save/recovery and old default behavior. Bilingual simple/pro offscreen renders and independent review are distinct gates. Only matching frozen-source aggregate and fresh-extraction receipts establish local candidate acceptance; focused counts alone do not. No remote/CI action, user-footage upload, Windows/macOS package, signing or native installation is claimed by this round.
