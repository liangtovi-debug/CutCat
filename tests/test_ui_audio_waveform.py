"""Headless timeline waveform lifecycle and display-only regression coverage.

Decoding is replaced at the controller boundary, never by an actual decoder:
these tests exercise real Qt ownership/timers, file identities and peak caches.
The gated extractor intentionally ignores cancellation until released, proving
that switching/deleting/hiding/closing does not wait on the GUI thread.
"""
from copy import deepcopy
import os
from pathlib import Path
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from lumen.audio_waveform import identity, peaks_from_samples
from lumen.model import Clip, Editor, Media, Project
from lumen.ui import Timeline
from lumen.ui_audio_waveform import WaveformController


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def peak_fixture(path):
    return peaks_from_samples(np.full(48000, .75, dtype=np.float32),
                              file_identity=identity(path))


class Extractor:
    def __init__(self, *, blocked=False, fail=False):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail = fail
        if not blocked:
            self.release.set()

    def __call__(self, path, cancel):
        # Capture the original identity before the test can mutate the file.
        peaks = peak_fixture(path)
        self.calls.append((path, cancel, peaks))
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError('Test did not release its fake extractor')
        if self.fail:
            raise ValueError('Unsupported or invalid audio')
        return peaks


def poll_until(app, controller, condition, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        controller.poll()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError('Waveform controller did not reach the expected state')


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'source.wav'
    path.write_bytes(b'identity-only audio fixture')
    return str(path)


@pytest.fixture
def controllers(app):
    live = []

    def make(*, blocked=False, fail=False):
        owner = QWidget()
        changed = []
        extractor = Extractor(blocked=blocked, fail=fail)
        controller = WaveformController(owner, lambda: changed.append(threading.get_ident()),
                                        extractor=extractor)
        live.append((owner, controller, extractor))
        return owner, controller, extractor, changed

    yield make
    for owner, controller, extractor in live:
        if isValid(controller):
            controller.close()
        extractor.release.set()
        if controller.worker is not None:
            controller.worker.join(2)
            assert not controller.worker.is_alive()
        if isValid(owner):
            owner.close()
            owner.deleteLater()
    app.processEvents()


def test_duplicate_requests_share_one_decode_and_gui_thread_notification(app, controllers, source):
    _, controller, extractor, changed = controllers(blocked=True)
    project = object()
    controller.request([source, source, source], project)
    assert extractor.started.wait(1)
    first_worker = controller.worker
    for _ in range(8):
        controller.request([source, source], project)
    assert len(extractor.calls) == 1 and controller.worker is first_worker
    assert list(controller.desired) == [source]
    assert changed and set(changed) == {threading.get_ident()}
    extractor.release.set()
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    assert set(changed) == {threading.get_ident()}
    for _ in range(8):
        controller.request([source], project)
    assert len(extractor.calls) == 1


@pytest.mark.parametrize('transition', ['project', 'delete', 'suspend', 'close'])
def test_pending_work_cancel_is_nonblocking_and_late_result_is_discarded(
        app, controllers, source, transition):
    _, controller, extractor, changed = controllers(blocked=True)
    project = object()
    controller.request([source], project)
    assert extractor.started.wait(1)
    worker = controller.worker
    generation = controller.generation
    key = identity(source)
    started = time.monotonic()
    if transition == 'project':
        controller.request([], object())
    elif transition == 'delete':
        controller.request([], project)
    elif transition == 'suspend':
        controller.suspend()
    else:
        controller.close()
    assert time.monotonic() - started < .5
    assert worker.is_alive(), 'Transition must return before a blocked decoder exits'
    assert extractor.calls[0][1].is_set()
    # Simulate success already enqueued at the cancellation boundary, in
    # addition to the real fake worker's eventual result.
    changed.clear()
    controller.results.put((generation, source, key, 'ready', extractor.calls[0][2]))
    controller.poll()
    assert changed == [] and controller.get(source) is None
    assert controller.cache.get(key) is None
    extractor.release.set()
    worker.join(1)
    assert not worker.is_alive()
    controller.poll()
    assert changed == [] and controller.cache.get(key) is None
    if transition == 'close':
        controller.request([source], object())
        assert controller.closed and not controller.timer.isActive()
        assert len(extractor.calls) == 1


def test_project_switch_with_same_source_rejects_old_generation(app, controllers, source):
    _, controller, extractor, changed = controllers(blocked=True)
    controller.request([source], object())
    assert extractor.started.wait(1)
    old_generation = controller.generation
    old_key = identity(source)
    new_project = object()
    controller.request([source], new_project)
    changed.clear()
    controller.results.put((old_generation, source, old_key, 'ready', extractor.calls[0][2]))
    controller.poll()
    assert controller.get(source) is None and changed == []
    assert extractor.calls[0][1].is_set()
    assert len(extractor.calls) == 1  # Never run two decoders concurrently.
    extractor.release.set()
    poll_until(app, controller, lambda: controller.worker is None)
    controller.request([source], new_project)
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    assert len(extractor.calls) == 2
    assert set(changed) == {threading.get_ident()}


def test_destroyed_owner_cancels_worker_without_cross_thread_callback(app, controllers, source):
    owner, controller, extractor, changed = controllers(blocked=True)
    controller.request([source], object())
    assert extractor.started.wait(1)
    worker = controller.worker
    changed.clear()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(owner) and not isValid(controller)
    assert extractor.calls[0][1].is_set() and worker.is_alive()
    extractor.release.set()
    worker.join(1)
    app.processEvents()
    assert not worker.is_alive() and changed == []


def test_failures_do_not_retry_until_identity_or_context_changes(app, controllers, source):
    _, controller, extractor, changed = controllers(fail=True)
    project = object()
    controller.request([source], project)
    poll_until(app, controller, lambda: identity(source) in controller.failed and controller.worker is None)
    for _ in range(10):
        controller.request([source, source], project)
        controller.poll()
    poll_until(app, controller, lambda: controller.worker is None)
    assert len(extractor.calls) == 1 and controller.status(source) == 'unavailable'
    Path(source).write_bytes(b'changed source with a different size')
    controller.request([source], project)
    poll_until(app, controller, lambda: len(extractor.calls) == 2 and controller.worker is None)
    assert identity(source) in controller.failed
    controller.request([source], object())
    poll_until(app, controller, lambda: len(extractor.calls) == 3 and controller.worker is None)
    assert set(changed) == {threading.get_ident()} and controller.get(source) is None


def test_missing_source_is_skipped_and_can_be_relinked(app, controllers, tmp_path):
    _, controller, extractor, _ = controllers()
    source = str(tmp_path / 'not-here.wav')
    project = object()
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.status(source) == 'unavailable' and controller.worker is None)
    assert controller.desired == {} and not extractor.calls
    Path(source).write_bytes(b'now available')
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    assert len(extractor.calls) == 1


