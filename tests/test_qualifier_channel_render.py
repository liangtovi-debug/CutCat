"""Independent channel-gate acceptance, decoded RGB16 and fresh deliveries.

Synthetic media only. Expected masks use scalar colorsys and independently
computed ramps, never production mask/grade helpers.
"""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace
from itertools import product
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.media import ffmpeg_available
from lumen.model import Editor, Grade, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierRange, QualifierSettings, apply_qualifier, qualifier_mask
from test_grade_transfer import project_pair
from test_qualifier_render import source_project, decode_rgb, engine
from test_qualifier_sets_render import assert_deliveries

CHANNELS = ('hue_enabled', 'saturation_enabled', 'luma_enabled')
TARGETS = ('base', 'inclusion_ranges', 'exclusion_ranges')
FLAGS = list(product((False, True), repeat=3))


def settings_for(target, flags):
    key = QualifierRange(hue_center=0., hue_width=.03, hue_softness=.08,
        saturation_low=.4, saturation_high=.7, saturation_softness=.1,
        saturation_softness_low=.2, saturation_softness_high=.15,
        luma_low=.18, luma_high=.32, luma_softness=.05,
        luma_softness_low=.1, luma_softness_high=.2,
        **dict(zip(CHANNELS, flags)))
    settings = QualifierSettings(enabled=True, exposure=.25)
    if target == 'base':
        for name, value in asdict(key).items():
            setattr(settings, name, value)
    elif target == 'inclusion_ranges':
        # No fixture matches this base, so additions cannot be hidden by it.
        settings.hue_center=.8; settings.hue_width=0.; settings.hue_softness=0.
        settings.inclusion_ranges=[key]
    else:
        settings.hue_enabled=False; settings.saturation_enabled=False
        settings.luma_enabled=False; settings.exclusion_ranges=[key]
    return settings


def samples():
    # Same hue with independent saturation/luma failures, wrap, other hues,
    # neutral and black: exactly eight 64-pixel patches for delivery assertions.
    return np.array([[.6,.2,.2], [.3,.25,.25], [.08,.02,.02], [.8,.3,.3],
                     [.6,.18,.205], [.2,.5,.2], [.25,.25,.25], [0.,0.,0.]])


def ramp(value, low, high, lower, upper):
    if low <= value <= high:
        return 1.
    distance, feather = (low-value, lower) if value < low else (value-high, upper)
    return max(0., 1.-distance/feather) if feather else 0.


def range_reference(rgb, key):
    if not np.isfinite(rgb).all() or min(rgb)<0 or max(rgb)>1:
        return 0.
    hue, saturation, _ = colorsys.rgb_to_hsv(*map(float, rgb))
    weight = 1.
    if key.hue_enabled and key.hue_width < .5:
        if max(rgb)-min(rgb) <= 1e-6:
            return 0.
        weight *= ramp(abs((hue-key.hue_center+.5)%1.-.5),0.,key.hue_width,
                       key.hue_softness,key.hue_softness)
    for channel,value in [('saturation',saturation),('luma',np.dot(rgb,[.2126,.7152,.0722]))]:
        if not getattr(key,channel+'_enabled'):
            continue
        lower,upper = [getattr(key,channel+'_softness_'+side) for side in ('low','high')]
        lower = getattr(key,channel+'_softness') if lower is None else lower
        upper = getattr(key,channel+'_softness') if upper is None else upper
        weight *= ramp(value,getattr(key,channel+'_low'),getattr(key,channel+'_high'),lower,upper)
    return weight


def reference(rgb, settings):
    rgb=np.asarray(rgb,float)
    matte=np.array([max(range_reference(pixel,key) for key in [settings,*settings.inclusion_ranges]) *
        (1.-max([0.,*(range_reference(pixel,key) for key in settings.exclusion_ranges)]))
        for pixel in rgb.reshape(-1,3)]).reshape(rgb.shape[:-1])
    if not settings.enabled:
        matte[:]=0
    return matte, rgb + matte[...,None]*(rgb*2**settings.exposure-rgb)


@pytest.mark.parametrize('target',TARGETS)
@pytest.mark.parametrize('flags',FLAGS)
def test_channel_combinations_match_independent_mask_and_corrected_pixels(target,flags):
    settings=settings_for(target,flags)
    rgb=np.concatenate([samples(), [[-.1,.2,.3],[.2,1.1,.3]]]).astype(np.float32)[None]
    expected,corrected=reference(rgb,settings)
    np.testing.assert_allclose(qualifier_mask(rgb,settings),expected,atol=8e-7,rtol=0)
    np.testing.assert_allclose(apply_qualifier(rgb,settings),corrected,atol=8e-7,rtol=0)
    assert expected[0,-1]==expected[0,-2]==0
    if flags == (False,False,False):
        np.testing.assert_array_equal(expected[0,:8],np.full(8,0. if target=='exclusion_ranges' else 1.))


@pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg/ffprobe required')
@pytest.mark.parametrize('target',TARGETS)
@pytest.mark.parametrize('flags',[(False,True,True),(True,False,True),(True,True,False),(False,False,False)])
def test_channels_real_rgb16_preview_and_prores(tmp_path,engine,target,flags):
    settings=settings_for(target,flags)
    colors=np.rint(samples()*65535).astype('<u2')
    image=np.repeat(np.repeat(colors[None],64,axis=0),64,axis=1)
    project,frames=source_project(tmp_path,np.repeat(image[None],3,axis=0),grade=Grade(qualifier=settings),name='channels')
    expected,wanted=reference(frames[0].astype(float)/65535,settings)
    np.testing.assert_allclose(qualifier_mask(frames[0].astype(np.float32)/65535,settings),expected,atol=1e-6,rtol=0)
    stage=engine._graded_segment(project.media[0],project.clips[0],project,None)
    np.testing.assert_allclose(decode_rgb(stage,project.width),wanted,atol=3/65535,rtol=0)
    assert_deliveries(project,wanted,tmp_path,'channels-')


