"""Schema-sixteen static geometry and unchanged legacy picture defaults."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction
import pytest
from lumen.model import Clip, Editor, Media, Project, VERSION
from lumen.clip_transform import ClipTransform
from lumen.timing import ClipTimeMap


def project():
    return Project(media=[Media('m', '/tmp/source.mp4', 'Source', 10, 64, 48, True, True)],
                   clips=[Clip('c', 'm', 0, 0, 0, 21, time_map=ClipTimeMap(Fraction(1, 2), 31, Fraction(3, 2)))])


def test_transform_roundtrip_undo_split_and_deepcopy(tmp_path):
    e = Editor(project()); bind_missing_project_paths(e.project, tmp_path); before = deepcopy(e.project)
    t = ClipTransform(12.5, -3, .75, 25, True, False)
    e.update_clip('c', transform=t); after = deepcopy(e.project)
    t.x = 999
    assert e.project.clips[0].transform.x == 12.5
    assert e.project.clips[0].timing == before.clips[0].timing
    e.undo(); assert e.project == before
    e.redo(); assert e.project == after
    path = tmp_path/'transform.lumen'; e.project.save(path)
    assert Project.load(path) == after
    left, right = e.split_clip('c', 10)
    assert left.transform == right.transform == after.clips[0].transform
    left.transform.x = 0
    assert right.transform.x == 12.5


@pytest.mark.parametrize('version', range(1, 16))
def test_all_legacy_generations_default_to_exact_identity(version):
    p = project(); p.clips[0] = Clip('c', 'm', 0, 0, 0, 30)
    data = p.to_dict(); data['version'] = version; strip_contrast_pivot_field(data)
    item = data['clips'][0]; item.pop('transform')
    item['grade'].pop('hue_hue_curve')
    item['grade'].pop('hue_sat_curve')
    item['grade'].pop('qualifier')
    if version < 9: item.pop('restoration')
    if version < 8: item.pop('stabilization')
    if version < 7:
        item.pop('time_map'); item['source_in'] = 0
    frozen = deepcopy(data)
    restored = Project.from_dict(data)
    assert restored == p and data == frozen
    assert restored.clips[0].transform == ClipTransform()
    assert restored.to_dict()['version'] == VERSION


@pytest.mark.parametrize('mutation', ['missing','extra','nan','wrong_type','legacy_smuggle'])
def test_malformed_transforms_rejected(mutation):
    data = project().to_dict(); item=data['clips'][0]
    if mutation == 'missing': item.pop('transform')
    if mutation == 'extra': item['transform']['opacity'] = .5
    if mutation == 'nan': item['transform']['x'] = float('nan')
    if mutation == 'wrong_type': item['transform'] = []
    if mutation == 'legacy_smuggle': data['version'] = 15; strip_contrast_pivot_field(data)
    with pytest.raises(ValueError): Project.from_dict(data)


def test_invalid_transform_and_audio_track_are_atomic():
    e=Editor(project()); before=deepcopy(e.project)
    invalid=ClipTransform(); invalid.scale=float('inf')
    with pytest.raises(ValueError): e.update_clip('c', transform=invalid)
    assert e.project == before and not e.can_undo
    e.update_clip('c', track=2); before=deepcopy(e.project); undo=deepcopy(e._undo)
    with pytest.raises(ValueError, match='video track'): e.update_clip('c', transform=ClipTransform(x=1))
    assert e.project == before and e._undo == undo
    e.update_clip('c', transform=ClipTransform())
    assert e._undo == undo


def test_reset_dictionary_and_old_reader_gate(monkeypatch):
    e=Editor(project()); e.update_clip('c', transform=asdict(ClipTransform(scale=2)))
    e.update_clip('c', transform=asdict(ClipTransform()))
    assert e.project.clips[0].transform == ClipTransform()
    data=e.project.to_dict()
    monkeypatch.setattr('lumen.model.VERSION', 15)
    with pytest.raises(ValueError, match='version'): Project.from_dict(data)
