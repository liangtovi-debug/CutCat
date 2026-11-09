from copy import deepcopy
from fractions import Fraction
import os
import shutil
import subprocess
import threading

import numpy as np
import pytest

from lumen.color import apply_grade
from lumen.color_picker_source import (capture_picker_snapshot, PickerSourceError,
                                       PickerSourceCancelled)
from lumen.model import Clip, Media, Project
from lumen.timing import ClipTimeMap


@pytest.fixture
def chart(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg required for real RGB16 decoder tests')
    # Low bits distinguish this from an RGB8 round trip. Each source frame has
    # a different color, plus truly black picture pixels (not synthetic pads).
    frames = np.empty((12, 4, 8, 3), dtype='<u2')
    for k in range(12):
        frames[k] = [1025 + k * 1024, 18439 + k * 256, 50003 - k * 512]
        frames[k, 0, 0] = 0
    path = tmp_path / 'chart.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb48le',
                    '-video_size', '8x4', '-framerate', '6', '-i', 'pipe:0',
                    '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', '-threads', '1', str(path)],
                   input=frames.tobytes(), check=True, capture_output=True)
    media = Media('m', str(path), 'chart', 2., 8, 4, fps_num=6)
    clip = Clip('c', 'm', 0, 3, 0, 12)
    return Project(width=8, height=4, fps_num=6, media=[media], clips=[clip]), frames


def test_real_rgb16_and_immutable_identity(chart):
    project, frames = chart
    snapshot = capture_picker_snapshot(project, 'c', 7)
    np.testing.assert_array_equal(snapshot.frame, frames[4].astype(np.float32) / np.float32(65535))
    assert snapshot.frame.dtype == np.float32
    assert snapshot.valid_mask.all()
    assert snapshot.source_frame == 4 and snapshot.source_time == Fraction(2, 3)
    assert snapshot.is_current(project, 'c', 7)
    assert not snapshot.is_current(project, 'other', 7)
    assert not snapshot.is_current(project, 'c', 8)
    with pytest.raises(ValueError):
        snapshot.frame.setflags(write=True)
    with pytest.raises(ValueError):
        snapshot.valid_mask.setflags(write=True)


@pytest.mark.parametrize('source_in,speed,local,index', [
    (Fraction(2), Fraction(1), 2, 4),
    (Fraction(1, 2), Fraction(3, 2), 3, 5),
    (Fraction(3, 2), Fraction(1, 2), 3, 3),
])
def test_exact_timing_legacy_and_retimed(chart, source_in, speed, local, index):
    project, frames = chart
    project.clips[0] = Clip('c', 'm', 0, 3, source_in, 4,
                           time_map=ClipTimeMap(source_in, source_in + 4 * speed, speed))
    snapshot = capture_picker_snapshot(project, 'c', 3 + local)
    assert snapshot.source_frame == index
    np.testing.assert_array_equal(snapshot.frame, frames[index].astype(np.float32) / np.float32(65535))


def test_normalization_primary_rgb_curves_before_selective_and_lut(chart):
    project, frames = chart
    grade = project.clips[0].grade
    grade.input_encoding = 'srgb'
    grade.linear_exposure = .5
    grade.temperature = .1
    grade.red_curve = [[0, 0], [.5, .65], [1, 1]]
    grade.hue_hue_curve = [[0., .3], [1., .3]]
    grade.hue_sat_curve = [[0., 0.], [1., 0.]]
    grade.lut_path = '/intentionally/missing/lut.cube'
    before = deepcopy(grade)
    expected_grade = deepcopy(grade)
    expected_grade.hue_hue_curve = [[0., 0.], [1., 0.]]
    expected_grade.hue_sat_curve = [[0., 1.], [1., 1.]]
    expected_grade.lut_path = ''
    snapshot = capture_picker_snapshot(project, 'c', 3)
    expected = apply_grade(frames[0].astype(np.float32) / np.float32(65535), expected_grade)
    np.testing.assert_array_equal(snapshot.frame, expected)
    assert grade == before


