"""Independent scalar contrast math and unchanged-production v25 compatibility."""
from dataclasses import fields
import json
import math
from pathlib import Path
import struct

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.model import Clip, Grade, Media, Project

PIVOTS = (0., .123456789012345, .18, .5, .731234567890123, 1.)


def f32(value):
    """Scalar IEEE-754 rounding without using the NumPy grading expression."""
    return struct.unpack('=f', struct.pack('=f', float(value)))[0]


def scalar_contrast(value, contrast, pivot):
    if contrast == 1:
        return float(value)
    center = f32(pivot)
    return f32(f32(f32(float(value) - center) * f32(contrast)) + center)


def samples():
    return np.array([[-2., -.1, -0.], [0., .018, .081], [.1234567, .18, .5],
                     [.7312345, 1., 3.], [1/65535, 32769/65535, 65534/65535]], np.float32)


@pytest.mark.parametrize('pivot', PIVOTS)
@pytest.mark.parametrize('contrast', (0., .25, .75, 1., 1.0000001, 1.7, 4.))
def test_contrast_matches_independent_scalar_float32_reference(pivot, contrast):
    rgb = samples(); original = rgb.copy()
    expected = np.array([scalar_contrast(value, contrast, pivot) for value in rgb.flat],
                        np.float32).reshape(rgb.shape)
    actual = apply_grade(rgb, Grade(contrast=contrast, contrast_pivot=pivot))
    np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
    np.testing.assert_array_equal(rgb.view(np.uint32), original.view(np.uint32))
    assert actual.dtype == np.float32 and not np.shares_memory(actual, rgb)


@pytest.mark.parametrize('pivot', PIVOTS)
def test_neutral_contrast_is_exact_identity_including_extended_values_and_signed_zero(pivot):
    rgb = samples()
    actual = apply_grade(rgb, Grade(contrast_pivot=pivot))
    assert actual.tobytes() == rgb.tobytes()
    assert actual.min() < 0 and actual.max() > 1


@pytest.mark.parametrize('pivot', PIVOTS)
def test_zero_contrast_collapses_to_pivot_without_quantizing(pivot):
    result = apply_grade(samples(), Grade(contrast=0, contrast_pivot=pivot))
    np.testing.assert_array_equal(result, np.full(result.shape, pivot, np.float32))
    if pivot not in (0, 1):
        assert np.any(np.abs(result*255 - np.rint(result*255)) > 1e-4)


@pytest.mark.parametrize('pivot', PIVOTS)
@pytest.mark.parametrize('contrast', (.3, 2., 4.))
def test_pivot_is_stationary_and_contrast_does_not_clip(pivot, contrast):
    rgb = np.array([[pivot, -2., 3.]], np.float32)
    result = apply_grade(rgb, Grade(contrast=contrast, contrast_pivot=pivot))
    assert result[0, 0] == np.float32(pivot)
    if contrast >= 2:
        assert result[0, 1] < 0 and result[0, 2] > 1


@pytest.mark.parametrize('encoding', ('rec709', 'srgb', 'linear_rec709'))
def test_pivot_acts_in_normalized_encoded_rec709_not_input_or_linear_space(encoding):
    rgb=np.array([[.02,.18,.7],[-.04,.4,1.2]],np.float32)
    def encode(linear):
        magnitude=abs(linear)
        encoded=4.5*magnitude if magnitude < .018 else 1.099*magnitude**.45-.099
        return math.copysign(encoded,linear)
    def normalize(value):
        if encoding=='rec709': return value
        if encoding=='linear_rec709': return encode(value)
        magnitude=abs(value)
        linear=magnitude/12.92 if magnitude<=.04045 else ((magnitude+.055)/1.055)**2.4
        linear=math.copysign(linear,value)
        return encode(linear)
    expected=np.array([(.6*(normalize(float(v))-.18)+.18) for v in rgb.flat]).reshape(rgb.shape)
    result=apply_grade(rgb,Grade(input_encoding=encoding,contrast=.6,contrast_pivot=.18))
    np.testing.assert_allclose(result,expected,rtol=0,atol=2e-7)


