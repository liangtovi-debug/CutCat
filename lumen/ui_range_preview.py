"""Session-only, bounded rendered A/V playback with worker-owned media files.

Opening never renders; ordinary Build waits for Play. The explicit selection
command may build and loop. One registered job and one latest request are retained. Sources are rechecked before publication, every explicit
Play and every loop, and periodically during playback. Rechecks are asynchronous
freshness checks, not an operating-system guarantee against instantaneous edits.
No project, history, full-preview cache, scopes or reference still is written.
"""
from __future__ import annotations

import copy
from fractions import Fraction
import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout

from .i18n import tr, tr_error
from .i18n_catalog_range_preview import ERROR_ZH_CN
from .i18n_widgets import QCheckBox, QDialog, QDoubleSpinBox, QLabel, QPushButton, QWidget
from .preview_color import preview_frame_image
from .ui_final_frame_preview import _freeze_visual
from .ui_frame_grade_preview import GradePreviewCanvas
from .ui_interval_preview import fit_dialog

# Import lazily so the optional dialog does not affect main-player startup.
def capture_range_preview(*args, **kwargs):
    from .range_preview import capture_range_preview as capture
    return capture(*args, **kwargs)


def verify_range_preview(*args, **kwargs):
    from .range_preview import verify_range_preview as verify
    return verify(*args, **kwargs)


class _OwnedSnapshots:
    """A worker may finish after cancellation suppresses Job.result entirely."""
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self.failures = []
        self.failed_ids = set()

    def add(self, item):
        with self.lock:
            self.items.append(item)
        return item

    def has_discarded(self, keep):
        with self.lock:
            return any(id(item) not in self.failed_ids and not any(item is retained for retained in keep)
                       for item in self.items)

    def clean(self, keep=()):
        with self.lock:
            discarded = [item for item in self.items if id(item) not in self.failed_ids
                         and not any(item is retained for retained in keep)]
        for item in discarded:
            # Native media backends may release a Windows file handle shortly
            # after setSource(empty). Retry only owned-cache cleanup, bounded
            # to two seconds. A terminal failure stays owned and is surfaced;
            # it never schedules an endless automatic cleanup loop.
            deadline = time.monotonic() + 2
            while True:
                try:
                    item.close()
                    with self.lock:
                        self.items = [entry for entry in self.items if entry is not item]
                    break
                except Exception as error:
                    if isinstance(error, OSError) and time.monotonic() < deadline:
                        time.sleep(.05)
                        continue
                    with self.lock:
                        self.failed_ids.add(id(item))
                        self.failures.append(f'{item.path}: {error}')
                    break

    def cleanup_errors(self):
        with self.lock:
            return tuple(self.failures)


def _range_token(project, start, end):
    active = [clip for clip in project.clips if clip.start < end and start < clip.end]
    overlays = [item for item in project.overlays if item.enabled and item.start < end and start < item.end]
    captions = [item for item in project.captions if item.start < end and start < item.end]
    if len(active) > 32 or len(overlays) > 4096 or len(captions) > 4096:
        return ('excessive_range_records', len(active), len(overlays), len(captions))
    media_ids = {clip.media_id for clip in active}
    scalar_blur = any(clip.track < 2 and (clip.motion_keyframes or not clip.composite.is_legacy())
        and any(e.enabled and e.kind == 'blur' and e.amount > 0 for e in clip.effects)
        for clip in project.clips)
    return (project.width, project.height, project.fps_num, project.fps_den,
            project.duration_frames, project.master_gain_db,
            tuple(_freeze_visual(item) for item in active),
            tuple(_freeze_visual(item) for item in project.media if item.id in media_ids),
            tuple(_freeze_visual(item) for item in overlays),
            tuple(_freeze_visual(item) for item in captions),
            _freeze_visual(project.caption_style) if captions else None,
            tuple(_freeze_visual(item) for item in project.video_transitions), scalar_blur)


