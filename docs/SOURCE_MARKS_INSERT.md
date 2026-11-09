> Current schema38 successor: [explicit per-operation participation](SOURCE_INSERT_PARTICIPATION.md) can include other Clip tracks, captions and stickers. Destination-only remains the unchanged default described below; this historical document does not describe the opt-in multi-collection behavior.

> Schema38 successor: [named source ranges](SAVED_SOURCE_RANGES.md) can now preserve selected bounds. Unsaved working marks/cache remain session-only; the original edit and rendering contracts below are unchanged.

# Source marks → single-track Insert v1 / 源选区本轨插入

**Insert moves only the selected Clip track. Other tracks, captions and stickers stay at their existing times. It does not keep them synchronized with the moved picture or sound.**

**本轨插入不会联动其他轨道、字幕或贴纸。** 插入点之后的独立配乐、字幕、贴纸可能不再与被推后的片段对齐；请先查看影响预览。这不是跨轨同步插入、同步锁、音画链接或磁性时间线。

This is a bounded addition to the existing [source viewer and Overwrite](SOURCE_MARKS_OVERWRITE.md), not full three/four-point editing, source patching or paid-editor parity. Schema stays **37**; the historical renderer and audio compilers are unchanged. This document describes the candidate's contract, not aggregate acceptance or a native release receipt.

## Workflow / 操作

1. Select one media-bin item and open **Open source viewer…**. Inspect an exact frame and mark In/Out. Positions are whole **project frames**. The selected interval is `[In, Out)`; Mark Out includes the displayed frame by storing cursor + 1.
2. Choose the explicit **Insert** or **Overwrite** mode and destination track. Overwrite remains the default and retains its non-rippling semantics. Existing whole-source Add/double-click behavior is unchanged.
3. Capture the timeline playhead. Moving the playhead later does not silently change this captured destination.
4. Preview the impact: destination interval, affected clip identities, retained split pieces and downstream old/new positions. No project edit has happened yet.
5. Apply once. Worker-side source verification precedes one atomic edit. Cancel or Close does not apply the proposal. A successful operation is one Undo/Redo step; saved and reopened projects contain the ordinary resulting clip records, not a hidden insert operation.

Marks remain session-only and disappear when the source viewer closes. Inspection and audition remain source-only, not the final timeline composite or mix. Audition is limited to 30 seconds; marking is disabled during playback. I/O shortcuts remain scoped to the focused source image.

## Exact single-track semantics

Let `D = Out − In` and `C` be the captured destination frame.

- The new source clip occupies `[C, C + D)` on the chosen track, at 1× with ordinary source insertion defaults. Explicit Apple Log input interpretation is retained where applicable; another timeline clip's creative settings are not copied to the new take.
- Existing clips on that track with `start >= C` move right by exactly `D` project frames. Their source ranges, durations and supported settings remain unchanged. Existing gaps between downstream clips retain their sizes.
- If `C` lies strictly inside an eligible clip, its left source content stays at the original position. Its right source content resumes at `C + D`; the right piece receives a new identity. No old source content is deliberately overwritten or removed.
- At a clip boundary there is no zero-length split. In a gap, the portion before the insertion stays fixed and downstream content shifts. Beyond the last clip, the existing clips remain fixed and the new source is placed at the captured destination.
- Other Clip tracks, captions, stickers, media organization and project settings remain fixed. Overall project length need not grow by exactly `D`: a different fixed track or overlay can already extend farther than the edited track.

The entire transaction is rejected when it would touch an edit-locked target, any grouped clip, a transition endpoint or an unsupported clip configuration. It never silently unlocks, ungroups, removes a transition, drops settings, moves only part of a rejected batch or creates overlapping fallback clips.

## Compatibility and audio-preservation gates

Selected picture and cut picture ranges require actual matched-CFR source/project rates, complete frames and agreement with the unchanged legacy source-selection path. General mixed-rate/VFR source edits require a separately versioned timing design, not a change to existing projects' renderer in this feature.

Affected clips must have whole-frame 1× timing and no partial source tail. Interior splits use the conservative Overwrite cutting rules: fades, animation, effects, non-default restoration/stabilization, transforms and transparent compositing are not silently reconstructed. Supported static grade/LUT, gain and mute remain on retained content. Whole downstream moves reject those same complex configurations: retiming, partial source tails, motion/window animation, clip effects, non-default restoration/stabilization, transforms and non-legacy compositing. The sole additional allowance is fades on an otherwise eligible whole moved clip, and only when the complete audio compilation proves equivalent. For a whole moved video clip, nominal source/project rates must match; this operation does not rescan every downstream frame to establish actual CFR. Its existing source trim/cadence is unchanged.

**Moving a clip by whole frames is not sufficient proof that its local PCM stays unchanged.** The existing 48 kHz compiler rounds global start/end, late-audio onset and fade boundaries. At fractional project rates, a new start phase can change a sample count, leading silence or fade extent even though `source_in` and `duration` did not change.

