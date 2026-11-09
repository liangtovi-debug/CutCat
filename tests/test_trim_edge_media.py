"""Real-media acceptance for non-ripple trims on either timeline edge.

Numbered RGB48 video and independently selected deterministic/impulse PCM are
output oracles. The expected source interval is computed from the requested
edge delta, never from the edited model or production timing compiler. Nothing
is downloaded, and no user footage, native build or speaker playback is used.
"""
from copy import deepcopy
from fractions import Fraction
import math
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.timing import ClipTimeMap
from test_retime_video import (
    WIDTH, HEIGHT, PROJECT_RATES, assert_delivery_frames, decode, engine,
    expected_indices, frame_sources,
)
from test_roll_media import exact_pcm, independently_rounded_samples, pcm_decode

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')

SPEEDS = (Fraction(1, 4), Fraction(1), Fraction(3, 2), Fraction(4))
START = 9
ORIGIN = Fraction(65, 4)
LOCAL_SPAN = Fraction(35, 4)  # Nine pictures, but only 8.75 frames of source time.
DURATION = math.ceil(LOCAL_SPAN)


def _clip(cid, media_id, track, start, origin, endpoint, speed=Fraction(1)):
    return Clip(cid, media_id, track, start, 0,
                math.ceil((endpoint - origin) / speed),
                time_map=ClipTimeMap(origin, endpoint, speed))


def _edited_edges(edge, delta, speed):
    """Independent definition of the edit, in rational project-frame units."""
    endpoint = ORIGIN + LOCAL_SPAN * speed
    if edge == 'left':
        return START + delta, START + DURATION, ORIGIN + delta * speed, endpoint
    return START, START + DURATION + delta, ORIGIN, endpoint + delta * speed


def _picture_project(source, fps, speed):
    selected = _clip('selected', source.media.id, 0, START, ORIGIN,
                     ORIGIN + LOCAL_SPAN * speed, speed)
    # Real before/after neighbours and another track must remain anchored.
    anchors = [Clip('before', source.media.id, 0, 1, 60, 2),
               Clip('after', source.media.id, 0, START + DURATION + 6, 64, 2),
               Clip('other-track', source.media.id, 1, 4, 68, 2)]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[deepcopy(source.media)],
                      clips=[selected, *anchors])
    project.validate()
    return project


def _picture_oracle(source, project_end, edge, delta, speed):
    start, end, origin, endpoint = _edited_edges(edge, delta, speed)
    selected = source.original[expected_indices(origin, endpoint, speed)]
    assert len(selected) == end - start
    timeline = np.zeros((project_end, HEIGHT, WIDTH, 3), dtype=np.uint16)
    timeline[1:3] = source.original[60:62]
    timeline[4:6] = source.original[68:70]
    timeline[START + DURATION + 6:START + DURATION + 8] = source.original[64:66]
    timeline[start:end] = selected
    return selected, timeline


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', SPEEDS, ids=['quarter', 'one', 'one-and-half', 'four'])
@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('delta', [-2, 2], ids=['backward', 'forward'])
def test_trim_edge_real_pictures_delivery_cache_and_history(
        tmp_path, frame_sources, engine, fps, speed, edge, delta):
    source = frame_sources[fps]
    editor = Editor(_picture_project(source, fps, speed))
    before = deepcopy(editor.project)
    old = before.clips[0]
    old_segment = engine._graded_segment(source.media, old, before, None)
    old_preview = engine.preview(before)
    anchor_paths = [engine._graded_segment(source.media, c, before, None)
                    for c in before.clips[1:]]
    selected_expected, timeline_expected = _picture_oracle(
        source, before.duration_frames, edge, delta, speed)
    start, end, origin, endpoint = _edited_edges(edge, delta, speed)

    editor.trim_edge('selected', edge, delta)
    changed = editor.project.clips[0]
    assert (changed.start, changed.end, changed.source_in, changed.source_out,
            changed.speed) == (start, end, origin, endpoint, speed)
    # Preserve the fractional final interval, not just ceil(duration).
    assert changed.duration * speed - (changed.source_out - changed.source_in) == speed / 4
    assert editor.project.clips[1:] == before.clips[1:]
    assert editor.project.media == before.media
    assert editor.project.duration_frames == before.duration_frames
    new_segment = engine._graded_segment(source.media, changed, editor.project, None)
    assert new_segment != old_segment
    np.testing.assert_array_equal(decode(new_segment), selected_expected)
    assert [engine._graded_segment(source.media, c, editor.project, None)
            for c in editor.project.clips[1:]] == anchor_paths

    new_preview = engine.preview(editor.project)
    assert new_preview != old_preview
    assert engine.preview(editor.project) == new_preview
    exported = engine.render(editor.project, tmp_path / 'trimmed.mp4')
    for path in (new_preview, exported):
        actual = decode(path, rgb16=False)
        assert_delivery_frames(actual, timeline_expected)
        # Explicitly reject holds/leaks in every blank frame, including both
        # sides of the moved edge and the gap before the anchored next clip.
        blank = np.all(timeline_expected == 0, axis=(1, 2, 3))
        assert actual[blank].max() <= 3
        metadata = probe(path)
        assert (metadata.fps_num, metadata.fps_den) == (fps.numerator, fps.denominator)

    saved = tmp_path / 'trimmed.lumen'
    editor.project.save(saved)
    loaded = Project.load(saved)
    after = deepcopy(editor.project)
    assert loaded == after
    assert engine.preview(loaded) == new_preview
    np.testing.assert_array_equal(
        decode(engine._graded_segment(source.media, loaded.clips[0], loaded, None)),
        selected_expected)
    assert editor.undo() and editor.project == before
    assert engine.preview(editor.project) == old_preview
    assert engine._graded_segment(source.media, editor.project.clips[0], editor.project, None) == old_segment
    assert editor.redo() and editor.project == after
    assert engine.preview(editor.project) == new_preview
    assert engine._graded_segment(source.media, editor.project.clips[0], editor.project, None) == new_segment


