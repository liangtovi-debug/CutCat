"""Independent asymmetric S/Y' scalar, RGB16 and historic-output acceptance.

Never use production range/mask/HSV helpers to compute expected pixels.
Historic NPZ contains actual v14 outputs, not a second invocation of new code.
"""
import colorsys
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_picker_source import capture_picker_snapshot
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.media import ffmpeg_available
from lumen.model import Editor, Grade, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierRange, QualifierSettings, qualifier_mask, apply_qualifier
from lumen.qualifier_overlay import selection_overlay
from lumen.qualifier_picker import soften_qualifier_range
from lumen.qualifier_viewport import centered
from test_grade_transfer import project_pair
from test_matte_morphology_render import scalar_morphology, scalar_smoothing
from test_qualifier_render import source_project, decode_rgb, engine
from test_qualifier_sets_render import assert_deliveries
from test_static_window_render import spatial_reference


def edge_weight(value, low, high, lower, upper):
    if low <= value <= high:
        return 1.
    distance, feather = (low-value, lower) if value < low else (value-high, upper)
    return max(0., 1.-distance/feather) if feather else 0.


def independent_range(rgb, key):
    if min(rgb) < 0 or max(rgb) > 1:
        return 0.
    hue, sat, _ = colorsys.rgb_to_hsv(*map(float, rgb))
    if key.hue_width == .5:
        weight = 1.
    elif max(rgb)-min(rgb) <= 1e-6:
        weight = 0.
    else:
        weight = edge_weight(abs((hue-key.hue_center+.5)%1.-.5), 0., key.hue_width,
                             key.hue_softness, key.hue_softness)
    for channel, value in [('saturation', sat), ('luma', sum(float(v)*c for v,c in zip(rgb,(.2126,.7152,.0722))))]:
        sides = [getattr(key, channel+'_softness_'+side) for side in ('low','high')]
        lower, upper = [getattr(key, channel+'_softness') if v is None else v for v in sides]
        weight *= edge_weight(value, getattr(key,channel+'_low'), getattr(key,channel+'_high'),lower,upper)
    return weight


def independent_reference(rgb, settings):
    rgb=np.asarray(rgb,float)
    raw=np.empty(rgb.shape[:2])
    for (y,x),_ in np.ndenumerate(raw):
        sample=rgb[y,x]
        included=max(independent_range(sample,k) for k in (settings,*settings.inclusion_ranges))
        excluded=max([0.,*(independent_range(sample,k) for k in settings.exclusion_ranges)])
        raw[y,x]=included*(1.-excluded)
    levels=np.clip((raw-settings.matte_black)/(settings.matte_white-settings.matte_black),0.,1.)
    morph=scalar_morphology(levels,settings.matte_morphology)
    smooth=scalar_smoothing(morph,settings.matte_smoothing)
    mask=smooth*spatial_reference(rgb.shape[1],rgb.shape[0],settings)
    mask[~np.all((rgb>=0)&(rgb<=1),axis=-1)]=0
    if not settings.enabled:
        mask[:]=0
    output=rgb.copy()
    for (y,x),weight in np.ndenumerate(mask):
        if not weight:
            continue
        h,s,v=colorsys.rgb_to_hsv(*rgb[y,x])
        rotated=np.array(colorsys.hsv_to_rgb((h+settings.hue_shift)%1.,s,v))
        luma=sum(v*c for v,c in zip(rotated,(.2126,.7152,.0722)))
        corrected=(luma+settings.saturation*(rotated-luma))*2**settings.exposure
        output[y,x]+=weight*(corrected-rgb[y,x])
    return mask,output


def configured(target='base',channel='saturation',lower=.4,upper=.1):
    key=QualifierRange(hue_width=.5, saturation_low=.35,saturation_high=.65,
        luma_low=.2,luma_high=.4,saturation_softness=.07,luma_softness=.03,
        saturation_softness_low=lower,saturation_softness_high=upper,
        luma_softness_low=lower,luma_softness_high=upper)
    if channel=='saturation': key=replace(key,luma_low=0.,luma_high=1.)
    if channel=='luma': key=replace(key,saturation_low=0.,saturation_high=1.)
    s=QualifierSettings(enabled=True,hue_width=.5,exposure=.3,saturation=.8,hue_shift=.08)
    if target=='base':
        for name,value in asdict(key).items(): setattr(s,name,value)
    elif target=='inclusion_ranges':
        s.hue_width=0.; s.hue_center=.8; s.hue_softness=0.; s.inclusion_ranges=[key]
    else: s.exclusion_ranges=[key]
    return s


