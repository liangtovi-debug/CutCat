# Schema 41 fixture-update ledger

Baseline: accepted `f074dfa5347981910216d69b4bb953aff24f3d07` (schema 40).
Production migration is strict: old formats reject the new field, schema 41
requires it. No production compatibility bypass or golden-output rewrite was
introduced. Existing captured JSON/NPZ/audio/compiler fixtures are unchanged.

## Narrow existing-test edits

Only the following historical-test support and current-writer assertions changed:

- `tests/source_audio_legacy_fixture.py`: chain the new, test-only
  `strip_audio_gain_keyframes_field` before removing the pre-40 source-audio
  fields. All previous guards and field removals remain unchanged.
- `tests/test_timeline_markers.py`: current writer assertions 40 → 41; future
  version fence now `VERSION + 1`; when comparing to captured schema 38, assert
  and remove exactly the newly migrated empty `audio_gain_keyframes` field.
- `tests/test_source_ranges_legacy_fixture.py`: current writer 40 → 41; when
  comparing to captured schema 37, assert and remove exactly the newly migrated
  empty `audio_gain_keyframes` field.
- Current-writer 40 → 41 assertions only in:
  - `tests/test_clip_copy_paste_model.py`
  - `tests/test_clip_groups_legacy_fixture.py`
  - `tests/test_clip_groups_model.py`
  - `tests/test_clip_groups_persistence.py`
  - `tests/test_gap_close_model.py`
  - `tests/test_insert_participation_model.py`
  - `tests/test_media_bins_model.py`
  - `tests/test_ripple_participation_model.py`
  - `tests/test_source_audio_reattach_model.py`
  - `tests/test_source_audio_schema.py`
  - `tests/test_source_insert_model.py`
  - `tests/test_source_overwrite_model.py`
  - `tests/test_source_ranges_model.py`
  - `tests/test_track_edit_locks_legacy_fixture.py`
  - `tests/test_track_edit_locks_model.py`
  - `tests/test_track_edit_locks_persistence.py`

- `tests/test_clip_composite_model.py` and `tests/test_clip_motion_model.py`:
  preserve the existing field-order checks while shifting their negative indices
  by one for the appended gain field; explicitly assert the complete three-field
  tail `source_audio_enabled, audio_mix_anchor, audio_gain_keyframes`.
  The initial full run exposed these two previously missed historical suffix
  assertions. No production field moved and no strict-settings assertion changed.

- `tests/test_source_audio_reattach_review.py`: retain the historical test ID,
  but seed distinct nonempty V/A envelopes, treat the envelope as the seventh
  approved audio field, and assert exact adoption of all current A audio values
  plus independent list ownership. The previous empty/empty case did not exercise
  envelope adoption; every unrelated-field equality assertion remains.

No old-format version assertion or expected rejection was weakened.

## New evidence

- `tests/audio_gain_legacy_fixture.py` removes only the new field, and only from
  explicit schema 1–40 test payloads. New tests assert its current/future/type
  rejection and preservation of all unrelated fields.
- `tests/fixtures/schema40-before-audio-gain-keyframes.json` was serialized by
  the unchanged accepted baseline, with bytecode writing disabled. SHA-256:
  `1e6438166fffddbd931f9b2f0c443bad2a3ae667be0ef9fa54562bfd75f0174b`.
  A new test migrates it, removes only the newly empty field and changes the
  writer tag back to 40, then compares every historical field exactly.
- `tests/test_audio_gain_keyframes_model.py` separately covers all 40 historical
  versions, malformed/overflow/duplicate/unsorted records, output budget,
  precise global-boundary rebasing, hidden handles, source edits, copy,
  separation/reattach, locks, persistence and undo/redo.

The first-version source overwrite guard conservatively rejects every affected
clip carrying an envelope, including fully covered removal, before transaction.
Source insert rejects cutting such a clip but permits proven whole-clip local
translations. No remnant with an incorrectly rebased envelope can be produced.
