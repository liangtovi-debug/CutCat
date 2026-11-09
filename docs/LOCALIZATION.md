# Simplified Chinese / English interface

The application now has an explicit live language selector beside the Simple / Professional workspace buttons. Fresh settings default to Simplified Chinese. Choosing English displays `CutCat`; choosing Simplified Chinese displays `剪辑猫`. The language preference is persisted separately from the project and does not depend on OS language. Existing `.lumen` projects and settings identities are unchanged.

## Scope and safeguards

Menus, action labels, forms, tooltips, working status text, captions/ASR/TTS, voice browsing, effects/stickers, AI assistant/search, color input, wheels/curves, scopes, timeline labels and application dialogs use explicit translation bindings. Qt's official Chinese standard-dialog catalog is included in frozen packages. User captions/scripts, filenames/paths, model and voice IDs, technical color metadata and third-party diagnostic details are not translated or rewritten.

Switching updates existing widgets rather than rebuilding the editor. Tests retain staged text, timing controls, selections, editor history, player/cache identity and serialized project state. Backend validation/render/inference semantics are unchanged. Known application errors have translated templates; unknown external errors display a Chinese explanation followed by the original technical details. This intentional fallback is not a claim that arbitrary third-party diagnostics are translated.

## Validation

The geometry matrix covers both languages, both workspace modes, captions/appearance/TTS/auditions, AI/search, color/wheels/curves and effects across 1366×768, 1440×900, 1920×1080 and 100%/150%/200% scaling: 414 actual Qt states per platform. No overflow assertions are relaxed for Chinese. Frozen-package smoke must load the standard Qt Chinese translation, verify visible Chinese glyphs, preserve editing state across language switches, decode a real rendered preview and save Chinese/English Simple/Professional screenshots.

This document describes the implemented acceptance criteria. Exact completed native runs and package checksums are recorded separately after CI finishes. The previous English-only package record remains historical evidence, not evidence for this new locale implementation.

Native automated screenshots use Qt offscreen and a separately hash-verified local Noto font fixture. Fonts are not bundled or downloaded by the app. Physical Windows/macOS desktop font fallback, IME behavior and all monitor configurations remain a separate manual acceptance gate. External FFmpeg and optional AI runtime/model requirements remain unchanged.
