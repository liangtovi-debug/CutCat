"""Independent edge mathematics, picker transactions and strict v24 migration."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import asdict, replace
import colorsys

import numpy as np
import pytest

from lumen.model import Project, VERSION
from lumen.qualifier import (ASYMMETRIC_SOFTNESS_FIELDS, QualifierRange,
    QualifierSettings, _interval_weight, qualifier_mask, range_softness)
from lumen.qualifier_picker import (add_qualifier_range, initialize_from_sample,
    soften_qualifier_range)
from test_qualifier_integration import project


@pytest.mark.parametrize('low_soft,high_soft', [(0, 0), (0, .4), (.2, 0), (.2, .4),
    (1, .01), (float.fromhex('0x0.0000000000001p-1022'), .3)])
def test_independent_ramps_match_scalar_oracle(low_soft, high_soft):
    values = np.array([-.1, 0, .1, .2, .25, .3, .4, .5, .7, .8, .9, 1, 1.1], np.float64)
    expected = []
    for value in values:
        if .3 <= value <= .7:
            result = 1
        elif value < .3:
            result = 0 if not low_soft or value <= .3-low_soft else 1-(.3-value)/low_soft
        else:
            result = 0 if not high_soft or value >= .7+high_soft else 1-(value-.7)/high_soft
        expected.append(result)
    with np.errstate(all='raise'):
        actual = _interval_weight(values, .3, .7, low_soft, high_soft)
    np.testing.assert_array_equal(actual, np.array(expected, np.float32))


@pytest.mark.parametrize('cls', [QualifierSettings, QualifierRange])
@pytest.mark.parametrize('field', sorted(ASYMMETRIC_SOFTNESS_FIELDS))
@pytest.mark.parametrize('bad', [True, False, '0', [], {}, float('nan'), float('inf'), -1e-10, 1.00001])
def test_side_values_reject_invalid_without_clamp(cls, field, bad):
    with pytest.raises(ValueError):
        cls(**{field: bad})


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
def test_legacy_fallback_and_explicit_overrides_have_no_hidden_rewriting(channel):
    q = QualifierSettings(**{channel+'_softness': .17})
    assert range_softness(q, channel, 'low') == range_softness(q, channel, 'high') == .17
    q = replace(q, **{channel+'_softness_low': .09})
    setattr(q, channel+'_softness', .32)
    assert range_softness(q, channel, 'low') == .09
    assert range_softness(q, channel, 'high') == .32
    assert getattr(q, channel+'_softness_high') is None
    assert QualifierSettings.from_dict(q.to_dict()) == q
    assert replace(q, exposure=.2).to_dict()[channel+'_softness_high'] is None
    inherited = replace(q, **{channel+'_softness_low': None})
    assert range_softness(inherited, channel, 'low') == .32


@pytest.mark.parametrize('target', [('base', 0), ('inclusion_ranges', 0), ('exclusion_ranges', 0)])
@pytest.mark.parametrize('channel', ['saturation', 'luma'])
@pytest.mark.parametrize('edge', ['low', 'high'])
def test_picker_changes_only_selected_outside_edge(target, channel, edge):
    core = dict(hue_width=.5, saturation_low=.4, saturation_high=.6,
        luma_low=.4, luma_high=.6, saturation_softness=.01, luma_softness=.01,
        saturation_softness_high=.025, luma_softness_high=.025)
    q = QualifierSettings(**core, inclusion_ranges=[QualifierRange(**core)],
                          exclusion_ranges=[QualifierRange(**core)])
    before = asdict(q)
    value = .25 if edge == 'low' else .75
    rgb = np.array(colorsys.hsv_to_rgb(0, value, 1), np.float32) if channel == 'saturation' else np.full(3, value, np.float32)
    out, sample = soften_qualifier_range(q, rgb, target=target, channel=channel)
    expected = deepcopy(before)
    selected = expected if target[0] == 'base' else expected[target[0]][target[1]]
    outside = .4-getattr(sample,channel) if edge=='low' else getattr(sample,channel)-.6
    selected[f'{channel}_softness_{edge}'] = 2*outside
    assert asdict(out) == expected
    assert asdict(q) == before
    repeated, _ = soften_qualifier_range(out, rgb, target=target, channel=channel)
    assert asdict(repeated) == expected


@pytest.mark.parametrize('channel', ['saturation', 'luma'])
def test_core_and_sufficient_inherited_feather_are_exact_serialized_noops(channel):
    q = QualifierSettings(saturation_low=.4, saturation_high=.6, luma_low=.4, luma_high=.6,
                          saturation_softness=.8, luma_softness=.8)
    for value in (.25, .5, .75):
        rgb = np.array(colorsys.hsv_to_rgb(0, value, 1), np.float32) if channel == 'saturation' else np.full(3, value, np.float32)
        out, _ = soften_qualifier_range(q, rgb, channel=channel)
        assert asdict(out) == asdict(q)


def test_replacement_and_new_ranges_preserve_all_four_edge_overrides():
    q = QualifierSettings(saturation_softness_low=.12, saturation_softness_high=.34,
                         luma_softness_low=.56, luma_softness_high=.78)
    source = np.array([.8, .3, .2], np.float32)
    replaced, _ = initialize_from_sample(source, q)
    for exclude in (False, True):
        added, _ = add_qualifier_range(q, source, exclude=exclude)
        entry = (added.exclusion_ranges if exclude else added.inclusion_ranges)[0]
        for name in ASYMMETRIC_SOFTNESS_FIELDS:
            assert getattr(entry, name) == getattr(replaced, name) == getattr(q, name)


def legacy_payload(version):
    p = project()
    q = QualifierSettings(enabled=True, saturation_low=.2, saturation_high=.7,
        saturation_softness=.137, luma_low=.1, luma_high=.8, luma_softness=.241,
        inclusion_ranges=[QualifierRange(saturation_softness=.123)],
        exclusion_ranges=[QualifierRange(luma_softness=.345)])
    p.clips[0].grade.qualifier = q
    data = p.to_dict(); data['version'] = version; strip_contrast_pivot_field(data)
    record = data['clips'][0]['grade']['qualifier']
    for entry in [record, *record['inclusion_ranges'], *record['exclusion_ranges']]:
        for name in (*ASYMMETRIC_SOFTNESS_FIELDS, 'hue_enabled', 'saturation_enabled', 'luma_enabled'):
            del entry[name]
    if version < 23: del record['matte_morphology']
    if version < 22: del record['matte_smoothing']
    if version < 21:
        del record['matte_black']; del record['matte_white']
    if version < 20:
        del record['inclusion_ranges']; del record['exclusion_ranges']
        q = replace(q, inclusion_ranges=[], exclusion_ranges=[])
    if version == 18:
        for name in list(record):
            if name.startswith('window_'): del record[name]
    return data, q


@pytest.mark.parametrize('version', range(18,24))
def test_legacy_migration_has_exact_numerical_and_output_compatibility(version):
    data, expected = legacy_payload(version)
    before = deepcopy(data)
    restored = Project.from_dict(data)
    q = restored.clips[0].grade.qualifier
    assert q == expected and data == before
    assert restored.to_dict()['version'] == VERSION
    rgb = np.random.default_rng(119).random((200,3),dtype=np.float32)
    np.testing.assert_array_equal(qualifier_mask(rgb,q), qualifier_mask(rgb,expected))


@pytest.mark.parametrize('version', range(18,24))
@pytest.mark.parametrize('field', sorted(ASYMMETRIC_SOFTNESS_FIELDS))
def test_legacy_version_cannot_smuggle_even_null_new_fields(version,field):
    data, _ = legacy_payload(version)
    data['clips'][0]['grade']['qualifier'][field] = None
    with pytest.raises(ValueError, match='legacy qualifier fields'):
        Project.from_dict(data)


@pytest.mark.parametrize('version', range(20,24))
@pytest.mark.parametrize('set_name', ['inclusion_ranges','exclusion_ranges'])
def test_nested_legacy_ranges_are_strictly_guarded(version,set_name):
    data, _ = legacy_payload(version)
    data['clips'][0]['grade']['qualifier'][set_name][0]['luma_softness_low'] = None
    with pytest.raises(ValueError, match='Legacy qualifier range'):
        Project.from_dict(data)


def test_schema24_all_edge_fields_required_and_roundtrip_is_independent():
    p = project()
    p.clips[0].grade.qualifier = QualifierSettings(saturation_softness_low=0,
        luma_softness_high=.345678901234, exclusion_ranges=[QualifierRange(luma_softness_low=.8)])
    payload = p.to_dict()
    loaded = Project.from_dict(payload)
    assert loaded == p
    loaded.clips[0].grade.qualifier.luma_softness_high=.9
    assert p.clips[0].grade.qualifier.luma_softness_high==.345678901234
    for name in ASYMMETRIC_SOFTNESS_FIELDS:
        missing=deepcopy(payload); del missing['clips'][0]['grade']['qualifier'][name]
        with pytest.raises(ValueError): Project.from_dict(missing)
    payload['version']=VERSION + 1
    with pytest.raises(ValueError,match='Unsupported'): Project.from_dict(payload)


@pytest.mark.parametrize('nested', [False, True])
def test_mutated_side_override_rejected_even_disabled(nested):
    q = QualifierSettings(inclusion_ranges=[QualifierRange()])
    target = q.inclusion_ranges[0] if nested else q
    object.__setattr__(target, 'luma_softness_high', float('nan'))
    with pytest.raises(ValueError, match='luma_softness_high'): q.to_dict()
    with pytest.raises(ValueError, match='luma_softness_high'):
        qualifier_mask(np.zeros((1,3), np.float32),q)


@pytest.mark.parametrize('edge', ['low', 'high'])
def test_one_edge_edit_cannot_change_core_or_opposite_weights(edge):
    q = QualifierSettings(enabled=True, hue_width=.5, luma_low=.3, luma_high=.7,
                          luma_softness=.2)
    rgb = np.repeat(np.array([.15,.25,.4,.5,.6,.75,.85],np.float32)[:,None],3,axis=1)
    before = qualifier_mask(rgb,q)
    after = qualifier_mask(rgb,replace(q,**{'luma_softness_'+edge:.4}))
    keep = slice(2,None) if edge=='low' else slice(None,5)
    np.testing.assert_array_equal(before[keep],after[keep])
    assert np.any(before!=after)


def test_v23_reader_rejects_v24_before_decoding_payload(monkeypatch):
    import lumen.model as model
    payload = project().to_dict()
    assert payload['version'] == VERSION
    # Deliberately malformed clips prove the forward-version guard runs before
    # the older reader attempts any field decoding or migration.
    payload['clips'] = 'not a clip list'
    monkeypatch.setattr(model, 'VERSION', 23)
    with pytest.raises(ValueError, match='Unsupported project format or version'):
        model.Project.from_dict(payload)
