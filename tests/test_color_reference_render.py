"""Actual proxy decoding and export isolation for frozen viewer references.

Only synthetic local frames are used. The still must exactly equal the application-converted
Qt decoded frame. A separate FFmpeg Rec.709 decode quantifies backend color
differences; it is diagnostic, not a false claim of colorimetric equivalence.
These offscreen checks do not certify a physical monitor or native packaging.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.preview_color import preview_frame_image
from lumen.color_scopes import rgb_from_qimage
from lumen.media import ffmpeg_available, ffmpeg_binary, ffprobe_binary, probe
from lumen.model import Clip, Editor, Grade, Project
from lumen.render import RenderEngine
from lumen.ui import MainWindow


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def ff(*args, data=None):
    return subprocess.run(
        [ffmpeg_binary(), '-hide_banner', '-loglevel', 'error', '-nostdin',
         '-y', '-threads', '1', *map(str, args)], input=data,
        capture_output=True, check=True, timeout=60).stdout


def two_clip_project(folder, fps=Fraction(30)):
    """Two distinct RGB16 sources; each frame has a unique temporal color."""
    width, height, count = 96, 64, 10
    media, clips = [], []
    palettes = (
        [[.53, .18, .10], [.30, .30, .30], [.12, .38, .32]],
        [[.12, .24, .54], [.38, .32, .26], [.38, .13, .31]],
    )
    tags = ['-colorspace', 'rgb', '-color_range', 'pc', '-color_trc', 'bt709',
            '-color_primaries', 'bt709']
    for name, palette, exposure in zip(('warm', 'cool'), palettes, (.3, -.35)):
        patches = np.repeat(np.asarray(palette), width // 3, axis=0)
        base = np.repeat(patches[None], height, axis=0)
        frames = np.rint(65535 * np.stack([base + .018 * n for n in range(count)])).astype('<u2')
        planar = np.moveaxis(frames[..., [1, 2, 0]], -1, 1).astype('<u2')
        path = folder / f'{name}-synthetic-rgb16.mkv'
        ff('-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-s', f'{width}x{height}',
           '-r', f'{fps.numerator}/{fps.denominator}', *tags, '-i', 'pipe:0',
           '-frames:v', count, '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'gbrp16le',
           *tags, path, data=planar.tobytes())
        assert ff('-i', path, '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', 'pipe:1') == planar.tobytes()
        source = probe(path)
        media.append(source)
        clips.append(Clip(name, source.id, 0, len(clips) * 8, 1, 8,
                          grade=Grade(exposure=exposure)))
    return Project(width=width, height=height, fps_num=fps.numerator,
                   fps_den=fps.denominator, media=media, clips=clips)


def decode_rgb(path, width, height, *, input_matrix=None):
    override = (['-vf', f'scale=in_color_matrix={input_matrix}:out_color_matrix={input_matrix}']
                if input_matrix else [])
    raw = ff('-i', path, '-map', '0:V:0', '-an', *override,
             '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)


def decoded_timestamps(path):
    raw = subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0', '-show_frames',
         '-show_entries', 'frame=best_effort_timestamp_time,pkt_duration_time,duration_time',
         '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(raw.stdout)['frames']


def decoded_stream_tags(path):
    raw = subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-select_streams', 'v:0', '-show_entries',
         'stream=pix_fmt,color_space,color_range,color_transfer,color_primaries',
         '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(raw.stdout)['streams'][0]


def patch_samples(rgb):
    return np.array([rgb[12:-12, left + 8:left + 24].mean(axis=(0, 1))
                     for left in (0, 32, 64)])


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(str(a[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    settings = QSettings(str(tmp_path / 'reference-settings.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, recovery_root=tmp_path / 'recovery', ui_language='en')
    w.resize(1280, 900)
    w.show()
    app.processEvents()
    w.test_warnings = warnings
    yield w
    w.cancel_jobs()
    for job in list(w.jobs):
        assert job.wait(5000)
    app.processEvents()
    w.dirty = False
    w.closing = True
    w.close()
    w.deleteLater()
    app.processEvents()


def wait_until(app, predicate, *, timeout=15, diagnostic=lambda: ''):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate(), diagnostic()


def observed_preview_matches(window, target=None):
    """A production-accepted frame must also exist in this test's recorder."""
    provenance = window.video.frame_provenance
    if provenance is None or (target is not None and provenance.timeline_frame != target):
        return False
    record = window._reference_test_decoded.get(provenance.timeline_frame)
    return (record is not None
            and record['start_us'] == provenance.start_us
            and record['end_us'] == provenance.end_us
            and record['generation'] == provenance.generation
            and record['revision'] == provenance.revision
            and record['preview_path'] == provenance.preview_path)


