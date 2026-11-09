"""Bounded, independent union/subtraction keys and strict portable records."""
from native_path_fixture import bind_missing_project_paths
from contrast_legacy_fixture import strip_contrast_pivot_field
from qualifier_legacy_fixture import strip_asymmetric_fields, inherited_asymmetric_fields
from copy import deepcopy
from dataclasses import asdict, fields, replace, FrozenInstanceError
import colorsys
from uuid import uuid4

import numpy as np
import pytest

from lumen.qualifier import (QualifierRange, QualifierSettings, qualifier_mask,
                             apply_qualifier, MAX_QUALIFIER_RANGES)
from lumen.qualifier_picker import (add_qualifier_range, replace_qualifier_range,
                                    clear_qualifier_ranges)
from lumen.model import VERSION, Project, Editor
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.project_recovery import RecoveryStore
from test_qualifier_integration import project
from test_grade_transfer import project_pair


def key(**kwargs):
    return QualifierRange(hue_center=kwargs.pop('hue_center', 0),
                          hue_width=kwargs.pop('hue_width', .02),
                          hue_softness=kwargs.pop('hue_softness', 0), **kwargs)


def settings(**kwargs):
    return QualifierSettings(enabled=True, hue_width=.02, hue_softness=0,
                             inclusion_ranges=[key(hue_center=2 / 3)], **kwargs)


def hsv(hues):
    return np.array([colorsys.hsv_to_rgb(h, 1, 1) for h in hues], np.float32)


def bits(a, b):
    np.testing.assert_array_equal(a.view(np.uint32), b.view(np.uint32))


def test_disjoint_red_blue_never_selects_intervening_colors_or_gray():
    rgb = np.concatenate([hsv([0, 1 / 6, 1 / 3, .5, 2 / 3, 5 / 6]),
                          np.full((1, 3), .5, np.float32)])
    np.testing.assert_array_equal(qualifier_mask(rgb, settings()), [1, 0, 0, 0, 1, 0, 0])
    q = settings(exclusion_ranges=[key()])
    np.testing.assert_array_equal(qualifier_mask(rgb, q), [0, 0, 0, 0, 1, 0, 0])
    np.testing.assert_array_equal(qualifier_mask(rgb, clear_qualifier_ranges(q)), [1, 0, 0, 0, 0, 0, 0])


def test_overlapping_feathers_use_max_not_sum_and_exclusions_multiply():
    q = QualifierSettings(enabled=True, hue_width=.5, luma_low=.4, luma_high=.6,
                         luma_softness=.2, inclusion_ranges=[
                             key(hue_width=.5, luma_low=.4, luma_high=.6, luma_softness=.2)],
                         exclusion_ranges=[key(hue_width=.5, luma_low=.4, luma_high=.6,
                                               luma_softness=.2)] * 2)
    rgb = np.array([[.3] * 3, [.5] * 3, [.7] * 3], np.float32)
    np.testing.assert_allclose(qualifier_mask(rgb, q), [.25, 0, .25], atol=1e-7)
    # Multiplication, not clamped subtraction: .5 * (1 - .5) = .25.


def test_hue_wrap_and_each_range_gray_policy():
    q = settings(exclusion_ranges=[key(hue_center=.99, hue_width=.03)])
    rgb = hsv([.99, .01, .66])
    np.testing.assert_array_equal(qualifier_mask(rgb, q), [0, 0, 1])
    q.inclusion_ranges.append(key(hue_width=.5, saturation_high=.01))
    assert qualifier_mask(np.array([.5] * 3, np.float32), q) == 1
    q.exclusion_ranges.append(key(hue_width=.5, saturation_high=.01))
    assert qualifier_mask(np.array([.5] * 3, np.float32), q) == 0


def test_order_duplicate_invariance_and_exclusion_removal_restores_key():
    rgb = np.random.default_rng(77).random((409, 3), dtype=np.float32)
    q = settings(exclusion_ranges=[key(hue_center=.1), key(hue_center=.66)])
    q.inclusion_ranges += [key(hue_center=.1), key(hue_center=.9)]
    baseline = qualifier_mask(rgb, q)
    reorder = replace(q, inclusion_ranges=q.inclusion_ranges[::-1], exclusion_ranges=q.exclusion_ranges[::-1])
    bits(qualifier_mask(rgb, reorder), baseline)
    duplicate = replace(q, inclusion_ranges=q.inclusion_ranges * 2, exclusion_ranges=q.exclusion_ranges * 2)
    bits(qualifier_mask(rgb, duplicate), baseline)
    bare = replace(q, exclusion_ranges=[])
    q.exclusion_ranges.clear()
    bits(qualifier_mask(rgb, q), qualifier_mask(rgb, bare))


