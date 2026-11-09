# Named, reusable source ranges / 命名源选区

This bounded schema **38** addition saves source selections, not source media,
proxies or a new clip type. It builds on the accepted source viewer and
[single-track Insert/Overwrite](SOURCE_MARKS_INSERT.md). Historical renderer,
audio timing/mix compiler and dependencies are unchanged.

## Workflow

1. Open one source from the media browser. Inspect and mark the ordinary `[In, Out)` selection. Out includes the displayed frame by storing cursor + 1.
2. Enter a plain-text name and choose **Save named range**. A fresh owned worker checks the local source. One central undoable transaction stores the selection's exact rational source-relative seconds, capture FPS, stable media ID and portable binding.
3. Saving the project includes these records. Closing the source viewer still clears its working cursor/marks and its audio cache. Reopening starts with **Unverified this session**; no verification status is persisted.
4. Select a saved range and **Recall range to marks**. Fresh worker verification must match the saved source binding and map both bounds exactly to whole frames at the current project FPS. Recall only sets the source cursor/marks. It does not insert a clip or create Undo history.
5. Choose Insert or Overwrite, destination track and captured playhead; Preview and Apply remain separate, mandatory steps. Saved ranges do not bypass source proof, matched-CFR, sample-preservation, relationship or resource restrictions.
6. Rename or delete a range with one Undo step. A no-op rename creates none. Existing clips are independent and never change when their originating saved range is renamed or deleted.

Names are displayed as plain text, including HTML-looking text. English/Chinese
controls support live language changes, keyboard focus and scrollable narrow
layouts. I/O shortcuts remain limited to the focused source image; text fields
never mark the source. Metadata list/filter/validation operations do not read
source files on the GUI thread.

## Strict metadata and compatibility

`Project.source_ranges` contains at most 10,000 records. Each has a unique bounded
ID, a nonempty one-line name of at most 128 characters, an existing media ID,
canonical reduced rational In/Out seconds with `0 ≤ In < Out`, canonical capture
FPS and a versioned binding. Integer fields exclude booleans and have explicit
bounds. Both endpoints must lie exactly on the recorded capture grid. Unknown,
omitted, malformed or forward fields reject; nothing is silently discarded.

Versions 1–37 migrate to an empty list. A legacy-version document carrying the
new field rejects rather than losing it. Version 38 requires the field; old
readers reject version 38. Metadata validation is pure and does not compare saved
bounds to a replacement source's current duration: missing/shorter sources must
remain loadable, saveable and recoverable with the original records intact.

Saved references prevent **Remove selected unused media** from removing their
source entry. **Used / Unused remains timeline usage**, not an overloaded range
reference count. The browser shows a separate **Saved ranges: N** indication.

## Portable binding, freshness and unavailable ranges

Binding v1 records file size, the existing bounded sampled-content SHA-256 and a
canonical semantic signature of source stream layout, timing/origins/rate,
dimensions, input interpretation, audio offsets and relevant generated-audio
provenance. It excludes path, inode, timestamps, organizational names/bins,
project dimensions/FPS and viewer tokens. Generated sources also retain their
existing full provenance-content validation within its 64 MiB limit.

The sampled identity detects ordinary changes; it is **not a whole-file
cryptographic immutability guarantee or an adversarial storage lock**.

| Event | Result |
|---|---|
| Same content moved/relinked under the same media ID | Fresh verification can succeed despite a new path/inode/name |
| Missing or changed source, timing or interpretation | Original record stays; Recall fails and indicates unavailable |
| Shorter replacement | Original Out remains; no truncation or automatic rebind |
| Fewer than one complete current-project frame | Saved records can still be viewed, renamed/deleted; source controls are disabled |
| Project frame-rate change | Exact rational mapping only; non-integral boundaries reject without rounding |
| Change while a worker is checking | Before/after identity checks reject, leaving marks and project unchanged |
| Cancel, Close, relink, Undo/Redo, New/Open, selection/mark/name/range change or newer request | Old results cannot revive a range, change marks or commit a save |

There is no automatic repair/rebind command. Explicitly save a new range from a
current valid source if that is what you intend; the old record remains until
you deliberately delete it. Rename/Delete do not require source availability.

## Bounds are not decode authorization

Saving/recalling metadata probes bounded headers and sampled identity, not the
entire selected interval; long selections may be saved. Apply still rejects an
operation exceeding its **30-second aggregate source proof**. It never silently
shortens a saved selection. Existing **120-second operation**, **30-minute
cumulative origin scan** and **48 MiB shared session storage** limits remain.
The source worker registry still serializes work and owns cleanup; the binding
check introduces no long-range decode cache, new dependency or upload path.

## Comparison-driven loop — official workflows checked 2026-10-25

- [Premiere's named subclips](https://helpx.adobe.com/premiere/desktop/edit-projects/intro-to-editing/create-a-subclip-from-the-project-panel.html) begin with source In/Out and create a named project item. CutCat now closes the narrower reopen-and-reuse selection gap, but its record is not a separately editable project-bin subclip with its own trim semantics.
- [Final Cut Pro range selection](https://support.apple.com/en-lamr/guide/final-cut-pro/ver28cca92/mac) spans browser/timeline ranges and associates ranges with ratings/keywords, including multiple browser selections. CutCat retains named source records but does not implement rating/keyword search, multi-range batch editing or the connected-storyline model.

These are documentation comparisons, not hands-on performance benchmarks or
proof of paid-tier exclusivity. The next distinct gaps remain explicit A/V
component links and patching, cross-track anchor/synchronization policy, richer
range organization, separately versioned mixed-rate/VFR timing and current
native Windows/macOS validation. No paid-editor parity is claimed.

## Acceptance boundary

New tests cover strict schema/migration, owner-thread Undo/Redo/recovery, genuine
schema37 loading, reference protection, portable relink and changed sources,
exact rate conversion, stale/canceled UI callbacks and plain-text bilingual
layouts. Real-media acceptance must include save→close→project reopen→recall→
Insert/Overwrite through real workers and the unchanged renderer, with
independent numbered-frame and full PCM/manual-mark comparisons.

Frozen-source focused tests, independent review, aggregate and fresh archive
extraction each need their own matching receipt. Linux/offscreen evidence is not
a signed Windows/macOS package, physical playback or native installation test.
This feature authorizes no repository publication or hosted CI action.