def patch_colors():
    probes=[(.33,.15,.3),(.33,.30,.3),(.33,.5,.3),(.33,.7,.3),
            (.33,.5,.12),(.33,.5,.45),(.33,0.,.3),(.33,.5,.7)]
    colors=[]
    for h,s,y in probes:
        unit=np.array(colorsys.hsv_to_rgb(h,s,1.))
        colors.append(unit*y/np.dot(unit,[.2126,.7152,.0722]))
    return np.asarray(colors)


@pytest.mark.parametrize('index',range(128))
def test_v14_actual_output_bit_identity_with_inherited_edges(index):
    with np.load(Path(__file__).parent/'fixtures/qualifier_v14_legacy_outputs.npz',allow_pickle=False) as old:
        values=json.loads(str(old['settings']))[index]
        values['inclusion_ranges']=[QualifierRange(**v) for v in values['inclusion_ranges']]
        values['exclusion_ranges']=[QualifierRange(**v) for v in values['exclusion_ranges']]
        settings=QualifierSettings(**values)
        actual_mask=qualifier_mask(old['rgb'][index],settings)
        actual_rgb=apply_qualifier(old['rgb'][index],settings)
        assert actual_mask.tobytes()==old['matte'][index].tobytes()
        assert actual_rgb.tobytes()==old['output'][index].tobytes()


@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
@pytest.mark.parametrize('channel',['saturation','luma'])
@pytest.mark.parametrize('lower,upper',[(0.,1.),(1.,0.),(.4,.1),(.1,.4)])
def test_scalar_asymmetric_zero_max_and_side_isolation(target,channel,lower,upper):
    settings=configured(target,channel,lower,upper)
    rgb=patch_colors().astype(np.float32)[None]
    wanted,corrected=independent_reference(rgb,settings)
    np.testing.assert_allclose(qualifier_mask(rgb,settings),wanted,atol=9e-7,rtol=0)
    np.testing.assert_allclose(apply_qualifier(rgb,settings),corrected,atol=9e-7,rtol=0)
    for side,other in [('low','high'),('high','low')]:
        changed=deepcopy(settings)
        key=changed if target=='base' else getattr(changed,target)[0]
        field=channel+'_softness_'+side
        if target=='base': setattr(key,field,.85)
        else: getattr(changed,target)[0]=replace(key,**{field:.85})
        a=qualifier_mask(rgb,settings); b=qualifier_mask(rgb,changed)
        values=[colorsys.rgb_to_hsv(*p)[1] if channel=='saturation' else np.dot(p,[.2126,.7152,.0722]) for p in rgb[0]]
        untouched=[i for i,v in enumerate(values) if (v>=getattr(key,channel+'_low') if side=='low' else v<=getattr(key,channel+'_high'))]
        np.testing.assert_array_equal(a[:,untouched],b[:,untouched])


@pytest.mark.parametrize('amount',[-1.,0.,1.])
def test_levels_morphology_smoothing_window_crop_overlay_order(amount):
    rgb=np.random.default_rng(742).random((17,23,3),dtype=np.float32)
    rgb[0,:4]=[[0,0,0],[.4,.4,.4],[-.1,.3,.5],[.2,1.1,.5]]
    settings=configured('inclusion_ranges',lower=.7,upper=.23)
    settings.exclusion_ranges=[replace(settings.inclusion_ranges[0],luma_low=.3,luma_high=.5,luma_softness_low=.15,luma_softness_high=.03)]
    settings.matte_black=.08; settings.matte_white=.88
    settings.matte_morphology=amount; settings.matte_smoothing=.65
    settings.window_shape='ellipse'; settings.window_width=.7; settings.window_height=.6; settings.window_feather=.8
    expected,corrected=independent_reference(rgb,settings)
    actual=qualifier_mask(rgb,settings)
    np.testing.assert_allclose(actual,expected,atol=1.5e-6,rtol=0)
    np.testing.assert_allclose(apply_qualifier(rgb,settings),corrected,atol=1.5e-6,rtol=0)
    crop=rgb[3:14,5:19]
    cut=qualifier_mask(crop,settings,canvas_size=(23,17),origin=(5,3),matte_source=rgb)
    np.testing.assert_array_equal(cut,actual[3:14,5:19])
    alpha=expected[3:14,5:19,None]/2
    overlay=np.clip(crop,0,1)*(1-alpha)+np.array([1.,0.,1.])*alpha
    np.testing.assert_allclose(selection_overlay(crop,cut),overlay,atol=1e-6,rtol=0)
    assert actual[0,2]==actual[0,3]==0


@pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg/ffprobe required')
@pytest.mark.parametrize('target,channel',[('base','saturation'),('inclusion_ranges','saturation'),('exclusion_ranges','saturation'),('base','luma'),('inclusion_ranges','luma'),('exclusion_ranges','luma')])
def test_asymmetric_rgb16_preview_and_prores(tmp_path,engine,target,channel):
    settings=configured(target,channel)
    colors=np.rint(patch_colors()*65535).astype('<u2')
    image=np.repeat(np.repeat(colors[None],64,axis=0),64,axis=1)
    project,frames=source_project(tmp_path,np.repeat(image[None],3,axis=0),grade=Grade(qualifier=settings),name='asymmetric')
    incoming=frames[0].astype(float)/65535
    matte,wanted=independent_reference(incoming,settings)
    np.testing.assert_allclose(qualifier_mask(incoming.astype(np.float32),settings),matte,atol=1e-6,rtol=0)
    stage=engine._graded_segment(project.media[0],project.clips[0],project,None)
    np.testing.assert_allclose(decode_rgb(stage,project.width),wanted,atol=3/65535,rtol=0)
    assert_deliveries(project,wanted,tmp_path,'asymmetric-')


def test_distinct_edges_roundtrip_recovery_copy_undo_redo(tmp_path):
    project,_=project_pair(tmp_path)
    editor=Editor(project); before=deepcopy(project)
    settings=configured('base','saturation',.11,.77)
    settings.inclusion_ranges=[QualifierRange(saturation_softness_low=.12,saturation_softness_high=.34,luma_softness_low=.56,luma_softness_high=.78)]
    settings.exclusion_ranges=[QualifierRange(saturation_softness_low=0.,saturation_softness_high=1.,luma_softness_low=None,luma_softness_high=.25)]
    grade=asdict(project.clips[0].grade); grade['qualifier']=asdict(settings)
    editor.update_clip('source',grade=grade); after=deepcopy(editor.project)
    assert editor.revision==1 and len(editor._undo)==1
    assert editor.undo() and editor.project==before
    assert editor.redo() and editor.project==after
    path=tmp_path/'asymmetric.lumen'; editor.project.save(path)
    assert Project.load(path)==after
    with RecoveryStore(tmp_path/'recovery') as store:
        assert store.load(store.write(editor.project,uuid4()))==after
    copied=copy_grade(editor.project,'source'); assert paste_grade(editor,'target',copied)
    assert editor.project.clips[1].grade.qualifier==settings
    revision=editor.revision; assert not paste_grade(editor,'target',copied); assert editor.revision==revision
    editor.project.clips[1].grade.qualifier.inclusion_ranges.clear()
    assert editor.project.clips[0].grade.qualifier==settings
    assert copied.grade_for_input('rec709').qualifier==settings