def test_pivot_follows_legacy_exposure_and_precedes_curves():
    rgb=samples()
    grade=Grade(exposure=1, contrast=1.7, contrast_pivot=.18,
                master_curve=[[0.,.1],[1.,.8]])
    expected=np.array([.1+.7*scalar_contrast(f32(float(v)*2),1.7,.18)
                       for v in rgb.flat]).reshape(rgb.shape)
    np.testing.assert_allclose(apply_grade(rgb,grade),expected,rtol=0,atol=1e-6)


@pytest.mark.parametrize('pivot', PIVOTS)
@pytest.mark.parametrize('mode', ('bypass','disabled'))
def test_creative_bypass_ignores_pivot_but_preserves_input_normalization(pivot, mode):
    rgb=samples()
    grade=Grade(contrast=3.,contrast_pivot=pivot,enabled=mode!='disabled')
    np.testing.assert_array_equal(apply_grade(rgb,grade,bypass=mode=='bypass'),rgb)
    grade.input_encoding='linear_rec709'
    expected=apply_grade(rgb,Grade(input_encoding='linear_rec709'))
    np.testing.assert_array_equal(apply_grade(rgb,grade,bypass=mode=='bypass'),expected)


def test_noncontiguous_float_frame_and_low_bits_remain_unquantized():
    original=np.random.default_rng(2601).random((13,17,3),dtype=np.float32)
    rgb=original[::2,::3]
    assert not rgb.flags.c_contiguous
    grade=Grade(contrast=.7123456789,contrast_pivot=.2345678901234)
    actual=apply_grade(rgb,grade)
    np.testing.assert_array_equal(actual,apply_grade(np.ascontiguousarray(rgb),grade))
    assert np.any(np.abs(actual*65535 - np.rint(actual*65535)) > .01)
    np.testing.assert_array_equal(rgb,original[::2,::3])


def test_appended_field_preserves_positional_grade_constructor():
    names=[field.name for field in fields(Grade)]
    assert names[-3:]==['contrast_pivot','white_balance_enabled','white_balance_gains']
    legacy=Grade(.25,1.7,.8,.1,-.1,.2,-.2,False,'')
    assert (legacy.exposure,legacy.contrast,legacy.saturation,legacy.enabled)==(.25,1.7,.8,False)
    assert legacy.contrast_pivot==.5


def test_default_pivot_is_bit_exact_against_actual_unchanged_v25_production_capture(tmp_path):
    import os
    from scripts.compare_contrast_v25_native import native_capture
    root = Path(__file__).resolve().parents[1]
    archive = Path(os.environ.get('LUMEN_V25_ARCHIVE', root/'build/private-v25.zip'))
    fixture_path = native_capture(archive, tmp_path/'native-reference')
    with np.load(fixture_path,allow_pickle=False) as fixture:
        manifest=json.loads(str(fixture['manifest']))
        assert manifest['schema']==25
        assert manifest['source_sha256']['color.py']=='9cd19971a9a0acc285844df1fdbbf249fa779e6a198be139589cbd01856f065e'
        cube=tmp_path/'original-synthetic.cube'; cube.write_text(str(fixture['cube']))
        records=json.loads(str(fixture['grades']))
        assert len(records)==18
        for index,record in enumerate(records):
            assert 'contrast_pivot' not in record
            if record['lut_path']: record['lut_path']=str(cube)
            # These immutable records are schema 25, not current constructor
            # payloads. Exercise the real historical project migration rather
            # than weakening strict current qualifier parsing or patching the
            # golden data with invented modern fields.
            media = Media('golden', 'synthetic-golden.mov', 'golden', 1.)
            legacy = Project(media=[media], clips=[Clip('clip', media.id, 0, 0, 0, 1)]).to_dict()
            legacy['version'] = manifest['schema']
            from clip_composite_legacy_fixture import strip_clip_composite_field
            strip_clip_composite_field(legacy)
            legacy['media'][0].pop('input_encoding')
            legacy['clips'][0]['grade'] = record
            migrated = Project.from_dict(legacy).clips[0].grade
            assert migrated.qualifier.window_keyframes == []
            assert migrated.contrast_pivot == .5
            actual=apply_grade(fixture['rgb'],migrated)
            np.testing.assert_array_equal(actual.view(np.uint32),fixture['outputs'][index].view(np.uint32),
                                          err_msg=f'v25 compatibility scenario {index}')