def _sample(frame, fps):
    # Independent half-up rounding. Do not use compile_audio_timing or the
    # production sample_boundary helper to manufacture the expected waveform.
    return math.floor(Fraction(frame) / fps * 48000 + Fraction(1, 2))


def _audio_project(media, fps):
    selected = _clip('selected', media.id, 2, START, ORIGIN, ORIGIN + LOCAL_SPAN)
    anchors = [Clip('before', media.id, 2, 1, 35, 2),
               Clip('after', media.id, 2, START + DURATION + 6, 45, 2),
               Clip('other-track', media.id, 3, 4, 55, 2)]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                      fps_den=fps.denominator, media=[media], clips=[selected, *anchors])
    project.validate()
    return project


def _pcm_oracle(samples, fps, project_end, edge, delta):
    start, end, origin, endpoint = _edited_edges(edge, delta, Fraction(1))
    spans = ((start, end, origin, endpoint), (1, 3, Fraction(35), Fraction(37)),
             (START + DURATION + 6, START + DURATION + 8, Fraction(45), Fraction(47)),
             (4, 6, Fraction(55), Fraction(57)))
    expected = np.zeros(_sample(project_end, fps), dtype=np.float32)
    for first, last, source_first, source_last in spans:
        left, right = _sample(first, fps), _sample(last, fps)
        source = samples[_sample(source_first, fps):_sample(source_last, fps)]
        count = min(right - left, len(source))
        expected[left:left + count] += source[:count]
    # The selected source tail is shorter than its rounded-up picture budget.
    # It must be padded with silence, never read to the next source frame.
    active_end = _sample(start, fps) + _sample(endpoint, fps) - _sample(origin, fps)
    assert active_end < _sample(end, fps)
    assert not np.any(expected[active_end:_sample(end, fps)])
    return expected


