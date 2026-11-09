"""Independent window interpolation, strict schema and timeline edit contracts."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, fields, replace
from fractions import Fraction
import json

import numpy as np
import pytest

from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Clip, Editor, Grade, Media, Project, VERSION
from lumen.qualifier import (MAX_WINDOW_FRAME, MAX_WINDOW_KEYFRAMES, QualifierRange,
                             QualifierSettings, WindowKeyframe, apply_qualifier,
                             qualifier_mask)
from lumen.timing import ClipTimeMap, duration_frames
from lumen.window_animation import evaluate_grade, evaluate_window, shift_window_animation


GEOMETRY = ('center_x', 'center_y', 'width', 'height')


def animated(interpolation='linear'):
    return QualifierSettings(enabled=True, exposure=.5, window_shape='ellipse',
        window_center_x=.12, window_center_y=.87, window_width=.25,
        window_height=.45, window_feather=.33, window_invert=True,
        inclusion_ranges=[QualifierRange(hue_center=.1)],
        exclusion_ranges=[QualifierRange(hue_center=.8)],
        window_keyframes=[
            WindowKeyframe(-5, .1, .9, .2, 1.8, interpolation),
            WindowKeyframe(15, .7, .3, 1.4, .6, 'hold'),
            WindowKeyframe(30, .9, .1, .8, 1.2, 'linear'),
            WindowKeyframe(100, .2, .8, .4, 1.6, 'linear')])


def geometry(settings):
    return tuple(getattr(settings, f'window_{name}') for name in GEOMETRY)


def independent_expected(settings, frame):
    """Decimal-rational interpolation oracle, without any production helper."""
    keys = settings.window_keyframes
    left = keys[0]
    for right in keys[1:]:
        if frame < right.frame:
            if frame <= left.frame or left.interpolation == 'hold':
                return tuple(getattr(left, name) for name in GEOMETRY)
            weight = Fraction(frame - left.frame, right.frame - left.frame)
            return tuple(float(Fraction(str(getattr(left, name))) * (1-weight)
                               + Fraction(str(getattr(right, name))) * weight)
                         for name in GEOMETRY)
        left = right
    return tuple(getattr(left, name) for name in GEOMETRY)


@pytest.mark.parametrize('interpolation', ['hold', 'linear'])
@pytest.mark.parametrize('frame', [-100, -5, -4, 0, 5, Fraction(29, 2), 15,
                                   Fraction(59, 2), 30, Fraction(301, 7), 99, 100, 10000])
def test_interpolation_matches_independent_oracle_and_is_nonmutating(frame, interpolation):
    settings = animated(interpolation); before = deepcopy(settings)
    result = evaluate_window(settings, frame)
    assert geometry(result) == pytest.approx(independent_expected(settings, frame), abs=4e-16)
    assert result.window_keyframes == [] and settings == before and result is not settings
    for name in {f.name for f in fields(settings)} - {f'window_{n}' for n in GEOMETRY} - {'window_keyframes'}:
        assert getattr(result, name) == getattr(settings, name)
    result.inclusion_ranges.clear(); result.exclusion_ranges.clear()
    assert settings == before


def test_single_key_clamp_exact_endpoints_and_outgoing_hold():
    q = animated()
    for key in q.window_keyframes:
        assert geometry(evaluate_window(q, key.frame)) == tuple(getattr(key, n) for n in GEOMETRY)
    assert geometry(evaluate_window(q, Fraction(299999999, 10000000))) == geometry(evaluate_window(q, 15))
    q.window_keyframes = [WindowKeyframe(8, .3, .2, .8, .7)]
    for frame in (-MAX_WINDOW_FRAME * 10, Fraction(1, 3), 8, MAX_WINDOW_FRAME * 10):
        assert geometry(evaluate_window(q, frame)) == (.3, .2, .8, .7)


def test_static_path_returns_borrowed_objects_and_preserves_float_bits():
    q = QualifierSettings(enabled=True, hue_enabled=False, saturation_enabled=False,
                          luma_enabled=False, exposure=0)
    g = Grade(qualifier=q)
    assert evaluate_window(q, Fraction(1, 3)) is q
    assert evaluate_grade(g, 5) is g
    rgb = np.array([[[-0., .4, -1.], [.8, np.finfo(np.float32).max, .5]]], np.float32)
    np.testing.assert_array_equal(apply_qualifier(rgb, evaluate_window(q, 3)).view(np.uint32), rgb.view(np.uint32))
    result = evaluate_grade(Grade(qualifier=animated()), 10)
    assert result.qualifier.window_keyframes == []


@pytest.mark.parametrize('bad', [True, False, 0., 1.5, '2', None, float('nan'), float('inf'), [], {}])
def test_evaluator_rejects_ambiguous_frame_units_even_without_keys(bad):
    with pytest.raises(ValueError, match='frame'): evaluate_window(QualifierSettings(), bad)


@pytest.mark.parametrize('field,bad', [
    ('frame', True), ('frame', 1.), ('frame', Fraction(1)),
    ('frame', -MAX_WINDOW_FRAME-1), ('frame', MAX_WINDOW_FRAME+1),
    ('center_x', -.001), ('center_x', 1.001), ('center_y', float('nan')),
    ('center_y', True), ('width', 0), ('width', .0009), ('height', 2.001),
    ('height', float('inf')), ('width', '1'), ('interpolation', None),
    ('interpolation', 'smooth'), ('interpolation', 1)])
def test_keyframes_strictly_validate_even_for_disabled_qualifiers(field, bad):
    data = asdict(WindowKeyframe()); data[field] = bad
    with pytest.raises(ValueError): WindowKeyframe.from_dict(data)
    q = QualifierSettings(); payload = q.to_dict(); payload['window_keyframes'] = [data]
    with pytest.raises(ValueError): QualifierSettings.from_dict(payload)


def test_bounded_signed_keys_are_frozen_strict_and_own_their_container():
    keys = [WindowKeyframe(-MAX_WINDOW_FRAME), WindowKeyframe(MAX_WINDOW_FRAME)]
    q = QualifierSettings(window_keyframes=keys); keys.clear()
    assert len(q.window_keyframes) == 2
    with pytest.raises(FrozenInstanceError): q.window_keyframes[0].frame = 1
    assert QualifierSettings.from_dict(q.to_dict()) == q
    for name in asdict(WindowKeyframe()):
        data = asdict(WindowKeyframe()); del data[name]
        with pytest.raises(ValueError): WindowKeyframe.from_dict(data)
    with pytest.raises(ValueError): WindowKeyframe.from_dict({**asdict(WindowKeyframe()), 'extra': 1})
    for entries in ([WindowKeyframe(0), WindowKeyframe(0)],
                    [WindowKeyframe(1), WindowKeyframe(0)], [asdict(WindowKeyframe())],
                    tuple(), None, 'keys', [WindowKeyframe(i) for i in range(129)]):
        with pytest.raises(ValueError): QualifierSettings(window_keyframes=entries)
    assert len(QualifierSettings(window_keyframes=[WindowKeyframe(i) for i in range(128)]).window_keyframes) == MAX_WINDOW_KEYFRAMES
    mutated = QualifierSettings(window_keyframes=[WindowKeyframe()])
    object.__setattr__(mutated.window_keyframes[0], 'frame', True)
    with pytest.raises(ValueError): mutated.to_dict()
    with pytest.raises(ValueError): evaluate_window(mutated, 0)


def test_direct_raster_apis_require_an_explicit_animation_time():
    q = animated(); rgb = np.zeros((4, 4, 3), np.float32)
    for fn in (qualifier_mask, apply_qualifier):
        with pytest.raises(ValueError, match='evaluate_window'): fn(rgb, q)
        fn(rgb, evaluate_window(q, 7))
    q.enabled = False
    with pytest.raises(ValueError, match='evaluate_window'): qualifier_mask(rgb, q)
    np.testing.assert_array_equal(apply_qualifier(rgb, q), rgb)


def make_editor(speed=Fraction(3, 2)):
    mapping = ClipTimeMap(Fraction(21, 2), Fraction(141, 2), speed)
    clip = Clip('picture', 'm', 0, 10, 0, duration_frames(mapping),
                time_map=mapping, grade=Grade(qualifier=animated()))
    return Editor(Project(media=[Media('m', '/tmp/video.mp4', 'Video', 30, 96, 54)], clips=[clip]))


def assert_geometry_rebased(old, new, delta):
    assert [k.frame for k in new.grade.qualifier.window_keyframes] == [k.frame-delta for k in old.grade.qualifier.window_keyframes]
    for frame in [Fraction(i, 3) for i in range(-30, 331)]:
        assert geometry(evaluate_grade(new.grade, frame).qualifier) == geometry(evaluate_grade(old.grade, frame+delta).qualifier)


def assert_roundtrip_and_history(editor, before, tmp_path):
    after = deepcopy(editor.project)
    path = tmp_path / 'animated.lumen'; editor.project.save(path)
    assert json.loads(path.read_text())['version'] == VERSION
    assert Project.load(path) == after
    assert len(editor._undo) == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('mode', ['split', 'left_trim'])
def test_split_and_left_trim_keep_all_hidden_keys_and_exact_motion(speed, mode, tmp_path):
    editor = make_editor(speed); bind_missing_project_paths(editor.project, tmp_path); before = deepcopy(editor.project); old = before.clips[0]
    delta = 7
    if mode == 'split':
        left, right = editor.split_clip(old.id, old.start+delta)
        assert left.grade == old.grade
        assert left.grade is not right.grade
        assert_geometry_rebased(old, right, delta)
        assert right.source_in == old.source_in + delta*speed
        assert right.source_out == old.source_out
        assert [k.frame for k in left.grade.qualifier.window_keyframes] == [-5, 15, 30, 100]
    else:
        right = editor.trim_clip_left(old.id, delta)
        assert_geometry_rebased(old, right, delta)
        assert right.start == old.start+delta and right.end == old.end
    assert_roundtrip_and_history(editor, before, tmp_path)


@pytest.mark.parametrize('speed', [Fraction(2, 3), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('delta', [-3, 4])
def test_roll_preview_and_commit_shift_only_right_animation(speed, delta, tmp_path):
    editor = make_editor(speed); bind_missing_project_paths(editor.project, tmp_path); left = editor.project.clips[0]
    right_map = ClipTimeMap(60, Fraction(241, 2), Fraction(2, 3))
    right = Clip('right', 'm', 0, left.end, 0, duration_frames(right_map),
                 time_map=right_map, grade=Grade(qualifier=animated('hold')))
    editor.project.clips.append(right); editor.project.validate()
    before = deepcopy(editor.project); cut = left.end + delta
    preview = editor.preview_roll_cut(left.id, cut)
    assert editor.project == before and not editor.can_undo
    a, b = editor.roll_cut(left.id, cut)
    assert (a, b) == preview and a.grade == before.clips[0].grade
    assert_geometry_rebased(before.clips[1], b, delta)
    assert (a.start, a.end, b.start, b.end) == (left.start, cut, cut, before.clips[1].end)
    assert_roundtrip_and_history(editor, before, tmp_path)


@pytest.mark.parametrize('edit', ['slip', 'speed', 'source_trim', 'output_trim', 'update_source', 'update_duration', 'move'])
def test_source_edits_speed_and_moves_preserve_local_timeline_key_times(edit):
    editor = make_editor(); clip = editor.project.clips[0]; original = deepcopy(clip.grade)
    if edit == 'slip': editor.slip_clip(clip.id, Fraction(25, 2))
    elif edit == 'speed': editor.set_clip_speed(clip.id, Fraction(2, 3))
    elif edit == 'source_trim': editor.trim_clip_source(clip.id, Fraction(25, 2), 60)
    elif edit == 'output_trim': editor.trim_clip(clip.id, Fraction(25, 2), 20)
    elif edit == 'update_source': editor.update_clip(clip.id, source_in=Fraction(25, 2))
    elif edit == 'update_duration': editor.update_clip(clip.id, duration=20)
    else: editor.move_clip(clip.id, 23)
    assert editor.project.clips[0].grade == original
    assert Project.from_dict(editor.project.to_dict()) == editor.project


def test_shift_copies_all_mutable_data_and_rejects_overflow():
    original = Grade(qualifier=animated()); before = deepcopy(original)
    result = shift_window_animation(original, 17)
    assert original == before
    result.lift[0] = .2; result.qualifier.inclusion_ranges.clear()
    assert original == before
    for delta in (1., True, Fraction(1), '1'):
        with pytest.raises(ValueError): shift_window_animation(original, delta)
    grade = Grade(qualifier=QualifierSettings(window_keyframes=[WindowKeyframe(-MAX_WINDOW_FRAME)]))
    with pytest.raises(ValueError): shift_window_animation(grade, 1)


@pytest.mark.parametrize('edit', ['split', 'left_trim', 'roll'])
def test_overflow_is_atomic_and_preserves_redo(edit):
    editor = make_editor(); editor.move_clip('picture', 11); editor.undo()
    editor.project.clips[0].grade.qualifier.window_keyframes = [WindowKeyframe(-MAX_WINDOW_FRAME)]
    if edit == 'roll':
        left = editor.project.clips[0]
        editor.project.clips.append(Clip('right', 'm', 0, left.end, 60, 30,
                                        grade=deepcopy(left.grade)))
    before, undo, redo = deepcopy((editor.project, editor._undo, editor._redo))
    with pytest.raises(ValueError, match='bounded signed'):
        if edit == 'split': editor.split_clip('picture', 12)
        elif edit == 'left_trim': editor.trim_clip_left('picture', 2)
        else: editor.roll_cut('picture', editor.project.clips[0].end+2)
    assert (editor.project, editor._undo, editor._redo) == (before, undo, redo)


def test_copy_paste_retains_local_times_without_scaling_and_is_independent(tmp_path):
    editor = make_editor(); bind_missing_project_paths(editor.project, tmp_path); source = editor.project.clips[0]
    target = Clip('target', 'm', 0, source.end+50, 0, 7,
                  grade=Grade(input_encoding='srgb'))
    editor.project.clips.append(target); before = deepcopy(editor.project)
    copied = copy_grade(editor.project, 'picture')
    assert paste_grade(editor, 'target', copied)
    assert target.grade.input_encoding == 'srgb'
    assert target.grade.qualifier == before.clips[0].grade.qualifier
    assert target.grade.qualifier.window_keyframes is not source.grade.qualifier.window_keyframes
    assert_roundtrip_and_history(editor, before, tmp_path)
    editor.project.clips[0].grade.qualifier.window_keyframes.clear()
    assert len(editor.project.clips[1].grade.qualifier.window_keyframes) == 4


def legacy_payload(version):
    """Construct the exact published qualifier vocabulary for each old schema."""
    payload = make_editor().project.to_dict(); payload['version'] = version
    from clip_composite_legacy_fixture import strip_clip_composite_field
    strip_clip_composite_field(payload)
    grade = payload['clips'][0]['grade']; q = grade['qualifier']; q.pop('window_keyframes')
    if version < 27:
        grade.pop('white_balance_enabled'); grade.pop('white_balance_gains')
    if version < 26: grade.pop('contrast_pivot')
    if version < 25:
        for record in (q, *q['inclusion_ranges'], *q['exclusion_ranges']):
            for channel in ('hue', 'saturation', 'luma'): record.pop(f'{channel}_enabled')
    if version < 24:
        for record in (q, *q['inclusion_ranges'], *q['exclusion_ranges']):
            for channel in ('saturation', 'luma'):
                for edge in ('low', 'high'): record.pop(f'{channel}_softness_{edge}')
    if version < 23: q.pop('matte_morphology')
    if version < 22: q.pop('matte_smoothing')
    if version < 21:
        q.pop('matte_black'); q.pop('matte_white')
    if version < 20:
        q.pop('inclusion_ranges'); q.pop('exclusion_ranges')
    if version == 18:
        for name in list(q):
            if name.startswith('window_'): q.pop(name)
    return payload


@pytest.mark.parametrize('version', range(18, 30))
def test_all_legacy_qualifier_versions_migrate_without_accepting_smuggled_keys(version):
    payload = legacy_payload(version); before = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert restored.clips[0].grade.qualifier.window_keyframes == [] and payload == before
    assert restored.clips[0].grade.qualifier.exposure == .5
    if version >= 19: assert restored.clips[0].grade.qualifier.window_center_x == .12
    if version >= 20: assert len(restored.clips[0].grade.qualifier.inclusion_ranges) == 1
    for keys in ([], [asdict(WindowKeyframe())]):
        bad = deepcopy(payload); bad['clips'][0]['grade']['qualifier']['window_keyframes'] = keys
        with pytest.raises(ValueError, match='legacy qualifier'): Project.from_dict(bad)
    for mutation in ('missing', 'unknown'):
        bad = deepcopy(payload); q = bad['clips'][0]['grade']['qualifier']
        if mutation == 'missing': q.pop('exposure')
        else: q['unknown_new_field'] = 0
        with pytest.raises(ValueError): Project.from_dict(bad)


def test_current_schema_requires_keys_and_prior_reader_rejects_before_decoding(monkeypatch):
    payload = make_editor().project.to_dict()
    assert payload['version'] == VERSION and Project.from_dict(payload) == make_editor().project
    del payload['clips'][0]['grade']['qualifier']['window_keyframes']
    with pytest.raises(ValueError): Project.from_dict(payload)
    payload = make_editor().project.to_dict(); payload['clips'] = 'not a collection'
    import lumen.model as model
    monkeypatch.setattr(model, 'VERSION', 29)
    with pytest.raises(ValueError, match='Unsupported'): Project.from_dict(payload)


@pytest.mark.parametrize('reverse', [False, True])
def test_extreme_valid_geometry_and_subframe_endpoint_roundoff_stay_bounded(reverse):
    keys = [WindowKeyframe(-MAX_WINDOW_FRAME, 0, 1, 2, .001),
            WindowKeyframe(MAX_WINDOW_FRAME, 1, 0, .001, 2)]
    if reverse:
        keys = [replace(keys[1], frame=keys[0].frame), replace(keys[0], frame=keys[1].frame)]
    q = QualifierSettings(window_keyframes=keys)
    for frame in (keys[0].frame, keys[0].frame+Fraction(1, 10**100), 0,
                  keys[1].frame-Fraction(1, 10**100), keys[1].frame):
        result = evaluate_window(q, frame)
        result.to_dict()
        assert geometry(result) == pytest.approx(independent_expected(q, frame), abs=5e-16)


def test_recovery_retains_negative_and_outside_keys_with_independent_snapshots(tmp_path):
    from uuid import uuid4
    from lumen.project_recovery import RecoveryStore
    editor = make_editor(); bind_missing_project_paths(editor.project, tmp_path); editor.trim_clip_left('picture', 10)
    before = deepcopy(editor.project)
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(editor.project, uuid4())
        loaded = store.load(entry)
        assert loaded == before
        assert [k.frame for k in loaded.clips[0].grade.qualifier.window_keyframes] == [-15, 5, 20, 90]
        loaded.clips[0].grade.qualifier.window_keyframes.clear()
        assert store.load(entry) == before and editor.project == before


def test_noop_left_trim_and_roll_preserve_redo_and_motion():
    editor = make_editor(); clip = editor.project.clips[0]
    editor.project.clips.append(Clip('right', 'm', 0, clip.end, 60, 40,
                                    grade=deepcopy(clip.grade)))
    editor.move_clip('picture', 9); editor.undo()
    before, undo, redo = deepcopy((editor.project, editor._undo, editor._redo))
    editor.trim_clip_left('picture', 0)
    editor.roll_cut('picture', editor.project.clips[0].end)
    assert (editor.project, editor._undo, editor._redo) == (before, undo, redo)


def test_color_pipeline_rejects_active_unresolved_animation_and_accepts_explicit_time():
    from lumen.color import apply_grade
    grade = Grade(qualifier=animated())
    rgb = np.full((4, 4, 3), .4, np.float32)
    before = deepcopy(grade)
    with pytest.raises(ValueError, match='evaluate_grade'):
        apply_grade(rgb, grade)
    assert grade == before
    assert apply_grade(rgb, evaluate_grade(grade, Fraction(5, 2))).shape == rgb.shape


@pytest.mark.parametrize('mode', ['bypass', 'grade_disabled', 'qualifier_disabled'])
@pytest.mark.parametrize('encoding', ['rec709', 'srgb'])
def test_color_pipeline_bypasses_unresolved_animation_but_keeps_input_normalization(mode, encoding):
    from lumen.color import apply_grade
    grade = Grade(input_encoding=encoding, qualifier=animated())
    if mode == 'grade_disabled': grade.enabled = False
    if mode == 'qualifier_disabled': grade.qualifier.enabled = False
    rgb = np.array([[[-0., .4, .1], [.8, .03, .5]]], np.float32)
    expected = apply_grade(rgb, Grade(input_encoding=encoding), bypass=True)
    actual = apply_grade(rgb, grade, bypass=mode == 'bypass')
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
    assert not np.shares_memory(actual, rgb)
