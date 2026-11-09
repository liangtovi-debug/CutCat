"""Independent frame IDs and every-sample PCM for atomic source overwrite.

Only synthetic local media. Expected output is constructed from original arrays
and the requested replacement interval, never from the production edit plan.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import wave

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from test_retime_video import (WIDTH, HEIGHT, PROJECT_RATES, assert_delivery_frames,
    decode, engine, frame_sources, normalized_source, ff)
from test_roll_media import exact_pcm

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')

# Existing [10,30), plus [40,50). All cases include a fixed downstream marker.
# Full cover, trailing/leading overlap, interior, gap, extension/multiple clips.
CASES = [(5, 30), (20, 15), (5, 15), (15, 10), (30, 10), (20, 40)]


def project_for(media, fps, track=0):
    return Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[deepcopy(media)], clips=[
            Clip('first', media.id, track, 10, 5, 20),
            Clip('second', media.id, track, 40, 25, 10),
            Clip('fixed', media.id, track, 65, 60, 5)])


def expected_picture(original, start, count):
    result = np.zeros((max(70, start+count), HEIGHT, WIDTH, 3), dtype=np.uint16)
    result[10:30] = original[5:25]
    result[40:50] = original[25:35]
    result[65:70] = original[60:65]
    result[start:start+count] = original[10:10+count]
    return result


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('start,count', CASES)
def test_numbered_frames_all_overlap_shapes_and_public_preview(
        tmp_path, frame_sources, engine, fps, start, count):
    source = frame_sources[fps]
    editor = Editor(project_for(source.media, fps))
    before = deepcopy(editor.project)
    source_hash = hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest()
    plan = editor.plan_source_overwrite(source.media.id, 0, start, 10, 10+count)
    editor.apply_source_overwrite(plan)
    expected = expected_picture(source.original, start, count)
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), expected)
    assert next(c for c in editor.project.clips if c.id == 'fixed') == before.clips[-1]
    saved = tmp_path/'overwrite.lumen'
    editor.project.save(saved)
    assert Project.load(saved) == editor.project
    assert editor.undo() and editor.project == before
    assert editor.redo()
    assert hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize('source_fps', [Fraction(24), Fraction(60)])
def test_mixed_source_fps_rejected_without_changing_legacy_contract(
        frame_sources, source_fps):
    fps = Fraction(30000, 1001)
    source = frame_sources[source_fps]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[deepcopy(source.media)],
        clips=[Clip('original', source.media.id, 0, 0, 0, 25)])
    editor = Editor(project)
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    with pytest.raises(ValueError, match='frame rate|mixed|matching'):
        editor.plan_source_overwrite(source.media.id, 0, 5, 15, 25)
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before


@pytest.mark.parametrize('mode', ['h264_8bit', 'prores_422_hq_10bit'])
@pytest.mark.parametrize('fps', PROJECT_RATES)
def test_matching_rate_source_grid_and_public_delivery(
        tmp_path, frame_sources, engine, fps, mode):
    source = frame_sources[fps]
    project = Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[deepcopy(source.media)],
        clips=[Clip('original', source.media.id, 0, 0, 0, 25)])
    editor = Editor(project)
    editor.apply_source_overwrite(editor.plan_source_overwrite(source.media.id, 0, 5, 15, 25))
    expected = source.original[:25].copy(); expected[5:15] = source.original[15:25]
    suffix = '.mov' if mode.startswith('prores') else '.mp4'
    result = engine.render(editor.project, tmp_path/('overwritten'+suffix), export_profile=mode)
    assert_delivery_frames(decode(result, rgb16=False), expected)


def sample(frame, fps):
    value = Fraction(frame*48000, 1)/fps
    return (2*value.numerator+value.denominator)//(2*value.denominator)


def pcm_source(tmp_path, offset):
    raw = np.random.default_rng(48271).integers(-12000, 12001, 48000*4, dtype=np.int16)
    path = tmp_path/'varied-source.wav'
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(raw.astype('<i2').tobytes())
    if offset:
        mux = tmp_path/'offset-source.mkv'
        ff('-f', 'lavfi', '-i', 'color=size=64x48:rate=24:duration=4',
           '-itsoffset', str(offset), '-i', path, '-map', '0:v', '-map', '1:a',
           '-c:v', 'ffv1', '-c:a', 'pcm_s16le', mux)
        path = mux
    media = probe(path)
    assert media.audio_offset == pytest.approx(offset, abs=1e-9)
    return media, raw.astype(np.float32)/32768


def put_pcm(target, raw, fps, start, origin, count, offset):
    """Independent sample rounding and clipped source selection, no timing helpers."""
    out_start, out_end = sample(start, fps), sample(start+count, fps)
    source_start = max(Fraction(0), Fraction(origin,1)/fps-Fraction(str(offset)))
    source_end = max(Fraction(0), Fraction(origin+count,1)/fps-Fraction(str(offset)))
    lo = math.floor(source_start*48000+Fraction(1,2))
    hi = math.floor(source_end*48000+Fraction(1,2))
    late = max(Fraction(0), Fraction(str(offset))-Fraction(origin,1)/fps)
    onset = math.floor((Fraction(start,1)/fps+late)*48000+Fraction(1,2))
    data = raw[lo:hi][:max(0,out_end-onset)]
    target[onset:onset+len(data)] = data


@pytest.mark.parametrize('fps', PROJECT_RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('offset', [0, .013])
@pytest.mark.parametrize('start,count', CASES)
def test_every_pcm_sample_outside_overwrite_unchanged_and_new_source_exact(
        tmp_path, engine, fps, offset, start, count):
    media, raw = pcm_source(tmp_path, offset)
    editor = Editor(project_for(media, fps, track=2))
    source_bytes = Path(media.path).read_bytes()
    original = np.zeros(sample(70, fps), np.float32)
    for a,b,c in [(10,5,20), (40,25,10), (65,60,5)]:
        put_pcm(original, raw, fps, a,b,c,offset)
    before_pcm = exact_pcm(engine, editor.project)
    np.testing.assert_allclose(before_pcm, original, rtol=3e-7, atol=3e-7)
    editor.apply_source_overwrite(editor.plan_source_overwrite(media.id, 2, start, 10, 10+count))
    expected = original.copy()
    expected[sample(start,fps):sample(start+count,fps)] = 0
    put_pcm(expected, raw, fps, start,10,count,offset)
    actual = exact_pcm(engine, editor.project)
    np.testing.assert_allclose(actual, expected, rtol=3e-7, atol=3e-7)
    np.testing.assert_array_equal(actual[:sample(start,fps)], before_pcm[:sample(start,fps)])
    np.testing.assert_array_equal(actual[sample(start+count,fps):], before_pcm[sample(start+count,fps):])
    assert Path(media.path).read_bytes() == source_bytes


@pytest.mark.parametrize('setting', ['static-grade', 'static-lut', 'static-gain'])
def test_retained_static_settings_and_signal_are_not_reset(
        tmp_path, frame_sources, engine, setting):
    from test_lut_workflow import cube
    from test_retime_video import segment_pixels
    fps = Fraction(24)
    source = frame_sources[fps]
    media, raw = pcm_source(tmp_path, 0)
    project = project_for(media if setting == 'static-gain' else source.media,
                          fps, 2 if setting == 'static-gain' else 0)
    clip = project.clips[0]
    if setting == 'static-grade':
        clip.grade.linear_exposure = .4
        clip.grade.saturation = .6
    elif setting == 'static-lut':
        clip.grade.lut_path = str(cube(tmp_path/'original-look.cube'))
        clip.grade.lut_strength = .75
    else:
        clip.gain_db = -6
    editor = Editor(project)
    original = deepcopy(clip)
    if setting == 'static-gain':
        before = exact_pcm(engine, editor.project)
    else:
        before = segment_pixels(engine, editor.project, clip)
    new = editor.apply_source_overwrite(editor.plan_source_overwrite(
        clip.media_id, clip.track, 15, 30, 40))
    left = next(c for c in editor.project.clips if c.id == 'first')
    right = next(c for c in editor.project.clips if c.start == 25)
    for retained in (left, right):
        assert retained.grade == original.grade
        assert retained.gain_db == original.gain_db
        assert retained.composite == original.composite
    assert new.gain_db == 0 and not new.grade.lut_path
    if setting == 'static-gain':
        after = exact_pcm(engine, editor.project)
        np.testing.assert_array_equal(after[:15*2000], before[:15*2000])
        np.testing.assert_array_equal(after[25*2000:], before[25*2000:])
        expected = raw[30*2000:40*2000]
        np.testing.assert_allclose(after[15*2000:25*2000], expected, rtol=3e-7, atol=3e-7)
    else:
        np.testing.assert_array_equal(segment_pixels(engine, editor.project, left), before[:5])
        np.testing.assert_array_equal(segment_pixels(engine, editor.project, right), before[15:])
        np.testing.assert_array_equal(segment_pixels(engine, editor.project, new), source.original[30:40])