def test_source_mutation_invalidates_cached_identity_on_rescan(app, controllers, source):
    _, controller, extractor, changed = controllers()
    project = object()
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    old_peaks = controller.get(source)
    Path(source).write_bytes(b'replacement with different size and contents')
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.get(source) is not None
               and controller.get(source).identity != old_peaks.identity and controller.worker is None)
    assert len(extractor.calls) == 2
    assert controller.get(source).identity == identity(source)


def test_relink_cancels_inflight_old_path_and_discards_late_result(
        app, controllers, source, tmp_path):
    _, controller, extractor, changed = controllers(blocked=True)
    project = object()
    controller.request([source], project)
    assert extractor.started.wait(1)
    old_key, old_generation = identity(source), controller.generation
    replacement = tmp_path / 'relinked.wav'
    replacement.write_bytes(b'relinked source')
    new_source = str(replacement)
    controller.request([new_source], project)
    assert extractor.calls[0][1].is_set() and controller.get(new_source) is None
    changed.clear()
    controller.results.put((old_generation, source, old_key, 'ready', extractor.calls[0][2]))
    controller.poll()
    assert changed == [] and controller.cache.get(old_key) is None
    extractor.release.set()
    poll_until(app, controller, lambda: controller.worker is None)
    controller.request([new_source], project)
    poll_until(app, controller, lambda: controller.get(new_source) is not None and controller.worker is None)
    assert len(extractor.calls) == 2
    assert controller.get(new_source).identity == identity(new_source)
    assert controller.cache.get(old_key) is None