class RangePreviewDialog(QDialog):
    def __init__(self, window, *, selected=False):
        super().__init__(window)
        self.selected = selected
        self._autoplay_generation = None
        self.window = window
        self.snapshot = None
        self.context = None
        self.job = None
        self._pending = None
        self._waiting_job = None
        self._generation = 0
        self._play_generation = 0
        self._play_intent = False
        self._closed_flag = False
        self._handoff_context = None
        self._next_verify = 0.0
        self._owned = _OwnedSnapshots()
        self._cleanup_error_count = 0
        self._bound_main_player = None
        self.player = None
        self.sink = None
        self.state = 'idle'
        self.setWindowTitle(tr('Build and loop selection') if selected else tr('Preview around playhead'))
        self.setModal(False)
        self.resize(760, 620)
        outer = QVBoxLayout(self)
        self.stage = self._label(tr('Rendered range · Rec.709 SDR · 48 kHz stereo mix'))
        outer.addWidget(self.stage)
        self.canvas = GradePreviewCanvas(self)
        self.canvas.setMinimumSize(0, 64)
        self.canvas.setAccessibleName(tr('Rendered range preview · display only'))
        outer.addWidget(self.canvas, 4)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setMinimumSize(0, 0)
        content = QWidget()
        layout = QVBoxLayout(content)
        self.status = self._label(tr('Build explicitly, then Play. Opening this window does not render.'))
        self.metadata = self._label(tr('No verified range preview.'))
        layout.addWidget(self.status)
        layout.addWidget(self.metadata)
        self.position_label = self._label(tr('Range position · not playing'))
        layout.addWidget(self.position_label)
        settings = QHBoxLayout()
        label = self._label(tr('Seconds before and after playhead'))
        self.radius = QDoubleSpinBox()
        self.radius.setRange(.5, 5)
        self.radius.setDecimals(1)
        self.radius.setSingleStep(.5)
        self.radius.setValue(2)
        self.radius.setAccessibleName(tr('Seconds before and after playhead'))
        settings.addWidget(label, 1)
        settings.addWidget(self.radius)
        layout.addLayout(settings)
        if selected:
            label.hide()
            self.radius.hide()
        self.selection_label = self._label('')
        self.selection_label.setVisible(selected)
        layout.addWidget(self.selection_label)
        self.loop_checkbox = QCheckBox(tr('Loop range'))
        self.loop_checkbox.setToolTip(tr('Each loop rechecks dependencies before restarting. A short gap may occur.'))
        layout.addWidget(self.loop_checkbox)
        self.limitations = self._label(tr('Up to 10 seconds, rendered at the original SDR canvas. Not realtime editing, HDR, or native-output validation. Unsupported combinations require the full timeline preview.'))
        layout.addWidget(self.limitations)
        self.freshness = self._label(tr('Source checks run in the background. This session preview does not replace full playback, scopes, reference stills or Undo history.'))
        layout.addWidget(self.freshness)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 3)
        buttons = QGridLayout()
        self.build_button = QPushButton(tr('Build range preview'))
        self.play_button = QPushButton(tr('Play'))
        self.cancel_button = QPushButton(tr('Cancel'))
        self.close_button = QPushButton(tr('Close'))
        self.timeline_button = QPushButton(tr('Build full timeline preview'))
        for button, source in ((self.build_button, 'Build range preview'), (self.play_button, 'Play'),
                (self.cancel_button, 'Cancel'), (self.close_button, 'Close'),
                (self.timeline_button, 'Build full timeline preview')):
            button.setAutoDefault(False)
            button.setAccessibleName(tr(source))
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        buttons.addWidget(self.build_button, 0, 0)
        buttons.addWidget(self.play_button, 0, 1)
        buttons.addWidget(self.cancel_button, 1, 0)
        buttons.addWidget(self.close_button, 1, 1)
        buttons.addWidget(self.timeline_button, 2, 0, 1, 2)
        outer.addLayout(buttons)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(1)
        self.build_button.clicked.connect(self.build_range)
        self.play_button.clicked.connect(self.toggle_play)
        self.cancel_button.clicked.connect(self.cancel_work)
        self.close_button.clicked.connect(self.reject)
        self.timeline_button.clicked.connect(self.build_timeline_preview)
        self.radius.valueChanged.connect(self.check_current)
        self.finished.connect(self._closed)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.check_current)
        self._bind_main_player()
        self.window.sound_audition.changed.connect(self._other_audition_changed)
        if hasattr(self.window, 'tts_preview_player'):
            self.window.tts_preview_player.playbackStateChanged.connect(self._main_playback_changed)
        self._observed_context = self._context()
        self.timer.start()
        if getattr(window, '_range_preview_cleanup_failure', None):
            self.status.setText(window._range_preview_cleanup_failure)
        self._update_buttons()

    @staticmethod
    def _label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def _bounds(self):
        project = self.window.project
        if self.selected:
            from .ui_preview_selection import selection
            start, end = selection(self.window)
            return (0 if start is None else start, 0 if end is None else end)
        radius = max(1, int(Fraction(str(self.radius.value())) * project.fps))
        frame = min(max(0, self.window.timeline.playhead), max(0, project.duration_frames - 1))
        return max(0, frame - radius), min(project.duration_frames, frame + radius)

    def _selection_token(self):
        if self.selected:
            from .ui_preview_selection import selection
            return selection(self.window)
        return self.radius.value()

    def _context(self):
        window = self.window
        start, end = self._bounds()
        return (id(window.project), window.revision, id(window.editor),
                window.editor.session_id, window.editor.revision,
                window.timeline.playhead, getattr(window, '_final_frame_seek_generation', 0),
                self._selection_token(), start, end, id(window.player),
                window.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                _range_token(window.project, start, end))

    def context_current(self, context=None):
        return (not self._closed_flag and not self.window.closing and self.isVisible()
                and (self.context if context is None else context) == self._context()
                and self.window.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState)

    def current(self):
        return self.snapshot is not None and self.context_current() and self.state in ('ready', 'paused', 'playing', 'checking', 'waiting')

    def _bind_main_player(self):
        if self._bound_main_player is self.window.player:
            return
        if self._bound_main_player is not None:
            try:
                self._bound_main_player.playbackStateChanged.disconnect(self._main_playback_changed)
            except (RuntimeError, TypeError):
                pass
        self._bound_main_player = self.window.player
        self._bound_main_player.playbackStateChanged.connect(self._main_playback_changed)

    def _main_playback_changed(self, state):
        if not self._closed_flag and state == QMediaPlayer.PlaybackState.PlayingState:
            self.invalidate()

    def _other_audition_changed(self, source_id):
        if source_id and not self._closed_flag:
            self.invalidate()

    def _other_jobs(self):
        own = getattr(self.window, 'range_preview_job', None)
        return [job for job in self.window.jobs if job is not own]

    def _update_buttons(self):
        active = not self._closed_flag and not self.window.closing
        if self.selected:
            from .ui_preview_selection import selection_text, selection_error
            self.selection_label.setText(selection_text(self.window))
            error = selection_error(self.window)
            if error:
                self.selection_label.setText(selection_text(self.window) + '\n' + error)
        busy = bool(self._other_jobs())
        self.build_button.setEnabled(active and not busy and self.window.project.duration_frames > 0
                                    and not getattr(self.window, '_range_preview_cleanup_failure', None))
        self.play_button.setEnabled(active and not busy and self.current())
        text = tr('Pause') if self._play_intent else tr('Play')
        self.play_button.setText(text)
        self.play_button.setAccessibleName(text)
        self.cancel_button.setEnabled(active and bool(self.snapshot or self.job or self._pending or self._waiting_job))
        self.timeline_button.setEnabled(active and not busy and bool(self.window.project.clips))

    def _keep(self):
        keep = [self.snapshot] if self.snapshot is not None else []
        if self._pending is not None and self._pending[3] is not None:
            keep.append(self._pending[3])
        return tuple(keep)

    def _stop_player(self, *, clear=True):
        self._play_generation += 1
        self._play_intent = False
        player, sink = self.player, self.sink
        self.player = self.sink = None
        if player is not None:
            player.stop()
            player.setVideoSink(None)
            player.setAudioOutput(None)
            player.setSource(QUrl())
            player.deleteLater()
        if sink is not None:
            sink.deleteLater()
        if clear:
            self.canvas.clear()

    def _clear(self):
        self._stop_player()
        self.snapshot = None
        self.metadata.setText(tr('No verified range preview.'))
        self.position_label.setText(tr('Range position · not playing'))

    def build_range(self, *_, autoplay=False):
        if self._closed_flag or self.window.closing or not self.isVisible() or self._other_jobs():
            return
        if self.selected:
            from .ui_preview_selection import selection_error
            error = selection_error(self.window)
            if error:
                self.invalidate()
                self.status.setText(error)
                return
        cleanup_error = getattr(self.window, '_range_preview_cleanup_failure', None)
        if cleanup_error:
            self.status.setText(cleanup_error)
            return
        self.window.player.pause()
        self.window.sound_audition.stop()
        if hasattr(self.window, 'close_tts_preview'):
            self.window.close_tts_preview()
        source_viewer = getattr(self.window, 'source_viewer_dialog', None)
        if source_viewer is not None:
            source_viewer.stop_audition()
        self._bind_main_player()
        self._handoff_context = None
        self._generation += 1
        if self.job is not None:
            self.job.cancel.set()
        self._clear()
        self.context = self._context()
        self._observed_context = self.context
        self._autoplay_generation = self._generation if autoplay else None
        self._pending = ('render', self._generation, self.context, None, None)
        self._start_pending()

    def _start_pending(self):
        previous = getattr(self.window, 'range_preview_job', None)
        if previous is not None and previous in self.window.jobs:
            if previous is not self.job:
                if self._pending is not None:
                    previous.cancel.set()
                if previous is not self._waiting_job:
                    self._waiting_job = previous
                    previous.finished.connect(self._previous_finished)
                    # A very short cleanup may have emitted finished before
                    # this dialog connected, while its GUI receipt is queued.
                    if previous.isFinished():
                        QTimer.singleShot(0, self._previous_finished)
            elif self._pending is not None and self.state != 'checking':
                previous.cancel.set()
            if self._pending is not None:
                self.state = 'waiting'
                self.status.setText(tr('Waiting for the previous range task to stop… Latest request retained.'))
            self._update_buttons()
            return
        keep = self._keep()
        if self._owned.has_discarded(keep):
            self._start_cleanup(keep)
            return
        if self._closed_flag or self.window.closing:
            self._complete_handoff()
            return
        if self._pending is None:
            return
        kind, generation, context, candidate, intent = self._pending
        self._pending = None
        if kind == 'render' and getattr(self.window, '_range_preview_cleanup_failure', None):
            self.state = 'cleanup_failed'
            self.status.setText(self.window._range_preview_cleanup_failure)
            self._update_buttons()
            return
        if generation != self._generation or not self.context_current(context):
            self.invalidate()
            return
        if self._other_jobs():
            self.invalidate()
            self.status.setText(tr('Wait for the current task to finish or cancel it before building this range.'))
            return
        self.state = 'rendering' if kind == 'render' else 'checking'
        self.status.setText(tr('Rendering the bounded final range…') if kind == 'render'
                            else tr('Checking range dependencies in the background…'))
        project = self.window.project
        owner = self._owned
        def operation(cancel, progress):
            try:
                detached = copy.deepcopy(project)
                if cancel.is_set():
                    return None, None
                if kind == 'render':
                    value = owner.add(capture_range_preview(detached, context[8], context[9], cancel=cancel, timeout=120))
                    return value, None
                return bool(verify_range_preview(candidate, detached, cancel=cancel, timeout=30)), None
            except Exception as error:
                return None, str(error)
        def before_start(job):
            self.job = job
            job.finished.connect(lambda: self._job_finished(job, generation))
        self.window.start_job(tr('Building range preview…') if kind == 'render' else tr('Checking range preview…'),
            operation, lambda result: self._accept_result(kind, generation, context, candidate, intent, result),
            'range_preview', before_start=before_start,
            is_current=lambda: generation == self._generation and self.context_current(context))
        self._update_buttons()

    def _start_cleanup(self, keep):
        owner = self._owned
        def before_start(job):
            self.job = job
            job._source_cleanup = True
            job.finished.connect(lambda: self._job_finished(job, self._generation))
        # Cleanup deliberately ignores cancel: no user input is processed here.
        self.window.start_job(tr('Releasing range preview files…'),
            lambda cancel, progress: owner.clean(keep), lambda _: None,
            'range_preview', before_start=before_start, is_current=lambda: False)

    @staticmethod
    def _error_text(error):
        detail = str(error).removesuffix('Use Build full timeline preview for this project/range.').rstrip()
        prefix = 'Cannot prepare range preview: '
        if detail.startswith(prefix):
            detail = detail[len(prefix):].rstrip()
        return tr(detail) if detail in ERROR_ZH_CN else tr_error(detail)

    def _accept_result(self, kind, generation, context, candidate, intent, result):
        if generation != self._generation or not self.context_current(context):
            return
        value, error = result
        if error is not None:
            self._clear()
            self._pending = None
            self.state = 'unsupported'
            self.status.setText(tr('Range preview unavailable · {error} Use Build full timeline preview.', error=self._error_text(error)))
        elif kind == 'render':
            self._pending = ('verify', generation, context, value, 'publish')
            self.state = 'checking'
        elif not value:
            self.invalidate()
            self.status.setText(tr('A source or dependency changed. Build the range again before playback.'))
        elif intent == 'publish':
            self.snapshot = candidate
            self.state = 'ready'
            self._next_verify = time.monotonic() + 1
            self.metadata.setText(tr('Timeline frames [{start}, {end}) · rendered range only', start=context[8], end=context[9]))
            self.position_label.setText(tr('Range position · timeline frame {frame}', frame=context[8]))
            self.status.setText(tr('Verified range ready. Press Play to hear the final mix.'))
            if self._autoplay_generation == generation:
                self._autoplay_generation = None
                QTimer.singleShot(0, lambda: self._play_built_selection(generation))
        elif isinstance(intent, tuple) and intent[0] in ('play', 'loop'):
            if self._play_intent and intent[1] == self._play_generation and self.snapshot is candidate:
                self._activate_player(candidate, restart=intent[0] == 'loop')
            else:
                self.state = 'paused'
        else:
            self.state = 'playing' if self._play_intent else 'paused'
            self._next_verify = time.monotonic() + 1
        self._update_buttons()

    def _play_built_selection(self, generation):
        if generation == self._generation and self.current() and self.state == 'ready' and not self._play_intent:
            self.toggle_play()

    def _job_finished(self, job, generation):
        if self.job is job:
            self.job = None
        if (not self._closed_flag and generation == self._generation
                and self._pending is None and self.snapshot is None
                and self.state in ('rendering', 'checking', 'waiting')):
            self.state = 'canceled'
        errors = self._owned.cleanup_errors()
        if len(errors) > self._cleanup_error_count:
            self._cleanup_error_count = len(errors)
            self._pending = None
            self._handoff_context = None
            self.state = 'cleanup_failed'
            message = tr('Some owned range preview files could not be released: {error} New range builds are disabled for this session; full preview remains available.', error='\n'.join(errors))
            self.status.setText(message)
            # Do not accumulate additional large failed session caches by
            # reopening the dialog. Full preview/export remain available.
            self.window._range_preview_cleanup_failure = message
            self.window.statusBar().showMessage(message, 15000)
        self._update_buttons()
        QTimer.singleShot(0, self._start_pending)

    def _previous_finished(self):
        self._waiting_job = None
        QTimer.singleShot(0, self._start_pending)

    def toggle_play(self, *_):
        if not self.current() or self._other_jobs():
            return
        if self._play_intent:
            self._play_generation += 1
            self._play_intent = False
            if self.player is not None:
                self.player.pause()
            self.state = 'paused'
            self.status.setText(tr('Range playback paused.'))
            self._update_buttons()
            return
        self._play_generation += 1
        self._play_intent = True
        self._pending = ('verify', self._generation, self.context, self.snapshot, ('play', self._play_generation))
        self._start_pending()
        self._update_buttons()

    def _activate_player(self, candidate, *, restart=False):
        if candidate is not self.snapshot or not self.current() or not self._play_intent:
            return
        generation = self._generation
        play_generation = self._play_generation
        player = self.player
        def activation_current():
            return (self.player is player and generation == self._generation
                    and play_generation == self._play_generation
                    and candidate is self.snapshot and self._play_intent and self.current())
        if player is None:
            player = QMediaPlayer(self)
            sink = QVideoSink(self)
            self.player, self.sink = player, sink
            def current():
                return self.player is player and generation == self._generation and self.current()
            def pixels(frame):
                if not current() or not frame.isValid():
                    return
                try:
                    self.canvas.image = preview_frame_image(frame)
                    self.canvas.update()
                except Exception as error:
                    self._playback_error(str(error))
            def status(value):
                if current() and value == QMediaPlayer.MediaStatus.EndOfMedia:
                    if not self.loop_checkbox.isChecked():
                        self._ended()  # State-only completion; no native restart.
                        return
                    # Retire the native status callback before pause/seek/play.
                    # Some multimedia backends still own playback locks here.
                    # Guard duplicates and a user pause/cancel before dispatch.
                    play_generation = self._play_generation
                    QTimer.singleShot(0, lambda: self._ended()
                        if current() and play_generation == self._play_generation else None)
            def error(*_):
                if current():
                    self._playback_error(player.errorString())
            sink.videoFrameChanged.connect(pixels)
            player.mediaStatusChanged.connect(status)
            player.errorOccurred.connect(error)
            player.positionChanged.connect(lambda milliseconds: self._position_changed(milliseconds) if current() else None)
            player.setAudioOutput(self.audio)
            player.setVideoSink(sink)
            player.setSource(QUrl.fromLocalFile(str(candidate.path)))
        # Native calls may synchronously emit errors, retire this player, or
        # activate another request. Never inspect/seek/play a retired instance
        # or accidentally operate on its replacement via self.player.
        if not activation_current():
            return
        if restart or player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            player.setPosition(0)
        if not activation_current():
            return
        self.state = 'playing'
        self._next_verify = time.monotonic() + 1
        player.play()
        if not activation_current():
            return
        self.status.setText(tr('Playing rendered range · SDR · final audio mix'))
        self._update_buttons()

    def _position_changed(self, milliseconds):
        if not self.current():
            return
        start, end = self.context[8:10]
        local = max(0, int(Fraction(max(0, milliseconds), 1000) * self.window.project.fps))
        frame = min(end - 1, start + local)
        self.position_label.setText(tr('Range position · timeline frame {frame}', frame=frame))

    def _ended(self):
        if not self.current() or not self._play_intent:
            return
        if not self.loop_checkbox.isChecked():
            self._play_intent = False
            self._play_generation += 1
            self.state = 'ready'
            self.status.setText(tr('Range playback finished.'))
            self._update_buttons()
            return
        if self.player is not None:
            self.player.pause()
        self._play_generation += 1
        self._pending = ('verify', self._generation, self.context, self.snapshot, ('loop', self._play_generation))
        self._start_pending()

    def _playback_error(self, message):
        self._stop_player()
        self.state = 'ready' if self.snapshot is not None else 'unsupported'
        self.status.setText(tr('Range playback unavailable · {error}', error=tr_error(message)))
        self._update_buttons()

    def invalidate(self):
        self._generation += 1
        self._pending = None
        if self.job is not None:
            self.job.cancel.set()
        self._clear()
        self.state = 'stale'
        self.status.setText(tr('Range preview is stale. Build it again for the current project and playhead.'))
        self._update_buttons()
        if self.job is None:
            QTimer.singleShot(0, self._start_pending)

    def check_current(self, *_):
        if self._closed_flag:
            return
        if self.window.closing:
            self.reject()
            return
        self._bind_main_player()
        token = self._context()
        changed = token != self._observed_context
        self._observed_context = token
        if changed:
            self.invalidate()
        elif (self._play_intent and self.snapshot is not None and self.current()
                and self.job is None and self._pending is None
                and not self._other_jobs() and time.monotonic() >= self._next_verify):
            self._pending = ('verify', self._generation, self.context, self.snapshot, 'periodic')
            self._start_pending()
        self._update_buttons()

    def cancel_work(self, *_):
        self._handoff_context = None
        self.invalidate()
        self._observed_context = self._context()
        self.state = 'canceled'
        self.status.setText(tr('Range preview canceled. The project and full timeline preview are unchanged.'))

    def build_timeline_preview(self):
        if self._closed_flag or self.window.closing or self._other_jobs():
            return
        self._handoff_context = (id(self.window.project), self.window.revision, self.window.preview_mode)
        self.reject()
        self._complete_handoff()

    def _complete_handoff(self):
        if (self._handoff_context is None or self.job is not None or self._waiting_job is not None
                or self._owned.has_discarded(())):
            return
        context = self._handoff_context
        self._handoff_context = None
        window = self.window
        if window.closing:
            return
        if window.jobs or context != (id(window.project), window.revision, window.preview_mode):
            window.statusBar().showMessage(tr('Full timeline preview was not started because the editing context changed.'), 8000)
            return
        window.build_preview()

    def _closed(self, *_):
        if self._closed_flag:
            if self.job is None:
                self._start_pending()
            return
        self._closed_flag = True
        self._generation += 1
        self._pending = None
        self.timer.stop()
        if self._bound_main_player is not None:
            try:
                self._bound_main_player.playbackStateChanged.disconnect(self._main_playback_changed)
            except (RuntimeError, TypeError):
                pass
            self._bound_main_player = None
        if self.job is not None:
            self.job.cancel.set()
        try:
            self.window.sound_audition.changed.disconnect(self._other_audition_changed)
            if hasattr(self.window, 'tts_preview_player'):
                self.window.tts_preview_player.playbackStateChanged.disconnect(self._main_playback_changed)
        except (RuntimeError, TypeError):
            pass
        self._clear()
        self._update_buttons()
        if self.job is None:
            # Register cleanup before MainWindow.closeEvent tests its job set.
            # A deferred callback could arrive after the application exits.
            self._start_pending()


def open_range_preview(window, *, selected=False):
    if window.closing:
        return None
    existing = getattr(window, 'range_preview_dialog', None)
    if existing is not None and existing.isVisible() and existing.selected == selected:
        existing.raise_()
        existing.activateWindow()
        return existing
    if existing is not None:
        existing.reject()
    dialog = RangePreviewDialog(window, selected=selected)
    window.range_preview_dialog = dialog
    dialog.show()
    return dialog


def cancel_range_previews(window, *, close=False):
    for dialog in window.findChildren(RangePreviewDialog):
        dialog.cancel_work()
        if close:
            dialog.reject()
