"""Local synthetic temporal stress tests, not evidence of flicker-free video.

Known integer motion permits exact compensation without optical-flow error.
The fixtures deliberately distinguish repeatable pixels from temporal quality.
Run this file as a script with an output path to write reproducible JSON metrics.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from lumen.beauty_mask_prototype import (
    FrameObservation, GateConfig, PreviewSequenceGate, ProtectedPolygons,
    build_face_mask, gated_mask, precompute_sequence_gate,
)
from lumen.beauty_prototype import apply_beauty_prototype

W, H, N = 112, 72, 24
KEY = 'synthetic-moving-texture:seed-311:geometry-v1'
FEATURES = {
    'eyes': (26, 23, 41, 28), 'lips': (29, 46, 43, 51),
    'brows': (24, 18, 43, 21), 'hair': (18, 10, 51, 15),
    'other': (48, 30, 53, 40),
}


def polygon(rect, dx=0.):
    x0, y0, x1, y1 = rect
    return [((x + dx) / W, y / H) for x, y in
            ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]


def observations(events=False):
    rows = [FrameObservation(i, i / 30, .99, 'face-a', True, True)
            for i in range(N)]
    if events:
        rows[7] = replace(rows[7], geometry_valid=False)  # declared occlusion
        rows[11] = replace(rows[11], detected=False)  # track loss
        rows[16] = replace(rows[16], scene_cut=True)
        rows[21:] = [replace(r, track_id='face-b') for r in rows[21:]]
    return rows


def sequence(feather=2., ramp=4, jitter=False, events=False):
    base = np.random.default_rng(311).normal(.5, .018, (H, W, 3)).astype(np.float32)
    rows = observations(events)
    plan = precompute_sequence_gate(rows, source_key=KEY,
                                    config=GateConfig(ramp_frames=ramp))
    frames, regions, masks, outputs, protections = [], [], [], [], []
    for i, row in enumerate(rows):
        frame = np.roll(base, i, axis=1)
        protection = np.zeros((H, W), dtype=bool)
        for rect in FEATURES.values():
            x0, y0, x1, y1 = rect
            protection[y0:y1, x0+i:x1+i] = True
        exclusions = ProtectedPolygons(**{
            name: [polygon(rect, i)] for name, rect in FEATURES.items()})
        offset = (1.25 if i % 2 else -1.25) if jitter else 0.
        region = build_face_mask(W, H, [polygon((14, 8, 58, 61), i + offset)],
                                 exclusions=exclusions,
                                 geometry_reviewed=row.geometry_valid,
                                 feather_px=feather)
        mask = gated_mask(region, plan.decisions[i])
        output = apply_beauty_prototype(frame, mask, protection, enabled=True,
                                        smoothing_strength=1., detail_protection=0.)
        frames.append(frame); regions.append(region); masks.append(mask)
        outputs.append(output); protections.append(protection)
    return dict(rows=rows, plan=plan, frames=frames, regions=regions,
                masks=masks, outputs=outputs, protections=protections)


def metrics(seq):
    # The fixed crop is inside every frame's common, non-wrapping support.
    crop = (slice(4, H-4), slice(4, W-N-4))
    aligned_outputs = [np.roll(o, -i, axis=1)[crop] for i, o in enumerate(seq['outputs'])]
    aligned_inputs = [np.roll(o, -i, axis=1)[crop] for i, o in enumerate(seq['frames'])]
    aligned_masks = [np.roll(m, -i, axis=1)[crop] for i, m in enumerate(seq['masks'])]
    result = []
    for i in range(1, N):
        delta = np.abs(aligned_outputs[i] - aligned_outputs[i-1])
        jump = np.abs(aligned_masks[i] - aligned_masks[i-1])
        source_delta = np.abs(aligned_inputs[i] - aligned_inputs[i-1])
        result.append(dict(frame=i, gain=seq['plan'].decisions[i].gain,
                           reason=seq['plan'].decisions[i].reason,
                           source_motion_compensated_max=float(source_delta.max()),
                           output_motion_compensated_max=float(delta.max()),
                           output_motion_compensated_mean=float(delta.mean()),
                           mask_jump_max=float(jump.max()), mask_jump_mean=float(jump.mean())))
    return result


@pytest.mark.parametrize('feather', [0., 2., 5.])
def test_known_translation_is_stable_after_ramp(feather):
    seq = sequence(feather=feather)
    steady = metrics(seq)[4:]
    assert max(m['source_motion_compensated_max'] for m in steady) == 0
    assert max(m['output_motion_compensated_max'] for m in steady) <= 1.2e-7
    assert max(m['mask_jump_max'] for m in steady) <= 1.2e-6
    assert np.max(np.abs(seq['outputs'][5] - seq['frames'][5])) > .001


@pytest.mark.parametrize('feather', [0., 2., 5.])
def test_jittered_geometry_still_causes_output_variation(feather):
    seq = sequence(feather=feather, jitter=True)
    steady = metrics(seq)[4:]
    assert max(m['source_motion_compensated_max'] for m in steady) == 0
    assert max(m['mask_jump_max'] for m in steady) > .1
    assert max(m['output_motion_compensated_max'] for m in steady) > .0005


def test_moving_protected_features_and_excluded_pixels_are_bit_exact():
    for jitter in (False, True):
        seq = sequence(jitter=jitter, events=True)
        for frame, output, region, protection in zip(seq['frames'], seq['outputs'],
                                                    seq['regions'], seq['protections']):
            # Check raw bits, including the full supplied feature rectangles.
            keep = protection | (region.mask == 0)
            np.testing.assert_array_equal(output[keep].view(np.uint32), frame[keep].view(np.uint32))
            assert np.count_nonzero(region.mask[protection]) == 0


def test_rejected_occlusion_and_track_loss_have_no_stale_effect():
    seq = sequence(events=True)
    for i, reason in ((7, 'unknown_geometry_or_occlusion'), (11, 'missing_detection')):
        assert seq['plan'].decisions[i].reason == reason
        assert not np.any(seq['masks'][i])
        assert seq['outputs'][i] is seq['frames'][i]
        # Even accidentally supplied old geometry cannot bypass a rejected gate.
        assert not np.any(gated_mask(seq['regions'][i-1], seq['plan'].decisions[i]))
    assert not seq['regions'][7].valid
    assert seq['regions'][7].reason == 'unreviewed_geometry_or_occlusion'


@pytest.mark.parametrize('ramp', [2, 4, 8])
def test_reset_is_abrupt_and_reentry_ramps_from_zero(ramp):
    seq = sequence(ramp=ramp, events=True)
    for start in (8, 12, 16, 21):
        assert seq['plan'].decisions[start].gain == 0
        assert seq['outputs'][start] is seq['frames'][start]
        assert seq['plan'].decisions[start+1].gain == pytest.approx(1/(ramp-1))
    assert seq['plan'].decisions[16].reason == 'scene_cut'
    assert seq['plan'].decisions[21].reason == 'track_changed'
    # A safe immediate rejection has a measurable temporal cost: no fade-out.
    by_frame = {m['frame']: m for m in metrics(seq)}
    assert by_frame[7]['output_motion_compensated_max'] > .001
    assert by_frame[7]['mask_jump_max'] > .1


def test_random_seek_pixel_parity_uses_canonical_gate_and_current_geometry():
    seq = sequence(jitter=True, events=True)
    streaming = PreviewSequenceGate(seq['plan'].config)
    assert [streaming.step(row) for row in seq['rows']] == list(seq['plan'].decisions)
    order = list(np.random.default_rng(19).permutation(N)) + [7, 16, 0, 23, 7]
    for index in order:
        i = int(index)
        decision = seq['plan'].decision_for(i, timestamp=i/30, source_key=KEY)
        out = apply_beauty_prototype(seq['frames'][i], gated_mask(seq['regions'][i], decision),
                                    seq['protections'][i], enabled=True,
                                    smoothing_strength=1., detail_protection=0.)
        np.testing.assert_array_equal(out.view(np.uint32), seq['outputs'][i].view(np.uint32))


@pytest.mark.parametrize('transpose', [False, True])
@pytest.mark.parametrize('dimension', [112, 16384])
def test_exclusion_corner_contact_at_both_axis_limits(transpose, dimension):
    from lumen.beauty_mask_prototype import _apply_polygon
    # Thin arrays exercise the maximum permitted coordinate without allocating
    # a maximum-area frame. Contacts are excluded for both polygon windings.
    shape = (8, dimension) if not transpose else (dimension, 8)
    for shift in (0, 1, 2, dimension-40):
        points = np.array(((24+shift, 3), (29+shift, 3),
                           (29+shift, 5), (24+shift, 5)), dtype=np.float64)
        if transpose:
            points = points[:, ::-1]
        points /= np.array(shape[::-1])
        results = []
        for vertices in (points, points[::-1], np.roll(points, 1, axis=0)):
            mask = np.ones(shape, dtype=np.float32)
            _apply_polygon(mask, vertices, 0., exclude=True)
            for x, y in ((23+shift, 2), (29+shift, 2),
                         (23+shift, 5), (29+shift, 5)):
                assert mask[x, y] == 0 if transpose else mask[y, x] == 0
            results.append(mask)
        np.testing.assert_array_equal(results[0], results[1])
        np.testing.assert_array_equal(results[0], results[2])


def report():
    cases = {}
    for feather in (0., 2., 5.):
        for jitter in (False, True):
            cases[f'feather_{feather}_jitter_{jitter}'] = metrics(sequence(feather=feather, jitter=jitter))
    for ramp in (2, 4, 8):
        cases[f'events_ramp_{ramp}'] = metrics(sequence(ramp=ramp, events=True))
    return dict(scope='LOCAL supplied-mask synthetic tests only; no production integration',
                seed=311, dimensions=[W, H], frames=N, channel_units='normalized encoded RGB',
                motion='Known integer translation, +1 x pixel/frame, non-wrapping common crop',
                limitations=['No optical flow, real footage, subpixel resampling, detector, or automatic occlusion inference',
                             'Cut marker uses unchanged translated texture to isolate the gate discontinuity; it is not a realistic scene cut',
                             'Declared invalid occlusion rejects immediately; validity is supplied, not inferred',
                             'Feathering and reacquisition ramps cannot guarantee flicker-free output',
                             'Canonical parity assumes caller supplies matching current-frame geometry; frame binding is not enforced by gated_mask',
                             'Zero-feather coverage remains discontinuous for real boundary motion; the numerical corner-contact bug is fixed by a conservative roundoff guard'],
                cases=cases)


if __name__ == '__main__':
    target = Path(sys.argv[1]); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report(), indent=2) + '\n')
    print(target)