def test_disabled_grade_still_normalizes(chart):
    project, frames = chart
    grade = project.clips[0].grade
    grade.enabled, grade.input_encoding, grade.exposure = False, 'srgb', 2
    snapshot = capture_picker_snapshot(project, 'c', 3)
    np.testing.assert_array_equal(snapshot.frame, apply_grade(frames[0].astype(np.float32) / np.float32(65535), grade))


def test_exact_active_picture_mask_not_black_heuristic(chart):
    project, _ = chart
    project.height = 8
    snapshot = capture_picker_snapshot(project, 'c', 3)
    assert snapshot.active_rect == (0, 2, 8, 4)
    assert not snapshot.valid_mask[:2].any() and not snapshot.valid_mask[6:].any()
    assert snapshot.valid_mask[2:6].all()
    assert snapshot.valid_mask[2, 0] and np.all(snapshot.frame[2, 0] == 0)


def test_stale_clip_canvas_source(chart):
    project, _ = chart
    snapshot = capture_picker_snapshot(project, 'c', 3)
    project.clips[0].grade.exposure = 1
    assert not snapshot.is_current(project)
    project.clips[0].grade.exposure = 0.0
    assert snapshot.is_current(project)
    project.width += 1
    assert not snapshot.is_current(project)
    project.width -= 1
    os.utime(project.media[0].path, None)
    assert not snapshot.is_current(project)


def test_change_during_decode_rejected(chart, monkeypatch):
    import lumen.color_picker_source as sampler
    project, _ = chart
    original = sampler.apply_grade
    def changed(*args, **kwargs):
        project.clips[0].grade.exposure += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(sampler, 'apply_grade', changed)
    with pytest.raises(PickerSourceError, match='changed during'):
        capture_picker_snapshot(project, 'c', 3)


@pytest.mark.parametrize('frame', [2, 15, 3.5, True])
def test_invalid_frame(chart, frame):
    with pytest.raises(PickerSourceError, match='inside'):
        capture_picker_snapshot(chart[0], 'c', frame)


def test_audio_only_and_memory_budget(chart):
    project, _ = chart
    project.media[0].has_video = False
    with pytest.raises(PickerSourceError, match='video'):
        capture_picker_snapshot(project, 'c', 3)
    project.media[0].has_video = True
    project.width = project.height = 16384
    with pytest.raises(PickerSourceError, match='budget'):
        capture_picker_snapshot(project, 'c', 3)


def test_cancel_and_timeout(chart):
    event = threading.Event()
    event.set()
    with pytest.raises(PickerSourceCancelled):
        capture_picker_snapshot(chart[0], 'c', 3, event)
    with pytest.raises(PickerSourceError, match='timed out'):
        capture_picker_snapshot(chart[0], 'c', 3, timeout=1e-9)


@pytest.mark.parametrize('setting', ['stabilization'])
def test_unsupported_active_processing(chart, setting):
    project, _ = chart
    settings = getattr(project.clips[0], setting)
    settings.enabled = True
    if setting == 'restoration':
        settings.denoise = .1
    else:
        settings.strength = .5
    with pytest.raises(PickerSourceError, match=setting):
        capture_picker_snapshot(project, 'c', 3)


def test_real_transparent_source_rejected(chart, tmp_path):
    project, _ = chart
    from PIL import Image
    path = tmp_path / 'transparent.png'
    Image.new('RGBA', (8, 4), (255, 0, 0, 128)).save(path)
    project.media[0].path = str(path)
    with pytest.raises(PickerSourceError, match='transparent'):
        capture_picker_snapshot(project, 'c', 3)


