"""Strict schema-32 clip motion, exact timeline edits and independent history."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, replace
from fractions import Fraction
import json
from uuid import uuid4

import pytest

from lumen.clip_composite import ClipComposite
from lumen.clip_motion import (ClipMotionKeyframe, MAX_MOTION_FRAME,
    MAX_MOTION_KEYFRAMES, evaluate_clip_motion, shift_clip_motion,
    validate_motion_keyframes)
from lumen.clip_transform import ClipTransform
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Clip, Editor, Grade, Media, Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import MAX_WINDOW_FRAME
from lumen.timing import ClipTimeMap, duration_frames


def animated(interpolation='linear'):
    return [ClipMotionKeyframe(-5, -12.5, 30, .2, .9, interpolation),
            ClipMotionKeyframe(15, 27.5, -10, 1.4, .3, 'hold'),
            ClipMotionKeyframe(30, -100, 200, .8, .1, 'linear'),
            ClipMotionKeyframe(100, 12, -8, .4, .8, 'linear')]


def values(key):
    return key.x, key.y, key.scale, key.opacity


def expected(keys, frame):
    """Independent decimal-rational outgoing-segment interpolation oracle."""
    left = keys[0]
    for right in keys[1:]:
        if frame < right.frame:
            if frame <= left.frame or left.interpolation == 'hold':
                return values(left)
            weight = Fraction(frame - left.frame) / (right.frame - left.frame)
            return tuple(float(Fraction(str(a)) * (1 - weight) + Fraction(str(b)) * weight)
                         for a, b in zip(values(left), values(right)))
        left = right
    return values(left)


@pytest.mark.parametrize('interpolation', ['linear', 'hold'])
@pytest.mark.parametrize('frame', [-100, -5, -4, 0, 5, Fraction(29, 2), 15,
                                  Fraction(59, 2), 30, Fraction(301, 7), 99, 100, 10000])
def test_evaluation_is_absolute_pure_and_matches_rational_oracle(frame, interpolation):
    keys = animated(interpolation); before = deepcopy(keys)
    actual = evaluate_clip_motion(keys, frame)
    assert isinstance(actual, tuple) and len(actual) == 4
    assert actual == pytest.approx(expected(keys, frame), abs=3e-14)
    assert keys == before


def test_exact_endpoints_outgoing_hold_and_single_key_clamp():
    keys = animated()
    for key in keys:
        assert evaluate_clip_motion(keys, key.frame) == values(key)
    assert evaluate_clip_motion(keys, Fraction(299999999, 10000000)) == values(keys[1])
    keys = [ClipMotionKeyframe(8, 1.25, -2.5, .3, .2, 'hold')]
    for frame in (-MAX_MOTION_FRAME * 10, Fraction(1, 3), 8, MAX_MOTION_FRAME * 10):
        assert evaluate_clip_motion(keys, frame) == values(keys[0])
    with pytest.raises(ValueError, match='static path'): evaluate_clip_motion([], 0)


@pytest.mark.parametrize('frame', [True, False, 0., 1.5, '2', None, float('nan'), float('inf'), [], {}])
def test_frame_units_are_strict_even_without_keys(frame):
    for keys in ([], animated()):
        with pytest.raises(ValueError, match='frame'): evaluate_clip_motion(keys, frame)


@pytest.mark.parametrize('name,bad', [
    ('frame', True), ('frame', 1.), ('frame', Fraction(1)),
    ('frame', -MAX_MOTION_FRAME - 1), ('frame', MAX_MOTION_FRAME + 1),
    ('x', -16384.001), ('x', 16384.001), ('x', True), ('y', float('nan')),
    ('y', float('inf')), ('y', None), ('scale', .09999), ('scale', 4.001),
    ('scale', '1'), ('opacity', -.001), ('opacity', 1.001), ('opacity', False),
    ('opacity', float('-inf')), ('interpolation', None), ('interpolation', 'smooth'),
    ('interpolation', 1), ('interpolation', 'Hold')])
def test_key_types_and_transform_composite_bounds_are_strict(name, bad):
    data = asdict(ClipMotionKeyframe()); data[name] = bad
    with pytest.raises(ValueError): ClipMotionKeyframe.from_dict(data)
    with pytest.raises(ValueError): ClipMotionKeyframe(**data)


def test_frozen_exact_key_schema_bounded_owned_container_and_revalidation():
    assert MAX_MOTION_FRAME == MAX_WINDOW_FRAME and MAX_MOTION_KEYFRAMES == 128
    keys = [ClipMotionKeyframe(-MAX_MOTION_FRAME), ClipMotionKeyframe(MAX_MOTION_FRAME)]
    clip = Clip('c', 'm', 0, 0, 0, 1, motion_keyframes=keys); keys.clear()
    assert len(clip.motion_keyframes) == 2 and fields(Clip)[-4].name == 'motion_keyframes'
    assert [item.name for item in fields(Clip)[-3:]] == ['source_audio_enabled', 'audio_mix_anchor', 'audio_gain_keyframes']
    with pytest.raises(FrozenInstanceError): clip.motion_keyframes[0].frame = 0
    assert ClipMotionKeyframe.from_dict(clip.motion_keyframes[0].to_dict()) == clip.motion_keyframes[0]
    for name in asdict(ClipMotionKeyframe()):
        data = asdict(ClipMotionKeyframe()); del data[name]
        with pytest.raises(ValueError): ClipMotionKeyframe.from_dict(data)
    for data in ({**asdict(ClipMotionKeyframe()), 'id': 'key'}, {}, [], None):
        with pytest.raises(ValueError): ClipMotionKeyframe.from_dict(data)
    for entries in ([ClipMotionKeyframe(), ClipMotionKeyframe()],
                    [ClipMotionKeyframe(1), ClipMotionKeyframe()], [asdict(ClipMotionKeyframe())],
                    (), None, 'keys', [ClipMotionKeyframe(i) for i in range(129)]):
        with pytest.raises(ValueError): validate_motion_keyframes(entries)
        with pytest.raises(ValueError): Clip('c', 'm', 0, 0, 0, 1, motion_keyframes=entries)
    validate_motion_keyframes([ClipMotionKeyframe(i) for i in range(128)])
    key = ClipMotionKeyframe(); object.__setattr__(key, 'frame', True)
    with pytest.raises(ValueError): key.to_dict()
    with pytest.raises(ValueError): evaluate_clip_motion([key], 0)


@pytest.mark.parametrize('reverse', [False, True])
def test_extreme_values_and_fraction_near_endpoints_remain_valid(reverse):
    keys = [ClipMotionKeyframe(-MAX_MOTION_FRAME, -16384, 16384, 4, 0),
            ClipMotionKeyframe(MAX_MOTION_FRAME, 16384, -16384, .1, 1)]
    if reverse:
        keys = [replace(keys[1], frame=keys[0].frame), replace(keys[0], frame=keys[1].frame)]
    for frame in (keys[0].frame, keys[0].frame + Fraction(1, 10**100), 0,
                  keys[1].frame - Fraction(1, 10**100), keys[1].frame):
        actual = evaluate_clip_motion(keys, frame)
        ClipMotionKeyframe(0, *actual)
        assert actual == pytest.approx(expected(keys, frame), abs=4e-12)


def make_editor(speed=Fraction(3, 2)):
    # Both fractional origin and a source tail not aligned to most output grids.
    mapping = ClipTimeMap(Fraction(21, 2), Fraction(142, 2), speed)
    clip = Clip('picture', 'm', 0, 10, 0, duration_frames(mapping), time_map=mapping,
        transform=ClipTransform(x=50, y=-20, scale=2, rotation=30, mirror_x=True),
        composite=ClipComposite(True, .7), motion_keyframes=animated())
    return Editor(Project(media=[Media('m', '/tmp/motion.mp4', 'Video', 30, 96, 54)], clips=[clip]))


def assert_rebased(old, new, delta):
    assert [key.frame for key in new.motion_keyframes] == [key.frame-delta for key in old.motion_keyframes]
    for frame in (Fraction(i, 3) for i in range(-90, 391)):
        assert evaluate_clip_motion(new.motion_keyframes, frame) == evaluate_clip_motion(old.motion_keyframes, frame+delta)
    assert new.transform == old.transform and new.composite == old.composite


def assert_roundtrip_history(editor, before, tmp_path):
    after = deepcopy(editor.project)
    path = tmp_path / 'motion.lumen'; editor.project.save(path)
    assert json.loads(path.read_text())['version'] == VERSION
    assert Project.load(path) == after
    assert len(editor._undo) == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('edit', ['split', 'left_trim', 'left_edge', 'left_extend'])
def test_left_origin_edits_keep_hidden_handles_exact_continuity_and_one_undo(speed, edit, tmp_path):
    editor = make_editor(speed); bind_missing_project_paths(editor.project, tmp_path); before = deepcopy(editor.project); old = before.clips[0]
    delta = -2 if edit == 'left_extend' else 7
    if edit == 'split':
        left, right = editor.split_clip(old.id, old.start+delta)
        assert left.motion_keyframes == old.motion_keyframes
        assert left.motion_keyframes is not right.motion_keyframes
    elif edit == 'left_trim':
        right = editor.trim_clip_left(old.id, delta)
    else:
        preview = editor.preview_trim_edge(old.id, 'left', delta)
        assert editor.project == before and not editor.can_undo
        right = editor.trim_edge(old.id, 'left', delta)
        assert right == preview
    assert_rebased(old, right, delta)
    assert right.source_in == old.source_in + delta*speed
    assert right.source_out == old.source_out
    assert_roundtrip_history(editor, before, tmp_path)


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('delta', [-2, 4])
def test_roll_preview_and_commit_shift_only_right_motion(speed, delta, tmp_path):
    editor = make_editor(speed); bind_missing_project_paths(editor.project, tmp_path); left = editor.project.clips[0]
    mapping = ClipTimeMap(60, Fraction(241, 2), Fraction(2, 3))
    right = Clip('right', 'm', 0, left.end, 0, duration_frames(mapping), time_map=mapping,
                 motion_keyframes=animated('hold'))
    editor.project.clips.append(right); editor.project.validate()
    before = deepcopy(editor.project); cut = left.end + delta
    preview = editor.preview_roll_cut(left.id, cut)
    assert editor.project == before and not editor.can_undo
    a, b = editor.roll_cut(left.id, cut)
    assert (a, b) == preview and a.motion_keyframes == before.clips[0].motion_keyframes
    assert_rebased(before.clips[1], b, delta)
    assert_roundtrip_history(editor, before, tmp_path)


@pytest.mark.parametrize('edit', ['slip', 'speed', 'source_trim', 'output_trim', 'update_source',
                                  'update_duration', 'move', 'batch_move', 'right_edge'])
def test_non_origin_edits_preserve_all_key_times_and_values(edit):
    editor = make_editor(); clip = editor.project.clips[0]; original = deepcopy(clip.motion_keyframes)
    if edit == 'slip': editor.slip_clip(clip.id, Fraction(25, 2))
    elif edit == 'speed': editor.set_clip_speed(clip.id, Fraction(2, 3))
    elif edit == 'source_trim': editor.trim_clip_source(clip.id, Fraction(25, 2), 60)
    elif edit == 'output_trim': editor.trim_clip(clip.id, Fraction(25, 2), 20)
    elif edit == 'update_source': editor.update_clip(clip.id, source_in=Fraction(25, 2))
    elif edit == 'update_duration': editor.update_clip(clip.id, duration=20)
    elif edit == 'batch_move':
        editor.project.clips.append(Clip('other', 'm', 1, 0, 0, 30, motion_keyframes=animated()))
        editor.move_clips((clip.id, 'other'), 11)
    elif edit == 'right_edge':
        preview = editor.preview_trim_edge(clip.id, 'right', -5)
        assert preview.motion_keyframes == original
        editor.trim_edge(clip.id, 'right', -5)
    else: editor.move_clip(clip.id, 23)
    assert all(c.motion_keyframes == original for c in editor.project.clips)
    assert Project.from_dict(editor.project.to_dict()) == editor.project


def test_rebase_detaches_mutable_state_and_rejects_overflow_without_mutation():
    original = make_editor().project.clips[0]; before = deepcopy(original)
    result = shift_clip_motion(original, 17)
    assert_rebased(original, result, 17)
    result.motion_keyframes.clear(); result.grade.lift[0] = .2; result.transform.x = 10
    assert original == before
    for delta in (1., True, Fraction(1), '1'):
        with pytest.raises(ValueError): shift_clip_motion(original, delta)
    original.motion_keyframes = [ClipMotionKeyframe(-MAX_MOTION_FRAME)]
    before = deepcopy(original)
    with pytest.raises(ValueError): shift_clip_motion(original, 1)
    assert original == before


@pytest.mark.parametrize('edit', ['split', 'left_trim', 'left_edge', 'left_extend', 'roll', 'preview_trim', 'preview_roll'])
def test_motion_overflow_is_atomic_preserving_history_redo_and_revision(edit):
    editor = make_editor(); editor.move_clip('picture', 11); editor.undo()
    boundary = MAX_MOTION_FRAME if edit == 'left_extend' else -MAX_MOTION_FRAME
    editor.project.clips[0].motion_keyframes = [ClipMotionKeyframe(boundary)]
    if edit in ('roll', 'preview_roll'):
        left = editor.project.clips[0]
        editor.project.clips.append(Clip('right', 'm', 0, left.end, 60, 30,
                                        motion_keyframes=deepcopy(left.motion_keyframes)))
    before, undo, redo = deepcopy((editor.project, editor._undo, editor._redo)); revision = editor.revision
    with pytest.raises(ValueError, match='bounded signed'):
        if edit == 'split': editor.split_clip('picture', 12)
        elif edit == 'left_trim': editor.trim_clip_left('picture', 2)
        elif edit == 'left_edge': editor.trim_edge('picture', 'left', 2)
        elif edit == 'left_extend': editor.trim_edge('picture', 'left', -2)
        elif edit == 'preview_trim': editor.preview_trim_edge('picture', 'left', 2)
        elif edit == 'preview_roll': editor.preview_roll_cut('picture', editor.project.clips[0].end+2)
        else: editor.roll_cut('picture', editor.project.clips[0].end+2)
    assert (editor.project, editor._undo, editor._redo) == (before, undo, redo)
    assert editor.revision == revision


@pytest.mark.parametrize('as_dict', [False, True])
def test_update_accepts_typed_or_strict_dict_keys_owns_them_and_is_one_undo(as_dict, tmp_path):
    editor = make_editor(); bind_missing_project_paths(editor.project, tmp_path); editor.project.clips[0].motion_keyframes = []
    before = deepcopy(editor.project); entries = animated()
    if as_dict: entries = [key.to_dict() for key in entries]
    editor.update_clip('picture', motion_keyframes=entries); entries.clear()
    assert editor.project.clips[0].motion_keyframes == animated()
    assert_roundtrip_history(editor, before, tmp_path)


def test_update_noop_invalid_edit_and_static_conversion_preserve_history():
    editor = make_editor(); editor.move_clip('picture', 11); editor.undo()
    before, undo, redo = deepcopy((editor.project, editor._undo, editor._redo)); revision = editor.revision
    editor.update_clip('picture', motion_keyframes=deepcopy(animated()))
    for entries in ([{}], [{'frame': 0}], None, (), [ClipMotionKeyframe(1), ClipMotionKeyframe(0)]):
        with pytest.raises(ValueError): editor.update_clip('picture', motion_keyframes=entries)
    assert (editor.project, editor._undo, editor._redo) == (before, undo, redo)
    assert editor.revision == revision
    old = before.clips[0]; wanted = expected(old.motion_keyframes, Fraction(5, 2))
    clip = editor.convert_clip_motion_to_static('picture', Fraction(5, 2))
    assert clip.motion_keyframes == []
    assert (clip.transform.x, clip.transform.y, clip.transform.scale, clip.composite.opacity) == pytest.approx(wanted)
    assert (clip.transform.rotation, clip.transform.mirror_x, clip.transform.mirror_y,
            clip.composite.transparent_outside_transform) == (30, True, False, True)
    assert clip.timing == old.timing and clip.grade == old.grade and clip.effects == old.effects
    after = deepcopy(editor.project)
    assert len(editor._undo) == 1 and not editor.can_redo
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    revision = editor.revision; undo = deepcopy(editor._undo)
    editor.convert_clip_motion_to_static('picture', 4)
    assert editor.revision == revision and editor._undo == undo
    with pytest.raises(ValueError): editor.convert_clip_motion_to_static('picture', True)


@pytest.mark.parametrize('track', [2, 3])
def test_motion_is_picture_only_even_for_identity_keys(track):
    editor = make_editor(); editor.project.clips[0].motion_keyframes = []
    editor.project.media[0].has_audio = True
    editor.project.clips[0].transform = ClipTransform(); editor.project.clips[0].composite = ClipComposite()
    editor.update_clip('picture', track=track); before = deepcopy(editor.project)
    with pytest.raises(ValueError, match='Motion requires'):
        editor.update_clip('picture', motion_keyframes=[ClipMotionKeyframe()])
    assert editor.project == before
    editor.project.media[0].has_video = False
    editor.project.clips[0].motion_keyframes = [ClipMotionKeyframe()]
    with pytest.raises(ValueError, match='Motion requires'): editor.project.validate()


def test_grade_copy_paste_excludes_clip_motion_and_static_geometry():
    editor = make_editor(); original = editor.project.clips[0]
    editor.project.clips.append(Clip('target', 'm', 1, 0, 0, 20,
        motion_keyframes=[ClipMotionKeyframe(9, 99, 12, .3, .2)], grade=Grade(contrast=2)))
    before = deepcopy(editor.project)
    assert paste_grade(editor, 'picture', copy_grade(editor.project, 'target'))
    assert original.motion_keyframes == before.clips[0].motion_keyframes
    assert original.transform == before.clips[0].transform and original.composite == before.clips[0].composite
    assert editor._clip('target') == before.clips[1]


@pytest.mark.parametrize('version', range(1, 32))
def test_all_historical_schemas_default_empty_and_reject_any_smuggled_motion(version):
    from test_clip_composite_model import legacy_payload, project
    from clip_motion_legacy_fixture import strip_clip_motion_field
    if version < 31:
        data = legacy_payload(version)
    else:
        data = project().to_dict(); data['version'] = version; strip_clip_motion_field(data)
    before = deepcopy(data); restored = Project.from_dict(data)
    assert restored == project() and data == before
    assert all(clip.motion_keyframes == [] for clip in restored.clips)
    assert restored.to_dict()['version'] == VERSION
    for smuggled in ([], None, [ClipMotionKeyframe().to_dict()]):
        forged = deepcopy(data); forged['clips'][0]['motion_keyframes'] = smuggled
        with pytest.raises(ValueError, match='Legacy clips cannot contain motion_keyframes'):
            Project.from_dict(forged)


def test_schema31_migration_preserves_nondefault_static_picture_exactly():
    from clip_motion_legacy_fixture import strip_clip_motion_field
    editor = make_editor(); editor.project.clips[0].motion_keyframes = []
    data = editor.project.to_dict(); data['version'] = 31
    strip_clip_motion_field(data)
    assert Project.from_dict(data) == editor.project


@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(3, 2), Fraction(4)])
def test_fractional_project_rate_does_not_rescale_local_split_keys(fps, speed):
    editor = make_editor(speed)
    editor.project.fps_num, editor.project.fps_den = fps.numerator, fps.denominator
    editor.project.validate(); before = deepcopy(editor.project)
    left, right = editor.split_clip('picture', 17)
    assert_rebased(before.clips[0], right, 7)
    assert left.motion_keyframes == before.clips[0].motion_keyframes
    assert left.duration + right.duration == before.clips[0].duration
    assert right.source_out == before.clips[0].source_out
    assert Project.from_dict(editor.project.to_dict()) == editor.project


def test_schema32_requires_field_and_prior_reader_rejects_before_decoding(monkeypatch):
    data = make_editor().project.to_dict(); assert data['version'] == VERSION
    assert Project.from_dict(data) == make_editor().project
    del data['clips'][0]['motion_keyframes']
    with pytest.raises(ValueError, match='require motion_keyframes'): Project.from_dict(data)
    data = make_editor().project.to_dict(); data['clips'] = 'not decoded'
    monkeypatch.setattr('lumen.model.VERSION', 31)
    with pytest.raises(ValueError, match='Unsupported'): Project.from_dict(data)


@pytest.mark.parametrize('keys', [None, {}, (), [None], [{}], [True],
    [{**asdict(ClipMotionKeyframe()), 'id': 'key'}],
    [asdict(ClipMotionKeyframe())] * 129])
def test_schema32_rejects_ambiguous_malformed_and_unbounded_keys(keys):
    data = make_editor().project.to_dict(); data['clips'][0]['motion_keyframes'] = keys
    before = deepcopy(data)
    with pytest.raises(ValueError): Project.from_dict(data)
    assert data == before


def test_recovery_keeps_hidden_keys_and_independent_snapshots(tmp_path):
    editor = make_editor(); bind_missing_project_paths(editor.project, tmp_path); editor.trim_clip_left('picture', 10); before = deepcopy(editor.project)
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(editor.project, uuid4()); loaded = store.load(entry)
        assert loaded == before
        assert [key.frame for key in loaded.clips[0].motion_keyframes] == [-15, 5, 20, 90]
        loaded.clips[0].motion_keyframes.clear()
        assert store.load(entry) == before and editor.project == before


def test_noop_origin_edits_preserve_redo_and_motion():
    editor = make_editor(); clip = editor.project.clips[0]
    editor.project.clips.append(Clip('right', 'm', 0, clip.end, 60, 40, motion_keyframes=animated()))
    editor.move_clip('picture', 9); editor.undo()
    before, undo, redo = deepcopy((editor.project, editor._undo, editor._redo)); revision = editor.revision
    editor.trim_clip_left('picture', 0)
    editor.trim_edge('picture', 'left', 0)
    editor.trim_edge('picture', 'right', 0)
    editor.roll_cut('picture', editor.project.clips[0].end)
    assert (editor.project, editor._undo, editor._redo) == (before, undo, redo)
    assert editor.revision == revision


def test_known_motion_diagnostics_translate_without_touching_unknown_details():
    from lumen.i18n import set_locale, tr_error
    from lumen.i18n_catalog_clip_motion import ERROR_ZH_CN
    set_locale('zh-CN')
    try:
        for source, translation in ERROR_ZH_CN.items():
            assert tr_error(ValueError(source)) == translation
        diagnostic = 'Unknown motion failure in /tmp/user_file.mp4'
        assert diagnostic in tr_error(ValueError(diagnostic))
    finally:
        set_locale('en')
    assert tr_error(ValueError('Motion animation delta must be an integer')) == 'Motion animation delta must be an integer'
