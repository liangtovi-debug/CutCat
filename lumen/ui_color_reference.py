"""Session-only, display-referred stills. Never part of the project/render graph.

The only stored pixels are one bounded immutable copy of the decoder's 8-bit
RGBA preview. No source frame is re-rendered, no still is written to disk, and
comparison drawing never feeds scopes, project serialization, undo or export.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

from PySide6.QtCore import Q_ARG, QCoreApplication, QEvent, QMetaObject, QObject, QRectF, QSize, Qt, QUrl, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy

from .preview_color import PreviewColorError, preview_frame_image
from .i18n import locale_events, tr
from .i18n_widgets import QWidget, QLabel, QPushButton, QComboBox, QSlider

MAX_REFERENCE_PIXELS = 4096 * 4096
MAX_REFERENCE_SIDE = 8192
REFERENCE_LIMITS = (
    'One session-only still from the displayed decoded 8-bit compressed Rec.709 preview. '
    'Not source pixels, a 10-bit/HDR comparison, or a calibrated monitor. '
    'Replace overwrites only this in-memory still; opening or creating a project clears it. '
    'Comparison never changes the project, undo history or export. Scopes measure only the current preview. '
    'Supported YUV frames use explicit matrix/range conversion; already-RGB backend frames are passed through, not color-verified. '
    'The exact displayed pixels are preserved, not a colorimetric guarantee.'
)


@dataclass(frozen=True)
class SourceIdentity:
    clip_id: str
    media_id: str
    name: str
    path: str
    track: int
    source_position: Fraction


@dataclass(frozen=True)
class FrameProvenance:
    start_us: int
    end_us: int
    timeline_frame: int
    revision: int
    preview_path: str
    preview_mode: str
    generation: int
    active_sources: tuple[SourceIdentity, ...]
    active_overlay_ids: tuple[str, ...] = ()
    active_caption_ids: tuple[str, ...] = ()

    @property
    def active_source(self) -> SourceIdentity | None:
        """Topmost video clip, in the same ordering as the render graph."""
        return self.active_sources[-1] if self.active_sources else None


@dataclass(frozen=True)
class ReferenceStill:
    width: int
    height: int
    pixels: bytes
    provenance: FrameProvenance

    @classmethod
    def capture(cls, image: QImage, provenance: FrameProvenance) -> 'ReferenceStill':
        if image.isNull():
            raise ValueError('No displayed preview frame is available.')
        width, height = image.width(), image.height()
        if max(width, height) > MAX_REFERENCE_SIDE or width * height > MAX_REFERENCE_PIXELS:
            raise ValueError('The preview frame is too large for a reference still.')
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        # RGBA8888 has exactly width*4 bytes per scanline. bytes() owns a deep,
        # immutable copy, independent of decoder buffers and future QImages.
        pixels = bytes(rgba.constBits()[:height * rgba.bytesPerLine()])
        return cls(width, height, pixels, provenance)

    @property
    def image(self) -> QImage:
        """A caller may mutate this image without mutating the saved still."""
        return self._paint_image().copy()

    def _paint_image(self) -> QImage:
        # Read-only internal painting view; callers only receive .image copies.
        return QImage(self.pixels, self.width, self.height, self.width * 4,
                      QImage.Format.Format_RGBA8888)


def frame_provenance(frame, project, revision: int, preview_path: str,
                     preview_mode: str, generation: int) -> FrameProvenance | None:
    """Identify the actually decoded frame, never the inspected clip/playhead.

    Preview PTS are CFR and begin at zero. Reject unknown, malformed and
    out-of-timeline timestamps instead of inventing provenance from a seek.
    Microsecond rounding at fractional frame rates is allowed, not frame drift.
    """
    try:
        start, end = int(frame.startTime()), int(frame.endTime())
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    fps = project.fps
    if start < 0 or end <= start or project.duration_frames <= 0:
        return None
    index = round(Fraction(start, 1_000_000) * fps)
    if not 0 <= index < project.duration_frames:
        return None
    expected_start = Fraction(index * 1_000_000, 1) / fps
    expected_end = Fraction((index + 1) * 1_000_000, 1) / fps
    if abs(start - expected_start) > 2 or abs(end - expected_end) > 2:
        return None
    media = {item.id: item for item in project.media}
    sources = tuple(SourceIdentity(clip.id, clip.media_id, media[clip.media_id].name,
                                   media[clip.media_id].path, clip.track,
                                   clip.source_in + (index - clip.start) * clip.speed)
                    for clip in sorted(project.clips, key=lambda c: (c.track, c.start, c.id))
                    if clip.track < 2 and clip.start <= index < clip.end
                    and clip.media_id in media and media[clip.media_id].has_video)
    overlays = tuple(item.id for item in sorted(project.overlays, key=lambda o: o.z_order)
                     if item.enabled and item.start <= index < item.end)
    captions = tuple(item.id for item in project.captions if item.start <= index < item.end)
    return FrameProvenance(start, end, index, revision, str(preview_path), preview_mode,
                           generation, sources, overlays, captions)


def fitted_rect(width: int, height: int, bounds: QRectF) -> QRectF:
    """Fit without cropping/stretching; both comparisons share normalized bounds."""
    if width <= 0 or height <= 0 or bounds.width() <= 0 or bounds.height() <= 0:
        return QRectF()
    scale = min(bounds.width() / width, bounds.height() / height)
    w, h = width * scale, height * scale
    return QRectF(bounds.x() + (bounds.width() - w) / 2,
                  bounds.y() + (bounds.height() - h) / 2, w, h)


class PreviewStatusLabel(QLabel):
    """A readable one-line status with complete text in the tooltip/accessibility.

    Long render/stale messages must not squeeze a small viewer and scopes into
    overlapping rectangles. Keep the full .text() for normal UI consumers.
    """
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.updateGeometry()

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()), self.fontMetrics().height())

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)


class VideoViewer(QWidget):
    """GUI-thread CPU painting, with a frozen reference separate from current."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.color_error = None
        self.frame_provenance: FrameProvenance | None = None
        self.reference: ReferenceStill | None = None
        self.comparison_mode = 'off'
        self.wipe_position = .5
        self._sink = QVideoSink(self)
        locale_events.changed.connect(self.update)

    def videoSink(self):
        return self._sink

    def set_frame(self, frame, provenance=None):
        try:
            self.set_image(preview_frame_image(frame) if frame.isValid() else QImage(), provenance)
        except (PreviewColorError, MemoryError) as error:
            self.clear_current()
            self.color_error = str(error)
            self.setToolTip(tr('Preview color unavailable. Unsupported format or color metadata; no guessed conversion is displayed.'))

    def set_image(self, image, provenance=None):
        self.image = image
        self.color_error = None
        self.frame_provenance = provenance if not image.isNull() else None
        self.update()

    def clear_current(self):
        self.set_image(QImage())

    def set_comparison_mode(self, mode):
        if mode not in ('off', 'side_by_side', 'wipe'):
            raise ValueError('Unknown reference comparison mode.')
        self.comparison_mode = mode
        self.update()

    def set_wipe_position(self, value):
        value = float(value)
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Wipe position must be between zero and one.')
        self.wipe_position = value
        self.update()

    def comparison_rects(self):
        bounds = QRectF(self.rect())
        if self.reference is not None and self.comparison_mode == 'side_by_side':
            gap = 4.
            width = max(0., (bounds.width() - gap) / 2)
            return (QRectF(0, 0, width, bounds.height()),
                    QRectF(width + gap, 0, width, bounds.height()))
        return bounds, bounds

    def _draw_image(self, painter, image, bounds):
        if not image.isNull():
            painter.drawImage(fitted_rect(image.width(), image.height(), bounds), image)

    def _label(self, painter, bounds, text, *, right=False):
        if bounds.width() < 20:
            return
        padding = 6
        width = min(bounds.width() - 4, painter.fontMetrics().horizontalAdvance(str(text)) + 2 * padding + 4)
        height = painter.fontMetrics().height() + 6
        x = bounds.right() - width - 2 if right else bounds.left() + 2
        rect = QRectF(x, bounds.top() + 2, width, height)
        painter.fillRect(rect, QColor(13, 17, 23, 220))
        painter.setPen(QColor('#e0e6ed'))
        full_text = str(text)
        available = max(0, round(width - 2 * padding))
        label = (full_text if painter.fontMetrics().horizontalAdvance(full_text) <= available
                 else painter.fontMetrics().elidedText(full_text, Qt.TextElideMode.ElideRight, available))
        painter.drawText(rect.adjusted(padding, 0, -padding, 0), Qt.AlignmentFlag.AlignVCenter, label)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#0d1117'))
        reference_bounds, current_bounds = self.comparison_rects()
        comparing = self.reference is not None and self.comparison_mode != 'off'
        if comparing and self.comparison_mode == 'side_by_side':
            self._draw_image(painter, self.reference._paint_image(), reference_bounds)
            self._draw_image(painter, self.image, current_bounds)
        elif comparing:
            self._draw_image(painter, self.image, current_bounds)
            edge = current_bounds.left() + current_bounds.width() * self.wipe_position
            painter.save()
            painter.setClipRect(QRectF(current_bounds.left(), current_bounds.top(),
                                      current_bounds.width() * self.wipe_position, current_bounds.height()))
            # Fill first: portrait/landscape differences must not expose current
            # pixels in the reference's letterbox region.
            painter.fillRect(current_bounds, QColor('#0d1117'))
            self._draw_image(painter, self.reference._paint_image(), reference_bounds)
            painter.restore()
            painter.setPen(QPen(QColor('#9cddcc'), 2))
            painter.drawLine(round(edge), 0, round(edge), self.height())
        else:
            self._draw_image(painter, self.image, current_bounds)
        if comparing:
            p = self.reference.provenance
            self._label(painter, reference_bounds, tr('Reference · f{frame}', frame=p.timeline_frame))
        p = self.frame_provenance
        text = (tr('Current · f{frame}', frame=p.timeline_frame) if p is not None
                else tr('Preview color unavailable') if self.color_error else tr('Current unavailable'))
        self._label(painter, current_bounds, text, right=comparing and self.comparison_mode == 'wipe')


