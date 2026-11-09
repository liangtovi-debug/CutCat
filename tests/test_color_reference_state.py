"""Interrupted/repeated reference workflows with explicitly timestamped frames.

These are state-machine tests, not decoder tests. Actual media and Qt decoder
acceptance are independently covered in test_color_reference_render.py.
"""
from copy import deepcopy
from fractions import Fraction
import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from lumen.model import Clip, Editor, Media, Project
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(str(a[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    settings = QSettings(str(tmp_path / 'state-settings.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    w = MainWindow(workspace_settings=settings, recovery_root=tmp_path / 'recovery', ui_language='en')
    source = tmp_path / 'synthetic-state-source.mp4'
    source.write_bytes(b'placeholder; decoded QVideoFrames are explicitly injected in state tests')
    media = Media('media', str(source), 'State source', 2., 96, 64, True, False)
    w.editor = Editor(Project(width=96, height=64, media=[media], clips=[
        Clip('first', media.id, 0, 0, 2, 10),
        Clip('second', media.id, 0, 10, 4, 10),
    ]))
    w.refresh()
    w.select_clip('first')
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


def activate(window, tmp_path, name='preview.mp4', mode='graded'):
    path = tmp_path / name
    path.write_bytes(b'placeholder preview for timestamped frame state tests')
    window.preview_paths[(window.revision, mode)] = path
    window.activate_preview(path, window.revision, mode)
    return path


def video_frame(index, fps=Fraction(30), color='#b36128', *, timestamp=True):
    image = QImage(96, 64, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    frame = QVideoFrame(image)
    if timestamp:
        frame.setStartTime(round(index / fps * 1_000_000))
        frame.setEndTime(round((index + 1) / fps * 1_000_000))
    return frame


def deliver(window, index, *, generation=None, color='#b36128'):
    frame = video_frame(index, window.project.fps, color)
    window.video_frame(frame, generation=window._preview_generation if generation is None else generation)
    return frame


def captured(window, tmp_path, index=3):
    activate(window, tmp_path)
    window.seek_frame(index)
    deliver(window, index)
    assert window.capture_reference()
    return window.video.reference


def test_reference_is_not_selection_metadata_and_does_not_edit_history(window, tmp_path):
    w = window
    original = deepcopy(w.project.to_dict())
    history = (w.revision, w.dirty, len(w.editor._undo), len(w.editor._redo))
    reference = captured(w, tmp_path)
    assert reference.provenance.timeline_frame == 3
    assert reference.provenance.active_sources[0].clip_id == 'first'
    assert reference.provenance.active_sources[0].source_position == Fraction(5)
    w.select_clip('second')
    assert w.video.reference is reference
    assert w.capture_reference()
    assert w.video.reference.provenance.active_sources[0].clip_id == 'first'
    for mode in ('side_by_side', 'wipe', 'off'):
        w.video.set_comparison_mode(mode)
        w.video.set_wipe_position(.72)
    w.clear_reference()
    assert w.video.reference is None
    assert w.project.to_dict() == original
    assert (w.revision, w.dirty, len(w.editor._undo), len(w.editor._redo)) == history


def test_seek_rejects_previous_and_out_of_order_frames_before_capture(window, tmp_path):
    w = window
    reference = captured(w, tmp_path)
    generation = w._preview_generation
    w.seek_frame(12)
    assert not w.capture_reference()
    deliver(w, 3, generation=generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    w.seek_frame(15)
    deliver(w, 12, generation=generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    deliver(w, 15, generation=generation, color='#3469ae')
    assert w.capture_reference()
    assert w.video.reference.provenance.timeline_frame == 15
    assert w.video.reference.provenance.active_sources[0].clip_id == 'second'
    assert w.video.reference.pixels != reference.pixels


def test_paused_seek_remains_locked_after_target_arrives(window, tmp_path):
    w = window
    captured(w, tmp_path)
    w.seek_frame(12)
    w.seek_frame(15)
    deliver(w, 15, color='#3469ae')
    assert w.capture_reference()
    reference = w.video.reference
    provenance = w.video.frame_provenance
    for late_frame in (3, 12, 14, 16, 19):
        deliver(w, late_frame, color='#f07215')
        assert w.video.frame_provenance is provenance
        assert w.reference_capture_available()
        assert w.video.reference is reference
    assert w.timeline.playhead == 15


def test_seek_queues_native_slot_instead_of_synchronous_python_binding(window, tmp_path, monkeypatch):
    """Native AudioRenderer teardown must not contend with a GIL-held seek.

    A direct Python setPosition call can hold the GIL while waiting for the
    QAudioOutput connection mutex, whose renderer-thread disconnectNotify
    wrapper is waiting for that same GIL. Dispatching the native Qt slot avoids
    that lock inversion without moving any QObject to another thread.
    """
    w = window
    reference = captured(w, tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(type(w.player), 'setPosition', lambda *_: pytest.fail(
            'Seek must not enter the native binding synchronously from Python'))
        w.seek_frame(12)
    assert w._pending_seek_frame == 12
    assert not w.capture_reference()
    assert w.video.reference is reference
    deliver(w, 12)
    assert w.capture_reference()
    assert w.video.reference.provenance.timeline_frame == 12


@pytest.mark.parametrize('invalid', ['missing-time', 'invalid-frame', 'outside-end', 'negative-start', 'reversed-time'])
def test_undecoded_or_invalid_timestamp_never_replaces_reference(window, tmp_path, invalid):
    w = window
    reference = captured(w, tmp_path)
    w.seek_frame(4)
    frame = video_frame(4, timestamp=invalid != 'missing-time')
    if invalid == 'invalid-frame':
        frame = QVideoFrame()
    elif invalid == 'outside-end':
        frame = video_frame(w.project.duration_frames)
    elif invalid == 'negative-start':
        frame.setStartTime(-10)
    elif invalid == 'reversed-time':
        frame.setEndTime(frame.startTime() - 1)
    w.video_frame(frame, generation=w._preview_generation)
    assert not w.capture_reference()
    assert w.video.reference is reference


def test_old_player_generation_cannot_repopulate_after_new_preview(window, tmp_path):
    w = window
    reference = captured(w, tmp_path)
    old_generation = w._preview_generation
    old_player = w.player
    old_frame = video_frame(3)
    activate(w, tmp_path, 'replacement.mp4')
    assert w._preview_generation != old_generation
    assert w.player is not old_player
    w.seek_frame(3)
    w.video_frame(old_frame, generation=old_generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    deliver(w, 3, color='#163f92')
    assert w.capture_reference()
    assert w.video.reference.pixels != reference.pixels
    current = w.video.reference
    w.video_frame(old_frame, generation=old_generation)
    assert w.video.reference is current
    assert w.video.frame_provenance.generation == w._preview_generation


def test_mode_switch_keeps_frozen_reference_but_refuses_old_after_frames(window, tmp_path, monkeypatch):
    w = window
    reference = captured(w, tmp_path)
    old_generation = w._preview_generation
    monkeypatch.setattr(w, 'build_preview', lambda: None)
    w.change_preview_mode(1)
    assert w.preview_mode == 'bypass'
    deliver(w, 3, generation=old_generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    activate(w, tmp_path, 'before.mp4', mode='bypass')
    w.seek_frame(3)
    deliver(w, 3, color='#596471')
    assert w.capture_reference()
    assert w.video.reference.provenance.preview_mode == 'bypass'
    assert reference.provenance.preview_mode == 'graded'


def test_grade_revision_rejects_late_old_frame_and_keeps_original_reference(window, tmp_path):
    w = window
    reference = captured(w, tmp_path)
    old_generation = w._preview_generation
    w.grade_spins['exposure'].setValue(.4)
    assert w.revision > reference.provenance.revision
    deliver(w, 3, generation=old_generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    assert w.video.reference.provenance.revision < w.revision


@pytest.mark.parametrize('operation', ['new', 'open'])
def test_successful_project_boundary_clears_reference_and_rejects_queued_frame(window, tmp_path, monkeypatch, operation):
    w = window
    captured(w, tmp_path)
    generation = w._preview_generation
    queued = video_frame(3)
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    if operation == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('1280 × 720 · 30 fps', True))
        w.new_project()
    else:
        path = tmp_path / 'opened-project.lumen'
        w.project.save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
        w.open_project()
    assert w.video.reference is None
    assert not w.capture_reference()
    w.video_frame(queued, generation=generation)
    assert not w.capture_reference()
    assert w.video.reference is None


@pytest.mark.parametrize('operation', ['new-dialog', 'open-dialog', 'discard-prompt', 'failed-open'])
def test_canceled_or_failed_project_change_preserves_reference(window, tmp_path, monkeypatch, operation):
    w = window
    reference = captured(w, tmp_path)
    original = deepcopy(w.project.to_dict())
    monkeypatch.setattr(w, 'confirm_discard', lambda: operation != 'discard-prompt')
    if operation == 'new-dialog':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a: ('1280 × 720 · 30 fps', False))
        w.new_project()
    elif operation == 'discard-prompt':
        w.new_project()
    else:
        path = str(tmp_path / 'does-not-exist.lumen') if operation == 'failed-open' else ''
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a: (path, ''))
        w.open_project()
    assert w.video.reference is reference
    assert w.project.to_dict() == original


def test_canceling_new_preview_cannot_publish_late_render_or_replace_reference(window, app, tmp_path, monkeypatch):
    w = window
    reference = captured(w, tmp_path)
    w.grade_spins['exposure'].setValue(.5)
    entered = threading.Event()
    release = threading.Event()
    def render(_engine, snapshot, target, **kwargs):
        entered.set()
        assert release.wait(5)
        return target  # Deliberately emulate an uncancellable late success.
    monkeypatch.setattr('lumen.ui.RenderEngine.render', render)
    w.build_preview()
    try:
        assert entered.wait(2)
        w.cancel_jobs()
        release.set()
        deadline = time.monotonic() + 5
        while w.jobs and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert not w.jobs
        assert w.preview_revision != w.revision
        assert not w.capture_reference()
        assert w.video.reference is reference
        assert (w.revision, w.preview_mode) not in w.preview_paths
    finally:
        release.set()


def test_invalid_end_frame_does_not_erase_frozen_reference(window, tmp_path):
    w = window
    reference = captured(w, tmp_path, index=19)
    assert reference.provenance.timeline_frame == w.project.duration_frames - 1
    w.video_frame(QVideoFrame(), generation=w._preview_generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    w.seek_frame(0)
    deliver(w, 0)
    assert w.capture_reference()
    assert w.video.reference.provenance.timeline_frame == 0


@pytest.mark.parametrize('status', [QMediaPlayer.MediaStatus.EndOfMedia,
                                    QMediaPlayer.MediaStatus.InvalidMedia])
def test_terminal_player_status_rejects_late_same_generation_frames(window, tmp_path, status):
    w = window
    reference = captured(w, tmp_path, index=19)
    generation = w._preview_generation
    w._preview_receiver.status(status)
    assert not w.capture_reference()
    assert w.video.reference is reference
    deliver(w, 19, generation=generation)
    assert not w.capture_reference()
    assert w.video.reference is reference
    activate(w, tmp_path, 'reopened-after-terminal-status.mp4')
    w.seek_frame(1)
    deliver(w, 1)
    assert w.capture_reference()
    assert w.video.reference.provenance.timeline_frame == 1


def test_old_player_position_error_and_end_status_cannot_corrupt_new_capture(window, tmp_path):
    w = window
    captured(w, tmp_path)
    old_receiver = w._preview_receiver
    activate(w, tmp_path, 'current-player.mp4')
    w.seek_frame(12)
    deliver(w, 12, color='#164f9b')
    assert w.capture_reference()
    current = w.video.reference
    provenance = w.video.frame_provenance
    old_receiver.position(0)
    old_receiver.status(QMediaPlayer.MediaStatus.EndOfMedia)
    old_receiver.error(QMediaPlayer.Error.ResourceError, 'Old source failed')
    old_receiver.frame(video_frame(3))
    assert w.timeline.playhead == 12
    assert w.video.reference is current
    assert w.video.frame_provenance is provenance
    assert w.reference_capture_available()
