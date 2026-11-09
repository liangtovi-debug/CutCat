# Schema40 existing-test adaptation inventory

Production versions 1–39 reject both new Clip fields; version40 requires both.
The test-only `source_audio_legacy_fixture.strip_source_audio_state_fields`
refuses current/future/invalid versions and removes only these fields from
explicitly synthesized legacy clip dictionaries. The existing pre-39 marker
helper chains it; all older helper guards and parameter cases remain unchanged.

Current emitted-version assertions change 39 to 40 in:

- `tests/test_gap_close_model.py`
- `tests/test_clip_copy_paste_model.py`
- `tests/test_clip_groups_legacy_fixture.py`
- `tests/test_source_insert_model.py`
- `tests/test_track_edit_locks_legacy_fixture.py`
- `tests/test_media_bins_model.py`
- `tests/test_source_ranges_legacy_fixture.py`
- `tests/test_clip_groups_persistence.py`
- `tests/test_ripple_participation_model.py`
- `tests/test_insert_participation_model.py`
- `tests/test_track_edit_locks_persistence.py`
- `tests/test_source_overwrite_model.py`
- `tests/test_caption_layout_model.py`
- `tests/test_source_ranges_model.py`
- `tests/test_timeline_markers.py`
- `tests/test_clip_groups_model.py`
- `tests/test_track_edit_locks_model.py`

Additional narrow adaptations:

- `test_timeline_markers.py`: unsupported future version becomes41. Its genuine
  accepted schema38 comparison explicitly verifies/removes only default True,
  None fields before comparing every historical field. Fixture bytes/hash unchanged.
- `test_source_ranges_legacy_fixture.py`: same verified default removal for the
  genuine schema37 equality oracle. Fixture bytes/hash unchanged.
- `test_caption_layout_model.py`: full versions1..VERSION matrix explicitly
  strips new fields when building pre-40 payloads; every prior version remains.
- `timeline_markers_legacy_fixture.py`: invokes the new pre-40 helper before its
  existing marker removal. Its earlier invalid-version fence remains unchanged.

No media/audio/frame oracle is weakened, no test removed or skipped, and no
production schema acceptance is broadened. New strict schema and legacy-state
coverage resides in `test_source_audio_schema.py`. Natural VERSION-relative
caption/native-manifest node IDs will be recorded by the final collection audit.

- `test_clip_motion_model.py` and `test_clip_composite_model.py`: preserve the
  existing relative field order checks while recognizing the two appended Clip
  fields, whose exact final names are additionally asserted. No validation or
  rendering expectations change.

## Collection-preservation mapping

The accepted Copy/Paste collection contains 16,448 cases. The current schema40
collection retains every case, with exactly two natural node-ID mappings in the
**unchanged** `test_native_manifest.py` parametrization:

- `test_manifest_requires_exact_current_project_schema_in_runtime_evidence[38]`
  → `[39]`: previous-schema runtime evidence is still rejected.
- `test_manifest_requires_exact_current_project_schema_in_runtime_evidence[40]`
  → `[41]`: future-schema runtime evidence is still rejected.

Both derive from `[PROJECT_VERSION - 1, PROJECT_VERSION + 1, True, None]`.
The boolean/null cases retain their IDs and assertions. No skip or parameter
case was removed. The caption-layout `range(1, VERSION + 1)` matrix adds its new
`[40]` case while retaining all old version cases. The future-version check in
`test_timeline_markers.py` changes its inner payload40→41 without changing its
node ID or rejection semantics. Final collection counts and hashes belong to
the corresponding `TEST_NODE_PRESERVATION.json`, not this explanatory ledger.
