# Separate source audio — independent clips (schema40 candidate)

## Workflow

1. Select one committed V0/V1 clip. Open **Edit → Separate source audio…** or
   the Audio inspector's separation button (Simple and Professional workspaces).
2. Explicitly choose **A0** or **A1**. No destination is preselected or auto-found.
3. Choose **Preview separation**. This verifies source freshness without editing.
4. Choose **Apply separation**. A second source check precedes one atomic edit;
   the new independent audio clip becomes selected.
5. Rebuild the rendered preview or export. The video inspector explicitly says
   its source audio was separated; the audio inspector says there is no link.

Cancel, Close and Escape discard the preview. A changed project/selection
invalidates it; close/reopen instead of silently rebasing to newer intent.
New/Open/Recover replace the session and discard stale work. Merely hiding/showing
a window does not commit anything. There is no default keyboard binding; custom
bindings retain the app's text-input, modal, focus and auto-repeat guards.

## Contract

This is **independent audio separation**, not linked editing, source extraction,
or an automatic Group operation. One committed V0/V1 clip gives its source-audio
contribution to a new clip on an explicitly selected, empty A0/A1 interval.
The video and audio keep the same timeline start, duration, complete rational
source interval/speed, gain, mute and fades. The original video keeps every
picture property and its existing audio settings; only its structural source
emission is disabled. The new audio clip starts with default picture fields.

Both clips reference the **same Media record and original source file**. There
is no fake audio-only source, new WAV, transcode, audio-offset rebase, media-bin
edit or source-range copy. The source file is not modified. Import/relink remains
the existing shared-Media operation: replacing that record affects both users.

Both affected tracks must be editable. The chosen A interval must be free.
Grouped sources and video-transition endpoints reject in this first version;
there is no automatic ungroup, link, overwrite, ripple or free-track search.

One transaction and one Undo/Redo covers the emission change and new clip ID.
The window records this exact session-only history edge: Undo selects the original
video and Redo selects the same new audio. Unrelated history edges do not use it.
Save/reopen and recovery retain the separated state, not session Undo history.
Deleting either clip leaves the other alone. Deleting the audio never restores
video sound. Muting/unmuting the video never restores its separated source audio.
A separate [strict explicit Reattach command](SOURCE_AUDIO_REATTACH.md) can now
recombine two explicitly selected components only when their shared Media and
complete timing still match. It transfers the selected audio settings and
removes only that audio clip; it is not a persistent link. Do not describe
Group/Ungroup as Link/Unlink.

## Independent editing limits

- Move, trim, slip, speed, gain and mute remain independent. Existing timing
  validity and exact-audio movement restrictions still apply; no synchronization
  indicator or automatic movement is promised.
- Split and source Insert/Overwrite cuts of separated/special-state clips reject
  conservatively. Whole translations can retain the structural state/anchor.
  Ordinary clips retain the existing split/cut behavior and retime caveats.
- Copy/Paste retains a suppressed video's disabled component. A copied audio
  clip is a **new contribution**, so its historical handoff slot is reset; no
  automatic relationship is invented when both components are copied.
- An explicit user-created group retains ordinary group semantics only.

## Why this is not clone + mute

`Clip.source_audio_enabled` is a separate, strict boolean. Generic parameter
updates and generic transaction callbacks cannot change it or mint historical
ordering metadata. The dedicated operation verifies an exact source change and
one exact audio append; it does not grant a general-purpose transaction bypass.

The immutable `AudioMixAnchor(track, start, clip_id)` is a **historical float32
summation slot**, not a live clip reference. It survives independent moves and
original-video deletion and does not assert a source-range or sync relationship.

Audio contributions formerly sum in `(track, start, id)` order. Floating-point
addition is not associative: three simultaneous contributions `1, -1, 2^-25`
produce `2^-25`, while a naive move of the first contribution to the last audio
track gives `-1, 2^-25, 1`, which produces zero. The separated A inherits the
video's effective slot. Only the final `amix` label list is ordered by
`(effective_anchor, current_start, current_id)`. Shared clip/input/picture-layer
traversal remains exactly legacy; untouched projects keep their previous graphs.
Analysis, preview and export use the same structural-audibility contract.
A valid imported schema40 project can contain multiple clips with the same
historical slot and current start. Separation rejects that ambiguous tie rather
than letting its fresh clip ID change summation order.

Schema1–39 projects default to enabled audio and no anchor and reject either new
field if injected into old-version input. Schema40 requires both canonical fields;
unknown or malformed anchor members reject. Save writes schema40. No silent
historical remix, source endpoint rounding or migration of user mute is allowed.

## Verification and resource boundaries

Planning and commit perform no filesystem I/O. Source preview and final Apply
verification run on cancellable workers with one immutable source snapshot.
Normal media use the existing bounded sampled local-file identity (not a full
hash or decoder compatibility proof). Generated provenance, if applicable,
requires a full bounded content hash; it is not repurposed as detach provenance.
Each worker observes the file twice, reads at most 128 MiB and checks a 60-second
cooperative deadline. No extraction or source-cut 30-second duration limit applies.
A receipt is a freshness observation, not a lock against external replacement
immediately after observation. Subsequent preview/export retain their normal
source validation.

Session/revision, full project signature, source selection, explicit destination,
new identity and candidate signature guard commit. Cancellation, stale context,
locks, collisions, repeated Apply and failed source checks do not edit project,
history or source files. Source settings are committed values, not uncommitted
inspector drafts.

Two local gates are required: (1) strict schema/legacy compiler compatibility and
real f32 PCM plus numbered-frame oracles, then (2) operation/UI integration.
Matching frozen-source focused/UI, independent review, aggregate and fresh
extraction receipts are required for local acceptance. Native Windows/macOS
packages, private remote main state and hosted CI remain separate gates.
Historical test totals from earlier rounds are not this candidate's evidence.

## Official-editor comparison (checked 2026-10-26)

Apple distinguishes expanding attached A/V from detaching an independent audio
clip; its magnetic/connected-clip workflow and reattachment via compound clips
are broader than this bounded track-based operation. [Final Cut Pro audio clip
views](https://support.apple.com/en-my/guide/final-cut-pro/ver549f212d/mac).

Adobe documents linked audio/video instances, including multiple audio clips
with one video clip and explicit unlinking. CutCat's independent separation and
ordinary groups do **not** implement those persistent linked-edit relationships.
[Premiere link audio and video](https://helpx.adobe.com/ae_en/premiere/desktop/add-audio-effects/basic-audio-editing/link-audio-and-video-clips.html).

Next relationship work needs typed components, exact rational sync baselines,
linked selection, lock-aware joint edits and explicit split/copy/unlink policies.
The later strict reattach operation has its own output-preserving contract
after independent audio-settings edits; source/timeline mismatch rejects. No paid-editor parity, multicam, multistream channel mapping,
sync-lock, magnetic timeline or realtime audio-engine claim is made here.