def _audio_source(path, fps, signal, edge, delta):
    if signal == 'varying-pcm':
        rng = np.random.default_rng(9347)
        raw = rng.integers(-10000, 10001, 48000 * 4, dtype=np.int16)
    else:
        raw = np.zeros(48000 * 4, dtype=np.int16)
        # Place identifiable one-sample impulses immediately before, exactly
        # at, and immediately after both original and edited exclusive edges.
        # Include stable inner landmarks and anchors on the other track.
        _, _, origin, endpoint = _edited_edges(edge, delta, Fraction(1))
        positions = {ORIGIN, ORIGIN + LOCAL_SPAN, origin, endpoint,
                     ORIGIN + 4, Fraction(36), Fraction(46), Fraction(56)}
        for position in positions:
            sample = _sample(position, fps)
            raw[sample - 1:sample + 2] = (14000, -21000, 28000)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(raw.astype('<i2').tobytes())
    return probe(path), raw.astype(np.float32) / 32768


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('edge', ['left', 'right'])
@pytest.mark.parametrize('delta', [-2, 2], ids=['backward', 'forward'])
@pytest.mark.parametrize('signal', ['varying-pcm', 'edge-impulses'])
def test_trim_edge_real_one_x_pcm_exact_samples_impulses_and_history(
        tmp_path, engine, fps, edge, delta, signal):
    media, samples = _audio_source(tmp_path / 'source.wav', fps, signal, edge, delta)
    editor = Editor(_audio_project(media, fps))
    before = deepcopy(editor.project)
    original_expected = _pcm_oracle(samples, fps, before.duration_frames, edge, 0)
    np.testing.assert_allclose(exact_pcm(engine, before), original_expected,
                               rtol=3e-7, atol=3e-7)
    old_preview = engine.preview(before) if signal == 'varying-pcm' else None

    editor.trim_edge('selected', edge, delta)
    assert editor.project.clips[1:] == before.clips[1:]
    assert editor.project.duration_frames == before.duration_frames
    expected = _pcm_oracle(samples, fps, before.duration_frames, edge, delta)
    actual = exact_pcm(engine, editor.project)
    assert len(actual) == _sample(before.duration_frames, fps)
    np.testing.assert_allclose(actual, expected, rtol=3e-7, atol=3e-7)
    assert not np.array_equal(actual, original_expected)
    if signal == 'edge-impulses':
        # Exact positions and signs, including endpoint-adjacent impulses,
        # make accidental late/early source reads and one-sample drift visible.
        np.testing.assert_array_equal(np.flatnonzero(np.abs(actual) > 1e-6),
                                      np.flatnonzero(expected))
        np.testing.assert_array_equal(np.sign(actual), np.sign(expected))
    else:
        new_preview = engine.preview(editor.project)
        assert new_preview != old_preview
        assert engine.preview(editor.project) == new_preview
        exported = engine.render(editor.project, tmp_path / 'trimmed-audio.mp4')
        start, end, _, _ = _edited_edges(edge, delta, Fraction(1))
        moved_from = START if edge == 'left' else START + DURATION
        moved_to = start if edge == 'left' else end
        middle = Fraction(moved_from + moved_to, 2)
        lo, hi = _sample(middle - Fraction(1, 8), fps), _sample(middle + Fraction(1, 8), fps)
        newly_audible = (edge == 'left' and delta < 0) or (edge == 'right' and delta > 0)
        for path in (new_preview, exported):
            delivered = pcm_decode(path)
            budget = independently_rounded_samples(before.duration_frames, fps)
            # Delivery is AAC, so codec padding/ringing is not mistaken for an
            # exact PCM guarantee. Check the central changed two-frame region,
            # more than one AAC block away from the changed timeline edge.
            assert budget <= len(delivered) < budget + 1024
            rms = np.sqrt(np.mean(delivered[lo:hi] ** 2))
            assert rms > .08 if newly_audible else rms < .005

    saved = tmp_path / 'trimmed-audio.lumen'
    editor.project.save(saved)
    loaded = Project.load(saved)
    after = deepcopy(editor.project)
    assert loaded == after
    np.testing.assert_allclose(exact_pcm(engine, loaded), expected, rtol=3e-7, atol=3e-7)
    assert editor.undo() and editor.project == before
    np.testing.assert_allclose(exact_pcm(engine, editor.project), original_expected,
                               rtol=3e-7, atol=3e-7)
    assert editor.redo() and editor.project == after
    np.testing.assert_allclose(exact_pcm(engine, editor.project), expected,
                               rtol=3e-7, atol=3e-7)
    if signal == 'varying-pcm':
        assert engine.preview(loaded) == new_preview
        assert engine.preview(editor.project) == new_preview
