# Simple and Professional workspaces

Use the two buttons in the top bar to switch with one click. A fresh app starts
in **Simple**. The last choice is saved as an application preference on this
computer using Qt's platform settings, independently of any project file.

## Simple

- Import, arrange, trim, split, move, undo/redo, audio gain/mute/fades, preview and export
- Basic exposure, contrast, saturation, warmth, tint, shadows and highlights
- Caption editing, SRT, optional local transcription, local voice generation and voice browser
- Local subtitle assistance and semantic search, with their existing local-model requirements
- Stickers with timing, placement, size, opacity and layer controls

## Professional

Everything in Simple, plus the existing implemented controls:

- Explicit SDR input-transfer assignment, with source status visible in both modes
- RGB lift/gamma/gain wheels, master/RGB curves, `.cube` LUT loading and processing details
- RGB histogram and luma waveform from the decoded SDR preview cache
- Ordered Gaussian blur and vignette effects
- Sticker transform keyframe editing

The mode does not add unsupported grading, editing or AI capabilities. Scopes
are still decoded-cache SDR scopes, not calibrated HDR measurement; changing
workspace does not change that limitation.

## Preservation guarantees

There is one project model and one render engine. Switching only changes
widget/tab visibility. It does not reconstruct controls, rewrite any project
parameter, change selections, create an undo entry, mark the project dirty,
invalidate a preview, start a render, cancel a job, or alter export settings.
Returning to Professional restores the prior advanced color/effects tab and
expanded detail choices. Staged sticker/keyframe values remain in their controls.

Advanced color, effects, animation and source normalization already in a project
remain in After preview and export in Simple; Before keeps its existing creative
grade bypass. A project-wide notice identifies these
hidden settings and distinguishes active settings from bypassed/disabled ones.
Switch to Professional to inspect or change them. A retained hidden effect
selection cannot be removed accidentally with the global Delete shortcut.
Color editing is disabled on audio-only/music clips, with any stored color values
retained and excluded from the active-picture-settings notice.

Simple's **Reset basic color** affects only the seven visible basic controls.
It preserves advanced grade values, LUTs, input interpretation, and grade bypass.
Professional's **Reset all color** resets the creative grade and LUT while
preserving source input interpretation. Basic-control changes write only the
changed field, so hidden values do not lose precision through spin-box rounding.

## Verification

Run the headless Qt checks with:

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_workspace_ui.py

Coverage includes one-click access, fresh defaults, preference reopening,
repeated round trips with nonempty undo/redo history and cached preview state,
advanced-project save/reopen, active/bypassed notices, staged keyframes, hidden
precision, Basic reset, and switching during an in-flight preview. The existing
advanced UI suite explicitly selects Professional with isolated settings.

Headless Qt checks and screenshots do not replace native Windows/macOS release
and display validation.