@pytest.mark.parametrize('channel,index',[('saturation',0),('saturation',3),('luma',4),('luma',5)])
@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
def test_full_resolution_detail_pick_changes_only_sampled_edge(channel,index,target):
    settings=configured(target,channel,0.,0.)
    rgb=patch_colors()[index].astype(np.float32)
    source=np.zeros((801,1027,3),np.float32); source[401,513]=rgb
    source.setflags(write=False)
    view=centered(1027,801,(513,401),4)
    gx,gy=view.grid_at(513,401)
    selected=view.extract(source)[gy,gx]
    np.testing.assert_array_equal(selected,source[401,513])
    selector=('base',0) if target=='base' else (target,0)
    result,_=soften_qualifier_range(settings,selected,target=selector,channel=channel)
    old=settings if target=='base' else getattr(settings,target)[0]
    new=result if target=='base' else getattr(result,target)[0]
    value=colorsys.rgb_to_hsv(*map(float,rgb))[1] if channel=='saturation' else np.dot(rgb.astype(float),[.2126,.7152,.0722])
    side='low' if value<getattr(old,channel+'_low') else 'high'
    distance=abs(value-getattr(old,channel+'_'+side))
    field=channel+'_softness_'+side
    assert getattr(new,field)==pytest.approx(2*distance,abs=3e-7)
    changed=[name for name,value in asdict(old).items() if asdict(new)[name]!=value]
    assert changed==[field]
    assert independent_range(selected,new)==pytest.approx(.5,abs=1e-6)
    matte=qualifier_mask(view.extract(source),result,canvas_size=(1027,801),origin=(view.x,view.y),stride=view.stride)
    assert matte[gy,gx]==pytest.approx(.5,abs=1e-6)
    alpha=float(matte[gy,gx])/2
    expected=rgb*(1-alpha)+np.array([1,0,1])*alpha
    np.testing.assert_allclose(selection_overlay(view.extract(source),matte)[gy,gx],expected,atol=1e-7)
    np.testing.assert_array_equal(source[401,513],rgb)


@pytest.mark.parametrize('field',['saturation_softness_low','saturation_softness_high','luma_softness_low','luma_softness_high'])
@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
def test_every_side_participates_in_both_cache_identities(tmp_path,monkeypatch,field,target):
    import lumen.color_media as color_media
    from lumen.color_source import SourceColor
    from lumen.render import RenderEngine
    from test_qualifier_integration import project as simple_project
    candidate=simple_project()
    original=configured(target)
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
        if target=='base': setattr(changed,field,.831)
        else: getattr(changed,target)[0]=replace(getattr(changed,target)[0],**{field:.831})
        clip.grade.qualifier=changed; after=paths()
        assert before[0]!=after[0] and before[1]!=after[1]
        assert calls==['stage','preview','stage','preview']
        assert paths()==after
        clip.grade.qualifier=original; assert paths()==before
        assert len(calls)==4
    finally: renderer.close()


@pytest.mark.skipif(not ffmpeg_available(),reason='FFmpeg/ffprobe required')
def test_real_asymmetric_cache_edits_keep_picker_source_and_undo_pixels(tmp_path,engine):
    settings=configured('base','saturation',.4,.03)
    colors=np.rint(patch_colors()*65535).astype('<u2')
    image=np.repeat(np.repeat(colors[None],64,axis=0),64,axis=1)
    project,frames=source_project(tmp_path,np.repeat(image[None],3,axis=0),grade=Grade(qualifier=settings),name='asymmetric-cache')
    original=deepcopy(settings); clip=project.clips[0]; media=project.media[0]
    snapshot=capture_picker_snapshot(project,'picture',0,stage='qualifier')
    before=(engine._graded_segment(media,clip,project,None),engine.preview(project))
    before_rgb=decode_rgb(before[0],project.width)
    settings.saturation_softness_high=.6
    after=(engine._graded_segment(media,clip,project,None),engine.preview(project))
    assert before[0]!=after[0] and before[1]!=after[1]
    assert not snapshot.is_current(project)
    fresh=capture_picker_snapshot(project,'picture',0,stage='qualifier')
    np.testing.assert_array_equal(fresh.frame,snapshot.frame)
    _,expected=independent_reference(frames[0].astype(float)/65535,settings)
    changed_rgb=decode_rgb(after[0],project.width)
    np.testing.assert_allclose(changed_rgb,expected,atol=3/65535,rtol=0)
    assert np.max(abs(changed_rgb-before_rgb))>.01
    # The low-side and core probes must not change when editing the high side.
    np.testing.assert_array_equal(changed_rgb[:,:3*64],before_rgb[:,:3*64])
    clip.grade.qualifier=original
    assert (engine._graded_segment(media,clip,project,None),engine.preview(project))==before
    np.testing.assert_array_equal(decode_rgb(before[0],project.width),before_rgb)
