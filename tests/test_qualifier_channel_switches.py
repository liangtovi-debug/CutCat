"""Independent channel truth tables and actual unchanged-v24 golden outputs."""
from contrast_legacy_fixture import strip_contrast_pivot_field
import colorsys
from copy import deepcopy
from dataclasses import asdict, fields, replace
from itertools import product
import json
from pathlib import Path

import numpy as np
import pytest

from lumen.model import Project, VERSION
from lumen.qualifier import QualifierRange, QualifierSettings, qualifier_mask, apply_qualifier
from test_qualifier_integration import project

CHANNELS = ('hue_enabled', 'saturation_enabled', 'luma_enabled')
COMBINATIONS = list(product((False, True), repeat=3))
CORE = dict(hue_center=.97, hue_width=.09, hue_softness=.12,
    saturation_low=.3, saturation_high=.7, saturation_softness=.01,
    saturation_softness_low=.2, saturation_softness_high=.15,
    luma_low=.2, luma_high=.65, luma_softness=.01,
    luma_softness_low=.13, luma_softness_high=.23)


def pixels():
    values = [colorsys.hsv_to_rgb(h,s,v)
        for h,s,v in product((0,.03,.09,.18,.5,.8,.88,.97),
                             (0,.1,.2,.3,.5,.7,.775,.85,1), (.1,.3,.7,1))]
    values += [(v,v,v) for v in (0,.07,.135,.2,.4,.65,.765,.88,1)]
    values += [(1,0,0),(0,1,0),(0,0,1),(-.01,.3,.2),(1.01,.3,.2)]
    return np.array(values, np.float32)


def ramp(value, low, high, low_feather, high_feather):
    if low <= value <= high:
        return 1.
    if value < low:
        return max(0.,1.-(low-value)/low_feather) if low_feather else 0.
    return max(0.,1.-(value-high)/high_feather) if high_feather else 0.


def scalar_key(pixel, flags):
    """No production helper or mask is used to calculate expected weights."""
    r,g,b = map(float,pixel)
    if min(r,g,b)<0 or max(r,g,b)>1:
        return 0.
    h,s,_=colorsys.rgb_to_hsv(r,g,b)
    y=.2126*r+.7152*g+.0722*b
    weights=[1.,1.,1.]
    if flags[0]:
        distance=min(abs(h-.97),1-abs(h-.97))
        weights[0]=ramp(distance,0,.09,.12,.12) if max(r,g,b)-min(r,g,b)>1e-6 else 0.
    if flags[1]: weights[1]=ramp(s,.3,.7,.2,.15)
    if flags[2]: weights[2]=ramp(y,.2,.65,.13,.23)
    return weights[0]*weights[1]*weights[2]


@pytest.mark.parametrize('flags',COMBINATIONS)
@pytest.mark.parametrize('target', ['base','add','exclude'])
def test_all_eight_combinations_against_independent_scalar_truth_table(flags,target):
    rgb=pixels()
    entry=QualifierRange(**CORE,**dict(zip(CHANNELS,flags)))
    expected=np.array([scalar_key(p,flags) for p in rgb],np.float32)
    if target=='base':
        q=QualifierSettings(enabled=True,**CORE,**dict(zip(CHANNELS,flags)))
    elif target=='add':
        # Base is identically empty: zero-saturation plateau plus chromatic hue.
        q=QualifierSettings(enabled=True,saturation_low=0,saturation_high=0,
            saturation_softness=0,inclusion_ranges=[entry])
    else:
        q=QualifierSettings(enabled=True,hue_width=.5,exclusion_ranges=[entry])
        valid=np.all((rgb>=0)&(rgb<=1),axis=-1)
        expected=np.where(valid,1-expected,0).astype(np.float32)
    actual=qualifier_mask(rgb,q)
    assert actual.dtype==np.float32
    np.testing.assert_allclose(actual,expected,rtol=0,atol=1.4e-6)
    assert np.all(np.isfinite(actual))
    if flags==(False,False,False):
        np.testing.assert_array_equal(actual[:-2],0 if target=='exclude' else 1)


@pytest.mark.parametrize('cls',[QualifierSettings,QualifierRange])
@pytest.mark.parametrize('field',CHANNELS)
@pytest.mark.parametrize('bad',[0,1,0.,1.,None,'true','false',[],{},np.bool_(True),float('nan')])
def test_switches_are_strict_booleans(cls,field,bad):
    with pytest.raises(ValueError): cls(**{field:bad})