def test_default_empty_sets_match_single_range_bit_for_bit_and_extended_bypasses():
    rgb = np.random.default_rng(5).random((2000, 3), dtype=np.float32)
    q = QualifierSettings(enabled=True, exposure=.5, hue_center=.2)
    original = qualifier_mask(rgb, q)
    duplicate = key(**{f.name: getattr(q, f.name) for f in fields(QualifierRange)})
    q.inclusion_ranges.append(duplicate)
    bits(qualifier_mask(rgb, q), original)
    limits = np.array([[-0., -.01, .2], [np.finfo(np.float32).max, 0, 0],
                       [0, -np.finfo(np.float32).max, 0], [0, 0, 1.00001]], np.float32)
    q.inclusion_ranges.append(key(hue_width=.5))
    with np.errstate(all='raise'):
        assert not qualifier_mask(limits, q).any()
        bits(apply_qualifier(limits, q), limits)


def test_sampling_replace_add_subtract_limits_and_independence():
    q = settings(exposure=.7, window_shape='ellipse')
    rgb = hsv([1 / 3])[0]
    added, sample = add_qualifier_range(q, rgb)
    assert len(added.inclusion_ranges) == 2 and len(q.inclusion_ranges) == 1
    assert added.hue_center == q.hue_center and sample.hue == pytest.approx(1 / 3)
    subtracted, _ = add_qualifier_range(added, rgb, exclude=True)
    assert len(subtracted.exclusion_ranges) == 1 and not added.exclusion_ranges
    replaced, _ = replace_qualifier_range(subtracted, rgb)
    assert not replaced.inclusion_ranges and not replaced.exclusion_ranges
    assert replaced.exposure == .7 and replaced.window_shape == 'ellipse' and replaced.enabled
    assert replaced.hue_softness == q.hue_softness
    full = replace(q, inclusion_ranges=[key()] * MAX_QUALIFIER_RANGES)
    with pytest.raises(ValueError, match='full'):
        add_qualifier_range(full, rgb)
    assert add_qualifier_range(full, np.zeros(3, np.float32)) is None
    assert add_qualifier_range(full, rgb, valid=False) is None
    with pytest.raises(ValueError):
        add_qualifier_range(q, rgb, exclude=1)
    with pytest.raises(FrozenInstanceError):
        q.inclusion_ranges[0].hue_center = .5
    copy = replace(q)
    copy.inclusion_ranges.clear()
    assert len(q.inclusion_ranges) == 1


@pytest.mark.parametrize('bad', [None, (), {}, True, [key()] * 9, [{}], [1]])
@pytest.mark.parametrize('name', ['inclusion_ranges', 'exclusion_ranges'])
def test_strict_typed_collection_validation(bad, name):
    with pytest.raises(ValueError):
        QualifierSettings(**{name: bad})


@pytest.mark.parametrize('name', [f.name for f in fields(QualifierRange) if not f.name.endswith('_enabled')])
def test_record_serialization_requires_all_fields(name):
    data = settings().to_dict()
    del data['inclusion_ranges'][0][name]
    with pytest.raises(ValueError):
        QualifierSettings.from_dict(data)


def test_unknown_nested_fields_and_mutated_invalid_records_are_rejected_even_disabled():
    data = settings().to_dict()
    data['inclusion_ranges'][0]['exposure'] = .5
    with pytest.raises(ValueError):
        QualifierSettings.from_dict(data)
    q = QualifierSettings(inclusion_ranges=[key()])
    object.__setattr__(q.inclusion_ranges[0], 'hue_width', float('nan'))
    with pytest.raises(ValueError):
        q.to_dict()
    with pytest.raises(ValueError):
        qualifier_mask(hsv([0]), q)