def seek_decoded(window, app, frame):
    window.player.pause()
    window.seek_frame(frame)
    wait_until(app, lambda: observed_preview_matches(window, frame),
               diagnostic=lambda: (window.player.errorString(), window.player.position(),
                                   window.video.frame_provenance,
                                   tuple(window._reference_test_decoded)))
    assert not window.video.image.isNull()
    return window.video.frame_provenance


def load_actual_preview(window, app, path):
    revision = window.revision
    preview_path = str(Path(path).expanduser().absolute())
    window.preview_paths[(revision, 'graded')] = path
    decoded = {}
    window._reference_test_decoded = decoded
    replace_player = window.replace_preview_player

    def replace_and_observe():
        result = replace_player()
        # activate_preview sets source/seeks only after this method returns.
        # Subscribe to the newly created sink at this boundary, never after
        # activation: a valid target frame can already have been accepted then.
        generation = window._preview_generation
        def receive(frame):
            if frame.isValid():
                format_ = frame.surfaceFormat()
                decoded[round(Fraction(frame.startTime(), 1_000_000) * window.project.fps)] = {
                    'image': preview_frame_image(frame),
                    'uncorrected_qt_image': frame.toImage().copy(),
                    'start_us': frame.startTime(), 'end_us': frame.endTime(),
                    'pixel_format': str(format_.pixelFormat()),
                    'color_space': str(format_.colorSpace()),
                    'color_range': str(format_.colorRange()),
                    'color_transfer': str(format_.colorTransfer()),
                    'generation': generation, 'revision': revision,
                    'preview_path': preview_path,
                }
        window.video.videoSink().videoFrameChanged.connect(receive)
        return result

    # Scope the test-only hook; restore the original method even on failure.
    with patch.object(window, 'replace_preview_player', replace_and_observe):
        assert window.activate_preview(path, revision, 'graded')
    # Qt's multimedia backend primes its decoder when playback first starts.
    window.player.play()
    wait_until(app, lambda: observed_preview_matches(window),
               diagnostic=lambda: (window.player.errorString(),
                                   window.video.frame_provenance, tuple(decoded)))
    window.player.pause()
    return decoded


