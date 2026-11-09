"""Real media acceptance for the shared local spatial restoration pipeline.

The lossless RGB48 stage is an exact integration oracle. Preview/export H.264
is intentionally compared with tolerances; independent picture statistics
establish real noise reduction/detail retention rather than a changed hash.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import subprocess
import threading

import numpy as np
import pytest

from lumen import color, color_media, restoration
from lumen.media import Media, ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, Editor, Grade, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.restoration_settings import RestorationSettings
from lumen.timing import ClipTimeMap, sample_boundary

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='External FFmpeg/ffprobe required')
WIDTH, HEIGHT, COUNT = 128, 96, 28
RATES = (Fraction(24000, 1001), Fraction(30000, 1001))


def ff(*args, data=None):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                           *map(str, args)], input=data, capture_output=True, check=True, timeout=45).stdout


def decode(path, width=WIDTH, height=HEIGHT, *, data=None, rgb16=True):
    fmt, dtype = ('rgb48le', '<u2') if rgb16 else ('rgb24', 'u1')
    raw = ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0', '-an',
             '-threads', '1', '-fps_mode', 'passthrough', '-pix_fmt', fmt,
             '-f', 'rawvideo', 'pipe:1', data=data)
    return np.frombuffer(raw, dtype).reshape(-1, height, width, 3)


def noisy_picture(count=COUNT, width=WIDTH, height=HEIGHT):
    rng = np.random.default_rng(829)
    base = np.full((height, width, 3), .32, np.float32)
    base[:, width // 2:] = .72
    frames = []
    for index in range(count):
        noise = rng.normal(0, .022, base.shape).astype(np.float32)
        frame = base + noise + np.float32(index * .0007)
        frames.append(np.rint(frame * np.float32(65535)).astype(np.uint16))
    return np.array(frames)


@dataclass
class PictureSource:
    media: Media
    frames: np.ndarray
    fps: Fraction


def write_source(path, frames, fps=RATES[0], transfer='bt709'):
    height, width = frames.shape[1:3]
    tags = ['-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', transfer, '-colorspace', 'rgb']
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{width}x{height}', '-r', str(fps),
       *tags, '-protocol_whitelist', 'file,pipe', '-i', 'pipe:0', '-an', '-c:v', 'ffv1',
       '-level', '3', '-pix_fmt', 'gbrp16le', '-threads', '1', *tags, path,
       data=frames.astype('<u2').tobytes())
    np.testing.assert_array_equal(decode(path, width, height), frames)
    return PictureSource(probe(path), frames, fps)


@pytest.fixture(scope='module')
def sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('restoration-video')
    frames = noisy_picture()
    return {rate: write_source(root / f'noise {rate.numerator}-{rate.denominator}.mkv', frames, rate)
            for rate in RATES}


@pytest.fixture
def engine():
    renderer = RenderEngine(timeout=60)
    try:
        yield renderer
    finally:
        renderer.close()


def project_for(source, *, origin=Fraction(0), endpoint=Fraction(COUNT-1), speed=Fraction(1),
                settings=None, grade=None, width=WIDTH, height=HEIGHT):
    mapping = ClipTimeMap(origin, endpoint, speed)
    clip = Clip('picture', source.media.id, 0, 0, 0, math.ceil((endpoint-origin)/speed),
                time_map=mapping, grade=grade or Grade(),
                restoration=settings or RestorationSettings(enabled=True, denoise=.8))
    result = Project(width=width, height=height, fps_num=source.fps.numerator,
                     fps_den=source.fps.denominator, media=[deepcopy(source.media)], clips=[clip])
    result.validate()
    return result


def segment(engine, project, clip=None):
    clip = clip or project.clips[0]
    media = next(m for m in project.media if m.id == clip.media_id)
    return decode(engine._graded_segment(media, clip, project, None), project.width, project.height)


def quantize(rgb):
    return np.rint(np.clip(rgb, 0, 1) * np.float32(65535)).astype(np.uint16)


def test_actual_noise_reduction_preserves_strong_detail_and_source_bytes(sources, engine):
    source = sources[RATES[0]]
    before_hash = hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest()
    project = project_for(source)
    before = source.frames[:project.clips[0].duration].astype(float) / 65535
    after = segment(engine, project).astype(float) / 65535
    flat = (slice(None), slice(8, -8), slice(8, WIDTH//2-8), slice(None))
    noise_before = np.std(before[flat], axis=(1, 2))
    noise_after = np.std(after[flat], axis=(1, 2))
    assert (noise_after < noise_before * .85).all(), (noise_before, noise_after)
    # A .4 contrast boundary must not bleed; compare both one-pixel sides.
    edge_before = np.mean(before[:, 8:-8, WIDTH//2] - before[:, 8:-8, WIDTH//2-1], axis=(1, 2))
    edge_after = np.mean(after[:, 8:-8, WIDTH//2] - after[:, 8:-8, WIDTH//2-1], axis=(1, 2))
    np.testing.assert_allclose(edge_after, edge_before, atol=.003)
    assert np.unique(segment(engine, project)[0, ..., 0]).size > 1000
    assert hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest() == before_hash


def test_real_repeated_weak_block_steps_reduce_without_weakening_strong_edge(tmp_path, engine):
    xx = np.arange(WIDTH)
    gray = .30 + .035 * ((xx // 8) % 2)
    gray[WIDTH//2:] += .4
    rgb = np.broadcast_to(gray[None, :, None], (HEIGHT, WIDTH, 3)).astype(np.float32)
    frames = np.broadcast_to(quantize(rgb), (4, HEIGHT, WIDTH, 3)).copy()
    source = write_source(tmp_path / 'blocks.mkv', frames)
    project = project_for(source, endpoint=Fraction(3), settings=RestorationSettings(enabled=True, deblock=1))
    after = segment(engine, project)[0].astype(float) / 65535
    before = frames[0].astype(float) / 65535
    seams = [8, 16, 24, 32, 40, 48, 56, 72, 80, 88, 96, 104, 112, 120]
    old = np.abs(before[:, seams] - before[:, np.array(seams)-1]).mean()
    new = np.abs(after[:, seams] - after[:, np.array(seams)-1]).mean()
    assert new < old * .75
    np.testing.assert_array_equal(after[:, 63:65], before[:, 63:65])


def test_disabled_and_all_zero_use_exact_old_pixels_and_skip_new_picture_work(sources, engine, monkeypatch):
    source = sources[RATES[0]]
    baseline = project_for(source, endpoint=Fraction(3), settings=RestorationSettings())
    expected = segment(engine, baseline)
    def unexpected(*args, **kwargs):
        pytest.fail('Bypass must not inspect active geometry or run restoration')
    monkeypatch.setattr(color_media, 'active_picture_size', unexpected)
    monkeypatch.setattr(restoration, 'apply_restoration', unexpected)
    for settings in (RestorationSettings(enabled=False, deblock=.6, denoise=.8, sharpen=.5),
                     RestorationSettings(enabled=True)):
        project = project_for(source, endpoint=Fraction(3), settings=settings)
        np.testing.assert_array_equal(segment(engine, project), expected)
    np.testing.assert_array_equal(expected, source.frames[:3])


@pytest.mark.parametrize('enabled', [True, False], ids=['creative-enabled', 'creative-bypassed'])
@pytest.mark.parametrize('encoding,transfer', [('srgb', 'iec61966-2-1'), ('linear_rec709', 'linear')])
def test_normalize_once_then_restore_float_then_creative_grade(tmp_path, engine, monkeypatch, enabled, encoding, transfer):
    frames = noisy_picture(4)
    source = write_source(tmp_path / f'{encoding}.mkv', frames, transfer=transfer)
    grade = Grade(input_encoding=encoding, enabled=enabled, gamma=[1.1, .9, 1.05], linear_exposure=.2)
    settings = RestorationSettings(enabled=True, deblock=.4, denoise=.7, sharpen=.35)
    project = project_for(source, endpoint=Fraction(3), grade=grade, settings=settings)
    seen = []
    actual = restoration.apply_restoration
    def inspect(rgb, settings, cancel=None):
        result = actual(rgb, settings, cancel=cancel)
        seen.append((rgb.copy(), result.copy()))
        return result
    monkeypatch.setattr(restoration, 'apply_restoration', inspect)
    rendered = segment(engine, project)
    assert len(seen) == 3
    normalized = [color.normalize_input(frame.astype(np.float32) / np.float32(65535), encoding)
                  for frame in frames[:3]]
    creative = deepcopy(grade); creative.input_encoding = 'rec709'
    for source_rgb, (before, restored), result in zip(normalized, seen, rendered):
        np.testing.assert_array_equal(before, source_rgb)
        assert before.dtype == restored.dtype == np.float32
        assert np.unique(before[..., 0]).size > 1000
        assert np.max(np.abs(restored * 65535 - np.rint(restored * 65535))) > .4
        np.testing.assert_array_equal(result, quantize(color.apply_grade(restored, creative)))
        assert not np.array_equal(result, quantize(color.apply_grade(before, creative)))


@pytest.mark.parametrize('fps', RATES, ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', (Fraction(1, 2), Fraction(3, 2), Fraction(4)), ids=['half', 'one-and-half', 'four'])
def test_fractional_retime_and_repeated_split_have_exact_restored_pixels(sources, engine, fps, speed):
    source = sources[fps]
    settings = RestorationSettings(enabled=True, deblock=.6, denoise=.75, sharpen=.4)
    full = segment(engine, project_for(source, settings=settings))
    origin, endpoint = Fraction(9, 8), Fraction(193, 8)
    project = project_for(source, origin=origin, endpoint=endpoint, speed=speed, settings=settings)
    indices = [math.floor(origin + k*speed) for k in range(math.ceil((endpoint-origin)/speed))]
    whole = segment(engine, project)
    np.testing.assert_array_equal(whole, full[indices])
    editor = Editor(deepcopy(project))
    _, right = editor.split_clip('picture', 2)
    editor.split_clip(right.id, 4)
    np.testing.assert_array_equal(np.concatenate([segment(engine, editor.project, clip)
                                                 for clip in editor.project.clips]), whole)


def test_preview_export_still_use_restored_shared_stage(sources, engine, tmp_path):
    source = sources[RATES[0]]
    project = project_for(source, origin=Fraction(9, 8), endpoint=Fraction(121, 8), speed=Fraction(3, 2))
    expected = segment(engine, project).astype(float) / 65535
    bypass = deepcopy(project); bypass.clips[0].restoration.enabled = False
    preview, export = engine.preview(project), engine.render(project, tmp_path / 'restored.mp4')
    before = decode(engine.preview(bypass)).astype(float) / 65535
    for path in (preview, export):
        actual = decode(path).astype(float) / 65535
        assert len(actual) == len(expected)
        # Independent-channel noise is partly removed by 4:2:0 chroma sampling.
        assert np.abs(actual - expected).mean() < .02
        assert np.abs(actual - before).mean() > .001
        # Delivery must carry the same measurable improvement, not only invoke
        # an evaluator whose result is accidentally discarded by the compiler.
        assert actual[:, 8:-8, 8:56].std(axis=(1, 2)).mean() < before[:, 8:-8, 8:56].std(axis=(1, 2)).mean() * .95
    still = decode('pipe:0', data=engine.frame(project, 3, width=WIDTH), rgb16=False)
    np.testing.assert_array_equal(still[0], decode(preview, rgb16=False)[3])


@pytest.mark.parametrize('source_size,sar,rotation,active', [
    ((81, 39), '1/1', 0, (80, 39)),
    ((80, 40), '2/1', 0, (80, 20)),
    ((80, 40), '1/1', 90, (40, 80)),
    ((80, 40), '2/1', 90, (20, 80)),
])
def test_actual_padding_geometry_handles_odd_rounding_sar_and_rotation(tmp_path, engine, monkeypatch, source_size, sar, rotation, active):
    width, height = source_size
    base, path = tmp_path / 'base.mov', tmp_path / 'oriented.mov'
    # Lossless RGB png inside MOV preserves odd source sizes and display matrix.
    ff('-f', 'lavfi', '-i', f'color=gray:s={width+width%2}x{height+height%2}:r=30:d=0.2',
       '-vf', f'format=rgb24,crop={width}:{height},setsar={sar}', '-an', '-c:v', 'png',
       '-threads', '1', '-color_trc', 'bt709', '-color_primaries', 'bt709', base)
    ff('-display_rotation', rotation, '-protocol_whitelist', 'file,pipe', '-i', base, '-c', 'copy', path)
    source = PictureSource(probe(path), np.empty((0,)), Fraction(30))
    settings = RestorationSettings(enabled=True, denoise=.8, sharpen=.5)
    project = project_for(source, endpoint=Fraction(3), width=80, height=80, settings=settings,
                          grade=Grade(lift=[.02, .01, .03]))
    baseline = deepcopy(project); baseline.clips[0].restoration.enabled = False
    old = segment(engine, baseline)
    seen = []
    actual = restoration.apply_restoration
    def inspect(rgb, settings, cancel=None):
        seen.append(rgb.copy())
        # Artificially brighten active pixels to catch even one wrong ROI row.
        return rgb + np.float32(.1)
    monkeypatch.setattr(restoration, 'apply_restoration', inspect)
    out = segment(engine, project)
    aw, ah = active; left, top = (80-aw)//2, (80-ah)//2
    assert all(frame.shape == (ah, aw, 3) for frame in seen)
    mask = np.zeros((80, 80), dtype=bool); mask[top:top+ah, left:left+aw] = True
    np.testing.assert_array_equal(out[:, ~mask], old[:, ~mask])
    assert np.min(out[:, mask].astype(int) - old[:, mask].astype(int)) > 1000
    # Grade still affects the synthetic bars exactly as it did before.
    assert old[:, ~mask].min() > 0
    monkeypatch.setattr(restoration, 'apply_restoration', actual)


def test_settings_undo_and_algorithm_version_invalidate_both_caches(sources, engine, monkeypatch):
    project = project_for(sources[RATES[0]], endpoint=Fraction(3))
    editor = Editor(project)
    def paths():
        clip = editor.project.clips[0]
        return (engine._graded_segment(editor.project.media[0], clip, editor.project, None),
                engine.preview(editor.project))
    first = paths()
    editor.update_restoration('picture', denoise=.3)
    second = paths()
    assert all(a != b for a, b in zip(first, second))
    assert editor.undo()
    assert paths() == first
    assert editor.redo()
    assert paths() == second
    monkeypatch.setattr(restoration, 'RESTORATION_ALGORITHM', 'test-next-restoration-version')
    third = paths()
    assert all(a != b for a, b in zip(second, third))


def test_cancel_inside_restoration_removes_partial_stage_then_recovers(sources, engine, monkeypatch, tmp_path):
    source = sources[RATES[0]]
    project = project_for(source, endpoint=Fraction(3))
    event = threading.Event()
    actual = restoration.apply_restoration
    def interrupt(rgb, settings, cancel=None):
        event.set()
        return actual(rgb, settings, cancel=cancel)
    monkeypatch.setattr(restoration, 'apply_restoration', interrupt)
    with pytest.raises(RenderCancelled):
        engine.render(project, tmp_path / 'cancelled.mp4', cancel=event)
    assert not (tmp_path / 'cancelled.mp4').exists()
    assert not list(Path(engine._cache_dir.name).glob('*.partial*'))
    assert not list(Path(engine._cache_dir.name).glob('*.mkv'))
    monkeypatch.setattr(restoration, 'apply_restoration', actual)
    event.clear()
    output = engine.render(project, tmp_path / 'recovered.mp4', cancel=event)
    assert len(decode(output)) == 3


@pytest.mark.parametrize('fps', RATES, ids=['23.976', '29.97'])
def test_restoration_preserves_encoded_audio_pcm_and_fractional_timing(sources, engine, tmp_path, fps):
    original = sources[fps]
    source_path = tmp_path / 'with offset audio.mkv'
    ff('-protocol_whitelist', 'file,pipe', '-i', original.media.path,
       '-itsoffset', '0.125', '-f', 'lavfi', '-i', 'sine=frequency=997:sample_rate=48000:duration=1.3',
       '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'pcm_s16le', source_path)
    source = PictureSource(probe(source_path), original.frames, fps)
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert abs(source.media.audio_offset - .125) < .002
    project = project_for(source, origin=Fraction(9, 8), endpoint=Fraction(193, 8), speed=Fraction(3, 2))
    bypass = deepcopy(project); bypass.clips[0].restoration.enabled = False
    before = engine.render(bypass, tmp_path / 'before.mp4')
    after = engine.render(project, tmp_path / 'after.mp4')
    def audio(path, encoded):
        codec = ['-c:a', 'copy', '-f', 'adts'] if encoded else ['-ac', '1', '-ar', '48000', '-f', 'f32le']
        return ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:a:0', '-vn', *codec, 'pipe:1')
    assert audio(before, True) == audio(after, True)
    pcm = audio(after, False)
    assert pcm == audio(before, False)
    budget = sample_boundary(project.duration_frames, fps, 48000)
    assert budget <= len(pcm)//4 < budget + 1024
    assert len(decode(after)) == project.duration_frames
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash


def test_actual_stabilized_float_picture_reaches_restoration_before_grade_and_excludes_padding(tmp_path, engine, monkeypatch):
    from lumen import stabilization_math
    from lumen.stabilization_settings import StabilizationSettings
    filters = subprocess.run([ffmpeg_binary(), '-hide_banner', '-filters'], capture_output=True, text=True, timeout=20)
    if any(name not in filters.stdout for name in ('vidstabdetect', 'vidstabtransform')):
        pytest.skip('External FFmpeg lacks libvidstab')
    width, height, count = 160, 120, 32
    rng = np.random.default_rng(622)
    base = np.repeat(np.repeat(rng.integers(8000, 52000, (12, 16, 3), dtype=np.uint16), 10, 0), 10, 1)
    yy, xx = np.mgrid[:height, :width]
    base += ((xx * 13 + yy * 7) % 251).astype(np.uint16)[..., None]
    frames = np.array([np.roll(base, (round(2*math.cos(i*.9)), round(3*math.sin(i*1.2))), (0, 1))
                       for i in range(count)])
    source = write_source(tmp_path / 'restored stabilized.mkv', frames)
    project = project_for(source, origin=Fraction(9, 8), endpoint=Fraction(97, 8), speed=Fraction(3, 2),
                          width=width, height=160, settings=RestorationSettings(enabled=True, denoise=.5, sharpen=.3),
                          grade=Grade(lift=[.01, .02, .03]))
    project.clips[0].stabilization = StabilizationSettings(enabled=True, strength=.8, smoothing=3, max_zoom=1.2)
    warped, received = [], []
    actual_warp, actual_restoration = stabilization_math.resample_float_rgb, restoration.apply_restoration
    def inspect_warp(*args, **kwargs):
        result = actual_warp(*args, **kwargs)
        warped.append(result.copy())
        return result
    def inspect_restoration(rgb, settings, cancel=None):
        result = actual_restoration(rgb, settings, cancel=cancel)
        received.append((rgb.copy(), result.copy()))
        return result
    monkeypatch.setattr(stabilization_math, 'resample_float_rgb', inspect_warp)
    monkeypatch.setattr(restoration, 'apply_restoration', inspect_restoration)
    actual = segment(engine, project)
    assert len(warped) == len(received) == len(actual) == 8
    for stable, (before, restored), result in zip(warped, received, actual):
        assert before.shape == (height, width, 3)
        np.testing.assert_array_equal(before, stable)
        # Warp samples genuinely retain fractions below the RGB48 ingress grid.
        assert np.max(np.abs(before * 65535 - np.rint(before * 65535))) > .4
        canvas = np.zeros((160, width, 3), np.float32)
        canvas[20:140] = restored
        np.testing.assert_array_equal(result, quantize(color.apply_grade(canvas, project.clips[0].grade)))
    bypass = deepcopy(project); bypass.clips[0].restoration.enabled = False
    old = segment(engine, bypass)
    np.testing.assert_array_equal(actual[:, :20], old[:, :20])
    np.testing.assert_array_equal(actual[:, 140:], old[:, 140:])


def test_normal_source_edit_invalidates_geometry_segment_and_preview_caches(tmp_path, engine, monkeypatch):
    path = tmp_path / 'edited source.mkv'
    source = write_source(path, noisy_picture(4))
    project = project_for(source, endpoint=Fraction(3))
    probes = []
    actual_probe = color_media.active_picture_size
    def inspect(*args, **kwargs):
        result = actual_probe(*args, **kwargs)
        probes.append(result)
        return result
    monkeypatch.setattr(color_media, 'active_picture_size', inspect)
    before_stage = engine._graded_segment(source.media, project.clips[0], project, None)
    before_preview = engine.preview(project)
    assert probes == [(WIDTH, HEIGHT)]
    # An ordinary source edit changes its stat identity and active geometry.
    # This does not promise content hashing for deliberately preserved Windows
    # timestamps; general media caches retain their existing source-stat policy.
    replacement = noisy_picture(4, width=WIDTH, height=72)
    write_source(path, replacement)
    after_stage = engine._graded_segment(source.media, project.clips[0], project, None)
    after_preview = engine.preview(project)
    assert before_stage != after_stage and before_preview != after_preview
    assert probes == [(WIDTH, HEIGHT), (WIDTH, 72)]
    pixels = decode(after_stage)
    np.testing.assert_array_equal(pixels[:, :12], 0)
    np.testing.assert_array_equal(pixels[:, -12:], 0)


def test_actual_restrained_sharpen_increases_soft_edge_slope_without_halos(tmp_path, engine):
    profile = np.full(WIDTH, .25, np.float32)
    profile[62:66] = [.27, .38, .62, .73]
    profile[66:] = .75
    frame = np.broadcast_to(profile[None, :, None], (HEIGHT, WIDTH, 3)).copy()
    frames = np.broadcast_to(quantize(frame), (4, HEIGHT, WIDTH, 3)).copy()
    source = write_source(tmp_path / 'soft edge.mkv', frames)
    project = project_for(source, endpoint=Fraction(3), settings=RestorationSettings(enabled=True, sharpen=1))
    after = segment(engine, project)[0].astype(float) / 65535
    before = frames[0].astype(float) / 65535
    assert np.diff(after[40, :, 0]).max() > np.diff(before[40, :, 0]).max() * 1.05
    assert after.min() >= before.min() and after.max() <= before.max()
    assert np.max(np.abs(after-before)) <= .015 + 1/65535
    np.testing.assert_array_equal(after[:, :60], before[:, :60])
    np.testing.assert_array_equal(after[:, 68:], before[:, 68:])


def test_mixed_source_project_rates_restore_the_same_normalized_source_grid(sources, engine):
    source = sources[RATES[0]]
    project = project_for(source)
    project.fps_num, project.fps_den = RATES[1].numerator, RATES[1].denominator
    project.validate()
    whole = segment(engine, project)
    selected = deepcopy(project)
    origin, endpoint, speed = Fraction(5, 4), Fraction(93, 4), Fraction(3, 2)
    selected.clips[0].time_map = ClipTimeMap(origin, endpoint, speed)
    selected.clips[0].source_in = origin
    selected.clips[0].duration = math.ceil((endpoint-origin)/speed)
    selected.validate()
    actual = segment(engine, selected)
    indices = [math.floor(origin + k*speed) for k in range(selected.clips[0].duration)]
    np.testing.assert_array_equal(actual, whole[indices])
    editor = Editor(selected)
    editor.split_clip('picture', 3)
    np.testing.assert_array_equal(np.concatenate([segment(engine, selected, clip) for clip in selected.clips]), actual)