@pytest.mark.parametrize('version', [18, 19])
def test_legacy_schema_migration_empty_sets_and_strict_forward_guard(version):
    p = project().to_dict()
    p['version'] = version; strip_contrast_pivot_field(p)
    record = p['clips'][0]['grade']['qualifier']
    strip_asymmetric_fields(record)
    record.pop('inclusion_ranges'); record.pop('exclusion_ranges')
    record.pop('matte_black'); record.pop('matte_white'); record.pop('matte_smoothing'); record.pop('matte_morphology')
    if version == 18:
        for name in list(record):
            if name.startswith('window_'):
                del record[name]
    before = deepcopy(p)
    restored = Project.from_dict(p)
    assert p == before
    assert restored.clips[0].grade.qualifier == QualifierSettings()
    assert restored.to_dict()['version'] == VERSION
    for name in ('inclusion_ranges', 'exclusion_ranges'):
        forged = deepcopy(p)
        forged['clips'][0]['grade']['qualifier'][name] = []
        with pytest.raises(ValueError):
            Project.from_dict(forged)


def test_schema20_exact_roundtrip_history_and_recovery(tmp_path):
    p = project(); bind_missing_project_paths(p, tmp_path)
    q = settings(exclusion_ranges=[key(hue_center=.99, hue_softness=.123456789)])
    editor = Editor(p)
    before = deepcopy(p)
    grade = asdict(p.clips[0].grade)
    grade['qualifier'] = q.to_dict()
    editor.update_clip('picture', grade=grade)
    after = deepcopy(editor.project)
    path = tmp_path / 'sets.lumen.json'
    after.save(path)
    assert Project.load(path) == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(after, uuid4())
        assert store.load(entry) == after
    invalid = asdict(after.clips[0].grade)
    invalid['qualifier']['exclusion_ranges'][0]['hue_width'] = 1
    revision = editor.revision
    with pytest.raises(ValueError):
        editor.update_clip('picture', grade=invalid)
    assert editor.project == after and editor.revision == revision


def test_copy_paste_sets_preserve_input_encoding_and_independent_containers(tmp_path):
    p, _ = project_pair(tmp_path)
    q = settings(exclusion_ranges=[key(hue_center=.1)])
    p.clips[0].grade.qualifier = q
    p.clips[1].grade.input_encoding = 'srgb'
    transfer = copy_grade(p, 'source')
    editor = Editor(p)
    assert paste_grade(editor, 'target', transfer)
    target = editor.project.clips[1].grade
    assert target.input_encoding == 'srgb' and target.qualifier == q
    target.qualifier.inclusion_ranges.clear()
    assert q.inclusion_ranges and transfer.grade_for_input('srgb').qualifier == q


@pytest.mark.parametrize('name', [f.name for f in fields(QualifierRange) if not f.name.endswith('_enabled')])
@pytest.mark.parametrize('bad', [True, None, '0', float('nan'), float('inf'), -1, 2])
def test_each_range_numeric_field_validates_at_constructor_and_load(name, bad):
    if bad is None and name.endswith(("_softness_low", "_softness_high")):
        entry = QualifierRange(**{name: bad})
        assert QualifierRange.from_dict(entry.to_dict()) == entry
        return
    with pytest.raises(ValueError):
        QualifierRange(**{name: bad})
    data = settings().to_dict()
    data['inclusion_ranges'][0][name] = bad
    with pytest.raises(ValueError):
        QualifierSettings.from_dict(data)


@pytest.mark.parametrize('name', ['inclusion_ranges', 'exclusion_ranges'])
def test_serialized_capacity_and_container_independence(name):
    q = settings()
    data = q.to_dict()
    data[name] = [key().to_dict()] * 8
    restored = QualifierSettings.from_dict(data)
    assert len(getattr(restored, name)) == 8
    data[name][0]['hue_center'] = .8
    assert getattr(restored, name)[0].hue_center == 0
    data[name].append(key().to_dict())
    with pytest.raises(ValueError):
        QualifierSettings.from_dict(data)


def test_maximum_sets_strided_blocks_match_contiguous_and_disabled_identity():
    rgb = np.random.default_rng(7).random((300, 500, 3), dtype=np.float32)[:, ::-1]
    q = settings(exposure=.3, exclusion_ranges=[key(hue_center=i / 8) for i in range(8)])
    q.inclusion_ranges = [key(hue_center=i / 8, hue_softness=.08) for i in range(8)]
    bits(qualifier_mask(rgb, q), qualifier_mask(np.ascontiguousarray(rgb), q))
    bits(apply_qualifier(rgb, q), apply_qualifier(np.ascontiguousarray(rgb), q))
    q.enabled = False
    assert not qualifier_mask(rgb, q).any()
    bits(apply_qualifier(rgb, q), rgb)
