# UI alignment acceptance

The UI-only milestone keeps project, undo, media and rendering semantics intact.
It replaces fixed-pixel body typography with a native-family, point-sized font
(minimum 10pt; larger user/app fonts are retained), font-metric form columns,
first-line alignment, consistent control heights and spacing. Narrow forms stack
labels consistently, long labels wrap, and long combo text remains accessible.

A vertical splitter replaces the fixed-height timeline. The complete six-lane
content remains vertically scrollable on small windows. Viewer, scopes, transport,
progress and status occupy non-overlapping layout regions. Timeline text uses
consistent vertical alignment and explicit ellipses for short clips. Original SVG
chevrons and explicit dark scrollbars preserve control affordances.

## Queued reflow while a parent window is hidden

The current candidate narrows a queued layout pass only when the controller root
is itself a hidden top-level window and descendant top-level windows are visible. It
reflows every visible child window's subtree, including its hidden tabs, instead
of repeatedly formatting the hidden main-window inspectors. Hidden main-window
geometry is deliberately deferred, not promised unchanged while hidden.

Construction and an explicit `apply_ui_layout_tokens(root)` still perform a full
refresh. A non-window controller root, a visible root window, or no visible
descendant window retains the full path. Visibility is checked when the queued callback runs, not when it is posted;
showing the root schedules full reflow. Discovery, event watchers and the complete
form inventory remain intact, and a needed second geometry-convergence pass is
not suppressed. Values, focus, visibility and project/undo state are not changed
to make layout work cheaper.

This targets shared queued form-layout work identified in a bounded, instrumented
GUI-thread profile. That profile does not prove the exact cause of an earlier
heartbeat failure or establish a performance ranking. Matching lifecycle,
accessibility, bilingual/DPR and unchanged responsiveness checks are separate
validation stages; consult the exact candidate's evidence before claiming a pass.

## Repeatable verification

`python scripts/capture_ui_alignment.py --output build/ui-alignment --scales 1,1.5,2`

The driver starts a fresh Qt process per scale, checks 1366x768,1440x900,1920x1080
logical client sizes in Simple/Professional, and captures applicable clip editing,
color/wheels/curves/audio, captions, appearance, voices/audition, local AI/search,
stickers and effects states. Reports include actual device pixel ratios, sizes,
fonts, form edges/baselines, text bounds and scroll reachability. Screenshots use
a synthetic UI fixture, not a claimed rendered scene. Automated geometry checks
supplement human visual inspection; a pass is not universal desktop certification.
Tests also deliberately inject a real viewer overflow and require rejection.

Native CI repeats the matrix on Windows/macOS before packaging. Separately, the
re-extracted frozen application smoke constructs the real MainWindow, plays a
rendered preview through its Qt video sink, and captures both workspaces.
Source-run and frozen/native evidence must remain distinguishable in reporting.

The prior baseline overlapped timeline/progress and viewer/transport at small
sizes. Before screenshots are retained outside source. Current local validation
and subsequent native run outcomes are delivered with their exact commits.
No inference engine, media processing, color math or project schema was changed.

## Native runner fonts and evidence limits

The Windows Qt `offscreen` backend exposed no system-font database in CI:
`QFontInfo` returned an empty family and pixel size -1, and even Latin labels
painted missing-glyph boxes. A test-only session fixture now registers the existing
hash-verified Noto Sans CJK fixture for the entire pytest process. The separate
re-extracted package smoke also registers that external fixture explicitly and
requires nonzero glyph indices and visible glyph rasters. No font is bundled,
installed, downloaded by the editor, or hidden inside the released archive.

Both the source UI matrix and packaged screenshots are real Qt output, but use
`offscreen`, not the physical Windows/macOS desktop plugin. They do not establish
clean-machine system-font fallback, monitor calibration, keyboard/IME behavior,
physical audio, or every monitor/DPI combination. Customer desktop validation
remains a separate gate. The product preserves installed explicit fonts and uses
installed Segoe UI for a generic Windows UI family when available.

Responsive forms account for hidden-tab viewport widths and field minimum sizes.
Zero-horizontal-overflow checks remain strict. Reports retain full font/control
geometry and a screenshot on failures; glyph absence cannot qualify as passing
UI evidence. Actual English interface branding is `CutCat`; the implemented Simplified Chinese
interface uses `剪辑猫`. The expanded matrix covers both languages (414 states).
See [current native validation](BILINGUAL_LUT_NATIVE_VALIDATION.md).