@pytest.mark.parametrize('field',CHANNELS)
@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
def test_mutated_switch_rejected_even_when_qualifier_disabled(field,target):
    q=QualifierSettings(inclusion_ranges=[QualifierRange()],exclusion_ranges=[QualifierRange()])
    record=q if target=='base' else getattr(q,target)[0]
    object.__setattr__(record,field,1)
    with pytest.raises(ValueError): q.to_dict()
    with pytest.raises(ValueError): qualifier_mask(np.zeros((2,3),np.float32),q)


@pytest.mark.parametrize('flags',COMBINATIONS)
def test_v25_roundtrip_and_required_fields(flags):
    p=project()
    kwargs=dict(zip(CHANNELS,flags))
    p.clips[0].grade.qualifier=QualifierSettings(enabled=True,**kwargs,
        inclusion_ranges=[QualifierRange(**kwargs)],exclusion_ranges=[QualifierRange(**kwargs)])
    payload=p.to_dict()
    assert payload['version']==VERSION
    assert Project.from_dict(payload)==p
    for target in ('base','inclusion_ranges','exclusion_ranges'):
        for field in CHANNELS:
            bad=deepcopy(payload)
            record=bad['clips'][0]['grade']['qualifier']
            if target!='base': record=record[target][0]
            del record[field]
            with pytest.raises(ValueError): Project.from_dict(bad)


def legacy_payload():
    p=project()
    p.clips[0].grade.qualifier=QualifierSettings(enabled=True,**CORE,
        inclusion_ranges=[QualifierRange(**CORE)],exclusion_ranges=[QualifierRange(**CORE)])
    data=p.to_dict(); data['version']=24; strip_contrast_pivot_field(data)
    q=data['clips'][0]['grade']['qualifier']
    for record in [q,*q['inclusion_ranges'],*q['exclusion_ranges']]:
        for field in CHANNELS: del record[field]
    return data


def test_v24_migration_defaults_to_all_channels_without_mutating_input():
    data=legacy_payload(); original=deepcopy(data)
    q=Project.from_dict(data).clips[0].grade.qualifier
    assert data==original
    for record in [q,*q.inclusion_ranges,*q.exclusion_ranges]:
        assert all(getattr(record,f) is True for f in CHANNELS)


@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
@pytest.mark.parametrize('field',CHANNELS)
def test_v24_cannot_smuggle_any_new_switch(target,field):
    data=legacy_payload(); record=data['clips'][0]['grade']['qualifier']
    if target!='base': record=record[target][0]
    record[field]=True
    with pytest.raises(ValueError): Project.from_dict(data)


def test_v24_reader_rejects_v25_before_parsing_clips(monkeypatch):
    import lumen.model as model
    payload=project().to_dict(); payload['clips']='malformed clip collection'
    monkeypatch.setattr(model,'VERSION',24)
    with pytest.raises(ValueError,match='Unsupported'): Project.from_dict(payload)


def test_all_enabled_is_bit_exact_against_actual_unchanged_v24_capture():
    path=Path(__file__).parent/'fixtures/channel_v24_golden.npz'
    with np.load(path,allow_pickle=False) as fixture:
        for index,record in enumerate(json.loads(str(fixture['settings']))):
            # Deliberately omitted switches exercise the appended True defaults.
            record['inclusion_ranges']=[QualifierRange(**x) for x in record['inclusion_ranges']]
            record['exclusion_ranges']=[QualifierRange(**x) for x in record['exclusion_ranges']]
            q=QualifierSettings(**record)
            np.testing.assert_array_equal(qualifier_mask(fixture['rgb'],q),fixture['masks'][index])
            np.testing.assert_array_equal(apply_qualifier(fixture['rgb'],q),fixture['corrected'][index])


