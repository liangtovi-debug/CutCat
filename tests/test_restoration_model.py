"""Schema-nine restoration remains strict, nondestructive and transactional."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from lumen.model import Clip, ClipEffect, Editor, Grade, Media, Project, VERSION
from lumen.restoration_settings import RestorationSettings, validate_restoration_settings
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap


def project(settings=None, *, track=0, has_video=True):
    return Project(media=[Media('m', '/tmp/source.mp4', 'Source', 10, 64, 64, has_video, True)],
                   clips=[Clip('c', 'm', track, 10, 0, 60,
                               restoration=settings if settings is not None else RestorationSettings())])


def legacy(project, version):
    payload = project.to_dict(); payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    for item in payload['clips']:
        item.pop('restoration')
        if version < 8:
            item.pop('stabilization')
        if version < 7:
            mapping = item.pop('time_map'); item['source_in'] = mapping['source_in_num']
    return payload


def test_identity_defaults_independent_and_old_positional_constructors_survive():
    old = Clip('c', 'm', 0, 1, 0, 30, Grade(contrast=1.2), -2, False, 2, 3,
               [ClipEffect('fx', 'vignette', .3)], ClipTimeMap(0, 60, 2),
               StabilizationSettings(True, .5, 15, 1.2, 'black'))
    other = Clip('d', 'm', 0, 31, 0, 30)
    assert old.restoration == RestorationSettings(False, 0, 0, 0)
    assert old.restoration is not other.restoration
    old.restoration.denoise = .5
    assert other.restoration.denoise == 0
    assert old.speed == 2 and old.grade.contrast == 1.2 and old.fade_out == 3
    assert old.stabilization.enabled


@pytest.mark.parametrize('field', ['deblock', 'denoise', 'sharpen'])
@pytest.mark.parametrize('value', [-.01, 1.01, True, False, '0.5', None, [], {},
                                  float('nan'), float('inf'), -float('inf')])
def test_strength_values_rejected_including_disabled_settings(field, value):
    with pytest.raises(ValueError): RestorationSettings(**{field: value})
    settings = RestorationSettings(); setattr(settings, field, value)
    with pytest.raises(ValueError): validate_restoration_settings(settings)
    with pytest.raises(ValueError): project(settings).to_dict()


@pytest.mark.parametrize('value', [0, 1, 'true', None, [], {}])
def test_enabled_is_strict_boolean(value):
    with pytest.raises(ValueError): RestorationSettings(enabled=value)


@pytest.mark.parametrize('value', [0, 1, .123456789])
def test_strength_bounds_and_precision_roundtrip(value):
    settings = RestorationSettings(True, value, value, value)
    assert RestorationSettings.from_dict(settings.to_dict()) == settings


def test_schema_nine_roundtrip_preserves_precision_and_input():
    p = project(RestorationSettings(True, .123456789, .987654321, .456789123))
    p.clips[0].stabilization = StabilizationSettings(True, .7, 19, 1.23, 'black')
    p.clips[0].grade = Grade(linear_exposure=.5)
    payload = p.to_dict(); before = deepcopy(payload)
    assert payload['version'] == VERSION
    assert payload['clips'][0]['restoration'] == p.clips[0].restoration.to_dict()
    assert Project.from_dict(json.loads(json.dumps(payload))) == p and payload == before


@pytest.mark.parametrize('version', range(1, 9))
def test_all_legacy_schemas_migrate_to_disabled_defaults(version):
    original = project(); original.clips[0].source_in = 5
    if version == 8:
        original.clips[0].stabilization = StabilizationSettings(True, .8, 27, 1.28, 'black')
    payload = legacy(original, version); before = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert restored == original and payload == before
    assert restored.to_dict()['version'] == VERSION
    assert restored.clips[0].restoration == RestorationSettings()


@pytest.mark.parametrize('version', range(1, 9))
def test_legacy_schemas_reject_unexpected_restoration(version):
    payload = legacy(project(), version)
    payload['clips'][0]['restoration'] = RestorationSettings().to_dict()
    with pytest.raises(ValueError, match='Legacy clips cannot contain restoration'):
        Project.from_dict(payload)


@pytest.mark.parametrize('mutation', ['missing', 'partial', 'unknown', 'none', 'list', 'object', 'legacy_timing'])
def test_schema_nine_requires_complete_settings(mutation):
    payload = project().to_dict(); item = payload['clips'][0]
    if mutation == 'missing': item.pop('restoration')
    elif mutation == 'partial': item['restoration'].pop('enabled')
    elif mutation == 'unknown': item['restoration']['temporal'] = True
    elif mutation == 'none': item['restoration'] = None
    elif mutation == 'list': item['restoration'] = []
    elif mutation == 'object': item['restoration'] = RestorationSettings()
    else: item['source_in'] = 0
    with pytest.raises(ValueError): Project.from_dict(payload)


def test_atomic_edit_undo_redo_noop_and_external_alias_isolation():
    editor = Editor(project()); before = deepcopy(editor.project)
    settings = RestorationSettings(True, .3, .4, .2)
    clip = editor.update_clip('c', restoration=settings)
    after = deepcopy(editor.project)
    assert clip.restoration == settings and clip.restoration is not settings
    settings.denoise = .9
    assert editor._clip('c').restoration.denoise == .4
    editor.undo(); assert editor.project == before
    editor.redo(); assert editor.project == after
    depth = len(editor._undo)
    editor.update_restoration('c', denoise=.4)
    assert len(editor._undo) == depth
    editor.update_restoration('c', enabled=False)
    assert len(editor._undo) == depth + 1
    assert editor._clip('c').restoration == RestorationSettings(False, .3, .4, .2)
    editor.undo(); assert editor.project == after
    redo = deepcopy(editor._redo); undo = deepcopy(editor._undo)
    for change in ({'denoise': 1.1}, {'invented': True}, {'enabled': 1}):
        with pytest.raises(ValueError): editor.update_restoration('c', **change)
        assert editor.project == after and editor._redo == redo and editor._undo == undo


def test_dict_edit_is_complete_and_atomic_with_other_clip_properties():
    editor = Editor(project()); before = deepcopy(editor.project)
    with pytest.raises(ValueError): editor.update_clip('c', start=50, restoration={'enabled': True})
    assert editor.project == before and not editor.can_undo
    editor.update_clip('c', start=50, restoration=RestorationSettings(True, .2, .3, .4).to_dict())
    assert editor._clip('c').start == 50 and editor._clip('c').restoration.enabled
    editor.undo(); assert editor.project == before


def test_split_independent_copies_and_speed_trim_preserve_settings():
    settings = RestorationSettings(True, .2, .3, .4)
    editor = Editor(project(settings))
    editor.set_clip_speed('c', Fraction(3, 2))
    editor.trim_clip_source('c', Fraction(1, 2), Fraction(101, 2))
    editor.trim_clip_left('c', 1)
    assert editor._clip('c').restoration == settings
    left, right = editor.split_clip('c', editor._clip('c').start + 7)
    assert left.restoration == right.restoration == settings
    assert left.restoration is not right.restoration
    editor.update_restoration(right.id, denoise=.8)
    assert left.restoration.denoise == .3 and right.restoration.denoise == .8
    assert left.source_out == right.source_in and left.speed == right.speed == Fraction(3, 2)
    assert Project.from_dict(editor.project.to_dict()) == editor.project


@pytest.mark.parametrize('track,has_video', [(2, False), (3, False), (2, True), (3, True)])
def test_audio_tracks_cannot_enable_restoration(track, has_video):
    editor = Editor(project(track=track, has_video=has_video)); before = deepcopy(editor.project)
    with pytest.raises(ValueError, match='picture clip'):
        editor.update_restoration('c', enabled=True, denoise=.5)
    assert editor.project == before and not editor.can_undo


def test_audio_source_on_picture_track_cannot_enable_restoration():
    p = project(RestorationSettings(True, 0, .5, 0), has_video=False)
    with pytest.raises(ValueError, match='picture clip'): p.validate()


def test_save_load_preserves_source_and_exact_settings(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'untouched source')
    p = project(RestorationSettings(True, .17, .28, .39)); p.media[0].path = str(source)
    destination = tmp_path / 'project.lumen'; p.save(destination)
    assert Project.load(destination) == p
    assert source.read_bytes() == b'untouched source'