@pytest.mark.parametrize('retimed', [False, True])
def test_non_native_fps_matches_full_render_temporal_chain(chart, retimed):
    from lumen.render import _n
    project, _ = chart
    project.fps_num = 5
    clip = project.clips[0]
    clip.source_in, clip.duration = 1, 5
    if retimed:
        clip.time_map = ClipTimeMap(Fraction(3, 2), Fraction(9), Fraction(3, 2))
        clip.source_in = Fraction(3, 2)
    mapping = clip.timing
    if retimed:
        last = (mapping.source_in + (clip.duration - 1) * mapping.speed).__floor__()
        temporal = ['setpts=PTS-STARTPTS', 'fps=fps=5/1:start_time=0', f'trim=end_frame={last + 1}']
    else:
        temporal = ['setpts=PTS-STARTPTS',
                    f'trim=start={_n(clip.source_in / 5)}:duration={_n(clip.duration / 5)}',
                    'setpts=PTS-STARTPTS', 'fps=fps=5/1:start_time=0']
    reference = subprocess.run(['ffmpeg', '-v', 'error', '-threads', '1', '-i', project.media[0].path,
                                '-vf', ','.join(temporal), '-threads', '1', '-pix_fmt', 'rgb48le',
                                '-f', 'rawvideo', 'pipe:1'], capture_output=True, check=True).stdout
    reference = np.frombuffer(reference, dtype='<u2').reshape(-1, 4, 8, 3)
    for k in (0, 2, 4):
        index = (mapping.source_in + k * mapping.speed).__floor__() if retimed else k
        snapshot = capture_picker_snapshot(project, 'c', clip.start + k)
        np.testing.assert_array_equal(snapshot.frame, reference[index].astype(np.float32) / np.float32(65535))


@pytest.mark.parametrize('cancelled', [False, True])
def test_running_child_is_reaped_on_cancel_or_timeout(monkeypatch, cancelled):
    import sys
    import time
    import lumen.color_picker_source as sampler
    children = []
    original = sampler.subprocess.Popen
    def record(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(sampler.subprocess, 'Popen', record)
    event = threading.Event()
    timer = threading.Timer(.1, event.set) if cancelled else None
    if timer:
        timer.start()
    try:
        with pytest.raises(PickerSourceCancelled if cancelled else PickerSourceError):
            sampler._run([sys.executable, '-c', 'import time; time.sleep(30)'], 100,
                         event, time.monotonic() + (10 if cancelled else .1))
        assert len(children) == 1 and children[0].poll() is not None
    finally:
        if timer:
            timer.cancel()


def test_source_replaced_during_decode_rejected(chart, monkeypatch):
    import lumen.color_picker_source as sampler
    project, _ = chart
    original = sampler.apply_grade
    def changed(*args, **kwargs):
        os.utime(project.media[0].path, None)
        return original(*args, **kwargs)
    monkeypatch.setattr(sampler, 'apply_grade', changed)
    with pytest.raises(PickerSourceError, match='changed during'):
        capture_picker_snapshot(project, 'c', 3)


def test_symlink_retarget_invalidates_snapshot(chart, tmp_path):
    from pathlib import Path
    project, _ = chart
    original = Path(project.media[0].path)
    alternate = tmp_path / 'alternate.mkv'
    shutil.copyfile(original, alternate)
    link = tmp_path / 'current.mkv'
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip('Symlinks unavailable on this platform')
    project.media[0].path = str(link)
    snapshot = capture_picker_snapshot(project, 'c', 3)
    assert snapshot.is_current(project)
    link.unlink()
    link.symlink_to(alternate)
    assert not snapshot.is_current(project)


def test_geometry_probe_uses_requested_frame_temporal_chain(chart, monkeypatch):
    import lumen.color_picker_source as sampler
    project, _ = chart
    original = sampler.active_picture_size
    seen = []
    def record(source, filters, *args, **kwargs):
        seen.append(list(filters))
        return original(source, filters, *args, **kwargs)
    monkeypatch.setattr(sampler, 'active_picture_size', record)
    capture_picker_snapshot(project, 'c', 7)
    assert len(seen) == 1
    assert 'trim=start_frame=4:end_frame=5' in seen[0]
    assert seen[0].index('trim=start_frame=4:end_frame=5') < seen[0].index('format=rgb48le')
    assert not any(f.startswith('pad=') for f in seen[0])


def test_independent_scalar_stage_oracle_with_real_nonidentity_lut(chart, tmp_path):
    """Independent RGB16/primary arithmetic, not apply_grade as the oracle."""
    import colorsys
    project, frames = chart
    grade = project.clips[0].grade
    grade.temperature, grade.tint = .8, .2
    grade.red_curve = [[0., 0.], [.5, .75], [1., 1.]]
    grade.hue_hue_curve = [[0., .2], [1., .2]]
    grade.hue_sat_curve = [[0., .8], [1., .8]]
    # A real nonidentity LUT exchanges red and blue. Red varies fastest in CUBE.
    lut = tmp_path / 'exchange-red-blue.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + ''.join(
        f'{blue} {green} {red}\n' for blue in (0, 1)
        for green in (0, 1) for red in (0, 1)))
    grade.lut_path, grade.lut_strength = str(lut), 1.

    # Decode chart frame 4, then calculate temperature/tint and the red segment
    # in scalar double precision. No production grade/curve/hue evaluator is
    # used to produce the expected sample or expected hue.
    codes = frames[4, 1, 1]
    raw = tuple(int(code) / 65535. for code in codes)
    red = raw[0] + .8 * .25 + (.2 * .25) / 2
    green = raw[1] - .2 * .25
    blue = raw[2] - .8 * .25 + (.2 * .25) / 2
    assert 0 < red < .5
    expected = (red * (.75 / .5), green, blue)
    expected_hue = colorsys.rgb_to_hsv(*expected)[0]
    snapshot = capture_picker_snapshot(project, 'c', 7)
    actual = snapshot.frame[1, 1]
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-7)
    assert abs(colorsys.rgb_to_hsv(*map(float, actual))[0] - expected_hue) < 2e-7
    # Prove both primary hue change and retained low-bit precision matter.
    assert abs(expected_hue - colorsys.rgb_to_hsv(*raw)[0]) > .02
    rgb8 = tuple(round(channel * 255) / 255 for channel in raw)
    rgb8_expected = ((rgb8[0] + .225) * 1.5, rgb8[1] - .05, rgb8[2] - .175)
    assert max(abs(float(a) - b) for a, b in zip(actual, rgb8_expected)) > 1e-4

    # The final evaluator is only a counterexample, never the expected oracle.
    # With enabled selective edits AND this real LUT, final pixels have a
    # markedly different hue from the stage the picker must expose.
    final = apply_grade(frames[4].astype(np.float32) / np.float32(65535), grade)[1, 1]
    final_hue = colorsys.rgb_to_hsv(*map(float, final))[0]
    delta = abs(final_hue - expected_hue)
    assert min(delta, 1 - delta) > .05


