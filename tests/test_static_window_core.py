"""Analytic canvas-space geometry, legacy protection and immutable grade edits."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import asdict, replace
import math

import numpy as np
import pytest

from lumen.qualifier import QualifierSettings, qualifier_mask, apply_qualifier
from lumen.model import VERSION, Editor, Project
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.project_recovery import RecoveryStore
from test_qualifier_integration import project


def settings(**values):
    return QualifierSettings(**dict(enabled=True, hue_width=.5, window_shape='ellipse',
                                   exposure=.5, **values))


def frame(height=11, width=19):
    return np.full((height, width, 3), .25, dtype=np.float32)


def scalar(x, y, width, height, s):
    dx = ((x+.5)/width-s.window_center_x)*2/s.window_width
    dy = ((y+.5)/height-s.window_center_y)*2/s.window_height
    r = math.hypot(dx, dy) if s.window_shape == 'ellipse' else max(abs(dx), abs(dy))
    w = 1 if r <= 1 else (0 if not s.window_feather else max(0, 1-(r-1)/s.window_feather))
    return 1-w if s.window_invert else w


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('size', [(7, 17), (18, 5), (1, 1), (256, 513)])
@pytest.mark.parametrize('invert', [False, True])
@pytest.mark.parametrize('feather', [0., .3, 1.])
def test_analytic_shape_pixel_centers_aspect_and_bounded_blocks(shape, size, invert, feather):
    h,w = size
    s=replace(settings(), window_shape=shape,
        window_center_x=.37, window_center_y=.64, window_width=.41,
        window_height=.73, window_feather=feather, window_invert=invert)
    expected=np.array([[scalar(x,y,w,h,s) for x in range(w)] for y in range(h)],np.float32)
    actual=qualifier_mask(frame(h,w),s)
    np.testing.assert_allclose(actual,expected,rtol=0,atol=6e-8)
    assert actual.dtype == np.float32


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
def test_exact_core_half_feather_and_outside(shape):
    # 9x1 pixel centers: .5, 1.5, ...8.5. Full width 4 pixels;
    # center is 4.5, core ends 2.5/6.5, feather ends .5/8.5.
    s=replace(settings(), window_shape=shape, window_width=4/9,
              window_height=1, window_feather=1)
    np.testing.assert_allclose(qualifier_mask(frame(1,9),s),
                               [[0,.5,1,1,1,1,1,.5,0]],rtol=0,atol=1e-7)


@pytest.mark.parametrize('shape', ['ellipse', 'rectangle'])
@pytest.mark.parametrize('stride', [1,2,5])
def test_crop_and_overview_match_full_canvas_without_feedback(shape,stride):
    rgb=frame(57,83); original=rgb.copy()
    s=replace(settings(),window_shape=shape,window_feather=.4)
    full=qualifier_mask(rgb,s); adjusted=apply_qualifier(rgb,s)
    local=rgb[7:49:stride,11:76:stride]
    kwargs=dict(canvas_size=(83,57),origin=(11,7),stride=stride)
    np.testing.assert_array_equal(qualifier_mask(local,s,**kwargs),full[7:49:stride,11:76:stride])
    np.testing.assert_array_equal(apply_qualifier(local,s,**kwargs),adjusted[7:49:stride,11:76:stride])
    np.testing.assert_array_equal(rgb,original)


def test_window_multiplies_color_range_not_union_and_invert_is_only_spatial():
    rgb=np.zeros((5,9,3),np.float32);rgb[...,0]=.5
    rgb[2,4]=[0,.5,0]
    s=replace(settings(),hue_width=.05,window_feather=0)
    mask=qualifier_mask(rgb,s)
    assert mask[2,3] == 1 and mask[2,4] == 0 and mask[2,0] == 0
    out=apply_qualifier(rgb,s)
    assert out[2,3,0] > rgb[2,3,0]
    np.testing.assert_array_equal(out[2,0],rgb[2,0])
    inv=qualifier_mask(rgb,replace(s,window_invert=True))
    assert inv[2,0] == 1 and inv[2,3] == inv[2,4] == 0
    whole=qualifier_mask(rgb,replace(s,window_shape='off'))
    np.testing.assert_array_equal(mask+inv,whole)


@pytest.mark.parametrize('shape',['off','ellipse','rectangle'])
@pytest.mark.parametrize('disabled',[False,True])
def test_neutral_or_disabled_preserve_signed_zero_and_extreme_finite_bits(shape,disabled):
    rgb=np.array([[[-0.,-.2,np.finfo(np.float32).max],[.25,.25,.25]]],np.float32)
    s=replace(settings(),window_shape=shape,enabled=not disabled,exposure=0)
    out=apply_qualifier(rgb,s)
    np.testing.assert_array_equal(out.view(np.uint32),rgb.view(np.uint32))
    assert not np.shares_memory(out,rgb)


@pytest.mark.parametrize('field,value', [('window_shape','polygon'),('window_shape',None),
    ('window_center_x',-.1),('window_center_y',1.1),('window_width',0),('window_width',2.1),
    ('window_height',float('nan')),('window_feather',-1),('window_feather',True),
    ('window_invert',1)])
def test_strict_window_validation_even_off(field,value):
    data=asdict(QualifierSettings());data[field]=value
    with pytest.raises(ValueError): QualifierSettings.from_dict(data)


@pytest.mark.parametrize('kw', [dict(canvas_size=(0,5)),dict(canvas_size=(5,True)),
    dict(origin=(-1,0)),dict(origin=(2,0)),dict(stride=0),dict(stride=2),dict(stride=True)])
def test_invalid_sample_grid_rejected(kw):
    with pytest.raises(ValueError): qualifier_mask(frame(5,5),settings(),**kw)


def test_spatial_window_requires_image_not_ambiguous_flat_swatches():
    with pytest.raises(ValueError): qualifier_mask(np.ones((9,3),np.float32),settings())


def test_schema19_and_exact_v18_migration_cannot_smuggle_or_drop_geometry():
    original=project(); original.clips[0].grade.qualifier=settings()
    payload=original.to_dict(); assert payload['version']== VERSION
    assert Project.from_dict(payload)==original
    old=deepcopy(payload);old['version']=18; strip_contrast_pivot_field(old)
    with pytest.raises(ValueError): Project.from_dict(old)
    old['clips'][0]['grade']['qualifier']={k:v for k,v in old['clips'][0]['grade']['qualifier'].items() if not k.startswith('window_') and k not in ('inclusion_ranges', 'exclusion_ranges', 'matte_black', 'matte_white', 'matte_smoothing', 'matte_morphology')}
    from qualifier_legacy_fixture import strip_asymmetric_fields
    strip_asymmetric_fields(old['clips'][0]['grade']['qualifier'])
    restored=Project.from_dict(old)
    assert restored.clips[0].grade.qualifier.window_shape=='off'
    assert restored.clips[0].grade.qualifier.exposure==.5
    assert 'window_shape' not in old['clips'][0]['grade']['qualifier']
    for field in [k for k in payload['clips'][0]['grade']['qualifier'] if k.startswith('window_')]:
        missing=deepcopy(payload);del missing['clips'][0]['grade']['qualifier'][field]
        with pytest.raises(ValueError): Project.from_dict(missing)


def test_window_persist_and_atomic_undo_redo(tmp_path):
    editor=Editor(project()); bind_missing_project_paths(editor.project, tmp_path);before=deepcopy(editor.project)
    grade=asdict(editor.project.clips[0].grade);grade['qualifier']=asdict(settings())
    editor.update_clip('picture',grade=grade)
    after=deepcopy(editor.project)
    path=tmp_path/'window.lumen';editor.project.save(path)
    assert Project.load(path)==after
    editor.undo();assert editor.project==before
    editor.redo();assert editor.project==after


def test_prior_reader_ceiling_rejects_schema20_before_decoding(monkeypatch):
    from lumen import model
    payload=project().to_dict()
    assert payload['version']== VERSION
    # Deliberately malformed payload proves version gate happens before decode.
    payload['clips']='must not be decoded by the old reader'
    monkeypatch.setattr(model,'VERSION',19)
    with pytest.raises(ValueError,match='Unsupported project format or version'):
        model.Project.from_dict(payload)


def test_v18_migration_preserves_frozen_prior_qualifier_output_bits():
    # Frozen directly from the schema18 implementation, not this evaluator.
    old_key={'enabled':True,'hue_center':.08,'hue_width':.2,'hue_softness':.2,
        'saturation_low':.2,'saturation_high':.7,'saturation_softness':.15,
        'luma_low':.15,'luma_high':.7,'luma_softness':.1,
        'exposure':.35,'saturation':1.2,'hue_shift':.07}
    payload=project().to_dict();payload['version']=18; strip_contrast_pivot_field(payload)
    payload['clips'][0]['grade']['qualifier']=old_key
    restored=Project.from_dict(payload).clips[0].grade.qualifier
    assert restored.window_shape=='off'
    rgb=np.array([[[.4,.2,.1],[.1,.3,.4],[.8,.5,.2],[.3,.3,.3]],
        [[.12,.22,.1],[.1,.14,.12],[.25,.11,.1],[-0.,1.2,-.1]]],np.float32)
    expected=np.array([
        [[1056491195,1051722416,1034147145],[1036831949,1050253722,1053609165],
         [1064675890,1062177038,1041720095],[1050253722,1050253722,1050253722]],
        [[1037762510,1049575782,1041525038],[1037494946,1041885819,1040800619],
         [1051128704,1045614562,1037551166],[2147483648,1067030938,3184315597]]
    ],dtype=np.uint32)
    np.testing.assert_array_equal(apply_qualifier(rgb,restored).view(np.uint32),expected)
    np.testing.assert_array_equal(apply_qualifier(rgb,QualifierSettings(**old_key)).view(np.uint32),expected)


@pytest.mark.parametrize('shape', [(0,5,3),(5,0,3),(0,0,3)])
@pytest.mark.parametrize('explicit_canvas',[False,True])
@pytest.mark.parametrize('operation',[qualifier_mask,apply_qualifier])
def test_active_windows_reject_empty_images_consistently(shape,explicit_canvas,operation):
    kwargs={'canvas_size':(5,5)} if explicit_canvas else {}
    with pytest.raises(ValueError,match='Invalid spatial window canvas coordinates'):
        operation(np.empty(shape,np.float32),settings(),**kwargs)
