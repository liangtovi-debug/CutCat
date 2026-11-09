> Subsequent schema43 extensions: [affine source slip](AFFINE_LINKED_SOURCE_SLIP.md) refines content with fixed timeline boundaries; [affine paired-edge trim](AFFINE_LINKED_EDGE_TRIM.md) moves corresponding edges together while preserving J/L offsets. Neither changes this audio-only roll topology. V2 split/joint roll remain refused; later acceptance is not inferred.

# Headless linked audio-cut roll and alignment reset

Milestone 3 staged-UI/media candidate, 2026-10-28. **Not a release or native acceptance.**
The schema43 foundation passed 423 tests on its own frozen source; the subsequent
headless command passed 659 tests in eight complete modules on identity
`64bb5aba31c1d6cb018ff575777c10758f22884fe915f4d445582332f5b478b1`.
Those historical results do not certify the current UI/media additions.

Exactly four unique clips must be two complete same-source A/V links. V roles
share one track and meet at fixed picture cut `Cv`; A roles share one track and
meet at audio cut `Ca`. The left outer starts and right outer ends match between
picture and audio. Selection order is irrelevant. Each pair may have a different
source and rational constant speed from the other pair. Unsupported topologies
are rejected, not repaired or silently unlinked.

For requested integer audio cut `C`, let `d=C-Ca`. Only two A records change:

| Role | Timeline | Source map |
|---|---|---|
| LV / RV | Exactly unchanged | Exactly unchanged |
| LA | `[LA.start, C)` | `[O, E+d*r)` |
| RA | `[C, RA.end)` | `[O+d*r, E)` |

Both source-tail residuals remain exact. Every stored right-A local picture/window
handle shifts by `-d` subject to its existing schema restrictions. Right-A gain
keys shift by `-[S(C)-S(Ca)]`, using the global 48 kHz half-up sample boundary.
Left-A keys remain fixed. Fades retain their edge-anchored lengths; inadequate
handles, overflow, fade fit, sample budget or collisions reject atomically.
Video fades and keys do not move and impose no new audio-edge limits.

All four involved members must be editable for a changing request, including
unchanged video during a subsequent v2→v2 roll. Unrelated track locks do not
block the operation. IDs, source ownership, mute/gain, historical mix anchors,
V objects/records and unrelated state stay unchanged. Both A roles retain their
full relative order against all potentially audible contributors, including
muted contributors. No anchors or IDs are rewritten to rescue a crossing.

`C<Cv` is a J cut, `C>Cv` is an L cut, and equality is aligned. This is relative
to the fixed picture edit, never the direction of the latest adjustment.

## Explicit reset versus an ordinary no-op

- A changing ordinary roll records v2, including when it happens to return to
  aligned geometry. An ordinary request at unchanged `Ca` preserves mode,
  history, redo and live identities.
- Explicit align targets `Cv` using the same two-A algebra. Each affected link
  downgrades to v1 only when its entire start/duration/rational-map equality holds.
  Matching visible edges alone is insufficient; unequal partial tails remain v2.
- An already-aligned strict-map v2 reset is an undoable metadata-only downgrade.
  An already-aligned pair that cannot downgrade is a true no-op. Reset never
  copies V payload over A or enables video source audio.

The public Editor methods are `preview_roll_linked_audio`, `roll_linked_audio`,
`preview_align_linked_audio`, and `align_linked_audio`; they return LV/LA/RV/RA.
The sole mutation uses the existing private exact-candidate fence, not independent
trim calls or temporary unlink/relink. Only A timing/local-key fields and the
approved link records are assigned; existing ownership/anchor fences remain.

Pure `plan_linked_audio_roll` / `plan_linked_audio_align` capture project identity,
session, revision, full-project signature and candidate signature. Apply does not
renew stale captures; it requalifies and then calls the same atomic command.
Normal changing edits expire by revision. A no-op plan is consumed once, with a
bounded replay-receipt set. These are freshness checks, not source-file locks or
an adversarial authorization seal. No worker, file observation or render occurs.

## Staged UI candidate

Edit → Roll linked audio cut… opens a separate **nonmodal** dialog for exactly
four eligible linked roles, in both Simple and Professional workspaces. The
accepted four-role joint-roll inspector remains separate. The new command is
unbound by default, including migration of older shortcut preferences.

The dialog shows fixed picture cut, current audio cut, requested integer cut,
J/L/aligned classification, unchanged V ranges and old/new A source ranges.
Input uses text rather than a 32-bit Qt spin box; unsupported, fractional, empty
or out-of-bound requests disable Apply without clamping. Every text change
creates only a pure detached preview; no debounce, source probe, worker, render,
audition or file write occurs. “Align audio cut to picture” only stages a reset.

Apply uses the captured plan, never a renewed capture. Project/editor identity,
session, revision, full values, exact selection, active role and selection
generation changes expire it, including Undo/Redo or selection ABA. Starting a
job invalidates it permanently through the job generation even if that job has
already finished. Playback, another modal/popup or IME composition cannot bypass
Apply checks. Cancel/Escape/close never edits history. One successful Apply closes
the draft; repeating Apply does not perform a second edit.

## Still required before shipping

Exact headless test receipts and independent review; then staged EN/ZH UI with
selection/active-role/session/ABA/job invalidation; full-scene unchanged-picture
and independent audio PCM proof; aggregate and fresh-extraction validation;
separate authorized Windows/macOS packaging gates. Retimed DSP transients are
not promised sample-identical to an uncut process. Crossfades, overlapping audio,
external recorder synchronization, subframe audio and joint ripple remain out
of scope.
