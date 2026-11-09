"""Schema26 pivot persistence, atomic history, recovery and cache freshness."""
from copy import deepcopy
from white_balance_legacy_fixture import strip_white_balance_fields
from dataclasses import asdict
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.color_picker_source import capture_picker_snapshot
from lumen.color_source import SourceColor
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Editor, Grade, Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.render import RenderEngine
from test_color_picker_source import chart
from test_grade_transfer import project_pair
from test_qualifier_integration import project

INVALID = [True,False,None,'0.5',[],{},-.00001,1.00001,float('nan'),float('inf'),-float('inf'),np.float32(.5)]


@pytest.mark.parametrize('value', INVALID)
@pytest.mark.parametrize('enabled', (False,True))
def test_invalid_pivot_cannot_save_deserialize_or_edit_even_when_disabled(value,enabled):
    p=project(); p.clips[0].grade.enabled=enabled
    editor=Editor(p); before=deepcopy(p)
    grade=asdict(p.clips[0].grade); grade['contrast_pivot']=value
    with pytest.raises(ValueError,match='contrast_pivot'): editor.update_clip('picture',grade=grade)
    assert editor.project==before and editor.revision==0
    assert not editor.can_undo and not editor.can_redo
    payload=before.to_dict(); payload['clips'][0]['grade']['contrast_pivot']=value
    with pytest.raises(ValueError,match='contrast_pivot'): Project.from_dict(payload)
    p.clips[0].grade.contrast_pivot=value
    with pytest.raises(ValueError,match='contrast_pivot'): p.to_dict()


@pytest.mark.parametrize('value', INVALID)
@pytest.mark.parametrize('mode', ('enabled','disabled','bypass'))
def test_apply_grade_validates_pivot_before_any_bypass(value,mode):
    grade=Grade(contrast_pivot=value,enabled=mode!='disabled')
    with pytest.raises(ValueError,match='contrast_pivot'):
        apply_grade(np.zeros((2,3),np.float32),grade,bypass=mode=='bypass')


def legacy_payload(version):
    p=project(); p.clips[0].grade=Grade(contrast=1.7,exposure=.25)
    data=p.to_dict(); data['version']=version; strip_white_balance_fields(data)
    clip=data['clips'][0]; grade=clip['grade']
    if version<26: grade.pop('contrast_pivot')
    if version<7: clip.pop('time_map'); clip['source_in']=0
    if version<8: clip.pop('stabilization')
    if version<9: clip.pop('restoration')
    if version<16: clip.pop('transform')
    if version<17: grade.pop('hue_hue_curve'); grade.pop('hue_sat_curve')
    if version<18: grade.pop('qualifier')
    else:
        q=grade['qualifier']
        if version<19:
            for field in list(q):
                if field.startswith('window_'): q.pop(field)
        if version<20: q.pop('inclusion_ranges'); q.pop('exclusion_ranges')
        if version<21: q.pop('matte_black'); q.pop('matte_white')
        if version<22: q.pop('matte_smoothing')
        if version<23: q.pop('matte_morphology')
        if version<24:
            for channel in ('saturation','luma'):
                for edge in ('low','high'): q.pop(f'{channel}_softness_{edge}')
        if version<25:
            for channel in ('hue','saturation','luma'): q.pop(f'{channel}_enabled')
    return data,p


@pytest.mark.parametrize('version',range(1,26))
def test_every_legacy_schema_migrates_absent_pivot_to_exact_half_without_mutation(version):
    payload,expected=legacy_payload(version); original=deepcopy(payload)
    restored=Project.from_dict(payload)
    assert payload==original and restored==expected
    assert restored.clips[0].grade.contrast_pivot==.5
    assert restored.to_dict()['version']==VERSION
    rgb=np.array([[-.25,.18,1.25]],np.float32)
    np.testing.assert_array_equal(apply_grade(rgb,restored.clips[0].grade),
                                   apply_grade(rgb,expected.clips[0].grade))


@pytest.mark.parametrize('version',range(1,26))
@pytest.mark.parametrize('value',(.5,.18,None))
def test_legacy_schema_cannot_smuggle_any_pivot_even_neutral_or_null(version,value):
    payload,_=legacy_payload(version); payload['clips'][0]['grade']['contrast_pivot']=value
    with pytest.raises(ValueError,match='Legacy projects cannot contain contrast_pivot'):
        Project.from_dict(payload)


def test_schema26_requires_pivot_and_older_reader_rejects_before_clip_parsing(monkeypatch):
    import lumen.model as model
    payload=project().to_dict(); del payload['clips'][0]['grade']['contrast_pivot']
    with pytest.raises(ValueError,match='require contrast_pivot'): Project.from_dict(payload)
    payload['clips']='not a list'
    monkeypatch.setattr(model,'VERSION',25)
    with pytest.raises(ValueError,match='Unsupported'): Project.from_dict(payload)


