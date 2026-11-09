# Persistent project timeline markers · schema39

## Current bounded candidate

Add named **point** or **range** annotations, comments and one of eight named colors. Save/reopen and local recovery preserve marker IDs and exact bounds. Timeline → Timeline markers opens the searchable list; Timeline → Add point marker at playhead opens an explicit draft. The independent marker lane sits below the existing timeline tracks; it does not shift clip, subtitle, sticker, transition or track-lock hit geometry.

This round is annotation metadata, not a new renderer or edit mode. Candidate acceptance requires matching frozen-source focused/UI, independent review, aggregate and fresh-extraction receipts. Existing receipts describe their original rounds, not this candidate. No Windows/macOS package, remote publication or paid-editor parity is claimed here.

## Frame and duration contract

- Every marker uses **fixed project-frame coordinates**, never a source or clip attachment. Clip moves/deletes, Insert, ripple delete and single-gap close leave these integers unchanged. Track edit locks do not block annotation changes.
- Point: `end_frame = null`. Range: `[start_frame, end_frame)`, where the end is exclusive and strictly greater than the start. The editor uses explicit project-frame inputs and optional playhead capture; source In/Out is not reused as a timeline selection.
- Markers do **not** contribute to `Project.duration_frames`, preview length or export length. Annotation-only projects remain empty timelines.
- Shortening or emptying the timeline retains annotations without clamping, deletion or hidden movement. The list shows out-of-range status. A point must have `start < duration`; a whole range must additionally have `end <= duration`. Navigation rejects before the ordinary seek API can clamp it. A partially out-of-range range is not treated as a valid start-only seek.
- FPS changes retain frame integers, not elapsed seconds. For example, frame 30 denotes one second at 30 fps and 1.25 seconds at 24 fps. There is no hidden retime or promise that clock time stays fixed.
- Previous/Next use all valid markers, not the search subset, and seek strictly earlier/later start frames without wrapping. Same-frame records remain distinct and accessible in the list; repeated Next does not cycle through equal-time annotations.

## Editing and navigation

The list searches names/comments case-insensitively and has a named-color filter. Point/range text, exact frames, color names and explicit out-of-range status supplement the color swatches. User names/comments are plain text, including HTML-looking text; tooltips escape them rather than opening links or interpreting markup. Valid non-BMP names, nonbreaking spaces and leading BOM characters round-trip exactly through the Qt editors; untouched Unicode is not silently normalized on Apply. Over-limit numeric/name drafts remain invalid rather than being truncated into a different valid frame or name.

Selection follows stable IDs, not filtered/sorted row numbers. Editing opens a draft tied to the current editor session, project identity and editor/UI revision. A project edit or switch makes that draft stale; it cannot overwrite a newer marker or a different project. Apply is one atomic history entry; identical Apply and Cancel are no-ops. Delete is explicit and undoable. Undo/Redo, saved projects and recovery copies preserve IDs and values.

Fresh shortcut profiles use `M` for the point-marker draft, `Ctrl/Cmd+Shift+Left` for Previous and `Ctrl/Cmd+Shift+Right` for Next. The list command is configurable but initially unbound. Existing saved profiles receive new commands **unbound**, avoiding collisions or silently changing users' choices. Existing app-local text-input, number-input, IME composition, modal/popup and unrelated list-focus safeguards remain in force; auto-repeat does not repeatedly create markers.

Completed currently active previews remain usable after marker-only edits and marker-only Undo/Redo. The project/UI revision still changes, invalidating staged content plans and in-flight task callbacks normally. Content edits still invalidate playback normally. No renderer cache key, compiler or output algorithm is changed to pretend reuse.

## Strict persistence

Schema39 requires `timeline_markers`. Versions 1–38 migrate to an empty list and reject the new field if it is smuggled into an old-version document. Each record requires exactly `id`, `name`, `comment`, `color`, `start_frame`, `end_frame`; unknown/missing fields, duplicate IDs and malformed records reject before project acceptance.

Limits: 10,000 records; IDs/names at most 128 Unicode code points and nonblank; comments at most 4,096 code points; frames are actual integers (not bool), from 0 through `2**53-1`. Names/IDs are single-line; comments allow LF and tab. CR, other C0/C1 controls, Unicode line/paragraph separators, unpaired surrogates and Unicode noncharacters (U+FDD0–FDEF plus each plane’s FFFE/FFFF) reject rather than silently normalize. Colors: red, orange, yellow, green, cyan, blue, purple, pink. The entire collection additionally permits at most 2,000,000 code points across IDs/names/comments; this bounds annotation storage without changing the existing 32 MiB total-project loader/recovery cap. Same-frame and overlapping annotations are legal.

A genuine schema38 serialized fixture was captured with the **unmodified accepted schema38 serializer**, using preserved schema37 fixture content plus a valid schema38 saved source range. This is a compatibility fixture generated before the schema bump, not a claim that it was a previously archived user project. Its capture provenance and hash are recorded with the fixture/schema tests.

## Official-editor comparison and remaining gaps

Checked official documentation on 2026-10-26. Adobe's [marker API](https://developer.adobe.com/premiere-pro/uxp/ppro-reference/classes/marker) exposes name, comments, color, position and duration. Its [marker navigation guide](https://helpx.adobe.com/premiere/desktop/organize-media/apply-labeling/find-move-and-delete-markers.html) documents finding, navigation, movement and deletion. CutCat's bounded point/range annotations address that organizational workflow, not Premiere's complete marker-type, interchange or sequence ecosystem.

Apple's [Final Cut Pro marker overview](https://support.apple.com/en-euro/guide/final-cut-pro/ver397279dd/12.4/mac/26.6) describes clip-associated markers and standard/chapter/to-do uses. CutCat's fixed project-frame annotations are **not clip-attached markers**, do not follow a moving clip and do not implement chapter/to-do publishing. These sources establish common editor workflows, not that every cited feature is exclusively paywalled.

Still open: clip/source-attached annotations, ripple-follow policy, to-do/chapters, marker interchange, range playback/export, source patching and explicit A/V relationship ownership; broader proxies, mixed-rate/VFR timing and native validation remain separate work. No completion percentage is inferred.

## Verification scope

Model/schema tests cover strict fields/types, v1–38 migration, genuine38 fixture, atomic CRUD/no-op/undo, recovery/reopen, fixed frames through content edits and unchanged duration. Native Qt tests cover bilingual/simple-professional/narrow layouts, list/filter stable selection, literal text, stale/cancel/project boundaries, independent lane hits, shortcut guards and metadata-only preview lifecycle. Real generated-media tests compare decoded preview/export frame sequences, PCM and duration before/after mutations and reopening/recovery, with fractional rates, audio offsets, captions, stickers and effects, and source SHA-256 unchanged.

Exact passed counts and source manifests belong in the final frozen acceptance receipt; the presence of a test or this plan is not itself a pass. Linux offscreen Qt is not physical Windows/macOS keyboard, IME, accessibility-device or native performance validation.
