"""Real saved selection → reopen → recall → source edit → rendered-media oracles.

All inputs are small synthetic numbered-frame + stereo AAC MOVs. The UI uses
its actual owned SourcePreviewBackend (no mocked verifier), and both outputs of
the unchanged renderer graph are consumed. Expected IDs come from original
arrays and expected PCM from an independent from-origin normalization, not from
saved-range or editing-plan code. Linux/offscreen tests are not native releases.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import time
import wave

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.render import RenderEngine
from lumen.source_viewer import SourcePreviewBackend, SourceViewerSession
from lumen.ui import MainWindow
from test_exact_audio_cache_backend import origin_oracle, production_pcm, selected_oracle
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                               ff, frame_id_rgb)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def pump(app, condition, timeout=45):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.003)
    app.processEvents()
    assert condition(), 'Owned saved-range/source-edit work did not reach completion'


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def numbered_aac(path, fps, offset, origin):
    """Exact-rate lossless PNG frames with independently varied AAC channels."""
    pixels = frame_id_rgb(80)
    rate = 44100
    count = math.ceil(Fraction(len(pixels) * rate, 1) / fps)
    rng = np.random.default_rng(10671)
    samples = rng.integers(-7000, 7001, (count, 2), dtype=np.int16)
    samples[::317, 0] = 22000
    samples[37::271, 1] = -22000
    sound = path.with_suffix('.wav')
    with wave.open(str(sound), 'wb') as stream:
        stream.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(samples.astype('<i2').tobytes())
    picture = path.with_suffix('.picture.mov')
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}',
       '-r', str(fps), '-i', 'pipe:0', '-an', '-r', str(fps),
       '-fps_mode', 'cfr', '-c:v', 'png', '-pix_fmt', 'rgb48be', '-threads', 1,
       '-video_track_timescale', fps.numerator * 3000,
       '-color_range', 'pc', '-color_primaries', 'bt709', '-color_trc', 'bt709',
       '-colorspace', 'rgb', picture, data=pixels.astype('<u2').tobytes())
    ff('-copyts', '-itsoffset', origin, '-i', picture,
       '-itsoffset', origin + offset, '-i', sound, '-map', '0:v:0', '-map', '1:a:0',
       '-c:v', 'copy', '-c:a', 'aac', '-threads', 1,
       '-video_track_timescale', fps.numerator * 3000, path)
    picture.unlink()
    sound.unlink()
    # Exact numbered sources are proved before the render oracle uses them.
    np.testing.assert_array_equal(decode(path), pixels)
    media = probe(path)
    assert media.has_video and media.has_audio
    assert Fraction(media.fps_num, media.fps_den) == fps
    if offset:
        assert (media.audio_offset > 0) == (offset > 0)
        assert abs(media.audio_offset) > .05
    return media, pixels


@pytest.fixture(scope='module')
def range_media(tmp_path_factory):
    root = tmp_path_factory.mktemp('saved-range-numbered-aac')
    cases = ((Fraction(25), .137, 5), (Fraction(25), -.113, 5),
             (Fraction(30000, 1001), 0, 0))
    return {case: numbered_aac(root / f'{case[0].numerator}-{case[1]}.mov', *case)
            for case in cases}


def project_for(media, fps):
    return Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                   fps_den=fps.denominator, media=[deepcopy(media)], clips=[
                       Clip('old', media.id, 0, 0, 0, 20),
                       Clip('marker', media.id, 0, 30, 40, 5)])


def old_picture(pixels):
    return np.concatenate([pixels[:20], np.zeros((10, HEIGHT, WIDTH, 3), np.uint16),
                           pixels[40:45]])


def boundary(frame, fps):
    value = Fraction(frame * 48000, 1) / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def old_pcm(full, session):
    silence = bytes((boundary(30, session.fps) - boundary(20, session.fps)) * 8)
    return (selected_oracle(full, session, 0, 20) + silence
            + selected_oracle(full, session, 40, 45))


def checked_manual_edit(project, mode):
    """Ordinary manually marked baseline, without saved ranges in its input."""
    manual = Editor(deepcopy(project))
    session = SourceViewerSession(manual.project, manual.project.media[0].id)
    session.seek(30); session.mark_in(); session.seek(39); session.mark_out()
    assert session.selection() == (30, 40)
    planner = getattr(manual, 'plan_source_' + mode)
    apply = getattr(manual, 'apply_source_' + mode)
    backend = SourcePreviewBackend()
    try:
        identity = backend.inspect_frame(session).identity
        plan = planner(session.media_id, 0, 10, *session.selection())
        assert getattr(backend, 'verify_' + mode + '_sources')(
            session, identity, manual.project, plan)
        apply(plan)
    finally:
        backend.close()
    return manual.project


@pytest.mark.parametrize('mode', ['insert', 'overwrite'])
@pytest.mark.parametrize('fps,offset,origin', [
    (Fraction(25), .137, 5), (Fraction(25), -.113, 5),
    (Fraction(30000, 1001), 0, 0),
], ids=['positive-delayed-aac', 'negative-delayed-aac', 'fractional-exact-grid'])
def test_real_save_close_reopen_recall_ui_and_manual_marks_match_frames_and_pcm(
        tmp_path, range_media, app, monkeypatch, record_property, mode, fps, offset, origin):
    media, pixels = range_media[(fps, offset, origin)]
    original_source_digest = hashlib.sha256(Path(media.path).read_bytes()).hexdigest()
    base = project_for(media, fps)
    session = SourceViewerSession(base, media.id)
    full = origin_oracle(media.path)
    original_pcm = old_pcm(full, session)
    original_frames = old_picture(pixels)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(str(args[-1])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: warnings.append(str(args[-1])))
    window = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                        recovery_root=tmp_path / 'recovery', ui_language='en')
    window.editor = Editor(deepcopy(base))
    window.refresh(); window.bin.setCurrentRow(0); window.show(); app.processEvents()
    engine = RenderEngine(timeout=45)
    try:
        assert_delivery_frames(decode(engine.preview(base), rgb16=False), original_frames)
        assert production_pcm(engine, base, tmp_path, 'before') == original_pcm
        dialog = window.open_source_viewer()
        assert dialog is not None and type(dialog.backend) is SourcePreviewBackend
        pump(app, lambda: not window.jobs)
        assert not dialog.canvas.image.isNull(), dialog.status.text()
        dialog.seek(30); dialog.mark_in(); dialog.seek(39); dialog.mark_out()
        dialog.saved_range_name.setText('Reusable <take> 可复用')
        save_revision = window.editor.revision
        dialog.save_range_button.click()
        pump(app, lambda: not window.jobs)
        assert window.editor.revision == save_revision + 1, dialog.status.text()
        assert len(window.project.source_ranges) == 1, warnings
        saved = window.project.source_ranges[0]
        assert (saved.in_seconds, saved.out_seconds) == (Fraction(30, 1) / fps, Fraction(40, 1) / fps)
        metadata = deepcopy(window.project)
        assert metadata.clips == base.clips
        # Metadata-only saving is independently rendered, not inferred from a
        # compile key or from dataclass equality.
        assert_delivery_frames(decode(engine.preview(metadata), rgb16=False), original_frames)
        assert production_pcm(engine, metadata, tmp_path, 'metadata') == original_pcm
        window.undo(); assert window.project == base
        window.redo(); assert window.project == metadata
        dialog.reject(); pump(app, lambda: not window.jobs)
        assert window.source_viewer_dialog is None
        window.project_path = tmp_path / 'ranges-before-edit.lumen'
        assert window.save_project(), warnings
        assert Project.load(window.project_path) == metadata
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kw: (str(window.project_path), ''))
        window.open_project(); pump(app, lambda: not window.jobs)
        assert window.project == metadata and window.source_viewer_dialog is None
        window.bin.setCurrentRow(0)
        dialog = window.open_source_viewer(); pump(app, lambda: not window.jobs)
        assert dialog.session.selection() != (30, 40)
        dialog.saved_range_choice.setCurrentIndex(dialog.saved_range_choice.findData(saved.id))
        assert 'Unverified this session' in dialog.saved_range_note.text()
        recall_revision = window.editor.revision
        recall_project = deepcopy(window.project)
        dialog.recall_range_button.click(); pump(app, lambda: not window.jobs)
        assert dialog.session.selection() == (30, 40), dialog.status.text()
        assert window.editor.revision == recall_revision and window.project == recall_project
        assert dialog.plan is None and not dialog.apply_button.isEnabled()
        dialog.mode_choice.setCurrentIndex(dialog.mode_choice.findData(mode))
        window.timeline.playhead = 10; dialog.capture_playhead()
        dialog.preview_button.click(); pump(app, lambda: not window.jobs)
        assert dialog.plan is not None and dialog.apply_button.isEnabled(), dialog.status.text()
        apply_revision = window.editor.revision
        dialog.apply_button.click(); dialog.apply_button.click(); dialog.apply_source_edit()
        pump(app, lambda: not window.jobs)
        assert window.editor.revision == apply_revision + 1, dialog.status.text()
        assert dialog.plan is None and not dialog.apply_button.isEnabled()
        assert window.project.source_ranges == [saved]
        after = deepcopy(window.project)
        manual = checked_manual_edit(base, mode)
        actual_frames = decode(engine.preview(after), rgb16=False)
        manual_frames = decode(engine.preview(manual), rgb16=False)
        cut, finish = boundary(10, fps) * 8, boundary(20, fps) * 8
        selected_pcm = selected_oracle(full, session, 30, 40)
        if mode == 'insert':
            expected_frames = np.concatenate([original_frames[:10], pixels[30:40], original_frames[10:]])
            expected_pcm = original_pcm[:cut] + selected_pcm + original_pcm[cut:]
        else:
            expected_frames = original_frames.copy(); expected_frames[10:20] = pixels[30:40]
            expected_pcm = original_pcm[:cut] + selected_pcm + original_pcm[finish:]
        assert_delivery_frames(actual_frames, expected_frames)
        np.testing.assert_array_equal(actual_frames, manual_frames)
        actual_pcm = production_pcm(engine, after, tmp_path, 'saved-recalled')
        assert actual_pcm == expected_pcm
        assert production_pcm(engine, manual, tmp_path, 'manual') == expected_pcm
        window.undo(); assert window.project == metadata
        window.redo(); assert window.project == after
        dialog.apply_source_edit(); assert window.project == after and not window.jobs
        dialog.reject(); pump(app, lambda: not window.jobs)
        window.project_path = tmp_path / 'ranges-after-edit.lumen'
        assert window.save_project()
        window.open_project(); pump(app, lambda: not window.jobs)
        assert window.project == after and window.source_viewer_dialog is None
        assert_delivery_frames(decode(engine.preview(window.project), rgb16=False), expected_frames)
        assert production_pcm(engine, window.project, tmp_path, 'reopened-after') == expected_pcm
        # Existing timeline clips are not linked to mutable saved metadata.
        clips = deepcopy(window.project.clips)
        window.editor.rename_source_range(saved.id, 'Changed name 改名')
        window.editor.delete_source_range(saved.id)
        assert window.project.clips == clips
        assert production_pcm(engine, window.project, tmp_path, 'deleted-range') == expected_pcm
        assert_delivery_frames(decode(engine.preview(window.project), rgb16=False), expected_frames)
        assert not warnings, warnings
        assert hashlib.sha256(Path(media.path).read_bytes()).hexdigest() == original_source_digest
        for key, value in {'real_owned_backend': True, 'mode': mode, 'fps': str(fps),
                           'source_audio_offset': media.audio_offset, 'source_origin': origin,
                           'output_frames': len(expected_frames), 'pcm_bytes': len(actual_pcm),
                           'pcm_sha256': hashlib.sha256(actual_pcm).hexdigest(),
                           'source_sha256': original_source_digest}.items():
            record_property(key, value)
    finally:
        engine.close()
        if window.source_viewer_dialog is not None:
            window.source_viewer_dialog.reject()
        window.cancel_jobs(); pump(app, lambda: not window.jobs)
        window.dirty = False; window.closing = True; window.close(); window.deleteLater(); app.processEvents()