@pytest.mark.parametrize('pivot',(0.,.123456789012345,.5,1.))
def test_full_precision_save_recovery_one_edit_undo_redo_and_reset(tmp_path,pivot):
    p,_=project_pair(tmp_path); editor=Editor(p); before=deepcopy(p)
    pending=asdict(p.clips[0].grade); pending['contrast_pivot']=pivot
    pending['contrast']=1.6789123456789
    editor.update_clip('source',grade=pending); after=deepcopy(editor.project)
    pending['contrast_pivot']=.999
    assert editor.project==after and len(editor._undo)==editor.revision==1
    path=tmp_path/'pivot.lumen'; editor.project.save(path)
    payload=json.loads(path.read_text())
    assert payload['version']== VERSION
    assert payload['clips'][0]['grade']['contrast_pivot']==pivot
    assert Project.load(path)==after
    with RecoveryStore(tmp_path/'recovery') as store:
        entry=store.write(editor.project,uuid4()); recovered=store.load(entry)
        assert recovered==after
        recovered.clips[0].grade.contrast_pivot=.777
        assert store.load(entry)==after and editor.project==after
    assert editor.undo() and editor.project==before
    assert editor.redo() and editor.project==after
    encoding=editor.project.clips[0].grade.input_encoding
    editor.update_clip('source',grade=Grade(input_encoding=encoding))
    assert editor.project.clips[0].grade.contrast_pivot==.5
    assert editor.project.clips[0].grade.input_encoding==encoding
    assert editor.undo() and editor.project==after


@pytest.mark.parametrize('encoding',('rec709','srgb','linear_rec709'))
def test_copy_paste_keeps_pivot_precision_target_interpretation_and_non_grade_state(tmp_path,encoding):
    p,_=project_pair(tmp_path); p.clips[0].grade.contrast_pivot=.123456789012345
    p.clips[1].grade.input_encoding=encoding; before=deepcopy(p)
    copied=copy_grade(p,'source'); editor=Editor(p)
    assert json.loads(copied.creative_json)['contrast_pivot']==.123456789012345
    assert paste_grade(editor,'target',copied)
    expected=deepcopy(before); expected.clips[1].grade=deepcopy(before.clips[0].grade)
    expected.clips[1].grade.input_encoding=encoding
    assert editor.project==expected and len(editor._undo)==editor.revision==1
    assert not paste_grade(editor,'target',copied) and editor.revision==1
    assert editor.undo() and editor.project==before
    assert editor.redo() and editor.project==expected
    editor.project.clips[0].grade.contrast_pivot=.8
    editor.project.clips[1].grade.contrast_pivot=.7
    assert copied.grade_for_input(encoding).contrast_pivot==.123456789012345


@pytest.mark.parametrize('contrast',(1.,1.7))
def test_in_place_pivot_change_invalidates_both_real_cache_keys(tmp_path,monkeypatch,contrast):
    import lumen.color_media as color_media
    p=project(); source=tmp_path/'source.mkv'; source.write_bytes(b'local synthetic cache fixture')
    p.media[0].path=str(source); clip=p.clips[0]; clip.grade.contrast=contrast
    color=SourceColor('gbrp16le','rgb','bt709','bt709','pc',True,'gbr','full')
    monkeypatch.setattr(color_media,'read_source_color',lambda *a,**k:color)
    monkeypatch.setattr(color_media,'source_rgb_filters',lambda *a,**k:[])
    calls=[]
    def stage(source,target,*a,**k): calls.append('stage'); Path(target).write_bytes(b'stage')
    def preview(project,target,*a,**k): calls.append('preview'); Path(target).write_bytes(b'preview'); return target
    monkeypatch.setattr(color_media,'grade_segment',stage)
    engine=RenderEngine(); monkeypatch.setattr(engine,'render',preview)
    def paths(): return engine._graded_segment(p.media[0],clip,p,None),engine.preview(p)
    try:
        before=paths(); clip.grade.contrast_pivot=.18; after=paths()
        assert before[0]!=after[0] and before[1]!=after[1]
        assert calls==['stage','preview','stage','preview']
        assert paths()==after
        clip.grade.contrast_pivot=.5; assert paths()==before
        assert len(calls)==4
    finally: engine.close()


@pytest.mark.parametrize('stage',('hue','qualifier'))
def test_real_source_picker_includes_primary_pivot_and_invalidates_old_snapshot(chart,stage):
    p,frames=chart; grade=p.clips[0].grade; grade.contrast=.6; grade.contrast_pivot=.18
    before=capture_picker_snapshot(p,'c',3,stage=stage)
    rgb=frames[0].astype(np.float32)/np.float32(65535)
    expected=np.array([.6*(float(v)-.18)+.18 for v in rgb.flat]).reshape(rgb.shape)
    np.testing.assert_allclose(before.frame,expected,rtol=0,atol=8e-8)
    grade.contrast_pivot=.8
    assert not before.is_current(p,'c',3)
    after=capture_picker_snapshot(p,'c',3,stage=stage)
    assert after.is_current(p,'c',3)
    assert np.max(abs(after.frame-before.frame))>.2
    grade.contrast_pivot=.18
    assert before.is_current(p,'c',3) and not after.is_current(p,'c',3)
