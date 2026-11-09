# PR-reference keyboard shortcuts

CutCat provides a configurable **PR-reference** subset for commands that this
prototype actually implements. It is not a Premiere compatibility mode.

Open **Settings → Keyboard shortcuts…** (Windows Ctrl+Alt+K, macOS Cmd+Alt+K).
Search by English or Chinese command name, command ID, category, or assigned
combination. Choose the Windows or macOS profile, select a command, and record
up to two single-stroke combinations. Clear a slot to unassign it. Conflicting
assignments are rejected. Reset affects only the profile being edited. Apply
saves the staged configuration; Cancel discards changes since the last Apply.

## Default subset

| Command | Windows | macOS |
| --- | --- | --- |
| New / open project | Ctrl+N / Ctrl+O | Cmd+N / Cmd+O |
| Save / save as | Ctrl+S / Ctrl+Shift+S | Cmd+S / Cmd+Shift+S |
| Import / export | Ctrl+I / Ctrl+M | Cmd+I / Cmd+M |
| Undo / redo | Ctrl+Z / Ctrl+Shift+Z | Cmd+Z / Cmd+Shift+Z |
| Split selected clip at playhead | Ctrl+K | Cmd+K |
| Delete active selection | Backspace or Delete | Delete or Forward Delete |
| Play / pause rendered preview | Space | Space |
| Previous / next frame | Left / Right | Left / Right |
| Back / forward five frames | Shift+Left / Shift+Right | Shift+Left / Shift+Right |
| Toggle timeline snapping | S | S |

The dialog uses explicit physical `Cmd` and `Ctrl` names. On a Mac keyboard,
`Backspace` in the stored/displayed binding means the backward-delete key
usually labeled Delete; `Delete` means Forward Delete (often Fn+Delete).
Qt swaps its internal Control/Meta meanings on macOS. Conversion uses the
actual host platform, not the chosen profile. Selecting a Windows profile on
a Mac therefore deliberately uses physical Control; it does not rename
Command. Native-key capture can use the host's standard glyphs/labels.

C is a razor-tool key in Premiere, not an instant-cut command. CutCat does not
implement a razor or multiple selection-tool modes, so C and V are not assigned.
J/K/L shuttling, general ripple trim, and all-track splitting remain absent
and unassigned. Source I/O marks, ripple delete, and single-gap closure exist
as explicit controls, but have no shortcut mapping. Unmodified I is not import; import uses Ctrl/Cmd+I.
Home/End sequence-navigation defaults are not claimed or added here.

The S toggle changes actual edge/playhead snapping during a timeline drag and
has a visible checked button/menu state. It does not change saved project data,
undo history, render cache, media, or active background jobs. It starts enabled
in a new app window.

## Input ownership and safety

- There are no system-wide/global OS shortcuts. Only the active editor window
  can receive the app's commands.
- All app commands, including file commands, are suppressed while a text,
  caption, numeric, combo-box, key-capture, or slider input has focus. Native
  typing, clipboard, text undo, delete, and cursor movement keep working.
- IME preedit/composition suspends commands until it commits, cancels, or loses
  focus. Modal and nonmodal dialogs, popups, and menus own their keys.
- Split and delete require explicit timeline/viewer focus (or the window
  itself). Matching caption, sticker, effect, and professional keyframe lists
  can delete their active item. Unrelated media/AI/voice lists and inspector
  controls cannot delete a stale timeline selection. List arrows retain native
  navigation; focused buttons retain native Space activation.
- Splitting only works strictly inside the active clip. Delete is ordinary
  deletion without rippling. Disabled commands do not dispatch. Playback needs
  a current rendered preview, exactly like the Play button.
- Cut, delete, undo/redo, file commands, and toggles ignore key auto-repeat.
  Frame navigation permits repeat. Modifier matching is exact: adding Alt,
  Shift, another primary modifier, or an unsupported modifier is not an alias.
  Qt’s documented macOS KeypadModifier annotation on arrow keys is normalized
  on macOS only; it is not treated as an extra pressed key.
- Multi-stroke chords, modifier-only keys, duplicate assignments, and selected
  OS-reserved combinations are rejected. Some system/desktop shortcuts may
  still be intercepted before the app receives them.

## Local preferences

Two independent profiles are stored as a bounded, versioned JSON value under
`shortcuts/v1` in the existing local Lumen Edit workspace settings. They are
separate from `.lumen` projects and contain only whitelisted command IDs and
key names, never callback names or executable code. Applying settings is atomic
for the active session. Save failure leaves current bindings unchanged and is
reported. Corrupt, conflicting, unknown-command, oversized, or incompatible
saved settings fall back to defaults without overwriting the original value.

## References and validation boundary

Mappings were checked against Adobe's current official documentation:

- [Default shortcuts](https://helpx.adobe.com/premiere/desktop/get-started/keyboard-shortcuts/default-keyboard-shortcuts.html)
- [Tools and basic playback](https://helpx.adobe.com/uk/premiere/desktop/edit-projects/intro-to-editing/edit-video-in-premiere.html)
- [Frame navigation](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/navigate-sequences-in-the-timeline.html)
- [Customization and conflicts](https://helpx.adobe.com/premiere/desktop/get-started/keyboard-shortcuts/customize-keyboard-shortcuts.html)
- [Qt's macOS Control/Meta semantics](https://doc.qt.io/qt-6/qkeysequence.html)
- [Qt's macOS arrow-key annotation](https://doc.qt.io/qt-6/qt.html#KeyboardModifier-enum)

Automated tests use actual Qt/QTest key delivery, IME events, key repeat,
focus changes, dialogs, rebind/reset/restart, and real timeline mouse drags.
Linux offscreen tests and injected host conversion tests do not establish
physical Windows/macOS keyboard, IME, desktop-plugin, accessibility, or
OS-reserved-shortcut validation. Those remain native acceptance gates.

## Recorded local verification · 2026-10-15

- Full Linux/offscreen aggregate: **1,715 passed, 2 skipped** in 457.65 seconds.
  The skips were native Windows path semantics and the explicit CJK branding
  fixture. The existing verified font fixture was then enabled separately:
  all **8 branding tests passed**, including actual Chinese glyph pixels.
- Focused shortcut/model/dialog/localization suite: **156 passed**. This
  includes the actual-host macOS arrow annotation contract and injected host
  conversion cases; it does not simulate a physical OS keyboard driver.
- All **60 speed-model tests passed** after the parent native-CI fixture-only
  path canonicalization. All **27 dialog/localization checks passed** after
  preserving the existing Chinese effect terminology. These two changes were
  made after aggregate collection and verified separately; production retiming
  was unchanged.
- Existing full-window layout checks passed across English/Chinese and
  1×/1.5×/2× rendering, including the 414-capture geometry matrix. Four actual
  shortcut-dialog screenshots were inspected for dark-theme contrast, text
  readability, scroll access, and fixed Apply/Cancel access.

Native Windows/macOS packaging, physical key/IME behavior, native menu key
handling, and accessibility acceptance remain separate gates for this
shortcut milestone. The source checkpoint is not a signed native release.
