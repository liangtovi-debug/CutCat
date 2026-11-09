"""Single-track Insert: lossless source content or whole-transaction refusal."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from threading import Thread
from uuid import uuid4

import pytest

from lumen.audio_mix import AudioMixBuilder
from lumen.audio_timing import compile_audio_timing
from lumen.model import (MAX_CAPTION_FRAME, Caption, Clip, ClipEffect, Editor,
                         Grade, Media, Overlay, Project, VERSION, VideoTransition)
from lumen.project_recovery import RecoveryStore
from lumen.timing import sample_boundary
from test_source_overwrite_model import (sample, state, rejected, pcm_at)


@pytest.mark.parametrize('track', range(4))
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('start', [0, 10, 15, 20, 30, 59, 60, 65, 90, 100, 110, 150, 200])
def test_insert_shapes_preserve_all_content_settings_gaps_other_tracks_and_history(track, fps, start):
    editor = sample(track, fps)
    original = deepcopy(editor.project)
    live = {c.id: c for c in editor.project.clips}
    before = state(editor)
    plan = editor.plan_source_insert('new', track, start, 11, 21)
    assert state(editor) == before
    assert plan.duration == 10 and plan.end == start + 10
    assert plan.affected_clip_ids == tuple(c.id for c in original.clips
                                         if c.track == track and c.end > start)
    split = [c for c in original.clips if c.track == track and c.start < start < c.end]
    assert plan.proof_clip_ids == tuple(c.id for c in split)
    assert len(plan.remnants) == 2 * len(split)
    assert len(plan.translations) == sum(c.track == track and c.start >= start for c in original.clips)
    inserted = editor.apply_source_insert(plan)
    assert inserted.grade == Grade() and inserted.start == start and inserted.source_in == 11
    assert inserted.duration == 10 and inserted.track == track
    for c in original.clips:
        if c.track != track or c.end <= start:
            assert editor._clip(c.id) is live[c.id]
            assert editor._clip(c.id) == c
        elif c.start >= start:
            moved = deepcopy(c); moved.start += 10
            assert editor._clip(c.id) == moved
        else:
            for remnant in [r for r in plan.remnants if r.original_clip_id == c.id]:
                wanted = deepcopy(c)
                wanted.id, wanted.start = remnant.clip_id, remnant.start
                wanted.source_in, wanted.duration = remnant.source_in, remnant.duration
                assert editor._clip(remnant.clip_id) == wanted
                assert editor._clip(remnant.clip_id).grade is not live[c.id].grade
    for field in ('media', 'captions', 'overlays', 'clip_groups', 'video_transitions',
                  'track_edit_locks', 'master_gain_db', 'media_bins', 'media_bin_ids'):
        assert getattr(editor.project, field) == getattr(original, field)
    def picture(project, t, f):
        matches = [c for c in project.clips if c.track == t and c.start <= f < c.end]
        return None if not matches else (matches[0].media_id,
             matches[0].source_in + f - matches[0].start, matches[0].grade)
    for t in range(4):
        for f in range(230):
            if t == track and start <= f < plan.end:
                assert picture(editor.project, t, f)[:2] == ('new', 11 + f - start)
            else:
                old_f = f - 10 if t == track and f >= plan.end else f
                assert picture(editor.project, t, f) == picture(original, t, old_f)
    assert len(editor._undo) == 1 and editor.revision == 1
    after = deepcopy(editor.project)
    assert Project.from_dict(after.to_dict()) == after
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == after


def local_audio_contract(clip, project):
    """Independent exact expected compiler signature, including gain/fades."""
    media = next(m for m in project.media if m.id == clip.media_id)
    timing = compile_audio_timing(clip, project.fps, media.audio_offset)
    single = Project(fps_num=project.fps_num, fps_den=project.fps_den,
                     media=[media], clips=[clip])
    fragment = AudioMixBuilder(single).clip_fragment(0, media)
    filters = None if fragment is None else fragment.fragment.rsplit(',adelay=', 1)[0]
    return replace(timing, start_sample=0), filters


@pytest.mark.parametrize('old_start', range(5))
@pytest.mark.parametrize('delta', range(1, 6))
@pytest.mark.parametrize('offset', [0, .001, -.001, .20001])
@pytest.mark.parametrize('fades', [(0, 0), (1, 2)])
def test_fractional_downstream_translation_compares_full_compiler_or_rejects(old_start, delta, offset, fades):
    media = Media('m', '/missing/m.wav', 'PCM', 20, has_video=False,
                  has_audio=True, audio_offset=offset)
    old = Clip('old', 'm', 2, old_start + 10, 0, 17, gain_db=-3,
               fade_in=fades[0], fade_out=fades[1])
    editor = Editor(Project(fps_num=30000, fps_den=1001, media=[media], clips=[old]))
    moved = deepcopy(old); moved.start += delta
    equal = local_audio_contract(old, editor.project) == local_audio_contract(moved, editor.project)
    if not equal:
        rejected(editor, lambda: editor.plan_source_insert('m', 2, 0, 50, 50 + delta), 'sample-grid phase')
    else:
        plan = editor.plan_source_insert('m', 2, 0, 50, 50 + delta)
        editor.apply_source_insert(plan)
        assert editor._clip('old') == moved
        assert editor._clip('old').fade_in == fades[0]
        assert editor._clip('old').fade_out == fades[1]


@pytest.mark.parametrize('offset', [0, .001, -.001, 2.1, 2.5])
@pytest.mark.parametrize('source_in', [60, 61, 62, 63, 64])
@pytest.mark.parametrize('cut', [30, 31, 32, 33, 34])
def test_fractional_split_retained_sample_mapping_before_and_after_shift(offset, source_in, cut):
    editor = sample(fps=Fraction(30000, 1001))
    editor.project.media[0].audio_offset = offset
    editor._clip('old').source_in = source_in
    old = deepcopy(editor._clip('old'))
    timing = compile_audio_timing(old, editor.project.fps, offset)
    before = state(editor)
    try:
        plan = editor.plan_source_insert('new', 0, cut, 0, 10)
    except ValueError as exc:
        assert 'sample-grid' in str(exc)
        assert state(editor) == before
        return
    editor.apply_source_insert(plan)
    delta = sample_boundary(10, editor.project.fps)
    for remnant in plan.remnants:
        compiled = compile_audio_timing(editor._clip(remnant.clip_id), editor.project.fps, offset)
        shift = delta if remnant.start >= plan.end else 0
        assert all(pcm_at(compiled, sample) == pcm_at(timing, sample - shift)
                   for sample in range(compiled.start_sample, compiled.start_sample + compiled.sample_count))


@pytest.mark.parametrize('target', ['old', 'later'])
@pytest.mark.parametrize('kind', ['group', 'transition', 'effect', 'motion', 'retimed'])
def test_unsupported_split_or_downstream_cases_reject_without_stripping_settings(target, kind):
    from lumen.clip_motion import ClipMotionKeyframe
    from lumen.timing import ClipTimeMap
    editor = sample()
    clip = editor._clip(target)
    if kind == 'group':
        editor.group_clips([target, 'other'])
    elif kind == 'transition':
        adjacent = Clip('adjacent', 'm', 0, clip.end, 250, 10)
        editor.project.clips.append(adjacent)
        editor.project.video_transitions = [VideoTransition('t', target, 'adjacent', 4)]
    elif kind == 'effect':
        clip.effects = [ClipEffect('fx', 'blur', 1, enabled=False)]
    elif kind == 'motion':
        clip.motion_keyframes = [ClipMotionKeyframe()]
    else:
        clip.time_map = ClipTimeMap(clip.source_in, clip.source_in + clip.duration * 2, 2)
    editor.project.validate()
    rejected(editor, lambda: editor.plan_source_insert('new', 0, 30, 0, 10))


@pytest.mark.parametrize('track', range(4))
def test_locked_target_rejects_even_empty_destination_but_other_locked_tracks_are_fixed(track):
    editor = sample(track=track)
    editor.set_track_edit_lock(track, True)
    rejected(editor, lambda: editor.plan_source_insert('new', track, 200, 0, 10), 'edit-locked')
    editor.set_track_edit_lock(track, False)
    editor.set_track_edit_lock((track + 1) % 4, True)
    original = deepcopy(editor.project)
    editor.apply_source_insert(editor.plan_source_insert('new', track, 30, 0, 10))
    assert editor._clip('other') == next(c for c in original.clips if c.id == 'other')


@pytest.mark.parametrize('field,bad', [('track', True), ('track', -1), ('track', 4),
    ('start', True), ('start', -1), ('start', MAX_CAPTION_FRAME), ('start', 1.5),
    ('source_in', -1), ('source_in', True), ('source_out', False),
    ('source_out', 0), ('source_out', 601), ('media_id', 'missing')])
def test_invalid_arguments_are_atomic(field, bad):
    editor = sample(); editor.move_clip('later', 91); editor.undo()
    args = dict(media_id='new', track=0, start=30, source_in=0, source_out=10)
    args[field] = bad
    rejected(editor, lambda: editor.plan_source_insert(**args))


def test_downstream_overflow_is_atomic():
    editor = sample()
    editor._clip('later').start = MAX_CAPTION_FRAME - 20
    editor.project.validate()
    rejected(editor, lambda: editor.plan_source_insert('new', 0, 30, 0, 10))


def test_muted_translation_can_keep_fractional_phase_but_split_fades_stay_unsupported():
    editor = sample(fps=Fraction(30000, 1001))
    editor._clip('later').muted = True
    editor._clip('later').fade_in = 1
    editor.apply_source_insert(editor.plan_source_insert('new', 0, 60, 0, 1))
    editor._clip('old').fade_in = 1
    rejected(editor, lambda: editor.plan_source_insert('new', 0, 30, 0, 10), 'cannot cut')


@pytest.mark.parametrize('mutate', [lambda e: e.move_clip('later', 91),
    lambda e: setattr(e._clip('later'), 'start', 91),
    lambda e: setattr(e.project.media[1], 'path', '/missing/relink.mov'),
    lambda e: setattr(e.project.captions[0], 'text', 'new'),
    lambda e: e.project.track_edit_locks.__setitem__(0, True),
    lambda e: setattr(e, 'project', deepcopy(e.project))])
def test_stale_direct_or_history_changes_reject(mutate):
    editor = sample(); plan = editor.plan_source_insert('new', 0, 30, 0, 10)
    mutate(editor)
    rejected(editor, lambda: editor.apply_source_insert(plan), 'stale')


def test_immutable_plan_tamper_replay_session_and_owner_protection():
    editor = sample(); plan = editor.plan_source_insert('new', 0, 30, 0, 10)
    with pytest.raises(FrozenInstanceError):
        plan.translations[0].start = 0
    for changed in (replace(plan, new_clip_id='other'), replace(plan, remnants=()),
        replace(plan, translations=()), replace(plan, affected_clip_ids=()),
        replace(plan, candidate_signature='x'), replace(plan, source_out=20),
        replace(plan, remnants=(plan.remnants[0], replace(plan.remnants[1], clip_id='later')))):
        rejected(editor, lambda: editor.apply_source_insert(changed))
    clone = deepcopy(editor)
    rejected(clone, lambda: clone.apply_source_insert(plan), 'stale')
    errors = []
    def worker():
        for call in (lambda: editor.plan_source_insert('new', 0, 30, 0, 10),
                     lambda: editor.apply_source_insert(plan)):
            try: call()
            except ValueError as exc: errors.append(str(exc))
    thread = Thread(target=worker); thread.start(); thread.join()
    assert len(errors) == 2 and all('owning thread' in error for error in errors)
    editor.apply_source_insert(plan)
    rejected(editor, lambda: editor.apply_source_insert(plan), 'stale')
    editor.undo(); rejected(editor, lambda: editor.apply_source_insert(plan), 'stale')


@pytest.mark.parametrize('asset_directory', ['assets', '素材 with spaces'])
def test_save_reopen_recovery_and_apple_log_defaults(tmp_path, asset_directory):
    editor = sample(); editor.project.media[1].input_encoding = 'apple_log_bt2020'
    # Round-trip comparisons require native absolute references, including on
    # Windows where '/missing/...' acquires a drive when the project is loaded.
    assets = (tmp_path / asset_directory).resolve()
    for media in editor.project.media:
        media.path = str(assets / f'{media.id}.mov')
    for existing in editor.project.clips:
        if existing.grade.lut_path:
            existing.grade.lut_path = str(assets / 'look.cube')
    for overlay in editor.project.overlays:
        overlay.asset_path = str(assets / f'{overlay.id}.png')
    clip = editor.apply_source_insert(editor.plan_source_insert('new', 0, 30, 10, 20))
    assert clip.grade.input_encoding == 'apple_log_bt2020'
    path = tmp_path / 'insert.lumen'; editor.project.save(path)
    assert Project.load(path).to_dict() == editor.project.to_dict()
    assert VERSION == editor.project.to_dict()['version'] == 43
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(editor.project, str(uuid4()), path)
        assert store.load(entry).to_dict() == editor.project.to_dict()
    finally: store.close()
