"""White-balance stage isolation, restoration, cache and sampling safety."""
from copy import deepcopy
import threading
import numpy as np
import pytest
from lumen.color import apply_grade
from lumen.color_picker_source import capture_picker_snapshot, PickerSourceCancelled
from lumen.model import Grade
from lumen.restoration_settings import RestorationSettings
from lumen.white_balance import validate_white_balance
from test_color_picker_restoration import (make_source, picture, normalize_reference,
                                          restoration_reference)
from test_white_balance_independent import oracle


@pytest.mark.parametrize('encoding,transfer', [('rec709','bt709'),('srgb','iec61966-2-1'),('linear_rec709','linear')])
@pytest.mark.parametrize('creative', [False,True])
def test_pre_wb_snapshot_retains_restoration_not_any_creative_stage(tmp_path,encoding,transfer,creative):
    project,frames=make_source(tmp_path,picture(),transfer=transfer)
    clip=project.clips[0]
    clip.restoration=RestorationSettings(enabled=True,denoise=.7,sharpen=.3,deblock=.2)
    clip.grade=Grade(input_encoding=encoding,enabled=creative,white_balance_enabled=True,
                     white_balance_gains=[1.3,.9,1.1],linear_exposure=1,exposure=.3,
                     contrast=1.2,temperature=.4,tint=.2,lut_path='missing.cube')
    clip.grade.qualifier.enabled=True;clip.grade.qualifier.exposure=.4
    source=frames[0].astype(float)/65535
    normalized=normalize_reference(source,encoding)
    expected=restoration_reference(normalized,clip.restoration)
    before=deepcopy(project)
    snap=capture_picker_snapshot(project,clip.id,0,stage='white_balance')
    np.testing.assert_allclose(snap.frame,expected,atol=8e-7,rtol=0)
    assert snap.restoration_applied and snap.stage=='white_balance'
    assert project==before and snap.is_current(project,clip.id,0)
    clip.grade.white_balance_gains[0]=1.31
    assert not snap.is_current(project,clip.id,0)


def test_wb_and_qualifier_sampling_stage_and_cache_keys(tmp_path):
    from test_white_balance_independent import make_source as make_chart, means
    from lumen.render import RenderEngine
    from test_contrast_pivot_render import ff
    grade=Grade(white_balance_enabled=True,white_balance_gains=[1.3,.9,1.1])
    project,source=make_chart(tmp_path,grade)
    clip=project.clips[0]
    snap=capture_picker_snapshot(project,clip.id,0,stage='qualifier')
    np.testing.assert_allclose(means(snap.frame),oracle(source,grade.white_balance_gains),atol=2e-6,rtol=0)
    engine=RenderEngine(timeout=90)
    try:
        one=engine._graded_segment(project.media[0],clip,project,None)
        assert engine._graded_segment(project.media[0],clip,project,None)==one
        clip.grade.white_balance_gains[0]=1.4
        two=engine._graded_segment(project.media[0],clip,project,None)
        assert one!=two
        clip.grade.white_balance_enabled=False
        three=engine._graded_segment(project.media[0],clip,project,None)
        assert three not in (one,two)
        raw=ff('-i',three,'-pix_fmt','rgb48le','-f','rawvideo','pipe:1')
        frame=np.frombuffer(raw,'<u2').reshape(-1,32,project.width,3)[0]/65535
        np.testing.assert_allclose(means(frame),source,atol=1/65535,rtol=0)
    finally:engine.close()


def test_wb_snapshot_pre_cancel_and_invalid_stage(tmp_path):
    from lumen.color_picker_source import PickerSourceError
    project,_=make_source(tmp_path,picture())
    cancel=threading.Event();cancel.set()
    with pytest.raises(PickerSourceCancelled):
        capture_picker_snapshot(project,project.clips[0].id,0,stage='white_balance',cancel=cancel)
    with pytest.raises(PickerSourceError,match='Unknown'):
        capture_picker_snapshot(project,project.clips[0].id,0,stage='final_white_balance')


@pytest.mark.parametrize('bad',[10**500,-10**500],ids=['oversized-positive-int','oversized-negative-int'])
def test_oversized_numeric_gain_rejected_as_value_error(bad):
    with pytest.raises(ValueError,match='White balance gains'):
        validate_white_balance(False,[bad,1,1])
    with pytest.raises(ValueError,match='White balance gains'):
        apply_grade(np.zeros((1,3),np.float32),Grade(white_balance_gains=[bad,1,1]))
