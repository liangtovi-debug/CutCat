"""Local supplied-geometry and canonical temporal-gate safety contract tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import threading

import numpy as np
import pytest

import lumen.beauty_mask_prototype as prototype
from lumen.beauty_mask_prototype import (
    CAPABILITIES, BeautyMaskCancelled, FrameObservation, GateConfig, GateDecision,
    PreviewSequenceGate, ProtectedPolygons, build_face_mask, gated_mask,
    precompute_sequence_gate,
)


def rectangle(x0=0., y0=0., x1=1., y1=1.):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def mask(faces=None, width=64, height=64, **kwargs):
    return build_face_mask(width, height, [rectangle()] if faces is None else faces,
                           geometry_reviewed=True, **kwargs)


def observed(index, **kwargs):
    values = dict(frame_index=index, timestamp=index / 30., confidence=.99,
                  track_id='face-a', detected=True, geometry_valid=True)
    values.update(kwargs)
    return FrameObservation(**values)


def warm(gate, count=4):
    return [gate.step(observed(index)) for index in range(count)]


def test_capabilities_do_not_claim_segmentation_or_occlusion_detection():
    assert CAPABILITIES.supplied_polygons_required
    assert CAPABILITIES.caller_review_required
    assert not CAPABILITIES.automatic_face_detection
    assert not CAPABILITIES.automatic_safe_segmentation
    assert not CAPABILITIES.detects_hand_occlusion
    assert not CAPABILITIES.detects_glasses_occlusion
    assert not CAPABILITIES.detects_hair_occlusion
    assert not CAPABILITIES.production_integration


def test_unreviewed_is_zero_by_default_even_with_plausible_faces():
    result = build_face_mask(64, 64, [rectangle()])
    assert not result.valid and not result.mask.any()
    assert result.reason == 'unreviewed_geometry_or_occlusion'
    decision = PreviewSequenceGate().step(FrameObservation(0, 0., .99, 'face-a'))
    assert not decision.accepted and decision.gain == 0.


@pytest.mark.parametrize('size', [(0, 64), (-1, 64), (64, 0), (64, -1),
                                  (1.5, 64), (True, 64), (np.nan, 64),
                                  (np.inf, 64), (16385, 1), (8192, 8192)])
def test_invalid_dimensions_rejected_before_allocation(size):
    with pytest.raises(ValueError, match='dimensions'):
        build_face_mask(*size, [rectangle()])


@pytest.mark.parametrize('dtype', [np.int16, np.uint16, np.int32, np.uint32])
def test_numpy_integer_dimensions_cannot_overflow_total_pixel_limit(dtype):
    # Validator-only: never allocate the 67-million-pixel image under test.
    with pytest.raises(ValueError, match='dimensions'):
        prototype._validate_size(dtype(8192), dtype(8192))
    prototype._validate_size(dtype(64), dtype(64))


@pytest.mark.parametrize('value', [-1., np.nan, np.inf, -np.inf, True, '3', 16385])
def test_invalid_feather_rejected(value):
    with pytest.raises(ValueError, match='Feather'):
        mask(feather_px=value)


def test_mask_bounds_dtype_and_conservative_frame_edge():
    result = mask(width=101, height=143)
    assert result.valid and result.mask.dtype == np.float32
    assert result.mask.shape == (143, 101)
    assert np.isfinite(result.mask).all()
    assert result.mask.min() == 0. and result.mask.max() == 1.
    assert not result.mask[0].any() and not result.mask[-1].any()
    assert not result.mask[:, 0].any() and not result.mask[:, -1].any()
    assert 0. < result.mask[1, 50] < result.mask[2, 50] < 1.
    assert not result.mask.flags.writeable


def test_feather_is_inward_only_and_does_not_expand_hard_mask():
    faces = [rectangle(.2, .2, .8, .8)]
    hard, soft = mask(faces, feather_px=0.), mask(faces, feather_px=7.)
    assert np.all(soft.mask <= hard.mask)
    assert not soft.mask[:13].any() and not soft.mask[:, :13].any()
    assert not soft.mask[51:].any() and not soft.mask[:, 51:].any()
    assert soft.mask[32, 32] == 1.


def test_winding_closure_and_input_arrays_do_not_change_result():
    polygon = np.asarray(rectangle(.1, .15, .9, .85))
    original = polygon.copy()
    forward = mask([polygon]).mask
    np.testing.assert_array_equal(forward, mask([polygon[::-1]]).mask)
    np.testing.assert_array_equal(forward, mask([np.vstack((polygon, polygon[:1]))]).mask)
    np.testing.assert_array_equal(polygon, original)


def test_concave_polygon_does_not_fill_its_cutout():
    polygon = [(0., 0.), (1., 0.), (1., .4), (.4, .4), (.4, 1.), (0., 1.)]
    result = mask([polygon], feather_px=1.)
    assert result.valid and result.mask[10, 40] == 1.
    assert result.mask[40, 10] == 1.
    assert not result.mask[26:, 26:].any()


BAD_POLYGONS = [
    [(-.01, .2), (.8, .2), (.5, .8)],
    [(.2, .2), (1.01, .2), (.5, .8)],
    [(np.nan, .2), (.8, .2), (.5, .8)],
    [(.2, np.inf), (.8, .2), (.5, .8)],
    [(.2, .2), (.8, .2)],
    [(.2, .2), (.5, .5), (.8, .8)],
    [(.2, .2), (.8, .8), (.2, .8), (.8, .2)],
    [(.1, .1), (.9, .8), (.1, .8), (.8, .1), (.5, .9)],
    [(.2, .2), (.8, .2), (.8, .2), (.2, .8)],
    [[.1, .1, 0.], [.9, .1, 0.], [.5, .9, 0.]],
    [[True, False], [False, True], [True, True]],
    [['.1', '.1'], ['.9', '.1'], ['.5', '.9']],
    [[.1 + .1j, .1], [.9, .1], [.5, .9]],
    [], None,
]


@pytest.mark.parametrize('polygon', BAD_POLYGONS)
def test_bad_face_or_exclusion_fails_entire_frame_closed(polygon):
    for result in (mask([rectangle(), polygon]),
                   mask(exclusions=ProtectedPolygons(eyes=[polygon]))):
        assert not result.valid and not result.mask.any()
        assert result.reason == 'invalid_polygon'


@pytest.mark.parametrize('faces', [[], None, np.array(1.), [rectangle()] * 65])
def test_missing_or_unbounded_polygon_collection_is_zero(faces):
    result = build_face_mask(32, 32, faces, geometry_reviewed=True)
    assert not result.valid and not result.mask.any()


@pytest.mark.parametrize('size', [(1, 1), (1, 64), (64, 1)])
def test_tiny_raster_has_no_safe_pixel_footprint(size):
    result = mask(width=size[0], height=size[1])
    assert not result.valid and not result.mask.any()


def test_tiny_region_and_fully_protected_face_are_zero():
    assert not mask([rectangle(.49, .49, .51, .51)]).mask.any()
    protected = mask(exclusions=ProtectedPolygons(hair=[rectangle()]))
    assert not protected.valid and not protected.mask.any()
    assert protected.reason == 'no_usable_interior'


@pytest.mark.parametrize('kind', ['eyes', 'lips', 'brows', 'hair', 'other'])
def test_each_exclusion_kind_is_protected_across_overlapping_faces(kind):
    faces = [rectangle(.05, .05, .8, .95), rectangle(.2, .05, .95, .95)]
    exclusions = ProtectedPolygons(**{kind: [rectangle(.4, .4, .6, .6)]})
    result = mask(faces, exclusions=exclusions, feather_px=2.)
    assert result.valid
    assert not result.mask[25:39, 25:39].any()
    assert result.mask[15, 32] == 1.
    assert result.mask[32, 15] == 1.
    assert 0. < result.mask[23, 32] < 1.


def test_exclusion_feather_only_removes_more_and_expands_no_face_coverage():
    exclusions = ProtectedPolygons(eyes=[rectangle(.4, .4, .6, .6)])
    hard = mask(exclusions=exclusions, feather_px=0.).mask
    soft = mask(exclusions=exclusions, feather_px=4.).mask
    assert np.all(soft <= hard)
    assert not soft[25:39, 25:39].any()
    assert 0. < soft[22, 32] < hard[22, 32]


def test_subpixel_exclusion_protects_whole_overlapping_pixel():
    # Pixel (32,32) has center 32.5. This exclusion misses the center but
    # touches the square pixel footprint, so the diagonal guard must protect it.
    tiny = rectangle(32.9 / 64, 32.9 / 64, 33.1 / 64, 33.1 / 64)
    result = mask(exclusions=ProtectedPolygons(other=[tiny]), feather_px=0.)
    assert result.mask[32, 32] == 0.
    assert result.mask[31, 31] == 1.


def test_all_exclusions_union_is_order_independent_and_never_adds_coverage():
    one, two = rectangle(.2, .2, .4, .4), rectangle(.6, .6, .8, .8)
    baseline = mask().mask
    a = mask(exclusions=ProtectedPolygons(eyes=[one], lips=[two])).mask
    b = mask(exclusions=ProtectedPolygons(eyes=[two], lips=[one])).mask
    np.testing.assert_array_equal(a, b)
    assert np.all(a <= baseline)


@pytest.mark.parametrize('kwargs', [
    {'minimum_confidence': -.1}, {'minimum_confidence': 1.1},
    {'minimum_confidence': np.nan}, {'minimum_confidence': True},
    {'ramp_frames': 1}, {'ramp_frames': -1}, {'ramp_frames': 4.5},
    {'ramp_frames': True}, {'ramp_frames': 10001},
    {'max_timestamp_gap': 0.}, {'max_timestamp_gap': -1.},
    {'max_timestamp_gap': np.nan}, {'max_timestamp_gap': np.inf},
])
def test_gate_config_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        GateConfig(**kwargs)


def test_valid_sequence_ramps_from_zero_and_saturates():
    decisions = warm(PreviewSequenceGate(), 7)
    assert [d.gain for d in decisions] == [0., 1 / 3, 2 / 3, 1., 1., 1., 1.]
    assert all(d.accepted for d in decisions)
    assert decisions[-1].reason == 'ready'


@pytest.mark.parametrize('changes', [
    {'detected': False}, {'geometry_valid': False}, {'confidence': None},
    {'confidence': .89}, {'confidence': -.1}, {'confidence': 1.1},
    {'confidence': np.nan}, {'confidence': np.inf}, {'confidence': True},
    {'track_id': None}, {'track_id': ''}, {'track_id': '   '}, {'track_id': 42},
])
def test_any_current_rejection_is_immediate_zero_then_reacquisition(changes):
    gate = PreviewSequenceGate()
    assert warm(gate)[-1].gain == 1.
    rejected = gate.step(observed(4, **changes))
    assert not rejected.accepted and rejected.gain == 0.
    assert not gated_mask(mask(), rejected).any()
    next_decisions = [gate.step(observed(i)) for i in range(5, 9)]
    assert [d.gain for d in next_decisions] == [0., 1 / 3, 2 / 3, 1.]


def test_repeated_missing_detections_never_fade_or_extrapolate():
    gate = PreviewSequenceGate()
    warm(gate)
    for i in range(4, 40):
        decision = gate.step(observed(i, detected=False))
        assert decision.gain == 0. and not decision.accepted
    assert gate.step(observed(40)).gain == 0.


def test_current_low_confidence_is_not_smoothed_from_history():
    gate = PreviewSequenceGate()
    warm(gate)
    assert gate.step(observed(4, confidence=.899999)).gain == 0.
    assert gate.step(observed(5, confidence=.9)).gain == 0.
    assert gate.step(observed(6, confidence=.9)).gain == 1 / 3


def test_cut_resets_valid_mask_even_when_track_identity_is_unchanged():
    gate = PreviewSequenceGate()
    warm(gate)
    cut = gate.step(observed(4, scene_cut=True))
    assert cut.accepted and cut.gain == 0. and cut.reason == 'scene_cut'
    assert gate.step(observed(5)).gain == 1 / 3


def test_track_change_and_return_cannot_reuse_old_ramp():
    gate = PreviewSequenceGate()
    warm(gate)
    changed = gate.step(observed(4, track_id='face-b'))
    assert changed.gain == 0. and changed.reason == 'track_changed'
    assert gate.step(observed(5, track_id='face-b')).gain == 1 / 3
    assert gate.step(observed(6, track_id='face-a')).gain == 0.


@pytest.mark.parametrize('changes', [
    {'timestamp': .1}, {'timestamp': .09}, {'timestamp': 2.},
    {'frame_index': 3}, {'frame_index': 1}, {'frame_index': 5},
])
def test_timestamp_or_frame_discontinuity_rejects_and_resets(changes):
    gate = PreviewSequenceGate()
    warm(gate)
    row = observed(4, **changes)
    decision = gate.step(row)
    assert not decision.accepted and decision.gain == 0.
    assert decision.reason == 'timestamp_or_frame_discontinuity'
    next_row = observed(row.frame_index + 1, timestamp=row.timestamp + 1 / 30)
    assert gate.step(next_row).gain == 0.


@pytest.mark.parametrize('changes', [
    {'timestamp': -.1}, {'timestamp': np.nan}, {'timestamp': np.inf},
    {'timestamp': '0.1'}, {'timestamp': True}, {'frame_index': -1},
    {'frame_index': 1.5}, {'frame_index': True}, {'scene_cut': None},
])
def test_invalid_frame_metadata_cannot_enter_sequence(changes):
    gate = PreviewSequenceGate()
    warm(gate)
    decision = gate.step(observed(4, **changes))
    assert not decision.accepted and decision.gain == 0.
    assert decision.reason == 'invalid_frame_metadata'
    assert gate.step(observed(5)).gain == 0.


def test_explicit_preview_reset_and_invalid_observation_clear_history():
    gate = PreviewSequenceGate()
    warm(gate)
    gate.reset()
    assert gate.step(observed(20)).gain == 0.
    rejected = gate.step(None)
    assert not rejected.accepted and rejected.gain == 0.
    assert gate.step(observed(0)).gain == 0.


def test_canonical_random_seek_is_exactly_equal_to_chronological_sequence():
    rows = [observed(i) for i in range(32)]
    rows[5] = replace(rows[5], detected=False)
    rows[10] = replace(rows[10], confidence=.2)
    rows[15] = replace(rows[15], scene_cut=True)
    for i in range(20, 32):
        rows[i] = replace(rows[i], track_id='face-b')
    preview = PreviewSequenceGate()
    chronological = tuple(preview.step(row) for row in rows)
    plan = precompute_sequence_gate(rows, source_key='source-fingerprint:analysis-v1')
    assert plan.uses_canonical_sequence
    assert plan.decisions == chronological
    for index in np.random.default_rng(42).integers(0, len(rows), size=200):
        index = int(index)
        assert plan.decision_for(index, timestamp=rows[index].timestamp,
                                 source_key=plan.source_key) == chronological[index]
    assert plan.decisions == chronological
    # A fresh/stateful seek starts at zero, proving it is NOT a canonical substitute.
    assert PreviewSequenceGate().step(rows[4]).gain == 0.
    assert plan.decision_for(4, timestamp=rows[4].timestamp,
                             source_key=plan.source_key).gain == 1.


def test_canonical_snapshot_not_changed_by_replacing_input_sequence():
    rows = [observed(i) for i in range(5)]
    plan = precompute_sequence_gate(rows, source_key='source:1')
    rows[4] = replace(rows[4], detected=False)
    assert plan.observations[4].detected
    assert plan.decisions[4].gain == 1.
    with pytest.raises(FrozenInstanceError):
        plan.observations[4].detected = False


@pytest.mark.parametrize('changes', [
    {'frame_index': -1}, {'frame_index': 9}, {'frame_index': True},
    {'timestamp': np.nan}, {'timestamp': .1000000001},
    {'source_key': 'different-source:1'}, {'source_key': 'source:2'},
])
def test_wrong_seek_identity_timestamp_or_range_is_immediate_zero(changes):
    plan = precompute_sequence_gate([observed(i) for i in range(5)], source_key='source:1')
    kwargs = dict(frame_index=3, timestamp=.1, source_key='source:1')
    kwargs.update(changes)
    decision = plan.decision_for(**kwargs)
    assert not decision.accepted and decision.gain == 0.


@pytest.mark.parametrize('rows', [
    [observed(1)], [observed(0), observed(2)], [observed(1), observed(0)],
    [observed(0), observed(0)], [observed(0), observed(1, timestamp=0.)],
    [observed(0), observed(1, timestamp=np.nan)], [observed(0, timestamp=-1.)],
    [observed(True)], [None],
])
def test_canonical_metadata_must_cover_ordered_source_frame_grid(rows):
    with pytest.raises(ValueError, match='Canonical metadata'):
        precompute_sequence_gate(rows, source_key='source:1')


def test_long_timestamp_gap_is_recorded_as_zero_in_canonical_plan():
    rows = [observed(i) for i in range(4)]
    rows += [observed(i, timestamp=2. + (i - 4) / 30.) for i in range(4, 8)]
    plan = precompute_sequence_gate(rows, source_key='source:1')
    assert [d.gain for d in plan.decisions] == [0., 1 / 3, 2 / 3, 1., 0., 0., 1 / 3, 2 / 3]


@pytest.mark.parametrize('source_key', ['', '  ', None, 3])
def test_canonical_plan_requires_explicit_source_analysis_identity(source_key):
    with pytest.raises(ValueError, match='source and analysis key'):
        precompute_sequence_gate([observed(0)], source_key=source_key)


def test_gated_mask_cannot_mutate_source_mask_or_reuse_history():
    result = mask()
    before = result.mask.copy()
    scaled = gated_mask(result, GateDecision(.25, True, 'acquiring'))
    np.testing.assert_array_equal(scaled, before * np.float32(.25))
    assert scaled.dtype == np.float32
    np.testing.assert_array_equal(result.mask, before)
    for decision in (GateDecision(1., False, 'rejected'), GateDecision(0., True, 'acquiring'),
                     GateDecision(-1., True, 'invalid'), GateDecision(np.nan, True, 'invalid'),
                     GateDecision(np.inf, True, 'invalid'), GateDecision(2., True, 'invalid')):
        assert not gated_mask(result, decision).any()
    unreviewed = build_face_mask(64, 64, [rectangle()])
    assert not gated_mask(unreviewed, GateDecision(1., True, 'ready')).any()


@pytest.mark.parametrize('field_name', ['timestamp', 'confidence'])
def test_huge_numeric_metadata_is_rejected_and_warm_state_cannot_survive(field_name):
    gate = PreviewSequenceGate()
    assert warm(gate)[-1].gain == 1.
    rejected = gate.step(observed(4, **{field_name: 10 ** 1000}))
    assert not rejected.accepted and rejected.gain == 0.
    assert gate.step(observed(5)).gain == 0.
    assert gate.step(observed(6)).gain == 1 / 3


def test_huge_numeric_configuration_and_lookup_fail_closed_without_overflow():
    huge = 10 ** 1000
    with pytest.raises(ValueError, match='Feather'):
        mask(feather_px=huge)
    for changes in ({'minimum_confidence': huge}, {'max_timestamp_gap': huge}):
        with pytest.raises(ValueError):
            GateConfig(**changes)
    plan = precompute_sequence_gate([observed(i) for i in range(4)], source_key='source:1')
    decision = plan.decision_for(3, timestamp=huge, source_key='source:1')
    assert not decision.accepted and decision.gain == 0.
    assert not gated_mask(mask(), GateDecision(huge, True, 'invalid')).any()


def test_event_cancellation_prevents_mask_and_plan_construction():
    event = threading.Event()
    event.set()
    with pytest.raises(BeautyMaskCancelled):
        mask(cancel=event)
    with pytest.raises(BeautyMaskCancelled):
        precompute_sequence_gate([observed(0)], source_key='source:1', cancel=event)
    event.clear()
    assert mask(cancel=event).valid


@pytest.mark.parametrize('stop_after', [3, 8, 14], ids=['validation', 'tile', 'later-tile'])
def test_mask_callback_cancel_never_publishes_partial_output(monkeypatch, stop_after):
    published = []
    original_type = prototype.PolygonMaskResult
    def publish(*args, **kwargs):
        published.append(True)
        return original_type(*args, **kwargs)
    monkeypatch.setattr(prototype, 'PolygonMaskResult', publish)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls > stop_after
    points = np.asarray(rectangle())
    before = points.copy()
    with pytest.raises(BeautyMaskCancelled):
        mask([points], height=192, cancel=cancel)
    assert not published and calls == stop_after + 1
    np.testing.assert_array_equal(points, before)


@pytest.mark.parametrize('stop_after', [3, 15], ids=['validation', 'evaluation'])
def test_sequence_callback_cancel_never_publishes_partial_plan(monkeypatch, stop_after):
    published = []
    original_type = prototype.CanonicalGatePlan
    def publish(*args, **kwargs):
        published.append(True)
        return original_type(*args, **kwargs)
    monkeypatch.setattr(prototype, 'CanonicalGatePlan', publish)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls > stop_after
    with pytest.raises(BeautyMaskCancelled):
        precompute_sequence_gate([observed(i) for i in range(10)],
                                 source_key='source:1', cancel=cancel)
    assert not published and calls == stop_after + 1


def test_callback_exceptions_are_not_misreported_as_invalid_geometry():
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise ValueError('caller cancelled validation')
        return False
    with pytest.raises(ValueError, match='caller cancelled validation'):
        mask(cancel=cancel)


def test_cancellation_requires_event_or_callable():
    with pytest.raises(ValueError, match='cancellation'):
        mask(cancel=True)
    with pytest.raises(ValueError, match='cancellation'):
        precompute_sequence_gate([], source_key='source:1', cancel=True)


def test_canonical_input_is_bounded_before_snapshot_allocation():
    class TooManyRows(list):
        def __len__(self):
            return prototype.MAX_SEQUENCE_FRAMES + 1
    for rows in (TooManyRows(), (observed(i) for i in range(5))):
        with pytest.raises(ValueError, match='bounded list or tuple'):
            precompute_sequence_gate(rows, source_key='source:1')


def test_geometry_vertex_and_combined_exclusion_limits_are_bounded():
    angles = np.arange(257) * 2 * np.pi / 257
    polygon = np.stack((.5 + .4 * np.cos(angles), .5 + .4 * np.sin(angles)), axis=1)
    result = mask([polygon])
    assert not result.valid and not result.mask.any()
    protected = ProtectedPolygons(eyes=[rectangle()] * 33, lips=[rectangle()] * 32)
    result = mask(exclusions=protected)
    assert not result.valid and not result.mask.any()


def test_oversized_arrays_and_sequences_are_rejected_before_array_conversion(monkeypatch):
    oversized = np.broadcast_to(np.array([[.1, .2]], np.float32), (257, 2))
    too_wide = np.broadcast_to(np.array([[.1]], np.float32), (3, 257))
    oversized_list = [(0., 0.)] * 257
    nested = [(oversized, oversized)] * 3
    forbidden = (oversized, too_wide, oversized_list, nested)
    original = prototype.np.asarray
    conversions = []
    def guarded(value, *args, **kwargs):
        if any(value is candidate for candidate in forbidden):
            conversions.append(value)
            raise AssertionError('Unbounded polygon reached array conversion')
        return original(value, *args, **kwargs)
    monkeypatch.setattr(prototype.np, 'asarray', guarded)
    for polygon in forbidden:
        result = mask([polygon])
        assert not result.valid and not result.mask.any()
    assert not conversions