@pytest.mark.parametrize('flags',COMBINATIONS)
def test_mixed_set_union_subtraction_then_levels_preserves_existing_order(flags):
    rgb=pixels()
    add_flags=(not flags[0],flags[1],not flags[2])
    exclude_flags=(flags[2],not flags[1],flags[0])
    q=QualifierSettings(enabled=True,**CORE,**dict(zip(CHANNELS,flags)),
        inclusion_ranges=[QualifierRange(**CORE,**dict(zip(CHANNELS,add_flags)))],
        exclusion_ranges=[QualifierRange(**CORE,**dict(zip(CHANNELS,exclude_flags)))],
        matte_black=.17,matte_white=.83)
    expected=[]
    for p in rgb:
        if min(p)<0 or max(p)>1:
            expected.append(0.); continue
        combined=max(scalar_key(p,flags),scalar_key(p,add_flags))
        combined*=1-scalar_key(p,exclude_flags)
        expected.append(min(1.,max(0.,(combined-.17)/(.83-.17))))
    np.testing.assert_allclose(qualifier_mask(rgb,q),expected,rtol=0,atol=2.2e-6)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf')])
def test_all_channels_disabled_still_rejects_nonfinite_input(value):
    q=QualifierSettings(enabled=True,**dict.fromkeys(CHANNELS,False))
    rgb=np.array([[.2,.3,.4],[.5,value,.5]],np.float32)
    with pytest.raises(ValueError,match='finite'): qualifier_mask(rgb,q)


@pytest.mark.parametrize('flags',COMBINATIONS)
def test_strided_input_and_fractional_masks_are_not_quantized(flags):
    rgb=pixels()[::3]
    assert not rgb.flags.c_contiguous
    q=QualifierSettings(enabled=True,**CORE,**dict(zip(CHANNELS,flags)))
    actual=qualifier_mask(rgb,q)
    np.testing.assert_array_equal(actual,qualifier_mask(np.ascontiguousarray(rgb),q))
    if any(flags):
        fractional=actual[(actual>0)&(actual<1)]
        assert fractional.size
        assert np.any(np.abs(fractional*255-np.rint(fractional*255))>1e-3)


def test_all_disabled_does_not_disable_master_bypass_or_spatial_window():
    image=np.full((7,9,3),.5,np.float32)
    q=QualifierSettings(enabled=False,**dict.fromkeys(CHANNELS,False),exposure=.7)
    np.testing.assert_array_equal(qualifier_mask(image,q),0)
    np.testing.assert_array_equal(apply_qualifier(image,q),image)
    q=replace(q,enabled=True,window_shape='rectangle',window_width=.5,
              window_height=.5,window_feather=0)
    actual=qualifier_mask(image,q)
    expected=np.zeros((7,9),np.float32)
    for y,x in product(range(7),range(9)):
        expected[y,x]=float(abs((x+.5)/9-.5)<=.25 and abs((y+.5)/7-.5)<=.25)
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
@pytest.mark.parametrize('field',CHANNELS)
@pytest.mark.parametrize('bad',[0,1,None,'false'])
def test_deserialization_rejects_malformed_booleans_at_every_range(target,field,bad):
    p=project(); p.clips[0].grade.qualifier=QualifierSettings(
        inclusion_ranges=[QualifierRange()],exclusion_ranges=[QualifierRange()])
    data=p.to_dict(); record=data['clips'][0]['grade']['qualifier']
    if target!='base': record=record[target][0]
    record[field]=bad
    with pytest.raises(ValueError): Project.from_dict(data)


