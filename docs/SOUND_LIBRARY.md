# Offline sound-effects library

Local-source milestone, 2026-10-21. This builds on the adjacent-cut-roll snapshot. It is not a newly built Windows/macOS release.

## What works

The independent **Sounds / 音效库** tab is visible in Simple and Professional workspaces. It offers categorized browsing, Chinese/English search, source/duration details, independent audition volume, stop/restart, and local import. Audition and insert actions stay visible at small window sizes while the catalog can scroll. Navigation, insertion, project changes and closing stop auditions. Replacing an audition uses a new generation so stale end/error events cannot stop or resurrect the next sound. Without an audio device the UI says so; insertion and export remain available.

Insert chooses Music 1 or Music 2 and uses the playhead, the end of that track, or an exact project frame. It inserts the entire sound as one undoable media-plus-clip transaction. Same-track overlap rejects the whole operation without replacing clips, moving other items, or leaving orphan media entries. Existing gain, mute, fades, trim and speed controls apply normally afterward.

## Included original assets

24 deterministic procedural WAVs: 5 UI, 5 transitions, 5 impacts, 5 notifications and 4 atmospheres. 48 kHz mono PCM16, 0.09–8 seconds, 42.48 seconds total. This is a usable starter collection, not a cinematic/professional stock catalog. Atmospheres are not advertised as seamless loops.

These 24 sounds were synthesized with the included NumPy generator, not copied from stock sites or recordings. See `lumen/assets/sounds/README.md`, `LICENSE.txt`, `catalog.json` and `scripts/create_original_sounds.py`. Asset-specific CC0 dedication does not license the rest of the private application. Regeneration is byte-identical in the tested pinned NumPy environment. Peaks are at most −6.37 dBFS; no clipped samples, finite PCM, shaped zero endpoints.

## Curated external CC0 assets

An additional 53 effects bring the offline library to 77 sounds. Kenney contributes Interface (18), Impact (19) and RPG (7) selections; creator-posted OpenGameArt sources contribute four bamboo swishes and one rain excerpt; muted.io contributes four Birds of Hawaii recordings. External categories: UI 9, transitions 8, impacts 14, notifications 5, foley 12 and atmosphere 5. The original 24 WAVs and their catalog remain byte-identical.

The UI identifies external sounds as external CC0, shows the credited author and pack, and provides source/license URLs and original filename in the details tooltip. Search includes author and pack. The separate `third_party_catalog.json`, full `THIRD_PARTY_PROVENANCE.json`, `licenses/` evidence and [third-party notice](../lumen/assets/sounds/THIRD_PARTY_README.md) retain per-asset provenance. CC0 is supported by source-specific publisher/creator declarations, not inferred from a “free download” label. Links do not trigger downloads. App code stays private and separately licensed.

Outputs total 11,343,456 bytes and 60.742042 seconds. All are 48 kHz mono/stereo PCM16 with peak at most 0.5. Only rain is excerpted (opening 15 seconds, 50-ms fades); other selections retain their full source intervals. No denoising; bird ambience and background noise remain. No seamless-loop or human-listening claim is made. Numeric checks cannot establish that every sound is free from all audible distortion.

Schema-13 sound projects migrate with empty third-party fields; schemas before 13 still reject sound metadata. New saves use schema 14, so older readers reject rather than silently discard source attribution. External metadata is never relabeled as original synthesis or as a rights-verified user import. Source author/page/license/file and both hashes survive insertion, undo/redo, save, relocation and reopening independently of the installed catalog.

## Local imports

Import accepts supported local audio files up to 64 MiB and 60 seconds. Existing FFmpeg/ffprobe are required. URLs, network paths, playlists, video streams and symbolic-link user-library paths are rejected; ordinary attached cover artwork is allowed. Reads/decodes are bounded and cancellable, use a local input snapshot and `file,pipe` protocol allowlisting, and never download assets or call a paid API.

Import writes a persistent user-local, hash-addressed 48 kHz stereo PCM16 WAV copy and a metadata sidecar. It preserves the original bytes, attenuates only when required to keep peak at or below 0.5, never boosts quiet input, and retains original/output hashes. The local collection survives project changes and restarts. Import alone does not insert a clip or mark a project dirty. Duplicate output is reused. Corrupt entries are isolated. User-imported rights are explicitly unverified: use only audio you have permission to use.

## Projects, portability and identity

Schema 14 stores immutable sound provenance, including third-party author/source/license links, exact sample rate/count and content digest. Older projects still load; older app readers must reject new saves rather than silently discard metadata. Complete sample tails use rational duration rounding upward to the next project frame; the unused remainder is silent. The same graph drives preview and export.

Saving a project copies sound-library WAVs into `<project name>.assets/sounds/` beside the `.lumen` file. Move the project and that folder together. This is not a collect-all-media feature: unrelated video, music, LUTs, fonts and stickers retain existing relative-path/relink behavior. Missing sounds remain saveable and are reported by existing relink UI. Re-linking requires the exact original WAV; changed content must be imported as a new sound. Export rechecks digest, actual PCM count/rate and peak, including cached previews. A valid existing portable copy is retained when a live-session library original disappears before re-save.

The app never overwrites source media. Asset copies are content-addressed and do not overwrite conflicts. Non-hardlink filesystems have an exclusive-create fallback; no fallback retries generic permission denial. Interrupted multi-asset saves can leave harmless already-copied WAVs, but the project JSON is replaced only after successful preparation.

## Audio and release limits

Conservative individual asset peaks are not a mastering limiter: summing tracks or increasing clip gain can still overload a mix. Audition volume does not change export gain. This milestone does not add roles, meters, crossfades, loop editing, licensing verification of user imports, or a downloadable online catalog.

Tests cover actual WAV/FFmpeg decoding, imported formats, malformed inputs, interruption/repeated actions, exact insertion, undo/redo, project relocation, source tampering and real preview/export audio. Linux offscreen Qt screenshots and ownership tests are not proof of audible speaker playback or native Windows/macOS behavior. The packaging manifest and native smoke path include all 77 sounds, but new platform builds/signing/listening remain unrun. No remote push, CI run or public release was performed.

Focused check: `QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_sound_assets.py tests/test_sound_library.py tests/test_external_sound_assets.py tests/test_sounds_model.py tests/test_sounds_ui.py tests/test_sounds_render.py tests/test_i18n_coverage.py`.

Real Qt capture: `QT_QPA_PLATFORM=offscreen python scripts/capture_sounds_ui.py --output build/sounds-ui`.
