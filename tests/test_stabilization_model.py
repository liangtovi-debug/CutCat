"""Schema-eight stabilization is bounded, nondestructive and transactional."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from fractions import Fraction
import json

import pytest

from lumen.model import Clip, ClipEffect, Editor, Grade, Media, Project, VERSION
from lumen.stabilization_settings import StabilizationSettings, validate_stabilization_settings
from lumen.timing import ClipTimeMap


def project(settings=None, *, track=0, has_video=True):
    return Project(media=[Media('m', '/tmp/source.mp4', 'Source', 10, 64, 64, has_video, True)],
                   clips=[Clip('c', 'm', track, 10, 0, 60,
                               stabilization=settings if settings is not None else StabilizationSettings())])


def test_identity_defaults_are_independent_and_positional_constructors_survive():
    old = Clip('c', 'm', 0, 1, 0, 30, Grade(contrast=1.2), -2, False, 2, 3,
               [ClipEffect('fx', 'vignette', .3)], ClipTimeMap(0, 60, 2))
    other = Clip('d', 'm', 0, 31, 0, 30)
    assert old.stabilization == StabilizationSettings(False, 1.0, 15, 1.15, 'crop')
    assert old.stabilization is not other.stabilization
    old.stabilization.strength = .5
    assert other.stabilization.strength == 1
    assert old.speed == 2 and old.grade.contrast == 1.2 and old.fade_out == 3


@pytest.mark.parametrize('field,value', [
    ('enabled', 1), ('enabled', 'false'), ('enabled', None),
    ('strength', -.01), ('strength', 1.01), ('strength', True), ('strength', '1'),
    ('strength', float('nan')), ('strength', float('inf')),
    ('smoothing', 0), ('smoothing', 121), ('smoothing', 1.0), ('smoothing', True),
    ('smoothing', '15'), ('smoothing', None),
    ('max_zoom', .99), ('max_zoom', 1.501), ('max_zoom', True),
    ('max_zoom', float('nan')), ('max_zoom', float('inf')),
    ('border', 'mirror'), ('border', ''), ('border', None), ('border', ['crop']),
])
def test_settings_reject_invalid_values_even_when_disabled(field, value):
    with pytest.raises(ValueError):
        StabilizationSettings(**{field: value})
    settings = StabilizationSettings()
    setattr(settings, field, value)
    with pytest.raises(ValueError):
        validate_stabilization_settings(settings)
    with pytest.raises(ValueError):
        project(settings).to_dict()


@pytest.mark.parametrize('strength,smoothing,zoom,border', [(0, 1, 1, 'crop'), (1, 120, 1.5, 'black')])
def test_bounds_are_inclusive(strength, smoothing, zoom, border):
    settings = StabilizationSettings(True, strength, smoothing, zoom, border)
    assert StabilizationSettings.from_dict(settings.to_dict()) == settings


def test_schema_eight_roundtrip_preserves_all_values_without_mutating_input():
    p = project(StabilizationSettings(True, .123456789, 19, 1.23456789, 'black'))
    payload = p.to_dict(); before = deepcopy(payload)
    assert payload['version'] == VERSION
    assert payload['clips'][0]['stabilization'] == p.clips[0].stabilization.to_dict()
    assert 'source_in' not in payload['clips'][0]
    assert Project.from_dict(json.loads(json.dumps(payload))) == p
    assert payload == before


@pytest.mark.parametrize('version', range(1, 8))
def test_legacy_schemas_migrate_to_disabled_defaults(version):
    original = project()
    original.clips[0].source_in = 5
    payload = original.to_dict(); payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    item = payload['clips'][0]; item.pop('stabilization'); item.pop('restoration')
    if version < 7:
        mapping = item.pop('time_map'); item['source_in'] = mapping['source_in_num']
    before = deepcopy(payload)
    restored = Project.from_dict(payload)
    assert restored == original and payload == before
    assert restored.to_dict()['version'] == VERSION
    assert restored.clips[0].stabilization == StabilizationSettings()


@pytest.mark.parametrize('version', range(1, 8))
def test_legacy_schema_rejects_new_stabilization_field(version):
    payload = project().to_dict(); payload['version'] = version; strip_contrast_pivot_field(payload)
    if payload['version'] < 17:
        for old_clip in payload.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if payload['version'] < 16:
        for old_clip in payload.get('clips', []): old_clip.pop('transform', None)
    payload['clips'][0].pop('restoration')
    if version < 7:
        item = payload['clips'][0]; item.pop('time_map'); item['source_in'] = 0
    with pytest.raises(ValueError, match='Legacy clips cannot contain stabilization'):
        Project.from_dict(payload)


@pytest.mark.parametrize('mutation', ['missing', 'partial', 'unknown', 'none', 'list', 'object', 'legacy_timing'])
def test_schema_eight_rejects_missing_malformed_or_wrong_generation(mutation):
    payload = project().to_dict(); item = payload['clips'][0]
    if mutation == 'missing': item.pop('stabilization')
    elif mutation == 'partial': item['stabilization'].pop('enabled')
    elif mutation == 'unknown': item['stabilization']['smoothing_seconds'] = 1
    elif mutation == 'none': item['stabilization'] = None
    elif mutation == 'list': item['stabilization'] = []
    elif mutation == 'object': item['stabilization'] = StabilizationSettings()
    else: item['source_in'] = 0
    with pytest.raises(ValueError): Project.from_dict(payload)


def test_atomic_edit_undo_redo_noop_and_external_alias_isolation():
    editor = Editor(project()); before = deepcopy(editor.project)
    settings = StabilizationSettings(True, .7, 20, 1.2, 'black')
    clip = editor.update_clip('c', stabilization=settings)
    after = deepcopy(editor.project)
    assert clip.stabilization == settings and clip.stabilization is not settings
    settings.strength = .1
    assert editor._clip('c').stabilization.strength == .7
    editor.undo(); assert editor.project == before
    editor.redo(); assert editor.project == after
    depth = len(editor._undo)
    editor.update_stabilization('c', strength=.7)
    assert len(editor._undo) == depth
    editor.update_stabilization('c', enabled=False, smoothing=30)
    assert len(editor._undo) == depth + 1
    editor.undo(); assert editor.project == after
    redo = deepcopy(editor._redo); undo = deepcopy(editor._undo)
    with pytest.raises(ValueError): editor.update_stabilization('c', smoothing=121)
    assert editor.project == after and editor._redo == redo and editor._undo == undo
    with pytest.raises(ValueError): editor.update_stabilization('c', invented=True)
    assert editor.project == after and editor._redo == redo and editor._undo == undo


def test_dict_edit_is_complete_and_atomic_with_other_clip_properties():
    editor = Editor(project()); before = deepcopy(editor.project)
    with pytest.raises(ValueError): editor.update_clip('c', start=50, stabilization={'enabled': True})
    assert editor.project == before and not editor.can_undo
    editor.update_clip('c', start=50, stabilization=StabilizationSettings(True).to_dict())
    assert editor._clip('c').start == 50 and editor._clip('c').stabilization.enabled
    editor.undo(); assert editor.project == before


def test_split_clones_settings_speed_trim_preserve_them():
    settings = StabilizationSettings(True, .4, 33, 1.25, 'black')
    editor = Editor(project(settings))
    editor.set_clip_speed('c', Fraction(3, 2))
    editor.trim_clip_source('c', Fraction(1, 2), Fraction(101, 2))
    editor.trim_clip_left('c', 1)
    assert editor._clip('c').stabilization == settings
    left, right = editor.split_clip('c', editor._clip('c').start + 7)
    assert left.stabilization == right.stabilization == settings
    assert left.stabilization is not right.stabilization
    editor.update_stabilization(right.id, strength=.8)
    assert left.stabilization.strength == .4 and right.stabilization.strength == .8
    assert left.source_out == right.source_in and left.speed == right.speed == Fraction(3, 2)
    assert Project.from_dict(editor.project.to_dict()) == editor.project


@pytest.mark.parametrize('track,has_video', [(2, False), (3, False), (2, True), (3, True)])
def test_audio_tracks_cannot_enable_stabilization(track, has_video):
    editor = Editor(project(track=track, has_video=has_video)); before = deepcopy(editor.project)
    with pytest.raises(ValueError, match='picture clip'):
        editor.update_stabilization('c', enabled=True)
    assert editor.project == before and not editor.can_undo


def test_save_load_preserves_source_file_and_exact_settings(tmp_path):
    source = tmp_path / 'source.mp4'; source.write_bytes(b'untouched source')
    p = project(StabilizationSettings(True, .82, 27, 1.234, 'black'))
    p.media[0].path = str(source)
    destination = tmp_path / 'project.lumen'; p.save(destination)
    assert Project.load(destination) == p
    assert source.read_bytes() == b'untouched source'
