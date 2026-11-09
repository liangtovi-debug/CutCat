# Schema39 existing-test adaptation inventory

Production migration fences are not relaxed. New marker tests additionally prove
that all versions 1–38 reject `timeline_markers`, even an empty/null value, and
that version39 requires it. The new test-only
`timeline_markers_legacy_fixture.strip_timeline_markers_field()` refuses current,
future, boolean and otherwise invalid versions. It removes only this new field
when explicitly constructing a pre-39 test payload.

Existing tracked test files changed for compatibility:

1. `tests/source_ranges_legacy_fixture.py`: pre-38 helper now calls the pre-39
   helper before removing `source_ranges`; all previous version assertions stay.
2. `tests/test_source_ranges_model.py`: current emitted version 38→39; assert
   `source_ranges` remains penultimate and `timeline_markers` is the new last
   Project dataclass field. All range validation/migration assertions unchanged.
3. `tests/test_source_ranges_legacy_fixture.py`: preserved genuine schema37
   comparison explicitly removes and verifies the new empty marker list before
   comparing every historical field; current emitted version 38→39. Original
   schema37 fixture bytes/hash and legacy-invalid-version tests unchanged.
4. `tests/test_track_edit_locks_legacy_fixture.py`: current emitted version 38→39.
5. `tests/test_track_edit_locks_model.py`: current emitted version 38→39.
6. `tests/test_track_edit_locks_persistence.py`: current emitted version 38→39.
7. `tests/test_clip_groups_legacy_fixture.py`: current emitted version 38→39.
8. `tests/test_clip_groups_model.py`: current emitted version 38→39.
9. `tests/test_clip_groups_persistence.py`: current emitted version 38→39.
10. `tests/test_media_bins_model.py`: current emitted version 38→39.
11. `tests/test_source_overwrite_model.py`: current emitted version 38→39.
12. `tests/test_source_insert_model.py`: current emitted version 38→39.
13. `tests/test_insert_participation_model.py`: current emitted version 38→39.
14. `tests/test_ripple_participation_model.py`: current emitted version 38→39.
15. `tests/test_gap_close_model.py`: current emitted version 38→39.
16. `tests/test_caption_layout_model.py`: its complete `range(1, VERSION + 1)`
    migration matrix explicitly invokes the pre-39 marker helper before older
    field helpers; this removes markers from its synthesized version38 payload
    without relaxing the production old-version/new-field fence. All 39 version
    cases and full caption style/content assertions remain unchanged.

These changes do not remove parameter cases, skip tests, change media/audio/frame
oracles, broaden historical acceptance, or alter expected content. New model
coverage is in `tests/test_timeline_markers.py`; its code-point-sized long string
parameters use short descriptive pytest IDs to retain the repository's portable
1024-UTF-16-unit node-ID budget. Payloads and validation expectations remain full
size.

The actual schema38 serializer capture, SHA-256 and construction provenance are
documented in [the schema contract](timeline-markers-v39.md). New fixture creation
never changes the existing schema37 fixture.

The unchanged `tests/test_native_manifest.py` parametrization uses
`PROJECT_VERSION - 1` and `PROJECT_VERSION + 1`. Its collected node suffixes
therefore change naturally from `[37]`/`[39]` to `[38]`/`[40]` when schema38
becomes39; both still reject non-current runtime evidence, with no test code
change, removed case or broadened acceptance. The collection preservation audit
records these two explicit node-ID mappings rather than misreporting them as
missing tests.