class PreviewReceiver(QObject):
    """One GUI-affine receiver per decoder generation, including all late events."""
    def __init__(self, owner, generation):
        super().__init__(owner)
        self.owner = owner
        self.generation = generation

    def current(self):
        return self.generation == self.owner._preview_generation and not self.owner.closing

    @Slot(QVideoFrame)
    def frame(self, frame):
        if self.current():
            self.owner.video_frame(frame, self.generation)

    @Slot(int)
    def position(self, value):
        if self.current():
            self.owner.playback_position(value)

    @Slot(QMediaPlayer.PlaybackState)
    def state(self, state):
        if self.current() and hasattr(self.owner, 'play_button'):
            self.owner.play_button.setText(tr('Pause') if state == QMediaPlayer.PlaybackState.PlayingState else tr('Play'))
            if hasattr(self.owner, 'shortcuts'): self.owner.shortcuts.update_enabled()

    @Slot(QMediaPlayer.MediaStatus)
    def status(self, status):
        if not self.current() or not hasattr(self.owner, 'scopes'):
            return
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia,
                      QMediaPlayer.MediaStatus.NoMedia):
            self.owner._preview_ended = status == QMediaPlayer.MediaStatus.EndOfMedia
            if status == QMediaPlayer.MediaStatus.InvalidMedia:
                self.owner._preview_failed = True
            self.owner.video.clear_current()
            self.owner.scopes.clear()
            self.owner.update_reference_controls()

    @Slot(QMediaPlayer.Error, str)
    def error(self, _error, message):
        if self.current():
            self.owner._preview_failed = True
            from .i18n import tr_error
            self.owner.video.clear_current()
            self.owner.scopes.clear()
            self.owner.update_reference_controls()
            self.owner.statusBar().showMessage(tr('Playback: {error}', error=tr_error(message)), 12000)


