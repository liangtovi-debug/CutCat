"""Schema-seven constant-speed edit invariants and legacy migrations."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from lumen.model import Caption, Clip, ClipEffect, Editor, Grade, Media, Project, VERSION
from lumen.timing import ClipTimeMap, duration_frames, source_position


def project(*, fps=Fraction(30), clips=None):
    return Project(fps_num=fps.numerator, fps_den=fps.denominator,
                   media=[Media('m', '/tmp/source.mp4', 'Source', 10, 64, 64, True, True)],
                   clips=[Clip('c', 'm', 0, 0, 0, 60)] if clips is None else clips)


def retimed(origin=5, endpoint=50, speed=Fraction(3, 2), start=0):
    mapping = ClipTimeMap(origin, endpoint, speed)
    return Clip('c', 'm', 0, start, 0, duration_frames(mapping), time_map=mapping)


@pytest.mark.parametrize('version', range(1, 7))
def test_real_legacy_migration_and_canonical_roundtrip(version):
    original = project()
    clip = original.clips[0]
    clip.source_in = 5
    clip.grade = Grade(exposure=2, lut_path='/tmp/look.cube')
    clip.effects = [ClipEffect('effect', 'vignette', .25)]
    original.captions = [Caption('caption', 2, 3, 'Keep my anchor')]
    payload = original.to_dict()
    payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    for item in payload['clips']:
        item.pop('restoration')
        item.pop('stabilization')
        mapping = item.pop('time_map')
        item['source_in'] = mapping['source_in_num']
    before = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert payload == before
    assert restored == original
    assert restored.clips[0].timing == ClipTimeMap(5, 65, 1)
    canonical = restored.to_dict()
    assert canonical['version'] == VERSION
    assert 'source_in' not in canonical['clips'][0]
    assert canonical['clips'][0]['time_map'] == ClipTimeMap(5, 65).to_dict()
    assert Project.from_dict(json.loads(json.dumps(canonical))) == restored


@pytest.mark.parametrize('version', range(1, 8))
def test_duplicate_or_wrong_generation_timing_rejected(version):
    data = project().to_dict()
    data['version'] = version; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    for item in data['clips']:
        item.pop('restoration')
        item.pop('stabilization')
    if version == 7:
        data['clips'][0]['source_in'] = 0
    with pytest.raises(ValueError):
        Project.from_dict(data)


def test_schema_seven_requires_map_and_validated_redundant_duration():
    for change in ['missing_map', 'bad_duration', 'zero_duration', 'unreduced_map']:
        data = project().to_dict()
        clip = data['clips'][0]
        if change == 'missing_map':
            clip.pop('time_map')
            clip['source_in'] = 0
        elif change == 'bad_duration':
            clip['duration'] -= 1
        elif change == 'zero_duration':
            clip['duration'] = 0
        else:
            clip['time_map']['speed_num'] = 2
            clip['time_map']['speed_den'] = 2
        with pytest.raises(ValueError):
            Project.from_dict(data)


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001), Fraction(24000, 1001)])
@pytest.mark.parametrize('rate', [Fraction(1, 4), Fraction(1, 2), Fraction(2, 3),
                                  Fraction(1), Fraction(3, 2), Fraction(2), Fraction(4)])
def test_repeated_odd_splits_preserve_span_phase_and_output_count(fps, rate):
    original = retimed(Fraction(5, 7), 71, rate, start=11)
    editor = Editor(project(fps=fps, clips=[original]))
    mapping, duration = original.timing, original.duration
    final_id = original.id
    while editor._clip(final_id).duration > 7:
        current = editor._clip(final_id)
        left, right = editor.split_clip(final_id, current.start + 7)
        assert left.source_out == right.timing.source_in
        assert left.duration == 7
        final_id = right.id
    clips = editor.project.clips
    assert sum(c.duration for c in clips) == duration
    assert sum((c.source_out - c.timing.source_in for c in clips), Fraction(0)) == mapping.source_out - mapping.source_in
    assert clips[0].timing.source_in == mapping.source_in
    assert clips[-1].source_out == mapping.source_out
    for clip in clips:
        for local in range(clip.duration):
            assert source_position(clip.timing, local) == source_position(mapping, clip.start - 11 + local)
    assert Project.from_dict(editor.project.to_dict()) == editor.project


def test_documented_fractional_split_fades_and_independence():
    clip = retimed()
    clip.fade_in, clip.fade_out = 15, 20
    clip.grade.exposure = 2
    editor = Editor(project(clips=[clip]))
    left, right = editor.split_clip('c', 7)
    assert (left.source_out, right.timing.source_in) == (Fraction(31, 2), Fraction(31, 2))
    assert (left.duration, right.duration) == (7, 23)
    assert (left.fade_in, left.fade_out, right.fade_in, right.fade_out) == (7, 0, 0, 20)
    assert left.grade is not right.grade


def test_speed_edit_retains_partial_tail_start_fades_and_undo_redo(tmp_path):
    editor = Editor(project(clips=[Clip('c', 'm', 0, 11, 0, 31, fade_in=20, fade_out=20)]))
    # Use a native canonical source path: macOS resolves /tmp to /private/tmp,
    # and Windows requires a drive. Keep the full round-trip equality assertion.
    editor.project.media[0].path = str((tmp_path / 'source.mp4').resolve())
    original = editor.project.to_dict()
    clip = editor.set_clip_speed('c', '2')
    assert (clip.start, clip.source_in, clip.source_out, clip.duration) == (11, 0, 31, 16)
    assert (clip.fade_in, clip.fade_out) == (16, 16)
    changed = editor.project.to_dict()
    editor.undo()
    assert editor.project.to_dict() == original
    editor.redo()
    assert editor.project.to_dict() == changed
    editor.project.save(tmp_path / 'speed.lumen')
    assert Project.load(tmp_path / 'speed.lumen') == editor.project
    editor.set_clip_speed('c', '1')
    assert editor.project.clips[0].duration == 31
    assert editor.project.clips[0].source_out == 31


def test_failed_speed_collision_and_redo_history_are_unchanged():
    editor = Editor(project(clips=[Clip('a', 'm', 0, 0, 0, 30), Clip('b', 'm', 0, 30, 0, 30)]))
    editor.set_clip_speed('a', 2)
    editor.undo()
    before = editor.project.to_dict()
    history = (deepcopy(editor._undo), deepcopy(editor._redo))
    with pytest.raises(ValueError, match='overlap'):
        editor.set_clip_speed('a', '0.5')
    assert editor.project.to_dict() == before
    assert (editor._undo, editor._redo) == history
    assert editor.redo()
    assert editor.project.clips[0].speed == 2


def test_source_and_timeline_trims_are_explicit_and_tail_preserving():
    editor = Editor(project(clips=[retimed(0, 31, 2, start=10)]))
    clip = editor.trim_clip_left('c', 3)
    assert (clip.start, clip.source_in, clip.source_out, clip.duration) == (13, 6, 31, 13)
    editor.move_clip('c', 20, 1)
    assert (clip.start, clip.source_in, clip.source_out, clip.duration, clip.track) == (20, 6, 31, 13, 1)
    clip = editor.trim_clip_source('c', '13/2', '61/2')
    assert (clip.source_in, clip.source_out, clip.duration) == (Fraction(13, 2), Fraction(61, 2), 12)
    clip = editor.trim_clip('c', Fraction(13, 2), 13)
    assert (clip.source_in, clip.source_out, clip.duration) == (Fraction(13, 2), Fraction(65, 2), 13)


def test_update_routes_timing_but_unrelated_changes_never_replace_tail():
    editor = Editor(project(clips=[retimed(0, 31, 2)]))
    baseline = editor.project.clips[0].timing
    editor.update_clip('c', start=5, gain_db=-3, grade=Grade(exposure=1))
    assert editor.project.clips[0].timing == baseline
    editor.update_clip('c', source_in='1.5')
    clip = editor.project.clips[0]
    assert (clip.source_in, clip.source_out, clip.duration) == (Fraction(3, 2), 31, 15)
    editor.update_clip('c', duration=12)
    assert clip.source_out == Fraction(51, 2)
    editor.update_clip('c', source_in=3, duration=10, start=10)
    assert (clip.source_in, clip.source_out, clip.duration, clip.start) == (3, 23, 10, 10)
    with pytest.raises(ValueError):
        editor.update_clip('c', time_map=ClipTimeMap(0, 1))


@pytest.mark.parametrize('edit', [lambda e: e.trim_clip_source('c', 0, 301),
    lambda e: e.trim_clip('c', 0, 151), lambda e: e.trim_clip_source('c', 31, 31),
    lambda e: e.trim_clip_left('c', 16), lambda e: e.trim_clip_left('c', True),
    lambda e: e.set_clip_speed('c', 0.5), lambda e: e.set_clip_speed('c', '4.000001'),
    lambda e: e.update_clip('c', source_in=-1), lambda e: e.update_clip('c', duration=0)])
def test_invalid_edit_is_atomic(edit):
    editor = Editor(project(clips=[retimed(0, 31, 2)]))
    before = editor.project.to_dict()
    with pytest.raises(ValueError):
        edit(editor)
    assert editor.project.to_dict() == before
    assert not editor.can_undo
    assert not editor.can_redo


def test_repeated_arithmetic_overflow_fails_instead_of_approximating():
    clip = retimed(Fraction(1, 999983), 31, Fraction(2, 3))
    editor = Editor(project(clips=[clip]))
    before = editor.project.to_dict()
    with pytest.raises(ValueError, match='rational bounds'):
        editor.split_clip('c', 1)
    assert editor.project.to_dict() == before
    assert not editor.can_undo


def test_exclusive_endpoint_checked_even_when_last_sample_is_inside():
    # Last selected position 298 is in bounds, but [298, 301) crosses the 300-frame extent.
    editor = Editor(project(clips=[Clip('c', 'm', 0, 0, 298, 2)]))
    before = editor.project.to_dict()
    with pytest.raises(ValueError, match='source duration'):
        editor.trim_clip_source('c', 298, 301)
    assert editor.project.to_dict() == before


def test_captions_do_not_retime_or_move_with_clip():
    editor = Editor(project(clips=[retimed(0, 31, 2)]))
    editor.project.captions = [Caption('caption', 5, 10, 'Absolute')]
    expected = deepcopy(editor.project.captions)
    editor.set_clip_speed('c', '1/4')
    editor.trim_clip_left('c', 7)
    editor.move_clip('c', 40)
    editor.split_clip('c', 47)
    assert editor.project.captions == expected


def test_direct_inconsistent_map_or_duration_mutation_is_rejected():
    p = project(clips=[retimed(0, 31, 2)])
    p.clips[0].duration = 15
    with pytest.raises(ValueError, match='duration does not match'):
        p.validate()
    p.clips[0].duration = 16
    p.clips[0].source_in = 1
    with pytest.raises(ValueError, match='origin does not match'):
        p.validate()