@pytest.mark.parametrize('flags',COMBINATIONS)
@pytest.mark.parametrize('operation',['replace','add','subtract'])
def test_picker_changes_only_enabled_cores_and_preserves_all_other_state(flags,operation):
    from lumen.qualifier_picker import replace_qualifier_range, add_qualifier_range
    q=QualifierSettings(enabled=True,**CORE,**dict(zip(CHANNELS,flags)),
        exposure=.27,saturation=.83,hue_shift=.04,matte_black=.12,matte_white=.79,
        matte_smoothing=.4,matte_morphology=-.2,window_shape='ellipse',window_width=.7,
        inclusion_ranges=[QualifierRange(hue_center=.25)],
        exclusion_ranges=[QualifierRange(hue_center=.75)])
    before=asdict(q); rgb=np.array([.81,.42,.23],np.float32); rgb_before=rgb.copy()
    h,s,_=colorsys.rgb_to_hsv(*map(float,rgb))
    y=sum(float(v)*c for v,c in zip(rgb,(.2126,.7152,.0722)))
    expected={}
    if flags[0]: expected.update(hue_center=h,hue_width=10/360)
    if flags[1]: expected.update(saturation_low=max(0,s-.1),saturation_high=min(1,s+.1))
    if flags[2]: expected.update(luma_low=max(0,y-.1),luma_high=min(1,y+.1))
    if operation=='replace':
        result,_=replace_qualifier_range(q,rgb)
        selected=result
        preserved=asdict(result)
        for name in expected: preserved[name]=before[name]
        preserved['inclusion_ranges']=before['inclusion_ranges']
        preserved['exclusion_ranges']=before['exclusion_ranges']
        assert preserved==before
        assert result.inclusion_ranges==result.exclusion_ranges==[]
    else:
        target='exclusion_ranges' if operation=='subtract' else 'inclusion_ranges'
        result,_=add_qualifier_range(q,rgb,exclude=operation=='subtract')
        selected=getattr(result,target)[-1]
        preserved=asdict(result); preserved[target]=preserved[target][:-1]
        assert preserved==before
        for field in fields(QualifierRange):
            if field.name not in expected:
                assert getattr(selected,field.name)==getattr(q,field.name)
    for name,value in expected.items():
        assert getattr(selected,name)==pytest.approx(value,abs=1e-7)
    assert all(getattr(selected,name) is flag for name,flag in zip(CHANNELS,flags))
    assert asdict(q)==before
    np.testing.assert_array_equal(rgb,rgb_before)


@pytest.mark.parametrize('channel',['hue','saturation','luma'])
@pytest.mark.parametrize('target',['base','inclusion_ranges','exclusion_ranges'])
def test_disabled_channel_softness_pick_rejects_without_any_mutation(channel,target):
    from lumen.qualifier_picker import soften_qualifier_range
    options={channel+'_enabled':False}
    q=QualifierSettings(**options,inclusion_ranges=[QualifierRange(**options)],
                         exclusion_ranges=[QualifierRange(**options)])
    before=asdict(q); rgb=np.array([.8,.4,.2],np.float32)
    with pytest.raises(ValueError,match='Enable'):
        soften_qualifier_range(q,rgb,target=(target,0),channel=channel)
    assert asdict(q)==before


@pytest.mark.parametrize('operation',['replace','add','subtract','initialize'])
@pytest.mark.parametrize('hue_enabled',[False,True])
def test_gray_is_pickable_exactly_when_hue_is_disabled(operation,hue_enabled):
    from lumen.qualifier_picker import (replace_qualifier_range,add_qualifier_range,
                                       initialize_from_sample)
    q=QualifierSettings(**CORE,hue_enabled=hue_enabled)
    before=asdict(q); gray=np.full(3,.5,np.float32)
    if operation=='initialize':
        if hue_enabled:
            with pytest.raises(ValueError,match='gray'): initialize_from_sample(gray,q)
            assert asdict(q)==before
            return
        result=initialize_from_sample(gray,q)
    elif operation=='replace': result=replace_qualifier_range(q,gray)
    else: result=add_qualifier_range(q,gray,exclude=operation=='subtract')
    assert asdict(q)==before
    if hue_enabled:
        assert result is None
    else:
        updated,sample=result
        selected=(updated.inclusion_ranges[-1] if operation=='add' else
                  updated.exclusion_ranges[-1] if operation=='subtract' else updated)
        assert sample.hue is None
        assert selected.hue_center==q.hue_center and selected.hue_width==q.hue_width
        assert selected.hue_enabled is False
        assert selected.saturation_low==0 and selected.saturation_high==pytest.approx(.1)
        assert selected.luma_low==pytest.approx(.4) and selected.luma_high==pytest.approx(.6)


def test_all_off_replace_retains_every_core_but_explicitly_clears_extra_sets():
    from lumen.qualifier_picker import replace_qualifier_range
    q=QualifierSettings(**CORE,**dict.fromkeys(CHANNELS,False),
        inclusion_ranges=[QualifierRange()],exclusion_ranges=[QualifierRange()])
    before=asdict(q)
    result,sample=replace_qualifier_range(q,np.full(3,.5,np.float32))
    expected=deepcopy(before); expected.update(inclusion_ranges=[],exclusion_ranges=[])
    assert asdict(result)==expected and asdict(q)==before
    assert sample.hue is None