def test_slow_identity_lookup_and_cancellation_do_not_block_gui(
        app, controllers, source, monkeypatch):
    import lumen.ui_audio_waveform as module
    _, controller, extractor, changed = controllers()
    entered, release = threading.Event(), threading.Event()
    thread_ids = []

    def slow_identity(path):
        thread_ids.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return identity(path)

    monkeypatch.setattr(module, 'identity', slow_identity)
    started = time.monotonic()
    try:
        controller.request([source], object())
        assert time.monotonic() - started < .5
        assert entered.wait(1)
        assert thread_ids == [controller.worker.ident]
        assert thread_ids[0] != threading.get_ident()
        started = time.monotonic()
        controller.request([], object())
        controller.close()
        assert time.monotonic() - started < .5
        assert controller.worker.is_alive()
        assert not extractor.calls
    finally:
        release.set()


def test_visible_batch_is_bounded_and_ready_sources_do_not_decode_again(app, controllers, tmp_path):
    from lumen.ui_audio_waveform import MAX_VISIBLE_SOURCES
    _, controller, extractor, _ = controllers()
    paths = []
    for index in range(MAX_VISIBLE_SOURCES + 1):
        path = tmp_path / f'source-{index}.wav'
        path.write_bytes(str(index).encode())
        paths.append(str(path))
    project = object()
    controller.request(paths, project)
    poll_until(app, controller, lambda: controller.worker is None)
    assert len(extractor.calls) == MAX_VISIBLE_SOURCES
    assert all(controller.get(path) is not None for path in paths[:-1])
    assert controller.get(paths[-1]) is None and controller.status(paths[-1]) == 'limited'
    assert len(controller.cache) <= MAX_VISIBLE_SOURCES
    assert controller.cache.nbytes <= controller.cache.max_bytes
    for _ in range(4):
        controller.request(paths, project)
        poll_until(app, controller, lambda: controller.worker is None)
    assert len(extractor.calls) == MAX_VISIBLE_SOURCES


def test_deleted_cached_source_is_invalidated_without_decoder_retry(app, controllers, source):
    _, controller, extractor, _ = controllers()
    project = object()
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    Path(source).unlink()
    controller.request([source], project)
    poll_until(app, controller, lambda: controller.worker is None)
    assert controller.get(source) is None and controller.status(source) == 'unavailable'
    assert len(extractor.calls) == 1


def test_ready_source_is_shared_across_project_contexts(app, controllers, source):
    _, controller, extractor, _ = controllers()
    controller.request([source], object())
    poll_until(app, controller, lambda: controller.get(source) is not None and controller.worker is None)
    cached = controller.get(source)
    controller.request([source], object())
    poll_until(app, controller, lambda: controller.get(source) is cached and controller.worker is None)
    assert len(extractor.calls) == 1


@pytest.fixture
def timeline(app, source):
    widget = Timeline()
    extractor = Extractor()
    widget.waveforms.extractor = extractor
    media = Media('audio', source, 'Test source', 1, has_video=False, has_audio=True)
    clip = Clip('clip', 'audio', 2, 0, 0, 30)
    project = Project(media=[media], clips=[clip])
    widget.set_project(project)
    widget.resize(800, 390)
    widget.show()
    poll_until(app, widget.waveforms,
               lambda: widget.waveforms.get(source) is not None and widget.waveforms.worker is None)
    yield widget, extractor, project
    widget.close()
    extractor.release.set()
    if widget.waveforms.worker is not None:
        widget.waveforms.worker.join(2)
        assert not widget.waveforms.worker.is_alive()
    widget.deleteLater()
    app.processEvents()


def test_zoom_repaint_and_selection_do_not_decode_or_change_model(timeline, app, source):
    widget, extractor, project = timeline
    editor = Editor(project)
    before, undo = deepcopy(project), deepcopy(editor._undo)
    for scale in (.5, 1, 4, 12, 4):
        widget.scale = scale
        widget.set_project(project, 'clip')
        widget.grab()
        widget.refresh_waveforms()
        app.processEvents()
    assert len(extractor.calls) == 1
    assert project == before and editor._undo == undo
    assert widget.selection == 'clip'
    assert widget.waveforms.get(source) is not None


