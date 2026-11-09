# Reattach source audio — explicit, exact recombination

## What this round adds

Select exactly one video-track clip whose source audio is structurally disabled
and exactly one enabled audio-track clip. Preview their explicit IDs and roles,
then confirm **Apply reattachment**. The audio clip may be user-muted. The operation
returns its contribution to the selected video, transfers the selected audio's
current gain/mute/fades and effective historical mixing slot, and **deletes only
that selected audio clip**. The video keeps its ID and every picture/source field.

This is a bounded recombination, **not** persistent A/V linking, a compound clip,
a source extraction, a synchronization repair, or automatic matching. Multiple
compatible audio clips may exist; selection determines the one used. There is no
search-and-guess, duplicate cleanup, source-file write, new Media record or WAV.
The confirmation's deletion applies to the project clip, never the source file.

## Eligibility and exactness

- Both IDs must identify current committed clips: one V0/V1, one A0/A1. An
  already-enabled video rejects rather than doubling or replacing its sound.
- Both must reference the **same Media ID**, occupy the same timeline start and
  duration, and have the same complete semantic source time map: exact rational
  origin, endpoint and speed. Shared Media also means the same audio offset.
  Matching filenames, rounded seconds, or merely similar sound are not enough.
- Independent gain, mute and fade edits are allowed if ordinary project
  validation accepts them. No source mismatch is repaired, rebased or clamped.
- Both tracks must be editable. Group membership, transition endpoints,
  malformed state and ambiguous effective audio-mixing slots reject atomically.
  No group, transition, marker, caption or sticker is silently removed.
- Historical anchors are ordering metadata, not live links or proof that two
  clips were originally separated together. A compatible explicit same-source
  selection may be recombined without guessing a relationship.

The transaction enables the video's source contribution, copies the selected
A's gain/mute/fades and effective anchor, and deletes only A. A muted A remains
muted after transfer; **structural enablement is not user unmute**. All other
clips, media, bin membership, ranges, annotations and settings remain exact.
One Undo/Redo restores those same clip IDs and settings. Save/reopen retains the
result in the existing **schema40**, not session-only undo history.

## Mixing order and pictures

The audio contribution must occupy A's former effective float32 summation slot,
not the video's old track slot. For example, `1 + (-1) + 2^-25` retains `2^-25`,
whereas moving the tiny term first can round the output to zero. The established
schema40 anchor is transferred; no mixer or renderer change is required.

The tie-break order is historical `(track, start, ID)`, then current start and
current ID. A different current start can safely distinguish two contributions
sharing a historical anchor. A same-anchor/same-start peer is conservatively
rejected (even if user-muted), because replacing A's ID with V's ID must not
change this order. The retained suppressed video and selected A are excluded
from that peer ambiguity check: they are the one intentional handoff.

Only the established final audio label ordering uses the anchor. The shared
clip/input/picture traversal stays legacy order. Independent tests compare the
**whole pre-edit stereo f32 mix byte-for-byte**, rather than checking one solo
clip or an AAC file. Numbered source frames separately verify unchanged picture
selection and compositing. The source-file hashes are checked unchanged.

## Preview, Apply and stale work

Planning and commit perform no filesystem I/O. Preview and Apply each inspect
one immutable shared-source snapshot on cancellable workers. Normal local media
use bounded sampled identity. Existing generated speech/sound provenance is
audio-only and therefore ineligible for this shared A/V operation; no provenance
validation is bypassed or repurposed as an A/V link. The worker retains the
defensive full-hash path if such provenance becomes eligible in a future model.
A freshness receipt is not a file lock against later external replacement.
Existing preview/export validation still applies afterward.

Session/revision, complete project signature, explicit selection/roles and the
candidate signature guard acceptance. New/Open/Recover, relink, lock changes,
undo/redo ABA, selection changes, cancel/close and repeated Apply cannot rebase a
previous preview or edit a newer project. Source validation failure leaves
project/history/source unchanged. No hidden pending inspector draft is applied.

There is no generic structural-state authorization flag. The dedicated narrow
transaction validates its exact expected result. Existing separation, copy,
split and Source Insert/Overwrite-remnant restrictions remain unchanged.

## Official-editor comparison (checked 2026-10-26)

Apple documents independent audio detachment and describes reattachment through
a compound clip. CutCat's exact one-video/one-audio recombination is a different,
smaller operation; it does not add compound clips or Final Cut Pro's connected
and magnetic timeline behavior. [Final Cut Pro: ways to view audio clips](https://support.apple.com/en-my/guide/final-cut-pro/ver549f212d/mac).

Adobe documents persistent links between a video and multiple audio instances,
or between multiple audio clips, with joint editing and explicit unlinking.
CutCat's recombination removes one separate audio instance and restores one
video's own contribution; it does not implement these linked-selection and
linked-edit relationships. [Premiere: link audio and video clips](https://helpx.adobe.com/ae_en/premiere/desktop/add-audio-effects/basic-audio-editing/link-audio-and-video-clips.html).

The next relationship gap remains typed multi-component links, exact rational
sync baselines, linked selection, lock-aware joint edits and explicit
split/copy/unlink policies. No paid-editor parity, automatic sync, multistream
channel mapping, realtime audio engine or native-package readiness is claimed.

## Evidence gates

The baseline is accepted schema40 commit
`540178990dae51c69c3eaefa7c08feca36956d86` (949 source files). The genuine saved
schema40 fixture is captured through that baseline's model and narrow separation
operation, not retrofitted from schema39. A separate baseline checkout subprocess
renders the exact same synthetic inputs as the candidate to compare real RGB and
f32 output; the frozen audio-only fixture is supplemental, not a substitute for
that genuine-checkout comparison.

Focused runtime cases cover four project rates, both signed embedded offsets,
retiming, rational partial tails, edited gain/fades, mute and structural silence,
multiple explicitly selectable audio candidates, an order-sensitive cancellation
fixture, numbered picture frames and unchanged source bytes. Matching frozen
focused/UI, independent-review, aggregate and fresh-extraction receipts are
required for source acceptance. Remote main, hosted CI and Windows/macOS test
packages remain separate end-to-end gates. Historical test totals are not this
round's acceptance evidence.
