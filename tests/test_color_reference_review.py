"""Independent adversarial reference-frame review, without source re-rendering.

Synthetic QVideoFrames intentionally exercise queued callbacks and provenance.
Actual decoder/export and geometry checks live in the separate render/UI suites.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from fractions import Fraction
import os
import threading
from types import SimpleNamespace
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, QSettings, QThread, Qt, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QColor, QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from lumen.model import Caption, Clip, Editor, Media, Overlay, Project
from lumen.project_recovery import RecoveryStore
from lumen.timing import ClipTimeMap
from lumen.ui import MainWindow
from lumen.ui_color_reference import (
    MAX_REFERENCE_SIDE, ReferenceStill, frame_provenance,
)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def image(color='red', width=32, height=18):
    result = QImage(width, height, QImage.Format.Format_RGB888)
    result.fill(QColor(color))
    return result


def decoded(number, fps=Fraction(30), color='red'):
    result = QVideoFrame(image(color))
    result.setStartTime(round(Fraction(number * 1_000_000) / fps))
    result.setEndTime(round(Fraction((number + 1) * 1_000_000) / fps))
    return result


def project(path, fps=Fraction(30)):
    return Project(width=32, height=18, fps_num=fps.numerator,
                   fps_den=fps.denominator,
                   media=[Media('media', str(path), 'Timeline picture', 10, 32, 18)],
                   clips=[Clip('base', 'media', 0, 0, 0, 60)])


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(25), Fraction(30),
                                  Fraction(24000, 1001), Fraction(30000, 1001)])
def test_timestamp_quantization_never_relabels_a_decoded_frame(fps, tmp_path):
    p = project(tmp_path / 'picture.mp4', fps)
    for index in (0, 1, 2, 29, 58, 59):
        value = frame_provenance(decoded(index, fps), p, 4, 'proxy.mp4', 'graded', 7)
        assert value is not None and value.timeline_frame == index
        assert value.active_sources[0].source_position == index
        assert value.start_us == round(Fraction(index * 1_000_000) / fps)


@pytest.mark.parametrize('start,end', [(-1, 33333), (0, -1), (0, 0),
                                       (33333, 33332), (1000, 34333),
                                       (0, 66667), (2_000_000, 2_033_333)])
def test_unknown_malformed_and_outside_timestamps_have_no_provenance(tmp_path, start, end):
    frame = QVideoFrame(image())
    frame.setStartTime(start); frame.setEndTime(end)
    assert frame_provenance(frame, project(tmp_path / 'source.mp4'), 0,
                            'proxy.mp4', 'graded', 1) is None


def test_provenance_is_immutable_timeline_source_mix_not_inspected_clip(tmp_path):
    p = project(tmp_path / 'picture.mp4')
    p.media.extend([Media('upper', 'upper.mp4', 'Upper', 10, 32, 18),
                    Media('audio', 'audio.wav', 'Audio', 10, has_video=False, has_audio=True)])
    p.clips.extend([
        Clip('upper', 'upper', 1, 4, Fraction(3, 2), 6,
             time_map=ClipTimeMap(Fraction(3, 2), Fraction(21, 2), Fraction(3, 2))),
        Clip('audio', 'audio', 2, 0, 0, 60),
        Clip('future', 'upper', 1, 15, 0, 5),
    ])
    value = frame_provenance(decoded(7), p, 4, 'proxy.mp4', 'graded', 7)
    assert [source.clip_id for source in value.active_sources] == ['base', 'upper']
    assert [source.source_position for source in value.active_sources] == [7, 6]
    assert value.active_source.clip_id == 'upper'
    frozen = deepcopy(value)
    p.media[1].name = 'Renamed'; p.clips[1].start = 9; p.clips[1].grade.exposure = 2
    assert value == frozen
    with pytest.raises(FrozenInstanceError):
        value.active_sources[0].name = 'Changed'
    boundary = frame_provenance(decoded(10), p, 4, 'proxy.mp4', 'graded', 7)
    assert boundary.active_sources[0].clip_id == 'base'


def test_overlay_and_caption_membership_use_visible_half_open_timeline_intervals(tmp_path):
    p = project(tmp_path / 'picture.mp4')
    p.overlays = [Overlay('later-layer', 'overlay.png', 'a' * 64, 2, 8, z_order=7),
                  Overlay('first-layer', 'overlay.png', 'a' * 64, 2, 8, z_order=-1),
                  Overlay('disabled', 'overlay.png', 'a' * 64, 0, 50, enabled=False),
                  Overlay('ended', 'overlay.png', 'a' * 64, 0, 7)]
    p.captions = [Caption('active', 7, 3, 'visible'), Caption('ended', 0, 7, 'past'),
                  Caption('future', 8, 3, 'future')]
    value = frame_provenance(decoded(7), p, 0, 'proxy.mp4', 'graded', 1)
    assert value.active_overlay_ids == ('first-layer', 'later-layer')
    assert value.active_caption_ids == ('active',)
    p.overlays[0].id = 'changed'; p.captions[0].id = 'changed'
    assert value.active_overlay_ids == ('first-layer', 'later-layer')
    assert value.active_caption_ids == ('active',)


def test_reference_pixels_are_defensive_and_capture_budget_is_enforced(tmp_path, monkeypatch):
    from lumen import ui_color_reference as reference
    p = frame_provenance(decoded(0), project(tmp_path / 'source.mp4'),
                         0, 'proxy.mp4', 'graded', 1)
    rgb = image(width=3, height=2)  # RGB888 scanlines include alignment padding.
    still = ReferenceStill.capture(rgb, p)
    assert len(still.pixels) == 3 * 2 * 4
    rgb.fill(QColor('blue')); returned = still.image; returned.fill(QColor('green'))
    assert still.image.pixelColor(1, 1) == QColor('red')
    with pytest.raises(ValueError):
        ReferenceStill.capture(QImage(), p)
    with pytest.raises(ValueError):
        ReferenceStill.capture(image(width=MAX_REFERENCE_SIDE + 1, height=1), p)
    monkeypatch.setattr(reference, 'MAX_REFERENCE_PIXELS', 9)
    with pytest.raises(ValueError):
        ReferenceStill.capture(image(width=4, height=3), p)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: pytest.fail(str(args[2])))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'),
                                              QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    source = tmp_path / 'source.png'; assert image().save(str(source))
    w.editor = Editor(project(source)); w.refresh()
    # Preserve real signal wiring/generation creation but never open a decoder.
    # Supplying source() explicitly retains the production source-equality gate.
    replace = w.replace_preview_player
    def transport_only():
        replace()
        source_url = [QUrl()]
        w.player.setSource = lambda url: source_url.__setitem__(0, url)
        w.player.source = lambda: source_url[0]
    monkeypatch.setattr(w, 'replace_preview_player', transport_only)
    yield w
    w.dirty = False; w.closing = True; w.close(); w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def activate(w, app, tmp_path, suffix='a'):
    path = tmp_path / f'proxy-{suffix}.mp4'; path.write_bytes(b'fixture, not a video')
    w.activate_preview(path, w.revision, w.preview_mode)
    app.processEvents()
    return path


def deliver(w, app, index, color='red'):
    w.video.videoSink().setVideoFrame(decoded(index, w.project.fps, color))
    app.processEvents()


def test_worker_thread_delivery_is_gui_queued_and_old_source_cannot_overwrite(window, app, tmp_path):
    w = window; activate(w, app, tmp_path)
    old_sink = w.video.videoSink(); old_receiver = w._preview_receiver
    worker = threading.Thread(target=lambda: old_sink.setVideoFrame(decoded(0)))
    worker.start(); worker.join()
    assert w.video.frame_provenance is None  # No UI work in the sending thread.
    activate(w, app, tmp_path, 'b')
    assert w.video.frame_provenance is None and w.video.image.isNull()
    old_receiver.frame(decoded(0, color='magenta'))
    old_receiver.position(1500)
    assert w.video.frame_provenance is None and w.timeline.playhead == 0
    deliver(w, app, 0, 'blue')
    assert w.capture_reference()
    assert w.video.reference.image.pixelColor(0, 0) == QColor('blue')
    assert w.video.reference.provenance.preview_path.endswith('proxy-b.mp4')


def test_repeated_seeks_accept_only_the_latest_exact_decoded_timestamp(window, app, tmp_path):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); frozen = w.video.reference
    w.seek_frame(10); w.seek_frame(20)
    deliver(w, app, 10, 'green')
    assert not w.reference_capture_available()
    assert w.video.frame_provenance is None
    assert w.video.reference is frozen
    deliver(w, app, 20, 'blue')
    assert w.reference_capture_available()
    assert w.video.frame_provenance.timeline_frame == 20
    assert w.capture_reference() and w.video.reference.provenance.timeline_frame == 20


@pytest.mark.parametrize('late_index', [0, 10, 19, 21, 30])
def test_paused_target_cannot_be_replaced_by_late_same_generation_frames(window, app, tmp_path, late_index):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    w.seek_frame(20); deliver(w, app, 20, 'blue')
    assert w.capture_reference(); accepted = w.video.frame_provenance
    assert w.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    deliver(w, app, late_index, 'red')
    assert w.video.frame_provenance == accepted
    assert w.video.image.pixelColor(0, 0) == QColor('blue')
    assert w.capture_reference() and w.video.reference.provenance.timeline_frame == 20


@pytest.mark.parametrize('failure', ['error', 'invalid'])
def test_terminal_decoder_failure_never_recaptures_queued_valid_frame(window, app, tmp_path, failure):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); frozen = w.video.reference
    if failure == 'error':
        w._preview_receiver.error(QMediaPlayer.Error.ResourceError, 'Synthetic transport failed')
    else:
        w._preview_receiver.status(QMediaPlayer.MediaStatus.InvalidMedia)
    deliver(w, app, 0, 'blue')
    assert w.video.image.isNull() and not w.capture_reference()
    assert w.video.reference is frozen
    activate(w, app, tmp_path, 'recovered'); deliver(w, app, 0, 'green')
    assert w.capture_reference()
    assert w.video.reference.image.pixelColor(0, 0) == QColor('green')


def test_project_edit_immediately_invalidates_current_pixels_without_losing_still(window, app, tmp_path):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); frozen = w.video.reference
    w.editor.update_clip('base', grade=deepcopy(w.project.clips[0].grade))
    w.project.clips[0].grade.exposure = .25
    w.changed()
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.video.reference is frozen and not w.reference_capture_available()


def test_real_mainwindow_rejects_missing_timestamp_instead_of_mixing_scopes(window, app, tmp_path):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); frozen = w.video.reference
    decoded_calls = []
    missing = SimpleNamespace(isValid=lambda: True,
                              toImage=lambda: decoded_calls.append(True) or image('blue'))
    w.video_frame(missing, generation=w._preview_generation)
    assert not decoded_calls
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.scopes.data is None and not w.reference_capture_available()
    assert w.video.reference is frozen


def test_rotating_preview_releases_previous_players_and_sinks(window, app, tmp_path):
    w = window
    baseline_players = len(w.findChildren(QMediaPlayer))
    for number in range(12):
        activate(w, app, tmp_path, str(number)); deliver(w, app, 0)
        assert w.capture_reference()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        assert len(w.video.findChildren(QVideoSink)) == 1
        assert len(w.findChildren(QMediaPlayer)) <= baseline_players
    assert isinstance(w.video.reference.pixels, bytes)


def test_play_after_end_of_media_restarts_acceptance_from_first_frame(window, app, tmp_path, monkeypatch):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    w.seek_frame(59); deliver(w, app, 59, 'blue')
    assert w.capture_reference(); frozen = w.video.reference
    w._preview_receiver.status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert not w.reference_capture_available()
    played = []
    monkeypatch.setattr(w.player, 'play', lambda: played.append(True))
    w.toggle_play()
    assert played and not w._preview_ended
    deliver(w, app, 0, 'green')
    assert w.video.frame_provenance.timeline_frame == 0
    assert w.reference_capture_available() and w.video.reference is frozen


@pytest.mark.parametrize('boundary', ['new', 'open', 'recover'])
def test_project_boundaries_drop_reference_and_reject_old_decoder(window, app, tmp_path, monkeypatch, boundary):
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); receiver = w._preview_receiver
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    replacement = Project(name='Replacement', captions=[Caption('new', 0, 4, 'Replacement')])
    if boundary == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
        w.new_project()
    elif boundary == 'open':
        path = tmp_path / 'replacement.lumen'; replacement.save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(path), ''))
        w.open_project()
    else:
        store = RecoveryStore(tmp_path / 'recovery')
        try:
            entry = store.write(replacement, str(uuid.uuid4()))
        finally:
            store.close()
        assert w.restore_recovery(entry)
    app.processEvents()
    receiver.frame(decoded(0))
    assert w.video.reference is None and w.video.frame_provenance is None
    assert w.video.image.isNull() and not w.reference_capture_available()
    assert w.comparison_mode_choice.currentData() == 'off'


class SeekDeliveryProbe(QObject):
    """A typed Qt receiver verifies queue/lifetime semantics, not native decoding."""
    def __init__(self, source, events, parent):
        super().__init__(parent)
        self.url = source
        self.events = events

    def source(self):
        return self.url

    @Slot('qint64')
    def setPosition(self, milliseconds):
        self.events.append(('seek', milliseconds, QThread.currentThread()))

    def stop(self):
        self.events.append(('stop',))

    def setSource(self, source):
        self.url = source

    def setVideoSink(self, sink):
        self.events.append(('sink', sink))

    def setAudioOutput(self, output):
        self.events.append(('audio', output))


def test_seek_request_uses_deferred_qt_delivery_on_gui_thread(window, app, tmp_path, monkeypatch):
    w = window; activate(w, app, tmp_path)
    calls = []
    probe = SeekDeliveryProbe(w.player.source(), calls, w)
    monkeypatch.setattr(w, 'player', probe)
    assert w.request_preview_seek(102)
    assert calls == []
    app.processEvents()
    assert calls == [('seek', 102, app.thread())]


@pytest.mark.parametrize('boundary', ['invalidate', 'source-switch', 'close'])
def test_pending_qt_seek_cannot_run_after_player_retirement(window, app, tmp_path, monkeypatch, boundary):
    w = window; activate(w, app, tmp_path)
    calls = []
    probe = SeekDeliveryProbe(w.player.source(), calls, w)
    monkeypatch.setattr(w, 'player', probe)
    assert w.request_preview_seek(102) and w.request_preview_seek(369)
    assert not calls
    if boundary == 'invalidate':
        w.invalidate_preview_frame(new_source=True)
    elif boundary == 'source-switch':
        activate(w, app, tmp_path, 'successor')
    else:
        w.dirty = False; w.closing = True; w.close()
    app.processEvents()
    assert not any(call[0] == 'seek' for call in calls)
    assert not w.reference_capture_available()


@pytest.mark.parametrize('cleanup', ['active-jobs', 'already-closing-jobs',
                                    'source-cleanup', 'range-cleanup', 'media-status'])
def test_approved_close_retires_native_seeks_before_async_cleanup(window, app, tmp_path, monkeypatch, cleanup):
    from lumen import ui
    w = window; activate(w, app, tmp_path)
    calls = []; unrelated_calls = []
    probe = SeekDeliveryProbe(w.player.source(), calls, w)
    unrelated = SeekDeliveryProbe(QUrl(), unrelated_calls, w)
    job = object()

    def dispatch_pending():
        # A cleanup callback may reenter Qt before closeEvent returns. Only
        # this player's transport queue may be retired, not global events.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)

    def add_cleanup_job(*args, **kwargs):
        w.jobs.add(job)
        dispatch_pending()

    with monkeypatch.context() as patch:
        patch.setattr(w, 'player', probe)
        patch.setattr(w, 'confirm_discard', lambda: True)
        patch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
        patch.setattr(ui, 'invalidate_source_viewer', lambda *args, **kwargs: None)
        patch.setattr(w, 'cancel_range_preview', lambda **kwargs: None)
        patch.setattr(w, 'cancel_jobs', dispatch_pending)
        if cleanup in ('active-jobs', 'already-closing-jobs'):
            w.jobs.add(job)
            w.closing = cleanup == 'already-closing-jobs'
        elif cleanup == 'source-cleanup':
            patch.setattr(ui, 'invalidate_source_viewer', add_cleanup_job)
        elif cleanup == 'range-cleanup':
            patch.setattr(w, 'cancel_range_preview', add_cleanup_job)
        else:
            patch.setattr(w, 'close_media_status', lambda: dispatch_pending() or False)
        # Queue through the production native Qt path before the user-approved
        # close state. already-closing models a second close while jobs drain.
        was_closing = w.closing
        w.closing = False
        assert w.request_preview_seek(102) and w.request_preview_seek(369)
        w.closing = was_closing
        assert QMetaObject.invokeMethod(unrelated, 'setPosition',
                                        Qt.ConnectionType.QueuedConnection, Q_ARG('qint64', 741))
        assert not calls and not unrelated_calls
        try:
            event = QCloseEvent(); w.closeEvent(event)
            dispatch_pending()
            assert not event.isAccepted() and w.closing
            assert not any(call[0] == 'seek' for call in calls)
            assert unrelated_calls == [('seek', 741, app.thread())]
            # Repeated close remains harmless while the same cleanup is pending.
            again = QCloseEvent(); w.closeEvent(again); dispatch_pending()
            assert not again.isAccepted()
            assert not any(call[0] == 'seek' for call in calls)
        finally:
            w.jobs.discard(job)


@pytest.mark.parametrize('decline', ['active-work', 'discard-with-jobs', 'discard-without-jobs'])
def test_declined_close_preserves_native_seek_and_playback(window, app, tmp_path, monkeypatch, decline):
    w = window; activate(w, app, tmp_path)
    calls = []
    probe = SeekDeliveryProbe(w.player.source(), calls, w)
    job = object()
    with monkeypatch.context() as patch:
        patch.setattr(w, 'player', probe)
        patch.setattr(QMessageBox, 'question', lambda *args:
                      QMessageBox.StandardButton.No if decline == 'active-work' else QMessageBox.StandardButton.Yes)
        patch.setattr(w, 'confirm_discard', lambda: False)
        if decline != 'discard-without-jobs':
            w.jobs.add(job)
        assert w.request_preview_seek(102) and w.request_preview_seek(369)
        try:
            event = QCloseEvent(); w.closeEvent(event)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
            assert not event.isAccepted() and not w.closing
            assert calls == [('seek', 102, app.thread()), ('seek', 369, app.thread())]
            assert w.centralWidget().isEnabled() and w.menuBar().isEnabled()
        finally:
            w.jobs.discard(job)


@pytest.mark.parametrize('failure', ['false', 'runtime-error', 'type-error'])
def test_qt_seek_invocation_failure_never_exposes_old_frame(window, app, tmp_path, monkeypatch, failure):
    from lumen import ui_color_reference as reference
    w = window; activate(w, app, tmp_path); deliver(w, app, 0)
    assert w.capture_reference(); still = w.video.reference
    def fail(*args):
        if failure == 'runtime-error':
            raise RuntimeError('Synthetic invoke failure')
        if failure == 'type-error':
            raise TypeError('Synthetic signature failure')
        return False
    monkeypatch.setattr(reference.QMetaObject, 'invokeMethod', fail)
    w.seek_frame(3)
    assert w._preview_failed
    assert w.video.image.isNull() and w.video.frame_provenance is None
    assert w.scopes.data is None and not w.reference_capture_available()
    assert w.video.reference is still
    assert 'seek' in w.statusBar().currentMessage().lower()


@pytest.mark.parametrize('reason', ['closing', 'failed', 'source-mismatch'])
def test_seek_request_refuses_ineligible_player_without_posting(window, app, tmp_path, monkeypatch, reason):
    from lumen import ui_color_reference as reference
    w = window; activate(w, app, tmp_path)
    calls = []
    monkeypatch.setattr(reference.QMetaObject, 'invokeMethod', lambda *args: calls.append(args) or True)
    if reason == 'closing':
        w.closing = True
    elif reason == 'failed':
        w._preview_failed = True
    else:
        w._preview_path = str(tmp_path / 'different-source.mp4')
    assert not w.request_preview_seek(102)
    assert not calls