def test_real_packed_vuya_transparency_rejected(chart, tmp_path):
    project, _ = chart
    path = tmp_path / 'transparent-vuya.nut'
    # A=0 is entirely transparent, despite its nonblack YUV components. Packed
    # VUYA passes generic color validation through the vuy pixel-format family.
    pixels = bytes([240, 90, 100, 0]) * (8 * 4)
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'vuya',
                             '-video_size', '8x4', '-framerate', '6', '-i', 'pipe:0',
                             '-c:v', 'rawvideo', '-pix_fmt', 'vuya', '-threads', '1',
                             str(path)], input=pixels, capture_output=True)
    if result.returncode and b'No such pixel format' in result.stderr:
        pytest.skip('This FFmpeg predates packed VUYA')
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    project.media[0].path = str(path)
    with pytest.raises(PickerSourceError, match='transparent'):
        capture_picker_snapshot(project, 'c', 3)


def test_all_decoder_alpha_formats_fail_closed():
    """Audit the installed decoder's alpha registry, not just remembered names."""
    import json
    from lumen.color_picker_source import _has_alpha
    from lumen.color_source import source_color_from_stream
    if not shutil.which('ffprobe'):
        pytest.skip('FFprobe required for actual pixel-format registry')
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_pixel_formats', '-of', 'json'],
                            capture_output=True, check=True)
    formats = json.loads(result.stdout)['pixel_formats']
    accepted_alpha = []
    for item in formats:
        if not item.get('flags', {}).get('alpha'):
            continue
        name = item['name']
        try:
            source_color_from_stream({'pix_fmt': name})
        except ValueError:
            continue  # Unsupported formats already fail closed upstream.
        accepted_alpha.append(name)
        assert _has_alpha(name), f'Accepted alpha format could lose transparency: {name}'
    assert accepted_alpha, 'Expected decoder alpha formats to be audited'
