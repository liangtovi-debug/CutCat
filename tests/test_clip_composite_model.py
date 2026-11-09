"""Strict schema 31, safe history and nondestructive clip compositing edits."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict, fields
from fractions import Fraction
import json

import pytest

from lumen.clip_composite import ClipComposite, validate_composite
from lumen.clip_transform import ClipTransform
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Clip, Editor, Grade, Media, Project, VERSION
from lumen.timing import ClipTimeMap


def project():
    return Project(media=[Media('m', '/tmp/composite.mp4', 'Picture', 30, 64, 48, True, True)],
                   clips=[Clip('c', 'm', 0, 10, 20, 60), Clip('next', 'm', 0, 70, 90, 50)])


def legacy_payload(version):
    p = project(); data = p.to_dict(); data['version'] = version
    from clip_composite_legacy_fixture import strip_clip_composite_field
    strip_clip_composite_field(data)
    for item in data['clips']:
        grade = item['grade']; q = grade['qualifier']
        if version < 30: q.pop('window_keyframes')
        if version < 27:
            grade.pop('white_balance_enabled'); grade.pop('white_balance_gains')
        if version < 26: grade.pop('contrast_pivot')
        if version < 25:
            for channel in ('hue', 'saturation', 'luma'): q.pop(f'{channel}_enabled')
        if version < 24:
            for channel in ('saturation', 'luma'):
                for edge in ('low', 'high'): q.pop(f'{channel}_softness_{edge}')
        if version < 23: q.pop('matte_morphology')
        if version < 22: q.pop('matte_smoothing')
        if version < 21: q.pop('matte_black'); q.pop('matte_white')
        if version < 20: q.pop('inclusion_ranges'); q.pop('exclusion_ranges')
        if version < 19:
            for name in list(q):
                if name.startswith('window_'): q.pop(name)
        if version < 18: grade.pop('qualifier')
        if version < 17: grade.pop('hue_hue_curve'); grade.pop('hue_sat_curve')
        if version < 16: item.pop('transform')
        if version < 9: item.pop('restoration')
        if version < 8: item.pop('stabilization')
        if version < 7:
            item['source_in'] = int(ClipTimeMap.from_dict(item.pop('time_map')).source_in)
    return data


def test_appended_field_and_strict_settings_roundtrip():
    assert fields(Clip)[-5].name == 'composite'
    assert [item.name for item in fields(Clip)[-3:]] == ['source_audio_enabled', 'audio_mix_anchor', 'audio_gain_keyframes']
    assert Clip('c', 'm', 0, 0, 0, 1).composite == ClipComposite()
    for toggle in (False, True):
        for opacity in (0, .123456789012345, 1):
            settings = ClipComposite(toggle, opacity)
            assert ClipComposite.from_dict(settings.to_dict()) == settings
            assert settings.is_legacy() == (not toggle and opacity == 1)


@pytest.mark.parametrize('field,value', [
    ('transparent_outside_transform', 0), ('transparent_outside_transform', 1),
    ('transparent_outside_transform', 'false'), ('transparent_outside_transform', None),
    ('opacity', True), ('opacity', False), ('opacity', -.01), ('opacity', 1.001),
    ('opacity', float('nan')), ('opacity', float('inf')), ('opacity', '0.5'), ('opacity', None),
])
def test_invalid_settings_rejected_at_construction_and_update(field, value):
    with pytest.raises(ValueError): ClipComposite(**{field: value})
    editor = Editor(project()); before = deepcopy(editor.project)
    settings = ClipComposite(); setattr(settings, field, value)
    with pytest.raises(ValueError): validate_composite(settings)
    with pytest.raises(ValueError): editor.update_clip('c', composite=settings)
    assert editor.project == before and not editor.can_undo and editor.revision == 0


@pytest.mark.parametrize('bad', [{}, [], None, {'opacity': .5},
    {'transparent_outside_transform': True}, {**asdict(ClipComposite()), 'blend_mode': 'screen'}])
def test_strict_current_schema_rejects_missing_unknown_and_wrong_type(bad):
    data = project().to_dict(); data['clips'][0]['composite'] = bad
    before = deepcopy(data)
    with pytest.raises(ValueError): Project.from_dict(data)
    assert data == before


def test_current_schema_requires_field_and_prior_reader_rejects_early(monkeypatch):
    data = project().to_dict(); assert data['version'] == VERSION
    del data['clips'][0]['composite']
    with pytest.raises(ValueError, match='require compositing'): Project.from_dict(data)
    data = project().to_dict(); data['clips'] = 'not decoded'
    monkeypatch.setattr('lumen.model.VERSION', 30)
    with pytest.raises(ValueError, match='Unsupported'): Project.from_dict(data)


@pytest.mark.parametrize('version', range(1, 31))
def test_every_historical_version_defaults_opaque_and_rejects_smuggling(version):
    data = legacy_payload(version); before = deepcopy(data)
    restored = Project.from_dict(data)
    assert restored == project() and data == before
    assert all(clip.composite.is_legacy() for clip in restored.clips)
    assert restored.to_dict()['version'] == VERSION
    for settings in (ClipComposite(), ClipComposite(True, .5)):
        forged = deepcopy(data); forged['clips'][0]['composite'] = settings.to_dict()
        with pytest.raises(ValueError, match='Legacy clips cannot contain compositing'):
            Project.from_dict(forged)


def test_edit_roundtrip_history_precision_and_zero_opacity_keeps_audio(tmp_path):
    editor = Editor(project()); bind_missing_project_paths(editor.project, tmp_path); before = deepcopy(editor.project)
    settings = ClipComposite(True, .123456789012345)
    editor.update_clip('c', composite=settings.to_dict()); after = deepcopy(editor.project)
    settings.opacity = .9
    assert editor.project.clips[0].composite.opacity == .123456789012345
    assert after.clips[0].timing == before.clips[0].timing
    path = tmp_path / 'composite.lumen'; editor.project.save(path)
    assert json.loads(path.read_text())['version'] == VERSION and Project.load(path) == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    undo = deepcopy(editor._undo); revision = editor.revision
    editor.update_clip('c', composite=deepcopy(after.clips[0].composite))
    assert editor.revision == revision and editor._undo == undo
    editor.update_clip('c', composite=ClipComposite(True, 0))
    c = editor._clip('c'); old = after.clips[0]
    assert (c.gain_db, c.muted, c.fade_in, c.fade_out) == (old.gain_db, old.muted, old.fade_in, old.fade_out)
    assert c.timing == old.timing and c.duration == old.duration


@pytest.mark.parametrize('track', [2, 3])
@pytest.mark.parametrize('settings', [ClipComposite(True), ClipComposite(False, .5), ClipComposite(True, 0)])
def test_audio_tracks_reject_nondefault_composite_atomically(track, settings):
    editor = Editor(project()); editor.update_clip('c', track=track)
    before = deepcopy(editor.project); history = deepcopy(editor._undo); revision = editor.revision
    with pytest.raises(ValueError, match='video track'): editor.update_clip('c', composite=settings)
    assert editor.project == before and editor._undo == history and editor.revision == revision
    editor.update_clip('c', composite=ClipComposite())
    assert editor.revision == revision


def test_audio_only_source_never_accepts_nondefault_composite():
    p = Project(media=[Media('audio', '/tmp/audio.wav', 'Audio', 1, has_video=False, has_audio=True)],
                clips=[Clip('a', 'audio', 2, 0, 0, 10)])
    p.clips[0].track = 0; p.clips[0].composite = ClipComposite(True)
    with pytest.raises(ValueError, match='Compositing requires'): p.validate()


@pytest.mark.parametrize('operation', ['trim', 'source_trim', 'left_trim', 'left_edge', 'right_edge',
                                      'split', 'slip', 'roll', 'speed', 'move', 'batch_move'])
def test_all_timeline_operations_preserve_composite_and_undo(operation):
    p = project(); p.clips[0].composite = ClipComposite(True, .123456789012345)
    p.clips[1].composite = ClipComposite(False, .67890123456789)
    editor = Editor(p); before = deepcopy(p)
    if operation == 'trim': editor.trim_clip('c', 25, 45)
    elif operation == 'source_trim': editor.trim_clip_source('c', Fraction(41, 2), 75)
    elif operation == 'left_trim': editor.trim_clip_left('c', 5)
    elif operation == 'left_edge': editor.trim_edge('c', 'left', -5)
    elif operation == 'right_edge': editor.trim_edge('c', 'right', -5)
    elif operation == 'split': editor.split_clip('c', 35)
    elif operation == 'slip': editor.slip_clip('c', Fraction(43, 2))
    elif operation == 'roll': editor.roll_cut('c', 75)
    elif operation == 'speed': editor.set_clip_speed('c', Fraction(3, 2))
    elif operation == 'move': editor.move_clip('c', 140, 1)
    elif operation == 'batch_move': editor.move_clips(('c', 'next'), 17)
    for clip in editor.project.clips:
        expected = before.clips[1].composite if clip.id == 'next' else before.clips[0].composite
        assert clip.composite == expected
    after = deepcopy(editor.project)
    assert len(editor._undo) == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    if operation == 'split':
        editor.project.clips[0].composite.opacity = .9
        assert editor.project.clips[1].composite.opacity == .123456789012345


def test_grade_transfer_excludes_compositing_and_geometry():
    p = project(); p.clips[0].grade = Grade(contrast=1.7)
    p.clips[0].composite = ClipComposite(True, .2)
    p.clips[1].composite = ClipComposite(False, .654321098765)
    p.clips[1].transform = ClipTransform(x=12, scale=.5)
    editor = Editor(p); before = deepcopy(p)
    assert paste_grade(editor, 'next', copy_grade(p, 'c'))
    assert editor._clip('next').grade == before.clips[0].grade
    assert editor._clip('next').composite == before.clips[1].composite
    assert editor._clip('next').transform == before.clips[1].transform
    assert editor._clip('c') == before.clips[0]


@pytest.mark.parametrize('operation', ['delete_clip', 'delete_clips', 'ripple_delete_clip'])
def test_deletions_preserve_sibling_composite_and_undo_restores_exact_settings(operation):
    p = project(); p.clips[0].composite = ClipComposite(True, .123456789012345)
    p.clips[1].composite = ClipComposite(False, .67890123456789)
    editor = Editor(p); before = deepcopy(p)
    getattr(editor, operation)(('c',) if operation == 'delete_clips' else 'c')
    assert len(editor.project.clips) == 1
    survivor = editor._clip('next')
    assert survivor.composite == before.clips[1].composite
    assert survivor.start == (10 if operation == 'ripple_delete_clip' else 70)
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