class RecordingPainter:
    def __init__(self):
        self.lines = []
        self.color = None
        self.clip = None
        self.saves = 0
        self.texts = []
        self.metrics = None

    def save(self):
        self.saves += 1

    def restore(self):
        self.saves -= 1

    def setClipRect(self, rect):
        self.clip = rect

    def setPen(self, pen):
        self.color = QColor(pen) if isinstance(pen, QColor) else pen.color()

    def fontMetrics(self):
        return self.metrics

    def drawText(self, *args):
        self.texts.append(args[-1])

    def drawLine(self, start, end):
        self.lines.append((start, end))


def test_muting_dims_waveform_without_changing_geometry_or_hit_area(timeline, app):
    widget, extractor, project = timeline
    clip, media = project.clips[0], project.media[0]
    rectangle = widget.rect_for(clip)
    selected = []
    widget.selected.connect(selected.append)
    normal = RecordingPainter()
    before = deepcopy(project)
    widget.paint_source_waveform(normal, clip, media, rectangle)
    assert project == before and normal.lines and normal.saves == 0
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     pos=rectangle.adjusted(8, 25, -8, -5).center().toPoint())
    clip.muted = True
    muted = RecordingPainter()
    before = deepcopy(project)
    widget.paint_source_waveform(muted, clip, media, rectangle)
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton,
                     pos=rectangle.adjusted(8, 25, -8, -5).center().toPoint())
    widget.grab()
    app.processEvents()
    assert muted.color.lightnessF() < normal.color.lightnessF()
    assert muted.lines == normal.lines and muted.clip == normal.clip
    assert widget.rect_for(clip) == rectangle and selected == ['clip', 'clip']
    assert project == before and len(extractor.calls) == 1


def test_waveform_render_does_not_apply_gain_or_fades(timeline):
    widget, _, project = timeline
    clip, media = project.clips[0], project.media[0]
    normal = RecordingPainter()
    widget.paint_source_waveform(normal, clip, media, widget.rect_for(clip))
    clip.gain_db, clip.fade_in, clip.fade_out = -18, 10, 7
    before = deepcopy(project)
    adjusted = RecordingPainter()
    widget.paint_source_waveform(adjusted, clip, media, widget.rect_for(clip))
    assert adjusted.lines == normal.lines and adjusted.color == normal.color
    assert project == before
    assert 'pre-gain' in widget.toolTip() and 'pre-fade' in widget.toolTip()


def test_hide_show_suspends_then_reuses_ready_cache(timeline, app, source):
    widget, extractor, project = timeline
    cached = widget.waveforms.get(source)
    widget.hide()
    app.processEvents()
    assert not widget.waveform_refresh.isActive()
    assert widget.waveforms.desired == {} and not widget.waveforms.closed
    widget.show()
    app.processEvents()
    widget.refresh_waveforms()
    assert widget.waveform_refresh.isActive()
    poll_until(app, widget.waveforms,
               lambda: widget.waveforms.get(source) is cached and widget.waveforms.worker is None)
    assert widget.waveforms.get(source) is cached
    assert len(extractor.calls) == 1


def test_hidden_timeline_never_starts_extraction(app, source):
    widget = Timeline()
    extractor = Extractor()
    widget.waveforms.extractor = extractor
    media = Media('a', source, 'Source', 1, has_audio=True)
    project = Project(media=[media], clips=[Clip('clip', 'a', 0, 0, 0, 30)])
    try:
        widget.set_project(project)
        widget.refresh_waveforms()
        assert not widget.isVisible() and not extractor.calls
        assert widget.waveforms.desired == {}
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


def test_timeline_deletion_and_project_switch_clear_visible_waveforms(timeline, app, source):
    widget, extractor, project = timeline
    before = deepcopy(project)
    project.clips.clear()
    widget.set_project(project)
    assert widget.waveforms.get(source) is None and widget.waveforms.desired == {}
    replacement = Project()
    widget.set_project(replacement)
    app.processEvents()
    assert widget.waveforms.context is replacement
    assert widget.waveforms.desired == {} and len(extractor.calls) == 1
    assert project.media == before.media