The Insert planner therefore compares the full local `AudioTiming` result and `AudioMixBuilder` clip fragment before/after a downstream translation, including gain and fades. Only the final whole-clip timeline delay may differ. A non-equivalent translation rejects the entire edit; choose another insertion duration/position rather than accept a one-sample shift. Split remnants additionally prove that their retained source samples match the original clip, then apply the same translation gate to the right piece. This is a fail-closed compatibility check, not a new resampler or a change to the legacy audio compiler.

## Bounded source proof, not a whole-timeline transcode

| Work / resource | Bound and behavior |
|---|---|
| Selected plus actually split source intervals | Merged aggregate at most **30 seconds**; actual decoded picture timing/hashes and exact embedded or independent audio proof |
| Whole downstream moves | Source identity/probe checks and local compiler-equivalence checks; unchanged source ranges are **not** decoded in full merely because their timeline start moved |
| One operation | **120 seconds maximum**, including checks, lock wait, decoding and cleanup |
| Cold exact audio preparation | At most **30 minutes cumulative** normalized from-origin scan endpoints per operation |
| Retained PCM, auditions and pending files | **48 MiB shared** session-storage cap |
| Audition | At most **30 seconds**, 24 MiB output; no more than four cached audition files |
| Sparse exact audio and EOF evidence | Existing bounded 32-entry/window limits; source and decoder identity bound |

The exact audio cache is inherited unchanged in purpose: normalize from the stream origin without input seeking, discard prefixes/gaps and retain only selected stereo f32 windows. Warm reuse avoids decoding, not identity/probe/hash validation. A late first window is not constant-time random access. See [Exact source-audio cache](EXACT_SOURCE_AUDIO_CACHE.md) for the full budget and EOF contract.

Short embedded audio can contribute absent silence only after clean actual decoder EOF, with all declared samples present. AAC padding beyond declared duration remains real source content; a header does not prove EOF. Independent audio-track proof does not pad a missing claimed tail. Cancel, timeout, insufficient disk, corrupt cache, changed source or missing evidence rejects acceptance rather than substituting shifted or degraded media.

Source identity is a portable file-state/sample-hash check, not an adversarial storage lock. Generated assets retain their provenance validation. Source files are never rewritten; no full-source proxy, model or new dependency is introduced.

## Atomicity and lifecycle

The immutable plan captures editor session/revision, project content, rate, mode, destination, marks, affected identities and the validated candidate. Apply reconstructs and validates the candidate before one central commit. Repeated Apply cannot replay a consumed or old plan. Undo, Redo, locks, relink, source selection, new/open project, target or mode changes, Close/Cancel and newer requests invalidate pending UI results. Verification is not itself an edit, and stale callbacks must never revive or commit a proposal.

## Comparison-driven scope / 官方工作流对照

Official documentation checked **2026-10-25**. This is a workflow comparison, not a hands-on performance test or proof that a feature is exclusive to a paid tier.

- **Premiere** documents source marks, per-track source patching and distinct Insert/Overwrite operations. CutCat now adds the bounded single-track Insert step, but still lacks explicit independent A/V source patching and relationship-aware cross-track insertion. [Adobe source patching](https://helpx.adobe.com/nz/premiere/desktop/edit-projects/intro-to-editing/add-media-to-the-timeline-using-source-patching.html)
- **Resolve** describes Insert at the playhead, pushing later content and splitting a clip when inserting inside it. This motivates CutCat's preserve-content semantics, not a claim of its wider edit/trim toolset or realtime behavior. [Blackmagic Edit page](https://www.blackmagicdesign.com/products/davinciresolve/edit)
- **Final Cut Pro** describes inserted content moving subsequent storyline content and resuming the original clip after an interior insertion. Its primary/selected storyline and connected-clip model is not equivalent to CutCat's fixed independent four tracks. [Apple Insert guide](https://support.apple.com/en-euro/guide/final-cut-pro/ver4e2eff6/mac)

Next distinct gaps remain explicit A/V links and source patching, cross-track synchronization/anchor policy, persistent named source ranges, versioned mixed-rate/VFR timing, broader transitions and native release validation. The [current capability inventory](CURRENT_EDITING_CAPABILITIES.zh-CN.md) supersedes stale “absent” entries without turning partial families into parity claims.

## Verification and delivery boundary

Acceptance must bind the exact frozen source to model/UI tests, independent numbered-frame and PCM old/new render oracles, offsets and late embedded AAC EOF, splits/gaps/end cases, cancellation/repeated/stale Apply, Undo/Redo/save/reopen, unchanged historical renderer/audio compiler bytes, independent review, aggregate regression and fresh archive extraction. No counts are asserted here before those receipts exist.

Linux/offscreen checks are not Windows/macOS packages, hardware playback, signing or an installation test. This candidate does not publish a repository, trigger hosted CI, upload user footage or claim all paid-editor features. Source implementation, local acceptance and native delivery are separate statuses.