def save_evidence(window, name, metadata, *, media_files=()):
    destination = os.environ.get('LUMEN_REFERENCE_ARTIFACTS')
    if not destination:
        return
    folder = Path(destination)
    folder.mkdir(parents=True, exist_ok=True)
    assert window.video.reference.image.save(str(folder / f'{name}-frozen-reference.png'))
    assert window.video.image.save(str(folder / f'{name}-current-decoded.png'))
    assert window.video.grab().save(str(folder / f'{name}-viewer.png'))
    for label, source in media_files:
        shutil.copyfile(source, folder / f'{name}-{label}{Path(source).suffix}')
    (folder / f'{name}-measurements.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)], ids=['30', '29.97'])
def test_real_two_clip_capture_has_decoded_pixels_timestamps_and_immutable_history(window, app, tmp_path, fps):
    w = window
    w.editor = Editor(two_clip_project(tmp_path, fps))
    w.refresh()
    w.select_clip('warm')
    engine = RenderEngine(timeout=90)
    try:
        path = engine.preview(w.project)
        pixels = decode_rgb(path, 96, 64)
        forced_601 = decode_rgb(path, 96, 64, input_matrix='bt601')
        tags = decoded_stream_tags(path)
        assert tags == dict(pix_fmt='yuv420p', color_range='tv', color_space='bt709',
                            color_transfer='bt709', color_primaries='bt709')
        timestamps = decoded_timestamps(path)
        assert len(pixels) == len(timestamps) == 16
        qt_decoded = load_actual_preview(w, app, path)
        seek_decoded(w, app, 3)
        assert w.capture_reference()
        reference = w.video.reference
        assert reference.width == 96 and reference.height == 64
        assert isinstance(reference.pixels, bytes)
        assert len(reference.pixels) == 96 * 64 * 4
        provenance = reference.provenance
        assert provenance.timeline_frame == 3
        assert provenance.revision == w.revision
        assert provenance.preview_mode == 'graded'
        assert Path(provenance.preview_path).resolve() == Path(path).resolve()
        assert abs(provenance.start_us - round(float(timestamps[3]['best_effort_timestamp_time']) * 1e6)) <= 1
        assert abs(provenance.start_us - float(3 / fps * 1_000_000)) <= 1
        assert abs((provenance.end_us - provenance.start_us) - float(1 / fps * 1_000_000)) <= 2
        assert [source.clip_id for source in provenance.active_sources] == ['warm']
        assert provenance.active_sources[0].source_position == Fraction(4)
        samples = patch_samples(rgb_from_qimage(reference.image))
        expected = patch_samples(pixels[3])
        assert np.abs(samples - expected).max() <= 2, 'Corrected BT.709 patch mismatch'
        # Exact source-of-truth is the app's metadata-aware mapped-frame RGB.
        # The uncorrected Qt conversion is retained only for diagnostics.
        np.testing.assert_array_equal(rgb_from_qimage(reference.image),
                                      rgb_from_qimage(qt_decoded[3]['image']))
        assert provenance.start_us == qt_decoded[3]['start_us']
        assert provenance.end_us == qt_decoded[3]['end_us']
        assert np.abs(samples - patch_samples(pixels[11])).max() > 50
        frozen = reference.pixels
        with pytest.raises(FrozenInstanceError):
            reference.width = 1
        detached_image = reference.image
        detached_image.fill(QColor('magenta'))
        assert w.video.reference.pixels == frozen
        np.testing.assert_array_equal(rgb_from_qimage(reference.image),
                                      rgb_from_qimage(qt_decoded[3]['image']))

        w.select_clip('cool')
        current = seek_decoded(w, app, 11)
        assert [source.clip_id for source in current.active_sources] == ['cool']
        assert current.active_sources[0].source_position == Fraction(4)
        np.testing.assert_array_equal(rgb_from_qimage(w.video.image),
                                      rgb_from_qimage(qt_decoded[11]['image']))
        w.video.set_comparison_mode('side_by_side')
        app.processEvents()
        assert w.video.reference is reference
        assert w.video.reference.pixels == frozen
        assert w.video.reference.provenance.timeline_frame == 3

        # Re-rendering a later project revision cannot refresh the frozen still.
        w.project.clips[0].grade.exposure += .7
        w.project.clips[1].grade.exposure -= .4
        w.changed()
        assert not w.capture_reference()
        newer = engine.preview(w.project)
        assert newer != path
        qt_new = load_actual_preview(w, app, newer)
        seek_decoded(w, app, 11)
        new_pixels = decode_rgb(newer, 96, 64)
        np.testing.assert_array_equal(rgb_from_qimage(w.video.image),
                                      rgb_from_qimage(qt_new[11]['image']))
        assert np.abs(patch_samples(new_pixels[11]) - patch_samples(pixels[11])).max() > 10
        assert w.video.reference is reference and reference.pixels == frozen
        assert reference.provenance.revision < w.revision
        independent_difference = np.abs(rgb_from_qimage(reference.image).astype(np.int16)
                                         - pixels[3].astype(np.int16))
        forced_601_difference = np.abs(rgb_from_qimage(reference.image).astype(np.int16)
                                       - forced_601[3].astype(np.int16))
        save_evidence(w, 'reference-' + str(fps).replace('/', '-'), {
            'fps': str(fps), 'reference_frame': 3, 'current_frame': 11,
            'decoded_start_us': provenance.start_us, 'decoded_end_us': provenance.end_us,
            'reference_rgb': samples.tolist(), 'ffmpeg_rgb': expected.tolist(),
            'exact_equality_with_actual_displayed_application_decode': True,
            'independent_ffmpeg_rec709_maximum_patch_difference_8bit_codes': float(np.abs(samples - expected).max()),
            'independent_ffmpeg_rec709_maximum_pixel_difference_8bit_codes': int(independent_difference.max()),
            'independent_ffmpeg_rec709_mean_pixel_difference_8bit_codes': float(independent_difference.mean()),
            'independent_ffmpeg_forced_bt601_rgb': patch_samples(forced_601[3]).tolist(),
            'independent_ffmpeg_forced_bt601_maximum_patch_difference_8bit_codes': float(
                np.abs(samples - patch_samples(forced_601[3])).max()),
            'independent_ffmpeg_forced_bt601_maximum_pixel_difference_8bit_codes': int(forced_601_difference.max()),
            'original_preview_stream_tags': tags,
            'independent_ffmpeg_colorimetric_equivalence_claimed': False,
            'diagnosis': 'Mapped YUV is converted using explicit BT.709/range metadata, bypassing the verified Qt6.8.3 CPU fixed-BT.601 helper.',
            'qt_frame_metadata': {key: value for key, value in qt_decoded[3].items() if key not in ('image', 'uncorrected_qt_image')},
            'reference_revision': provenance.revision, 'current_revision': w.revision,
            'frozen_after_new_grade': reference.pixels == frozen,
        }, media_files=[('original-preview', path), ('new-grade-preview', newer),
                        ('warm-source', w.project.media[0].path), ('cool-source', w.project.media[1].path)])
        assert not w.test_warnings
    finally:
        w.player.stop()
        w.player.setSource(QUrl())
        engine.close()


@pytest.mark.parametrize('profile', ['h264_8bit', 'prores_422_hq_10bit'])
def test_reference_modes_do_not_change_project_render_graph_or_decoded_exports(window, app, tmp_path, profile):
    w = window
    w.editor = Editor(two_clip_project(tmp_path))
    w.refresh()
    engine = RenderEngine(timeout=90)
    suffix = '.mov' if profile.startswith('prores') else '.mp4'
    native_format = 'yuv422p10le' if profile.startswith('prores') else 'yuv420p'
    try:
        project_before = deepcopy(w.project.to_dict())
        state_before = (w.revision, w.dirty, len(w.editor._undo), len(w.editor._redo))
        compiled_before = engine._compile(w.project, export_profile=profile)
        baseline = engine.render(w.project, tmp_path / ('baseline' + suffix), export_profile=profile)
        preview = engine.preview(w.project)
        load_actual_preview(w, app, preview)
        seek_decoded(w, app, 3)
        assert w.capture_reference()
        seek_decoded(w, app, 11)
        for mode in ('side_by_side', 'wipe', 'off', 'wipe'):
            w.video.set_comparison_mode(mode)
            w.video.set_wipe_position(.27)
            assert w.project.to_dict() == project_before
            assert engine._compile(w.project, export_profile=profile) == compiled_before
        assert (w.revision, w.dirty, len(w.editor._undo), len(w.editor._redo)) == state_before
        overlay_enabled = engine.render(w.project, tmp_path / ('reference-visible' + suffix), export_profile=profile)
        # Native decoded planes, not compressed file bytes or metadata-only
        # equality. This includes all frames of the actual 10-bit ProRes path.
        def native_decode(path):
            return ff('-i', path, '-map', '0:V:0', '-an', '-pix_fmt', native_format,
                      '-f', 'rawvideo', 'pipe:1')
        assert native_decode(overlay_enabled) == native_decode(baseline)
        assert ff('-i', overlay_enabled, '-map', '0:a:0', '-vn', '-f', 's16le', 'pipe:1') == ff(
            '-i', baseline, '-map', '0:a:0', '-vn', '-f', 's16le', 'pipe:1')
        saved = tmp_path / 'reference-is-not-project-data.lumen'
        w.project.save(saved)
        assert Project.load(saved).to_dict() == project_before
        assert not w.test_warnings
    finally:
        w.player.stop()
        w.player.setSource(QUrl())
        engine.close()