def test_visible_audio_only_and_embedded_sources_requested_not_silent_or_missing(app, tmp_path):
    widget = Timeline()
    requested = []
    widget.waveforms.request = lambda paths, context: requested.append((paths, context))
    paths = []
    for name in ('embedded', 'audio', 'silent', 'offscreen'):
        path = tmp_path / (name + '.wav')
        path.write_bytes(name.encode())
        paths.append(str(path))
    media = [Media('video', paths[0], 'Video with audio', 100, has_audio=True),
             Media('audio', paths[1], 'Audio', 100, has_video=False, has_audio=True),
             Media('silent', paths[2], 'Silent video', 100, has_audio=False),
             Media('away', paths[3], 'Outside viewport', 100, has_audio=True)]
    project = Project(media=media, clips=[Clip('v', 'video', 0, 0, 0, 30),
                                         Clip('a', 'audio', 2, 0, 0, 30),
                                         Clip('s', 'silent', 1, 0, 0, 30),
                                         Clip('missing', 'missing', 3, 0, 0, 30),
                                         Clip('far', 'away', 3, 10000, 0, 30)])
    # Keep the fixed widget viewport independent of project minimum width.
    widget.project = project
    widget.resize(800, 390)
    try:
        widget.show()
        app.processEvents()
        widget.refresh_waveforms()
        assert requested[-1] == ([paths[0], paths[1]], project)
    finally:
        widget.close()
        widget.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('status,label', [('loading', 'Waveform loading…'),
                                         ('unavailable', 'Waveform unavailable'),
                                         ('limited', 'Waveform view limit')])
def test_loading_failure_and_limit_have_explicit_display_only_labels(timeline, monkeypatch, status, label):
    widget, _, project = timeline
    widget.scale = 12
    monkeypatch.setattr(widget.waveforms, 'get', lambda _: None)
    monkeypatch.setattr(widget.waveforms, 'status', lambda _: status)
    before = deepcopy(project)
    painter = RecordingPainter()
    painter.metrics = QFontMetrics(widget.font())
    clip, media = project.clips[0], project.media[0]
    widget.paint_source_waveform(painter, clip, media, widget.rect_for(clip))
    assert painter.texts == [label]
    assert not painter.lines and painter.saves == 0
    assert project == before


@pytest.mark.parametrize('action', ['hide', 'close', 'delete', 'project'])
def test_timeline_hooks_cancel_pending_work_without_waiting(app, source, action):
    widget = Timeline()
    extractor = Extractor(blocked=True)
    widget.waveforms.extractor = extractor
    media = Media('audio', source, 'Source', 1, has_video=False, has_audio=True)
    project = Project(media=[media], clips=[Clip('clip', 'audio', 2, 0, 0, 30)])
    widget.set_project(project)
    widget.resize(800, 390)
    try:
        widget.show()
        assert extractor.started.wait(1)
        worker = widget.waveforms.worker
        started = time.monotonic()
        if action == 'hide':
            widget.hide()
        elif action == 'close':
            widget.close()
        elif action == 'delete':
            project.clips.clear()
            widget.set_project(project)
        else:
            widget.set_project(Project())
        assert time.monotonic() - started < .5
        assert worker.is_alive() and extractor.calls[0][1].is_set()
        assert widget.waveforms.get(source) is None
        if action in ('hide', 'close'):
            assert not widget.waveform_refresh.isActive()
        extractor.release.set()
        worker.join(1)
        widget.waveforms.poll()
        assert widget.waveforms.get(source) is None
        assert len(extractor.calls) == 1
    finally:
        widget.close()
        extractor.release.set()
        if widget.waveforms.worker is not None:
            widget.waveforms.worker.join(2)
            assert not widget.waveforms.worker.is_alive()
        widget.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('fail', [False, True])
def test_distinct_path_spellings_share_identity_decode_and_failure_guard(
        app, controllers, source, fail):
    _, controller, extractor, _ = controllers(fail=fail)
    path = Path(source)
    alias = str(path.parent) + os.sep + '.' + os.sep + path.name
    assert alias != source and identity(alias) == identity(source)
    controller.request([source, alias], object())
    poll_until(app, controller, lambda: controller.worker is None)
    assert len(extractor.calls) == 1
    if fail:
        assert controller.status(source) == controller.status(alias) == 'unavailable'
    else:
        assert controller.get(source) is controller.get(alias)
        assert controller.get(source) is not None