class ColorReferenceMixin:
    def init_reference_state(self):
        self._preview_generation = 0
        self._preview_path = ''
        self._pending_seek_frame = None
        self._seek_target_frame = None
        self._last_accepted_frame = None
        self._preview_ended = False
        self._preview_failed = False
        self._preview_project = None
        self._preview_receiver = None

    def replace_preview_player(self):
        """Do not reuse a decoder across source switches: old frames cannot leak."""
        self._preview_generation += 1
        old_player = getattr(self, 'player', None)
        old_receiver = self._preview_receiver
        old_sink = self.video._sink
        # Release the previous native player before binding shared audio to
        # its successor. Reattaching first can invalidate active ownership on
        # platform backends with asynchronous decoder teardown.
        if old_player is not None:
            self.cancel_preview_seek(old_player)
            old_player.stop()
            old_player.setVideoSink(None)
            old_player.setAudioOutput(None)
            old_player.setSource(QUrl())
            old_player.deleteLater()
        self.video._sink = QVideoSink(self.video)
        self._preview_receiver = PreviewReceiver(self, self._preview_generation)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoSink(self.video.videoSink())
        r = self._preview_receiver
        # Explicit queued QObject slots keep all painting/scopes on the GUI
        # thread even if a platform backend emits from its decoding thread.
        connection = Qt.ConnectionType.QueuedConnection
        self.video.videoSink().videoFrameChanged.connect(r.frame, connection)
        self.player.positionChanged.connect(r.position, connection)
        self.player.playbackStateChanged.connect(r.state, connection)
        self.player.mediaStatusChanged.connect(r.status, connection)
        self.player.errorOccurred.connect(r.error, connection)
        if old_receiver is not None:
            old_receiver.deleteLater()
        old_sink.deleteLater()

    def cancel_preview_seek(self, player=None):
        """Retire queued transport calls before a player/source is invalidated.

        Decoder/status signals target PreviewReceiver, not this QMediaPlayer.
        Only discard native calls on the player whose source is being retired;
        never remove frame events or a successor player's events.
        """
        player = player if player is not None else getattr(self, 'player', None)
        if player is not None:
            QCoreApplication.removePostedEvents(player, QEvent.Type.MetaCall)

    def request_preview_seek(self, milliseconds):
        """Queue Qt's native slot, not a Python callback around setPosition.

        Qt 6.8/PySide can deadlock if Python holds the GIL in setPosition while
        the FFmpeg renderer's destruction calls QAudioOutput.disconnectNotify:
        that worker holds the QObject connection lock and requests the GIL,
        while the new renderer connects to the same output on the GUI thread.
        Native queued dispatch runs on the player's GUI thread after Python
        returns to Qt, without retaining the wrapper's GIL across the seek.
        Frame acceptance still requires the latest requested PTS/generation.
        """
        if (self.closing or self._preview_failed
                or self.player.source() != QUrl.fromLocalFile(self._preview_path)):
            return False
        try:
            queued = QMetaObject.invokeMethod(self.player, 'setPosition',
                                             Qt.ConnectionType.QueuedConnection,
                                             Q_ARG('qint64', int(milliseconds)))
        except (RuntimeError, TypeError):
            queued = False
        if not queued:
            self._preview_failed = True
            self._pending_seek_frame = None
            self.video.clear_current()
            self.scopes.clear()
            self.update_reference_controls()
            self.statusBar().showMessage(tr('Playback: {error}', error=tr('Could not seek the preview.')), 12000)
        return bool(queued)

    def build_reference_controls(self, layout):
        panel = QWidget()
        row = QHBoxLayout(panel)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.capture_reference_button = QPushButton(tr('Capture still'))
        self.capture_reference_button.setObjectName('captureReference')
        self.capture_reference_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.capture_reference_button.clicked.connect(self.capture_reference)
        self.clear_reference_button = QPushButton(tr('Clear'))
        self.clear_reference_button.setObjectName('clearReference')
        self.clear_reference_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.clear_reference_button.setAccessibleName(tr('Clear reference still'))
        self.clear_reference_button.clicked.connect(self.clear_reference)
        self.comparison_mode_choice = QComboBox()
        self.comparison_mode_choice.setObjectName('referenceComparison')
        self.comparison_mode_choice.setAccessibleName(tr('Reference comparison'))
        for text, key in (('Compare off', 'off'), ('Side by side', 'side_by_side'), ('Wipe', 'wipe')):
            self.comparison_mode_choice.addItem(tr(text), key)
        self.comparison_mode_choice.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.comparison_mode_choice.currentIndexChanged.connect(self.change_reference_comparison)
        for widget in (self.capture_reference_button, self.clear_reference_button, self.comparison_mode_choice):
            row.addWidget(widget)
        row.setStretch(2, 1)
        layout.addWidget(panel)
        self.reference_controls = panel
        self.wipe_slider = QSlider(Qt.Orientation.Horizontal)
        self.wipe_slider.setObjectName('referenceWipe')
        self.wipe_slider.setRange(0, 100)
        self.wipe_slider.setValue(50)
        self.wipe_slider.setAccessibleName(tr('Reference wipe position'))
        self.wipe_slider.setToolTip(tr('Reference on the left; current preview on the right.'))
        self.wipe_slider.valueChanged.connect(lambda value: self.video.set_wipe_position(value / 100))
        self.wipe_slider.setMinimumWidth(48)
        self.wipe_slider.setMaximumWidth(80)
        self.wipe_slider.hide()
        row.addWidget(self.wipe_slider)
        self.reference_note = QLabel(tr('Info'))
        self.reference_note.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.reference_note.setAccessibleName(tr('Reference still information'))
        row.addWidget(self.reference_note)
        locale_events.changed.connect(self.update_reference_controls)
        self.update_reference_controls()

    def reference_capture_available(self):
        p = self.video.frame_provenance
        return bool(p is not None and not self.video.image.isNull()
                    and p.generation == self._preview_generation
                    and p.revision == self.revision == self.preview_revision
                    and p.preview_mode == self.preview_mode == self.loaded_preview_mode
                    and p.preview_path == self._preview_path
                    and self.player.source() == QUrl.fromLocalFile(self._preview_path)
                    and self._pending_seek_frame is None and not self._preview_ended
                    and not self._preview_failed
                    and self.project.duration_frames > p.timeline_frame)

    def capture_reference(self, *_):
        if not self.reference_capture_available():
            self.statusBar().showMessage(tr('Wait for a current decoded preview frame before capturing a still.'), 7000)
            return False
        try:
            still = ReferenceStill.capture(self.video.image, self.video.frame_provenance)
        except (ValueError, MemoryError):
            self.statusBar().showMessage(tr('This preview frame cannot fit in the bounded reference memory. The existing still was kept.'), 9000)
            return False
        self.video.reference = still
        if self.comparison_mode_choice.currentData() == 'off':
            self.comparison_mode_choice.setCurrentIndex(self.comparison_mode_choice.findData('side_by_side'))
        self.update_reference_controls()
        self.video.update()
        return True

    def clear_reference(self, *_):
        self.video.reference = None
        self.comparison_mode_choice.setCurrentIndex(self.comparison_mode_choice.findData('off'))
        self.video.set_comparison_mode('off')
        self.wipe_slider.hide()
        self.update_reference_controls()
        self.video.update()

    def change_reference_comparison(self, *_):
        mode = self.comparison_mode_choice.currentData()
        self.video.set_comparison_mode(mode)
        self.wipe_slider.setVisible(mode == 'wipe' and self.video.reference is not None)

    def invalidate_preview_frame(self, *, new_source=False, clear_reference=False):
        if new_source:
            self.cancel_preview_seek()
            self._preview_generation += 1
            self._preview_path = ''
        self._pending_seek_frame = None
        self._seek_target_frame = None
        self._last_accepted_frame = None
        self.video.clear_current()
        if clear_reference:
            self.clear_reference()
        else:
            self.update_reference_controls()

    def update_reference_controls(self, *_):
        if not hasattr(self, 'capture_reference_button'):
            return
        still = self.video.reference
        self.capture_reference_button.setEnabled(self.reference_capture_available())
        self.capture_reference_button.setText(tr('Replace still') if still else tr('Capture still'))
        self.clear_reference_button.setEnabled(still is not None)
        self.comparison_mode_choice.setEnabled(still is not None)
        self.capture_reference_button.setToolTip(tr(REFERENCE_LIMITS))
        self.reference_controls.setToolTip(tr(REFERENCE_LIMITS))
        if still:
            p = still.provenance
            mode = tr('Before') if p.preview_mode == 'bypass' else tr('After')
            self.reference_note.setText(tr('Info'))
            sources = '; '.join(f'{source.name} [V{source.track + 1}] · {source.clip_id}' for source in p.active_sources) or str(tr('Timeline background / overlays'))
            metadata = tr('Reference f{frame} · {mode}\nDecoded timestamp: {start}–{end} µs\nProject revision: {revision}\nActive video sources: {sources}\nPreview: {path}',
                          frame=p.timeline_frame, mode=mode, start=p.start_us, end=p.end_us,
                          revision=p.revision, sources=sources, path=p.preview_path)
            self.reference_note.setToolTip(str(metadata) + '\n\n' + str(tr(REFERENCE_LIMITS)))
            self.reference_note.setAccessibleDescription(metadata)
            self.video.setToolTip(self.reference_note.toolTip())
        else:
            self.reference_note.setText(tr('Info'))
            self.reference_note.setToolTip(tr(REFERENCE_LIMITS))
            self.reference_note.setAccessibleDescription(tr(REFERENCE_LIMITS))
            self.video.setToolTip(tr(REFERENCE_LIMITS))
        if getattr(self.video, 'color_error', None):
            self.video.setToolTip(str(tr('Preview color unavailable. Unsupported format or color metadata; no guessed conversion is displayed.')) + '\n' + self.video.toolTip())