@pytest.mark.parametrize('field',CHANNELS)
@pytest.mark.parametrize('target',TARGETS)
def test_channel_flags_invalidate_stage_and_preview_cache(tmp_path,monkeypatch,field,target):
    import lumen.color_media as color_media
    from lumen.color_source import SourceColor
    from lumen.render import RenderEngine
    from test_qualifier_integration import project as simple_project
    candidate=simple_project()
    original=settings_for(target,(True,True,True))
    candidate.clips[0].grade.qualifier=original
    for i,item in enumerate(candidate.media):
        file=tmp_path/f'media-{i}.mkv'; file.write_bytes(b'local fixture'); item.path=str(file)
    clip=candidate.clips[0]; media=candidate.media[0]; clip.grade.input_encoding='rec709'
    color=SourceColor('gbrp16le','rgb','bt709','bt709','pc',True,'gbr','full')
    monkeypatch.setattr(color_media,'read_source_color',lambda *a,**k:color)
    monkeypatch.setattr(color_media,'source_rgb_filters',lambda *a,**k:[])
    calls=[]
    def stage(source,target,*a,**k): calls.append('stage'); Path(target).write_bytes(b'stage')
    def preview(project,target,*a,**k): calls.append('preview'); Path(target).write_bytes(b'preview'); return target
    monkeypatch.setattr(color_media,'grade_segment',stage)
    renderer=RenderEngine(); monkeypatch.setattr(renderer,'render',preview)
    def paths(): return renderer._graded_segment(media,clip,candidate,None),renderer.preview(candidate)
    try:
        before=paths(); changed=deepcopy(original)
        if target=='base': setattr(changed,field,False)
        else: getattr(changed,target)[0]=replace(getattr(changed,target)[0],**{field:False})
        clip.grade.qualifier=changed; after=paths()
        assert before[0]!=after[0] and before[1]!=after[1]
        assert calls==['stage','preview','stage','preview']
        assert paths()==after
        clip.grade.qualifier=original; assert paths()==before
        assert len(calls)==4
    finally: renderer.close()


def test_flags_roundtrip_recovery_copy_undo_redo(tmp_path):
    project,_=project_pair(tmp_path)
    editor=Editor(project); before=deepcopy(project)
    settings=settings_for('base',(False,True,False))
    settings.inclusion_ranges=[QualifierRange(hue_enabled=True,saturation_enabled=False,luma_enabled=False)]
    settings.exclusion_ranges=[QualifierRange(hue_enabled=False,saturation_enabled=False,luma_enabled=False)]
    grade=asdict(project.clips[0].grade); grade['qualifier']=asdict(settings)
    editor.update_clip('source',grade=grade); after=deepcopy(editor.project)
    assert editor.revision==1 and len(editor._undo)==1
    assert editor.undo() and editor.project==before
    assert editor.redo() and editor.project==after
    path=tmp_path/'channels.lumen'; editor.project.save(path)
    assert Project.load(path)==after
    with RecoveryStore(tmp_path/'recovery') as store:
        assert store.load(store.write(editor.project,uuid4()))==after
    copied=copy_grade(editor.project,'source'); assert paste_grade(editor,'target',copied)
    assert editor.project.clips[1].grade.qualifier==settings
    revision=editor.revision; assert not paste_grade(editor,'target',copied); assert editor.revision==revision
    editor.project.clips[1].grade.qualifier.inclusion_ranges.clear()
    assert editor.project.clips[0].grade.qualifier==settings
    assert copied.grade_for_input('rec709').qualifier==settings


@pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg/ffprobe required')
def test_real_channel_edit_refreshes_cache_preserves_picker_input_and_restores_pixels(tmp_path,engine):
    settings=settings_for('base',(True,True,True))
    colors=np.rint(samples()*65535).astype('<u2')
    image=np.repeat(np.repeat(colors[None],64,axis=0),64,axis=1)
    project,frames=source_project(tmp_path,np.repeat(image[None],3,axis=0),grade=Grade(qualifier=settings),name='channel-cache')
    original=deepcopy(settings); clip=project.clips[0]; media=project.media[0]
    snapshot=capture_picker_snapshot(project,'picture',0,stage='qualifier')
    before=(engine._graded_segment(media,clip,project,None),engine.preview(project))
    before_rgb=decode_rgb(before[0],project.width)
    settings.hue_enabled=False
    after=(engine._graded_segment(media,clip,project,None),engine.preview(project))
    assert before[0]!=after[0] and before[1]!=after[1]
    assert not snapshot.is_current(project)
    fresh=capture_picker_snapshot(project,'picture',0,stage='qualifier')
    np.testing.assert_array_equal(fresh.frame,snapshot.frame)
    _,expected=reference(frames[0].astype(float)/65535,settings)
    changed_rgb=decode_rgb(after[0],project.width)
    np.testing.assert_allclose(changed_rgb,expected,atol=3/65535,rtol=0)
    assert np.max(abs(changed_rgb-before_rgb))>.01
    clip.grade.qualifier=original
    assert (engine._graded_segment(media,clip,project,None),engine.preview(project))==before
    np.testing.assert_array_equal(decode_rgb(before[0],project.width),before_rgb)
