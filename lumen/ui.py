"""Native Qt editor. The main timeline viewer uses the shared render graph."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from fractions import Fraction
import math
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import numpy as np
from PySide6 import QtWidgets
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QRectF, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QAction, QImage, QFontDatabase, QTextLayout
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QHBoxLayout, QSplitter, QListWidget, QListWidgetItem, QComboBox, QDoubleSpinBox,
    QSpinBox, QCheckBox, QFileDialog, QMessageBox, QFormLayout, QGroupBox,
    QScrollArea, QSlider, QProgressBar, QTabWidget, QToolBar, QInputDialog,
    QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QAbstractItemView,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink

from .color_widgets import WheelControl, CurveEditor
from .ui_selective_color import SelectiveCurveControl, SELECTIVE_CURVE_KEYS
from .model import Project, Editor, Grade, Clip
from .ui_component_setup import open_component_setup, restore_component_preferences
from .stabilization_settings import StabilizationSettings
from .restoration_settings import RestorationSettings
from .clip_transform import ClipTransform
from .clip_composite import ClipComposite
from .media import probe
from .render import RenderEngine
from .ui_color_export import choose_export_profile
from .ui_grade_transfer import GradeTransferPanelMixin
from .ui_white_balance import WhiteBalancePanelMixin
from .preview_color import PreviewColorError, preview_frame_image
from .color_scopes import Scopes
from .ui_color_reference import ColorReferenceMixin, PreviewStatusLabel, VideoViewer, frame_provenance
from .ui_recovery import RecoveryMixin, project_fingerprint
from .ui_captions import CaptionPanelMixin
from .ui_effects import EffectsPanelMixin
from .ui_sounds import SoundsPanelMixin
from .ui_ai import LocalAIPanelMixin
from .ui_workspace import WorkspaceModeMixin, BASIC_GRADE_FIELDS
from .ui_media_bins import MediaBinsMixin
from .ui_timeline_markers import TimelineMarkersMixin
from .ui_timeline_marker_band import (MARKER_BAND_BOTTOM, marker_band_rect, marker_rect, marker_at, marker_tooltip, paint_marker_band)
from .ui_track_edit_locks import TrackEditLocksMixin, TrackLockNote, locked_clips, LOCK_REASON
from .ui_clip_copy_paste import ClipClipboardMixin
from .ui_source_audio_separation import SourceAudioSeparationMixin
from .ui_source_audio_reattach import SourceAudioReattachMixin
from .ui_linked_av import (LinkedAVMixin, av_link, linked_pair, link_badge, LINK_TRIM_HELP)
from .ui_linked_av_roll import LinkedAVRollMixin
from .ui_interval_preview import plain_label
from .ui_clip_groups import (ClipGroupsMixin, clip_group, group_membership_token,
                             reconcile_group_ids, GROUP_TRIM_HELP)
from .ui_color_input import InputColorPanelMixin
from .timing import ClipTimeMap, duration_frames, parse_speed, parse_source_position
from .shortcuts import ShortcutController, COMMANDS
from .audio_waveform import map_peaks
from .ui_audio_waveform import WaveformController
from .ui_video_transition import VideoTransitionMixin, transition_rect, paint_transitions
from .ui_audio_analysis import open_audio_analysis, close_audio_analysis
from .ui_master_gain import open_master_gain, close_master_gain
from .ui_audio_gain import open_audio_gain, close_audio_gain, can_open_audio_gain
from .ui_ripple_delete import open_ripple_delete, close_ripple_delete
from .ui_gap_close import open_gap_close, close_gap_close
from .ui_loudness_proposal import close_loudness_proposal
from .ui_source_viewer import open_source_viewer, invalidate_source_viewer, stop_source_audition


from .ui_style import STYLE, apply_ui_layout_tokens
from .branding import display_name
from .i18n import tr, tr_error, get_locale, set_locale, locale_events
from .i18n_widgets import (QMainWindow, QWidget, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QComboBox, QDoubleSpinBox, QSpinBox, QCheckBox, QFormLayout,
    QGroupBox, QScrollArea, QSlider, QProgressBar, QTabWidget, QToolBar, QLineEdit,
    QPlainTextEdit, QTextEdit, QAction)



class ExactSourcePositionEdit(QLineEdit):
    """Exact project-frame source position; no integer-spinbox phase loss."""
    def setValue(self, value):
        self.setText(str(Fraction(value)))

    def value(self):
        return parse_source_position(self.text().strip())


def exact_speed_text(value) -> str:
    """Use a terminating decimal when exact, otherwise keep the fraction."""
    value = Fraction(value)
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2; twos += 1
    while denominator % 5 == 0:
        denominator //= 5; fives += 1
    if denominator != 1:
        return str(value)
    places = max(twos, fives)
    if places == 0:
        return str(value.numerator)
    scale = 10 ** places
    whole, tail = divmod(value.numerator * (scale // value.denominator), scale)
    return f'{whole}.{tail:0{places}d}'.rstrip('0').rstrip('.')


def _font_has_brand_glyphs(font: QFont) -> bool:
    """Check the actual Qt shaping result, including native font fallback."""
    layout = QTextLayout('剪辑猫', font)
    layout.beginLayout()
    line = layout.createLine()
    line.setLineWidth(1000)
    layout.endLayout()
    glyphs = [index for run in layout.glyphRuns() for index in run.glyphIndexes()]
    return len(glyphs) == 3 and all(index != 0 for index in glyphs)


def _make_brand_label(ui_language: str | None = None) -> QLabel:
    """Use only locally available fonts; never download or bundle a font."""
    ui_language = ui_language or get_locale()
    label = QLabel(display_name(ui_language))
    label.setObjectName('brandTitle')
    label.setStyleSheet('font-size: 15pt; font-weight: 700; color: #9cddcc;')
    _update_brand_label(label, ui_language)
    return label


def _update_brand_label(label, ui_language=None):
    ui_language = ui_language or get_locale()
    label.setText(display_name(ui_language))
    label.setToolTip('')
    label.setAccessibleDescription('')
    label.ensurePolished()
    font = QFont(label.font())
    if ui_language.replace('_', '-').split('-', 1)[0].lower() == 'zh' and not _font_has_brand_glyphs(font):
        preferred = ['PingFang SC', 'Microsoft YaHei UI', 'Microsoft YaHei',
                     'Noto Sans CJK SC', 'Source Han Sans SC', 'WenQuanYi Micro Hei']
        for family in dict.fromkeys(preferred + QFontDatabase.families()):
            candidate = QFont(font)
            candidate.setFamily(family)
            if _font_has_brand_glyphs(candidate):
                label.setFont(candidate)
                break
        else:
            label.setText('CutCat')
            label.setToolTip(tr('Chinese display name unavailable: install a local font '
                             'covering Simplified Chinese, then restart. No font is bundled '
                             'or downloaded automatically.'))
            label.setAccessibleDescription(label.toolTip())
    return label


def timecode(frame: int, project: Project) -> str:
    # Frame readout is authoritative; seconds are a readable aid, not edit storage.
    seconds = frame / float(project.fps)
    return f'{int(seconds // 60):02}:{seconds % 60:06.3f}  ·  f{frame}'


class Job(QThread):
    result = Signal(object)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            value = self.operation(self.cancel, self.progress.emit)
            if not self.cancel.is_set():
                self.result.emit(value)
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc))


@dataclass
class _TrimGesture:
    clip_id: str
    edge: str
    press_x: float
    scale: float
    project: Project
    editor: Editor
    context: tuple
    editor_revision: int
    editor_session: str
    fingerprint: str
    selection_context: object
    clip_ids: tuple[str, ...]
    membership: tuple
    generation: int
    candidates: tuple[Clip, ...] = ()
    candidate: Clip | None = None
    delta: int = 0
    error: str = ''


@dataclass
class _MoveGesture:
    clip_ids: tuple[str, ...]
    anchor_id: str
    primary_id: str
    press_x: float
    scale: float
    project: Project
    editor: Editor
    context: tuple
    editor_revision: int
    editor_session: str
    fingerprint: str
    selection_context: object
    generation: int
    originals: tuple[Clip, ...]
    membership: tuple = ()
    collapse_on_release: bool = False
    candidates: tuple[Clip, ...] = ()
    delta: int = 0
    target_track: int = 0
    dragged: bool = False
    error: str = ''


class Timeline(QWidget):
    marker_selected = Signal(str)
    marker_edit_requested = Signal(str)
    transition_selected = Signal(str)
    overlay_selected = Signal(str)
    caption_selected = Signal(str)
    selected = Signal(str)
    selection_toggled = Signal(str)
    seeked = Signal(int)
    moved = Signal(str, int, int)
    group_moved = Signal(object, int)
    trimmed = Signal(str, str, int)
    grouped_trim_blocked = Signal()
    linked_trim_blocked = Signal()
    track_edit_lock_changed = Signal(int, bool)
    track_edit_blocked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = Project()
        self.trim_drag = None
        self._trim_generation = 0
        self.drag = None
        self._move_generation = 0
        self._selected_clip_ids = ()
        # MainWindow supplies its live editor and UI revision. Standalone
        # timelines use a disposable Editor solely for validated previews.
        self.edit_context = lambda: (None, None)
        self.selection_context = lambda: None
        self.selection = ''
        self.preview_selection = (None, None)
        self.marker_selection = ''
        self.caption_selection = ''
        self.overlay_selection = ''
        self.sticker_names = {}
        self.playhead = 0
        self.scale = 4.0
        self.waveforms = WaveformController(self, self.update)
        self.waveform_refresh = QTimer(self)
        self.waveform_refresh.setInterval(500)
        self.waveform_refresh.timeout.connect(self.refresh_waveforms)
        self.snapping_enabled = True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(MARKER_BAND_BOTTOM + 6)
        self.setMouseTracking(True)
        self.track_lock_buttons = []
        for track in range(4):
            button = QPushButton(self)
            button.setObjectName(f'trackEditLock{track}')
            button.setProperty('compactTimelineControl', True)
            compact_font = QFont(self.font()); compact_font.setPointSizeF(min(10.0, max(8.0, compact_font.pointSizeF())))
            button.setFont(compact_font)
            button.setCheckable(True)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setGeometry(4, 34 + track * 53 + 20, 79, 22)
            button.setStyleSheet('QPushButton { padding: 0px; min-height: 0px; } QPushButton:checked { background: #66512d; border: 1px solid #edc475; }')
            button.clicked.connect(lambda checked, track=track: self._toggle_track_lock(track, checked))
            self.track_lock_buttons.append(button)
        locale_events.changed.connect(self._retranslate_timeline)
        self._retranslate_timeline()

    def _toggle_track_lock(self, track, checked):
        self.cancel_gestures()
        self.track_edit_lock_changed.emit(track, checked)
        self.refresh_track_lock_buttons()

    def refresh_track_lock_buttons(self):
        labels = ('V1 / A1', 'V2 / A2', tr('Music 1'), tr('Music 2'))
        for track, button in enumerate(self.track_lock_buttons):
            locked = self.project.is_track_edit_locked(track)
            button.blockSignals(True)
            button.setChecked(locked)
            button.blockSignals(False)
            button.setText(tr('Locked') if locked else tr('Lock'))
            button.setAccessibleName(tr('Edit lock · {track}', track=labels[track]))
            button.setAccessibleDescription(tr('Track is locked for editing. Activate to unlock; playback and export are unchanged.') if locked else tr('Track is editable. Activate to lock editing; playback and export are unchanged.'))
            button.setToolTip(button.accessibleDescription())

    def _retranslate_timeline(self, *_):
        self.refresh_track_lock_buttons()
        self.setToolTip(str(tr('Click a clip to inspect. Drag its body to move with snapping. Drag either edge to trim by whole frames without moving other clips; Esc cancels. Click ruler to seek.')) + '\n' + str(tr('Shift-click clips to toggle selection. Drag a selected clip body to move the group horizontally.')) + '\n' + str(tr('Persistent groups select, move and delete together. Ungroup before trimming or editing one member.')) + '\n' + str(tr(LINK_TRIM_HELP)) + '\n' + str(tr('Source waveform: pre-gain / pre-fade; approximate source-time view, not processed output.')))
        self.setAccessibleName(tr('Timeline clips'))
        self._refresh_group_accessibility()
        self.update()

    def _refresh_group_accessibility(self):
        self.setAccessibleDescription(tr(
            '{groups} persistent groups; {selected} clips selected. Grouped clips move and delete together. Ungroup to edit separately.',
            groups=len(self.project.clip_groups), selected=len(self.selected_clip_ids)) + ' ' + tr(
            '{markers} project markers; open Timeline markers to inspect all annotations.',
            markers=len(self.project.timeline_markers)) + ' ' + tr(
            '{links} linked A/V pairs. Select both together; active V/A roles edit parameters independently.',
            links=len(self.project.av_links)))

    @property
    def selection(self):
        return self._selection

    @selection.setter
    def selection(self, value):
        if getattr(self, '_selection', '') != value:
            self.cancel_gestures()
        self._selection = value

    @property
    def selected_clip_ids(self):
        return self._selected_clip_ids

    @selected_clip_ids.setter
    def selected_clip_ids(self, values):
        values = reconcile_group_ids(self.project, values)
        if self._selected_clip_ids != values:
            self.cancel_gestures()
            self._selected_clip_ids = values
            self._refresh_group_accessibility()
            self.update()

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        if getattr(self, '_scale', value) != value:
            self.cancel_gestures()
        self._scale = value

    def rect_for(self, clip):
        return QRectF(88 + clip.start * self.scale, 34 + clip.track * 53, max(2, clip.duration * self.scale), 43)

    def set_project(self, project, selection='', selected_clip_ids=None):
        previous = self.project
        self.project = project
        self.selection = selection
        if selected_clip_ids is not None:
            self.selected_clip_ids = selected_clip_ids
        elif previous is not project or not selection or selection not in self.selected_clip_ids:
            self.selected_clip_ids = (selection,) if selection else ()
        self._trim_is_current()
        self._move_is_current()
        if self.marker_selection not in {m.id for m in project.timeline_markers}:
            self.marker_selection = ''
        self._retranslate_timeline()
        self.setMinimumWidth(max(700, int(150 + max(project.duration_frames, 120) * self.scale)))
        self.refresh_waveforms()
        self.update()

    def refresh_waveforms(self):
        if not self.isVisible():
            return
        media = {m.id: m for m in self.project.media}
        visible = self.visibleRegion().boundingRect()
        paths = [media[c.media_id].path for c in self.project.clips
                 if c.media_id in media and media[c.media_id].has_audio
                 and self.rect_for(c).intersects(QRectF(visible))]
        self.waveforms.request(paths, self.project)

    def showEvent(self, event):
        super().showEvent(event)
        self.waveform_refresh.start()
        self.refresh_waveforms()

    def hideEvent(self, event):
        self.cancel_gestures()
        self.waveform_refresh.stop()
        self.waveforms.suspend()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.cancel_gestures()
        self.waveform_refresh.stop()
        self.waveforms.close()
        super().closeEvent(event)

    def cancel_trim(self):
        self._trim_generation += 1
        if self.trim_drag is not None:
            self.trim_drag = None
            self.unsetCursor()
            self._retranslate_timeline()

    def cancel_move(self):
        self._move_generation += 1
        if self.drag is not None:
            self.drag = None
            self.unsetCursor()
            self.update()

    def cancel_gestures(self):
        self.cancel_trim()
        self.cancel_move()

    def _edit_token(self):
        editor, revision = self.edit_context()
        return (editor, revision, editor.revision if editor is not None else None,
                editor.session_id if editor is not None else None)

    def _trim_is_current(self):
        gesture = self.trim_drag
        if gesture is None:
            return False
        try:
            valid = (self.project is gesture.project
                and self.selection == gesture.clip_id
                and self.selected_clip_ids == gesture.clip_ids
                and group_membership_token(self.project) == gesture.membership
                and self._trim_generation == gesture.generation
                and self.selection_context() == gesture.selection_context
                and self.scale == gesture.scale
                and self._edit_token() == gesture.context
                and gesture.editor.project is self.project
                and gesture.editor.revision == gesture.editor_revision
                and gesture.editor.session_id == gesture.editor_session
                and project_fingerprint(self.project) == gesture.fingerprint
                and any(c.id == gesture.clip_id for c in self.project.clips))
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            self.cancel_trim()
        return valid

    def _move_is_current(self):
        gesture = self.drag
        if gesture is None:
            return False
        try:
            valid = (self.project is gesture.project
                and self.selection == gesture.primary_id
                and self.selected_clip_ids == gesture.clip_ids
                and group_membership_token(self.project) == gesture.membership
                and self.selection_context() == gesture.selection_context
                and self._move_generation == gesture.generation
                and self.scale == gesture.scale
                and self._edit_token() == gesture.context
                and gesture.editor.project is self.project
                and gesture.editor.revision == gesture.editor_revision
                and gesture.editor.session_id == gesture.editor_session
                and project_fingerprint(self.project) == gesture.fingerprint
                and set(gesture.clip_ids) <= {c.id for c in self.project.clips})
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            self.cancel_move()
        return valid

    def _preview_move(self, pos):
        if not self._move_is_current():
            return
        gesture = self.drag
        distance = pos.x() - gesture.press_x
        gesture.dragged = gesture.dragged or abs(distance) >= 4
        anchor = next(c for c in gesture.originals if c.id == gesture.anchor_id)
        delta = round(distance / gesture.scale) if gesture.dragged else 0
        target_track = anchor.track
        if len(gesture.clip_ids) == 1 and gesture.dragged:
            track = max(0, min(3, int((pos.y() - 34) / 53)))
            if (anchor.track < 2) == (track < 2):
                target_track = track
        if self.snapping_enabled and gesture.dragged:
            edges = [0, self.playhead]
            for clip in self.project.clips:
                if clip.id not in gesture.clip_ids:
                    edges.extend((clip.start, clip.end))
            # A single anchor produces one shared delta. Selected clips never
            # become snap targets for one another or change their spacing.
            deltas = [edge - source for edge in edges
                      for source in (anchor.start, anchor.end)]
            nearest = min(deltas, key=lambda value: abs(value - delta))
            if abs(nearest - delta) * gesture.scale <= 8:
                delta = nearest
        if (delta == gesture.delta and target_track == gesture.target_track
                and gesture.candidates):
            return
        gesture.delta, gesture.target_track = delta, target_track
        attempted = tuple(copy.deepcopy(c) for c in gesture.originals)
        for candidate in attempted:
            candidate.start += delta
            if len(attempted) == 1:
                candidate.track = target_track
        gesture.candidates = attempted
        try:
            if len(attempted) > 1 or target_track == anchor.track:
                gesture.candidates = tuple(gesture.editor.preview_move_clips(gesture.clip_ids, delta))
            else:
                gesture.editor.guard_track_editable(anchor.track)
                gesture.editor.guard_track_editable(target_track)
                candidate_project = copy.deepcopy(self.project)
                candidate = next(c for c in candidate_project.clips if c.id == anchor.id)
                candidate.start += delta
                candidate.track = target_track
                candidate_project.validate()
                gesture.candidates = (candidate,)
            gesture.error = ''
        except (ValueError, TypeError) as exc:
            # Keep the exact attempted geometry visible, including negatives;
            # never clamp or search for a different placement after rejection.
            gesture.error = str(exc)
        self.update()

    def move_preview_text(self):
        gesture = self.drag
        if gesture is None or not gesture.dragged:
            return ''
        if gesture.error:
            return tr('Cannot move: {error} · Esc to cancel', error=tr_error(gesture.error))
        return tr('{count} clips · {delta} frames · Esc to cancel',
                  count=len(gesture.clip_ids), delta=f'{gesture.delta:+d}')

    def trim_edge_at(self, clip, pos):
        """Six logical pixels, not device pixels; left wins a narrow-clip tie."""
        rect = self.rect_for(clip)
        if not rect.contains(pos):
            return None
        left, right = abs(pos.x() - rect.left()), abs(pos.x() - rect.right())
        if min(left, right) > 6:
            return None
        return 'left' if left <= right else 'right'

    def _preview_trim(self, x, *, force=False):
        if not self._trim_is_current():
            return
        gesture = self.trim_drag
        delta = round((x - gesture.press_x) / gesture.scale)
        if not force and delta == gesture.delta and (gesture.candidate is not None or gesture.error):
            return
        gesture.delta = delta
        try:
            if len(gesture.clip_ids) == 2 and linked_pair(self.project, gesture.clip_ids):
                gesture.candidates = gesture.editor.preview_trim_linked_av(gesture.clip_id, gesture.edge, delta)
                gesture.candidate = next(c for c in gesture.candidates if c.id == gesture.clip_id)
            else:
                gesture.candidate = gesture.editor.preview_trim_edge(gesture.clip_id, gesture.edge, delta)
                gesture.candidates = (gesture.candidate,)
            gesture.error = ''
        except (ValueError, TypeError) as exc:
            gesture.candidate = None
            gesture.candidates = ()
            gesture.error = str(exc)
        # The full impact/rejection remains available when the drawn label is elided.
        self.setToolTip(self.trim_preview_text())
        self.setAccessibleDescription(self.trim_preview_text())
        self.update()

    def trim_preview_rects(self):
        """Both exact attempted outlines, even when a candidate is rejected."""
        gesture = self.trim_drag
        if gesture is None:
            return ()
        if gesture.candidates:
            return tuple(self.rect_for(c) for c in gesture.candidates)
        result = []
        for clip in self.project.clips:
            if clip.id not in gesture.clip_ids:
                continue
            rect = self.rect_for(clip)
            if gesture.edge == 'left':
                rect.setLeft(rect.left() + gesture.delta * gesture.scale)
            else:
                rect.setRight(rect.right() + gesture.delta * gesture.scale)
            result.append(rect.normalized())
        return tuple(result)

    def trim_preview_label_rect(self, text, anchor):
        """Keep the preview label inside visible content, clear of track locks."""
        visible = QRectF(self.visibleRegion().boundingRect())
        left_limit = max(88.0, visible.left()) + 6
        right_limit = visible.right() - 6
        width = min(self.fontMetrics().horizontalAdvance(text) + 28,
                    max(0, right_limit - left_limit))
        left = max(left_limit, min(anchor.left(), right_limit - width))
        return QRectF(left, anchor.top() + 3, width, 25)

    def trim_preview_text(self):
        gesture = self.trim_drag
        if gesture is None:
            return ''
        if gesture.error:
            if len(gesture.clip_ids) == 2:
                return tr('Cannot trim linked V+A: {delta} frames · {error} · Esc to cancel',
                          delta=f'{gesture.delta:+d}', error=tr_error(gesture.error))
            return tr('Cannot trim: {error} · Esc to cancel', error=tr_error(gesture.error))
        if gesture.candidate is None:
            return ''
        if len(gesture.clip_ids) == 2:
            link = linked_pair(self.project, gesture.clip_ids)
            candidates = {clip.id: clip for clip in gesture.candidates}
            if link is None or not {link.video_clip_id, link.audio_clip_id} <= candidates.keys():
                return ''
            return tr('V {video_duration}f · A {audio_duration}f · {delta} frames · Linked V+A · {edge} · J/L offsets preserved · Fades stay edge-anchored; audio output may change · Esc to cancel',
                      video_duration=candidates[link.video_clip_id].duration,
                      audio_duration=candidates[link.audio_clip_id].duration,
                      edge=tr('Trim left') if gesture.edge == 'left' else tr('Trim right'),
                      delta=f'{gesture.delta:+d}')
        return tr('{edge} · {delta} frames · Duration {duration} frames · Esc to cancel',
                  edge=tr('Trim left') if gesture.edge == 'left' else tr('Trim right'),
                  delta=f'{gesture.delta:+d}', duration=gesture.candidate.duration)

    def focusOutEvent(self, event):
        self.cancel_gestures()
        super().focusOutEvent(event)

    def group_tooltip(self, clip_id):
        group = clip_group(self.project, clip_id)
        link = av_link(self.project, clip_id)
        if link is not None:
            return tr('Linked A/V · video {video_id} · audio {audio_id}. Select a role for parameter edits; unlink for independent timing.', video_id=link.video_clip_id, audio_id=link.audio_clip_id)
        if group is None:
            return ''
        return tr('Group {number} · {count} clips. Ungroup to edit separately.',
                  number=self.project.clip_groups.index(group) + 1,
                  count=len(group.clip_ids))

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            marker = marker_at(self, event.pos())
            if marker is not None:
                QtWidgets.QToolTip.showText(event.globalPos(), marker_tooltip(self, marker), self)
                return True
            for clip in reversed(self.project.clips):
                if self.rect_for(clip).contains(event.pos()):
                    text = self.group_tooltip(clip.id)
                    if text:
                        QtWidgets.QToolTip.showText(event.globalPos(), text, self)
                        return True
                    break
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, 'trim_drag'):
            self.cancel_gestures()
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and (self.trim_drag is not None or self.drag is not None):
            self.cancel_gestures()
            event.accept()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event):
        if self.trim_drag is None:
            self.unsetCursor()
        super().leaveEvent(event)

    def paint_source_waveform(self, painter, clip, media, rect):
        peaks = self.waveforms.get(media.path)
        if peaks is None:
            status = self.waveforms.status(media.path)
            label = {'loading': 'Waveform loading…', 'unavailable': 'Waveform unavailable',
                     'limited': 'Waveform view limit'}.get(status, 'Waveform unavailable')
            painter.save()
            painter.setPen(QColor('#a6b8bd'))
            painter.drawText(rect.adjusted(7, 21, -5, -3), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(tr(label), Qt.TextElideMode.ElideRight, max(0, int(rect.width() - 12))))
            painter.restore()
            return
        visible = rect.adjusted(5, 22, -5, -4).intersected(QRectF(self.visibleRegion().boundingRect()))
        if visible.width() < 1 or visible.height() < 1:
            return
        left, right = visible.left(), visible.right()
        count = min(4096, max(1, int(visible.width())))
        values = map_peaks(peaks, clip, self.project.fps, media.audio_offset,
                          (left - rect.left()) / self.scale,
                          (right - rect.left()) / self.scale, count)
        painter.save()
        painter.setClipRect(visible)
        painter.setPen(QPen(QColor('#73968e' if clip.muted else '#a1e5d5'), 1))
        middle = visible.center().y()
        half = visible.height() / 2
        for i, value in enumerate(values):
            x = left + (i + 0.5) * visible.width() / count
            amplitude = min(1.0, float(value)) * half
            painter.drawLine(self._point(x, middle - amplitude), self._point(x, middle + amplitude))
        painter.restore()

    def paintEvent(self, event):
        self._trim_is_current()
        self._move_is_current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor('#181e26'))
        p.setFont(self.font())
        fps = float(self.project.fps)
        step_seconds = max(1, math.ceil(75 / max(1, self.scale * fps)))
        for sec in range(0, int(self.width() / max(1, self.scale * fps)) + 1, step_seconds):
            x = 88 + sec * fps * self.scale
            p.setPen(QColor('#35414e')); p.drawLine(int(x), 28, int(x), 353)
            p.setPen(QColor('#8997a8')); p.drawText(int(x) + 3, 20, f'{sec // 60}:{sec % 60:02}')
        start, end = self.preview_selection
        p.save()
        p.setClipRect(QRectF(88, 0, max(0, self.width() - 88), 33))
        if start is not None and end is not None and start < end:
            p.fillRect(QRectF(88 + start * self.scale, 24, (end - start) * self.scale, 8), QColor('#397f80'))
        p.setPen(QPen(QColor('#7ae0c4'), 2))
        for frame, label in ((start, 'I'), (end, 'O')):
            if frame is not None:
                x = 88 + frame * self.scale
                p.drawLine(int(x), 2, int(x), 32)
                p.drawText(int(x) + 3, 11, label)
        p.restore()
        p.setPen(QColor('#8997a8'))
        p.drawText(10, 365, tr('Source waveform · pre-gain / pre-fade'))
        for track, label in enumerate(('V1 / A1', 'V2 / A2', tr('Music 1'), tr('Music 2'), tr('Subtitles'), tr('Stickers'))):
            y = 34 + track * 53
            p.fillRect(0, y, 87, 45, QColor('#242e39'))
            p.setPen(QColor('#b4c1cf')); p.drawText(QRectF(10, y, 73, 19 if track < 4 else 43), Qt.AlignmentFlag.AlignVCenter, label)
        media = {m.id: m for m in self.project.media}
        for c in sorted(self.project.clips, key=lambda c: (c.track, c.start)):
            r = self.rect_for(c)
            color = '#514a3d' if self.project.is_track_edit_locked(c.track) else ('#365d83' if c.track < 2 else '#386554')
            selected = c.id in self.selected_clip_ids or c.id == self.selection
            p.setBrush(QColor(color)); p.setPen(QPen(QColor('#7ae0c4' if selected else '#5d7b93'), 2 if selected else 1))
            p.drawRoundedRect(r, 4, 4)
            source = media.get(c.media_id)
            has_waveform = source is not None and source.has_audio
            if has_waveform:
                self.paint_source_waveform(p, c, source, r)
            p.save(); p.setClipRect(r.adjusted(5, 0, -5, 0)); p.setPen(QColor('#f1f5fa'))
            badge_width = self.group_badge_width(p, c, r)
            right_inset = badge_width + 8 if badge_width else 4
            p.drawText(r.adjusted(7, 0, -right_inset, -21 if has_waveform else 0),
                       Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(
                           media.get(c.media_id).name if c.media_id in media else tr('Missing'),
                           Qt.TextElideMode.ElideRight, max(0, int(r.width() - 7 - right_inset))))
            p.restore()
            if c.fade_in or c.fade_out:
                p.setPen(QPen(QColor('#acdfcf'), 1))
                if c.fade_in: p.drawLine(r.bottomLeft(), r.topLeft() + self._point(c.fade_in * self.scale, 0))
                if c.fade_out: p.drawLine(r.topRight() - self._point(c.fade_out * self.scale, 0), r.bottomRight())
            self.paint_group_badge(p, c, r)
            pair = av_link(self.project, c.id)
            pair_selected = pair is not None and pair == linked_pair(self.project, self.selected_clip_ids)
            if ((c.id == self.selection and pair is None or pair_selected)
                    and not locked_clips(self.project, reconcile_group_ids(self.project, (c.id,)))
                    and clip_group(self.project, c.id) is None):
                p.setPen(QPen(QColor('#d5fff4'), 2))
                inset = min(3, r.width() / 4)
                for handle_x in (r.left() + inset, r.right() - inset):
                    p.drawLine(self._point(handle_x, r.top() + 14), self._point(handle_x, r.bottom() - 14))
        paint_transitions(self, p)
        for caption in self.project.captions:
            r = self.caption_rect(caption)
            p.setBrush(QColor('#675483')); p.setPen(QPen(QColor('#dfc0ff' if caption.id == self.caption_selection else '#9d81b8'), 1))
            p.drawRoundedRect(r, 4, 4)
            p.save(); p.setClipRect(r.adjusted(4, 0, -4, 0)); p.setPen(QColor('#f1f5fa'))
            p.drawText(r.adjusted(6, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(caption.text.replace('\n', ' / '), Qt.TextElideMode.ElideRight, max(0, int(r.width() - 9)))); p.restore()
        for overlay in sorted(self.project.overlays, key=lambda o: (o.z_order, o.id)):
            r = self.overlay_rect(overlay)
            p.setBrush(QColor('#80633b' if overlay.enabled else '#40464e'))
            p.setPen(QPen(QColor('#ffe0a1' if overlay.id == self.overlay_selection else '#b19362'), 2 if overlay.id == self.overlay_selection else 1))
            p.drawRoundedRect(r, 4, 4)
            p.save(); p.setClipRect(r.adjusted(4, 0, -4, 0)); p.setPen(QColor('#fff4df'))
            p.drawText(r.adjusted(6, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter, p.fontMetrics().elidedText(self.sticker_names.get(overlay.asset_path, Path(overlay.asset_path).stem) + (tr(' · hidden') if not overlay.enabled else ''), Qt.TextElideMode.ElideRight, max(0, int(r.width() - 9)))); p.restore()
        paint_marker_band(self, p)
        x = 88 + self.playhead * self.scale
        p.setPen(QPen(QColor('#f3be73'), 2)); p.drawLine(int(x), 25, int(x), 353)
        if not self.project.clips:
            p.setPen(QColor('#8d9bab')); p.drawText(125, 100, tr('Import media, then add it to a track. Your source files stay untouched.'))
        gesture = self.trim_drag
        if gesture is not None:
            original = next(c for c in self.project.clips if c.id == gesture.clip_id)
            rect = self.rect_for(gesture.candidate or original)
            color = QColor('#ff9a8b' if gesture.error else '#9cffe2')
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            for outline in self.trim_preview_rects():
                p.drawRoundedRect(outline, 4, 4)
            text = self.trim_preview_text()
            label = self.trim_preview_label_rect(text, rect)
            p.fillRect(label, QColor('#111820'))
            p.setPen(color)
            p.drawText(label.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(label.width() - 16))))
        gesture = self.drag
        if gesture is not None and gesture.dragged and gesture.candidates:
            color = QColor('#ff9a8b' if gesture.error else '#9cffe2')
            fill = QColor(color)
            fill.setAlpha(35)
            p.setBrush(fill)
            p.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
            for candidate in gesture.candidates:
                p.drawRoundedRect(self.rect_for(candidate), 4, 4)
            anchor = next(c for c in gesture.candidates if c.id == gesture.anchor_id)
            rect = self.rect_for(anchor)
            text = self.move_preview_text()
            visible = QRectF(self.visibleRegion().boundingRect())
            width = min(p.fontMetrics().horizontalAdvance(text) + 28, max(1, visible.width() - 12))
            left = max(visible.left() + 6, min(rect.left(), visible.right() - width - 6))
            label = QRectF(left, rect.top() + 3, width, 25)
            p.fillRect(label, QColor('#111820'))
            p.setPen(color)
            p.drawText(label.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(width - 16))))

    def caption_rect(self, caption):
        return QRectF(88 + caption.start * self.scale, 246, max(2, caption.duration * self.scale), 43)

    def overlay_rect(self, overlay):
        return QRectF(88 + overlay.start * self.scale, 299, max(2, overlay.duration * self.scale), 43)

    @staticmethod
    def _point(x, y):
        from PySide6.QtCore import QPointF
        return QPointF(x, y)

    def group_badge_width(self, painter, clip, rect):
        group = clip_group(self.project, clip.id)
        link = av_link(self.project, clip.id)
        if (group is None and link is None) or rect.width() < 42:
            return 0
        label = link_badge(self.project, clip) if link else tr('G{number}', number=self.project.clip_groups.index(group) + 1)
        return min(rect.width() - 8, painter.fontMetrics().horizontalAdvance(label) + 8)

    def paint_group_badge(self, painter, clip, rect):
        group = clip_group(self.project, clip.id)
        link = av_link(self.project, clip.id)
        if group is None and link is None:
            return
        painter.save()
        painter.setClipRect(rect.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor('#d1b4ff'), 2))
        painter.drawLine(rect.bottomLeft() + self._point(4, -3),
                         rect.bottomRight() + self._point(-4, -3))
        if rect.width() >= 42:
            label = link_badge(self.project, clip) if link else tr('G{number}', number=self.project.clip_groups.index(group) + 1)
            width = self.group_badge_width(painter, clip, rect)
            height = min(rect.height() - 8, painter.fontMetrics().height() + 2)
            badge = QRectF(rect.right() - width - 4, rect.top() + 3, width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#443657'))
            painter.drawRoundedRect(badge, 3, 3)
            painter.setPen(QColor('#eadcff'))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() != Qt.MouseButton.LeftButton: return
        self.cancel_gestures()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if marker_band_rect(self).contains(pos):
            marker = marker_at(self, pos)
            if marker is not None:
                self.marker_selection = marker.id
                self.marker_selected.emit(marker.id)
                self.update()
            return  # Empty marker lane is not a ruler/clip seek target.
        for transition in reversed(self.project.video_transitions):
            if transition_rect(self, transition).contains(pos):
                self.transition_selected.emit(transition.id); self.update(); return
        self.transition_selection = ""
        for overlay in sorted(self.project.overlays, key=lambda o: (o.z_order, o.id), reverse=True):
            if self.overlay_rect(overlay).contains(pos):
                self.overlay_selection = overlay.id; self.overlay_selected.emit(overlay.id); self.update(); return
        for caption in reversed(self.project.captions):
            if self.caption_rect(caption).contains(pos):
                self.caption_selection = caption.id; self.caption_selected.emit(caption.id); self.update(); return
        for c in reversed(self.project.clips):
            if self.rect_for(c).contains(pos):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.selection_toggled.emit(c.id)
                    self.update()
                    return
                if locked_clips(self.project, reconcile_group_ids(self.project, (c.id,))) or (
                        c.id in self.selected_clip_ids and locked_clips(self.project, self.selected_clip_ids)):
                    if c.id not in self.selected_clip_ids:
                        self.selection = c.id
                        self.selected_clip_ids = (c.id,)
                        self.selected.emit(c.id)
                    self.setToolTip(tr(LOCK_REASON))
                    self.track_edit_blocked.emit()
                    self.update()
                    return
                edge = self.trim_edge_at(c, pos)
                persistent_group = clip_group(self.project, c.id)
                pair = av_link(self.project, c.id)
                if edge and pair is not None and linked_pair(self.project, self.selected_clip_ids) != pair:
                    # Never collapse extras or silently turn a multi-pair edit
                    # into a trim of whichever member happened to be clicked.
                    self.setToolTip(tr(LINK_TRIM_HELP)); self.linked_trim_blocked.emit()
                    self.update(); return
                if edge and persistent_group is not None:
                    self.selection = c.id
                    self.selected_clip_ids = (c.id,)
                    self.selected.emit(c.id)
                    self.setToolTip(tr(GROUP_TRIM_HELP))
                    self.grouped_trim_blocked.emit()
                    self.update()
                    return
                project, context, scale = self.project, self._edit_token(), self.scale
                try:
                    fingerprint = project_fingerprint(project)
                except (ValueError, TypeError):
                    return
                preserve_group = not edge and len(self.selected_clip_ids) > 1 and c.id in self.selected_clip_ids
                if not preserve_group:
                    self.selection = c.id
                    self.selected_clip_ids = (c.id,)
                activate_member = not preserve_group or linked_pair(self.project, self.selected_clip_ids)
                if activate_member:
                    # Changing the explicit A/V role cancels the previous
                    # gesture. Match the window's clicked-first pair ordering
                    # before capturing the generation; selection listeners
                    # below must still leave that generation unchanged.
                    self.selection = c.id
                    self.selected_clip_ids = (c.id,)
                generation, move_generation = self._trim_generation, self._move_generation
                if activate_member:
                    self.selected.emit(c.id)
                # Selection listeners can synchronously refresh or even replace
                # the project. Start only against the exact clicked context.
                try:
                    current_fingerprint = project_fingerprint(self.project)
                    if (self.project is not project
                            or c.id not in self.selected_clip_ids
                            or (not preserve_group and self.selection != c.id)
                            or self._edit_token() != context or self.scale != scale
                            or self._trim_generation != generation
                            or self._move_generation != move_generation
                            or current_fingerprint != fingerprint):
                        return
                    editor = context[0] if context[0] is not None else Editor(project)
                except (ValueError, TypeError):
                    return
                if locked_clips(self.project, self.selected_clip_ids):
                    self.setToolTip(tr(LOCK_REASON))
                    self.track_edit_blocked.emit()
                    self.update()
                    return
                if edge:
                    self.trim_drag = _TrimGesture(c.id, edge, pos.x(), scale, project,
                        editor, context, editor.revision, editor.session_id, fingerprint,
                        self.selection_context(), self.selected_clip_ids,
                        group_membership_token(project), generation)
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    self._preview_trim(pos.x())
                    return
                ids = self.selected_clip_ids
                originals = tuple(copy.deepcopy(clip) for clip in project.clips if clip.id in ids)
                self.drag = _MoveGesture(ids, c.id, self.selection, pos.x(), scale,
                    project, editor, context, editor.revision, editor.session_id,
                    fingerprint, self.selection_context(), move_generation,
                    originals, membership=group_membership_token(project),
                    collapse_on_release=preserve_group and not any(clip_group(project, cid) or av_link(project, cid) for cid in ids),
                    target_track=c.track)
                self._preview_move(pos)
                self.update(); return
        if pos.x() >= 88:
            self.seeked.emit(max(0, min(self.project.duration_frames - 1, round((pos.x() - 88) / self.scale))))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and marker_band_rect(self).contains(event.position()):
            self.cancel_gestures()
            marker = marker_at(self, event.position())
            if marker is not None:
                self.marker_selection = marker.id
                self.marker_edit_requested.emit(marker.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if self.trim_drag is not None:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self.cancel_trim()
                return
            self._preview_trim(event.position().x())
            return
        if self.drag is not None:
            if not event.buttons() & Qt.MouseButton.LeftButton:
                self.cancel_move()
                return
            self._preview_move(event.position())
            return
        for clip in reversed(self.project.clips):
            if (not self.project.is_track_edit_locked(clip.track)
                    and clip_group(self.project, clip.id) is None
                    and not locked_clips(self.project, reconcile_group_ids(self.project, (clip.id,)))
                    and (av_link(self.project, clip.id) is None or
                         av_link(self.project, clip.id) == linked_pair(self.project, self.selected_clip_ids))
                    and self.trim_edge_at(clip, event.position()) is not None):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                return
        self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.trim_drag is not None:
            self._preview_trim(event.position().x(), force=True)
            gesture = self.trim_drag
            if gesture is None:
                return
            self.cancel_trim()
            if gesture.delta and gesture.candidate is not None and not gesture.error:
                self.trimmed.emit(gesture.clip_id, gesture.edge, gesture.delta)
            return
        if self.drag is None: return
        self._preview_move(event.position())
        gesture = self.drag
        if gesture is None:
            return
        self.cancel_move()
        if not gesture.dragged:
            if gesture.collapse_on_release:
                self.selection = gesture.anchor_id
                self.selected_clip_ids = (gesture.anchor_id,)
                self.selected.emit(gesture.anchor_id)
            return
        if gesture.error or not gesture.candidates:
            return
        if len(gesture.clip_ids) > 1:
            if gesture.delta:
                self.group_moved.emit(gesture.clip_ids, gesture.delta)
        else:
            candidate = gesture.candidates[0]
            original = gesture.originals[0]
            if candidate.start != original.start or candidate.track != original.track:
                self.moved.emit(candidate.id, candidate.start, candidate.track)


class MainWindow(LinkedAVRollMixin, LinkedAVMixin, SourceAudioReattachMixin, SourceAudioSeparationMixin, ClipClipboardMixin, TimelineMarkersMixin, TrackEditLocksMixin, ClipGroupsMixin, MediaBinsMixin, VideoTransitionMixin, WhiteBalancePanelMixin, ColorReferenceMixin, GradeTransferPanelMixin, RecoveryMixin, SoundsPanelMixin, WorkspaceModeMixin, InputColorPanelMixin, LocalAIPanelMixin, EffectsPanelMixin, CaptionPanelMixin, QMainWindow):
    def __init__(self, *, workspace_settings=None, ui_language=None, shortcut_settings=None, shortcut_platform=None, sound_library_root=None, recovery_root=None):
        super().__init__()
        self.sound_library_root = Path(sound_library_root) if sound_library_root is not None else None
        self.init_workspace(workspace_settings)
        saved_language = ui_language or self.workspace_settings.value('language', get_locale())
        try:
            set_locale(saved_language)
        except (ValueError, AttributeError):
            set_locale('zh-CN')
        self.setWindowTitle(tr('{brand} · independent desktop prototype', brand=display_name()))
        self.resize(1440, 960)
        self.setMinimumSize(1000, 700)
        self.setStyleSheet(STYLE)
        self.editor = Editor(Project())
        self.project_path = None
        self.selection = ''
        self.active_selection_type = 'clip'
        self.selection_generation = 0
        self._selection_editor_token = (id(self.editor), self.editor.session_id)
        self.dirty = False
        self.revision = 0
        self.preview_revision = -1
        self.preview_mode = "graded"
        self.loaded_preview_mode = None
        self.preview_paths = {}
        self.preview_request = 0
        self.preview_pending = False
        self.init_reference_state()
        self.jobs = set()
        self.init_clip_clipboard()
        self.init_source_audio_separation()
        self.init_source_audio_reattach()
        self.init_linked_av()
        self.init_linked_roll()
        self._job_generation = 0
        self.preview_job = None
        self.export_job = None
        self.import_job = None
        self.asr_job = None
        self.tts_job = None
        self.audio_analysis_job = None
        self.audio_analysis_dialog = None
        self.master_gain_dialog = None
        self.audio_gain_dialog = None
        self.ripple_delete_dialog = None
        self.gap_close_dialog = None
        self.timeline_markers_dialog = None
        self.source_viewer_dialog = None
        self.pending_speech = None
        self.cache = Path(tempfile.mkdtemp(prefix='lumen-preview-'))
        self.closing = False
        self._job_close_timer = QTimer(self)
        self._job_close_timer.setSingleShot(True)
        self._job_close_timer.timeout.connect(self.close)
        self.loading_controls = False
        self.scope_counter = 0
        self.setAcceptDrops(True)
        self._build_ui()
        self.shortcuts = ShortcutController(self, shortcut_settings, shortcut_platform)
        self._build_menu()
        apply_ui_layout_tokens(self)
        self.finish_workspace_ui()
        self.refresh()
        try:
            restore_component_preferences(self)
        except (OSError, ValueError) as exc:
            self.ai_model_status.setText(tr("Local component preferences need attention: {error}", error=tr_error(exc)))
        self.init_recovery(recovery_root)
        locale_events.changed.connect(self._retranslate_core)
        if self.shortcuts.load_warning:
            self.statusBar().showMessage(self.shortcuts.load_warning, 12000)

    @property
    def project(self): return self.editor.project

    def _button(self, text, fn, primary=False):
        b = QPushButton(text); b.clicked.connect(fn)
        if primary: b.setObjectName('primary')
        return b

    def _build_ui(self):
        self.audio = QAudioOutput(self); self.audio.setVolume(.8)
        self.video = VideoViewer(); self.video.setMinimumSize(280, 64)
        self.replace_preview_player()
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root); outer.setContentsMargins(12, 8, 12, 10)
        head = QHBoxLayout()
        self.brand_label = _make_brand_label()
        head.addWidget(self.brand_label); head.addStretch(); head.addWidget(self.build_workspace_switch()); head.addWidget(self.build_language_switch()); head.addStretch()
        self.format_label = QLabel(''); head.addWidget(self.format_label)
        self.export_button = self._button(tr('Export film'), self.export_dialog, True); head.addWidget(self.export_button)
        outer.addLayout(head)
        outer.addWidget(self.build_workspace_notice())
        self.vertical_split = QSplitter(Qt.Orientation.Vertical)
        self.vertical_split.setChildrenCollapsible(False)
        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.setChildrenCollapsible(False)
        bin_panel = QWidget(); bin_layout = QVBoxLayout(bin_panel); bin_layout.setContentsMargins(0, 0, 8, 0)
        bin_layout.addWidget(QLabel(tr('1   MEDIA')))
        bin_layout.addWidget(self._button(tr('+ Import video /\naudio'), self.import_dialog, True))
        self.import_apple_log_button = self._button(tr('Apple Log 1'), self.import_apple_log_dialog)
        self.import_apple_log_button.setToolTip(tr('Explicit assignment, not camera detection. Tested synthetic inputs only; no Apple Log 2, Apple LUT or Resolve matching claim.'))
        bin_layout.addWidget(self.import_apple_log_button)
        self.apple_log_import_badge = QLabel(tr('BT.2020 · experimental'))
        self.apple_log_import_badge.setWordWrap(True)
        self.apple_log_import_badge.setToolTip(tr('Explicit assignment, not camera detection. Tested synthetic inputs only; no Apple Log 2, Apple LUT or Resolve matching claim.'))
        bin_layout.addWidget(self.apple_log_import_badge)
        self.build_media_bins(bin_layout)
        self.track_choice = QComboBox(); self.track_choice.addItems([tr('V1 · base picture'), tr('V2 · upper picture'), tr('Music 1'), tr('Music 2')]); bin_layout.addWidget(self.track_choice)
        self.add_media_button = self._button(tr('Add to\nselected track'), self.add_selected_media)
        bin_layout.addWidget(self.add_media_button)
        self.source_viewer_button = self._button(tr('Open source\nviewer…'), self.open_source_viewer)
        self.source_viewer_button.setAccessibleName(tr('Open source viewer…'))
        self.source_viewer_button.setToolTip(tr('Open source\nviewer…'))
        bin_layout.addWidget(self.source_viewer_button)
        self.track_choice.currentIndexChanged.connect(lambda *_: self.refresh_track_lock_controls())
        bin_layout.addWidget(self._button(tr('Relink missing\nmedia…'), self.relink_dialog))
        media_help = QLabel(tr('Double-click to append.\nDrop local files to import.')); media_help.setWordWrap(True)
        bin_layout.addWidget(media_help)
        self.media_scroll = QScrollArea(); self.media_scroll.setWidgetResizable(True)
        self.media_scroll.setMinimumWidth(190)
        for control in bin_panel.findChildren(QtWidgets.QComboBox):
            control.setProperty('compactMediaControl', True)
        for control in bin_panel.findChildren(QtWidgets.QPushButton):
            control.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed)
            if not control.toolTip():
                control.setToolTip(control._translation_bindings.get('setText', control.text()))
        self.media_scroll.setWidget(bin_panel)
        self.main_split.addWidget(self.media_scroll)
        viewer = QWidget(); view_layout = QVBoxLayout(viewer); view_layout.setContentsMargins(0, 0, 0, 0)
        # Six inter-row gaps share the compact viewer budget with the graph.
        # Keep real controls/font metrics intact and use 2px gaps consistently.
        view_layout.setSpacing(2)
        # Propagate the graph/header floor through the splitter. Otherwise a
        # resized splitter can clip children below the viewer's size hint.
        view_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        viewer_head = QHBoxLayout(); viewer_head.setSpacing(6)
        # Keep the bounded preview entry in the existing header height. A
        # separate action row clips the scope graph at the 1000 x 700 floor.
        view_layout.addLayout(viewer_head)
        view_layout.addWidget(self.video, 1)
        self.preview_mode_choice = QComboBox(); self.preview_mode_choice.addItems([tr('After · all corrections'), tr('Before · restoration + color off')]); self.preview_mode_choice.setToolTip(tr('Viewer comparison only: Before bypasses quality restoration, every clip grade and LUT in a render snapshot. Input normalization, stabilization, effects and overlays stay active. Export always uses the stored project settings.')); self.preview_mode_choice.currentIndexChanged.connect(self.change_preview_mode); viewer_head.addWidget(self.preview_mode_choice, 1)
        self.build_reference_controls(view_layout)
        self.preview_label = PreviewStatusLabel(tr('Import and add a clip to begin.')); view_layout.addWidget(self.preview_label)
        playback = QHBoxLayout()
        self.play_button = self._button(tr('Play'), self.toggle_play); playback.addWidget(self.play_button)
        playback.addWidget(self._button(tr('−1 frame'), lambda: self.seek_frame(self.timeline.playhead - 1)))
        playback.addWidget(self._button(tr('+1 frame'), lambda: self.seek_frame(self.timeline.playhead + 1)))
        self.preview_button = self._button(tr('Build preview'), self.build_preview); playback.addWidget(self.preview_button)
        self.final_frame_preview_button = self._button(tr('Final still…'), self.open_final_frame_preview)
        self.final_frame_preview_button.setToolTip(tr('Final composite still · Rec.709 SDR · No audio'))
        playback.addWidget(self.final_frame_preview_button)
        playback.addStretch(); view_layout.addLayout(playback)
        self.range_preview_button = self._button(tr('Preview around playhead'), self.open_range_preview)
        self.range_preview_button.setToolTip(tr('Rendered range · Rec.709 SDR · 48 kHz stereo mix'))
        viewer_head.insertWidget(0, self.range_preview_button)
        self.position_label = QLabel('00:00.000 · f0'); self.position_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.scrub = QSlider(Qt.Orientation.Horizontal); self.scrub.valueChanged.connect(self.seek_frame)
        seek_row = QHBoxLayout(); seek_row.addWidget(self.scrub, 1); seek_row.addWidget(self.position_label); view_layout.addLayout(seek_row)
        self.scopes = Scopes(); view_layout.addWidget(self.scopes)
        self.main_split.addWidget(viewer)
        self.side_tabs = QTabWidget()
        self.side_tabs.setMinimumWidth(320)
        self.side_tabs.tabBar().setExpanding(False)
        self.side_tabs.setUsesScrollButtons(True)
        self.inspector = self._build_inspector(); self.side_tabs.addTab(self.inspector, tr("Clip"))
        self.side_tabs.addTab(self.build_caption_panel(), tr("Captions"))
        self.build_effects_panels()
        self.side_tabs.addTab(self.clip_effects_panel, tr('Effects'))
        self.side_tabs.addTab(self.stickers_panel, tr('Stickers'))
        self.ai_panel = self.build_ai_panel()
        self.side_tabs.addTab(self.ai_panel, tr('Local AI'))
        self.sounds_panel = self.build_sounds_panel()
        self.side_tabs.addTab(self.sounds_panel, tr('Sounds'))
        self.side_tabs.currentChanged.connect(lambda _: self.sound_audition.stop())
        self.main_split.addWidget(self.side_tabs)
        self.main_split.setSizes([210, 720, 460]); self.vertical_split.addWidget(self.main_split)
        timeline_panel = QWidget(); timeline_layout = QVBoxLayout(timeline_panel); timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        timeline_head = QHBoxLayout(); timeline_head.addWidget(QLabel(tr('3   TIMELINE')))
        self.split_clip_button = self._button(tr('Split at playhead'), self.split_selected)
        self.delete_clips_button = self._button(tr('Delete selected clips'), self.delete_selected)
        timeline_head.addWidget(self.split_clip_button)
        timeline_head.addWidget(self.delete_clips_button)
        timeline_head.addWidget(self._button(tr('Undo'), self.undo))
        timeline_head.addWidget(self._button(tr('Redo'), self.redo))
        self.snapping_button = self._button(tr('Snapping'), self.toggle_snapping)
        self.snapping_button.setCheckable(True)
        self.snapping_button.setChecked(True)
        self.snapping_button.setToolTip(tr('Snapping on'))
        timeline_head.addWidget(self.snapping_button)
        timeline_head.addStretch()
        timeline_head.addWidget(QLabel(tr('Zoom')))
        zoom = QSlider(Qt.Orientation.Horizontal); zoom.setRange(1, 20); zoom.setValue(4); zoom.setMaximumWidth(150); zoom.valueChanged.connect(self.zoom_timeline); timeline_head.addWidget(zoom); timeline_layout.addLayout(timeline_head)
        self.timeline = Timeline(); self.timeline.sticker_names = {self.sticker_choice.itemData(i): self.sticker_choice.itemText(i) for i in range(self.sticker_choice.count())}; self.timeline.overlay_selected.connect(self.select_overlay); self.timeline.caption_selected.connect(self.select_caption); self.timeline.selected.connect(self.select_clip); self.timeline.seeked.connect(self.seek_frame); self.timeline.moved.connect(self.move_clip)
        self.build_video_transition_controls(timeline_head)
        self.timeline.transition_selected.connect(self.select_video_transition)
        self.timeline.edit_context = lambda: (self.editor, self.revision)
        self.timeline.selection_context = lambda: (self.selection, self.active_selection_type,
            self.selected_clip_ids, self.selection_generation)
        self.timeline.selection_toggled.connect(self.toggle_clip_selection)
        self.timeline.group_moved.connect(self.move_selected_clips)
        self.timeline.trimmed.connect(self.trim_clip_edge)
        self.timeline.grouped_trim_blocked.connect(self.explain_grouped_trim)
        self.timeline.linked_trim_blocked.connect(self.explain_linked_trim)
        self.timeline.track_edit_lock_changed.connect(self.toggle_track_edit_lock)
        self.timeline.track_edit_blocked.connect(self.explain_track_edit_lock)
        self.timeline.marker_selected.connect(self.navigate_timeline_marker)
        self.timeline.marker_edit_requested.connect(self.edit_timeline_marker)
        self.timeline_scroll = QScrollArea(); self.timeline_scroll.setWidget(self.timeline); self.timeline_scroll.setWidgetResizable(True); self.timeline_scroll.setMinimumHeight(120); timeline_layout.addWidget(self.timeline_scroll, 1)
        self.vertical_split.addWidget(timeline_panel); self.vertical_split.setStretchFactor(0, 3); self.vertical_split.setStretchFactor(1, 2); self.vertical_split.setSizes([500, 300]); outer.addWidget(self.vertical_split, 1)
        progress = QHBoxLayout(); self.progress_label = QLabel(tr('Ready · local files only')); progress.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0); progress.addWidget(self.progress_bar, 1)
        self.cancel_button = self._button(tr('Cancel job'), self.cancel_jobs); self.cancel_button.setEnabled(False); progress.addWidget(self.cancel_button); outer.addLayout(progress)
        self.statusBar().showMessage(tr('Start here: import → add to track → select clip → cut or color → build preview → export'))

    def build_language_switch(self):
        self.language_choice = QComboBox()
        self.language_choice.setObjectName('uiLanguage')
        self.language_choice.setAccessibleName(tr('Language'))
        self.language_choice.addItem('简体中文', 'zh-CN')
        self.language_choice.addItem('English', 'en')
        self.language_choice.setCurrentIndex(self.language_choice.findData(get_locale()))
        self.language_choice.currentIndexChanged.connect(
            lambda _: self.set_ui_language(self.language_choice.currentData()))
        return self.language_choice

    @property
    def ui_language(self):
        return get_locale()

    def set_ui_language(self, language, *, persist=True):
        set_locale(language)
        if not persist:
            return
        self.workspace_settings.setValue('language', get_locale())
        self.workspace_settings.sync()
        from PySide6.QtCore import QSettings
        if self.workspace_settings.status() != QSettings.Status.NoError:
            self.statusBar().showMessage(tr('Language changed for this session; the preference could not be saved.'), 10000)

    def _retranslate_core(self, *_):
        # Never call refresh(): it replaces list contents and resets staged edits.
        self.retranslate_media_browser()
        self.retranslate_clip_group_notice()
        self.refresh_linked_av_controls()
        self.refresh_track_lock_controls()
        self.language_choice.blockSignals(True)
        self.language_choice.setCurrentIndex(self.language_choice.findData(get_locale()))
        self.language_choice.blockSignals(False)
        _update_brand_label(self.brand_label)
        QApplication.instance().setApplicationDisplayName(display_name())
        if self.source_viewer_dialog is not None:
            self.source_viewer_dialog.check_current()
        if self.audio_analysis_dialog is not None:
            self.audio_analysis_dialog.check_current()
        if self.master_gain_dialog is not None:
            self.master_gain_dialog.check_current()
        if self.ripple_delete_dialog is not None:
            self.ripple_delete_dialog.check_current()
        if self.gap_close_dialog is not None:
            self.gap_close_dialog.check_current()
        title = self.project_path.name if self.project_path else self.project.name
        self.setWindowTitle(f'{"● " if self.dirty else ""}{title} — {display_name()}')
        self.refresh_workspace_notice()
        self.refresh_timeline_markers()
        if getattr(self, 'range_preview_dialog', None) is not None:
            self.range_preview_dialog.check_current()
        self.timeline.sticker_names = {self.sticker_choice.itemData(i): self.sticker_choice.itemText(i) for i in range(self.sticker_choice.count())}
        self.timeline.update()
        self.scopes.update()
        self.curve_editor.update()

    def _build_inspector(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(275)
        content = QWidget(); layout = QVBoxLayout(content); scroll.setWidget(content)
        layout.addWidget(QLabel(tr('4   INSPECTOR')))
        self.clip_name = QLabel(tr('Select a timeline clip')); self.clip_name.setWordWrap(True); layout.addWidget(self.clip_name)
        self.track_lock_note = TrackLockNote(); self.track_lock_note.setWordWrap(True)
        self.track_lock_note.setStyleSheet('color: #edc475;')
        layout.addWidget(self.track_lock_note)
        self.linked_av_note = plain_label(''); layout.addWidget(self.linked_av_note)
        self.linked_video_button = self._button(tr('Inspect linked video · V'), lambda: self.select_linked_role('video')); layout.addWidget(self.linked_video_button)
        self.linked_audio_button = self._button(tr('Inspect linked audio · A'), lambda: self.select_linked_role('audio')); layout.addWidget(self.linked_audio_button)
        self.linked_slip_button = self._button(tr('Joint source slip…'), lambda: self.open_linked_av_slip()); layout.addWidget(self.linked_slip_button)
        self.inspector_tabs = QTabWidget(); layout.addWidget(self.inspector_tabs)
        timing = QGroupBox(tr('Timing · project frames')); form = QFormLayout(timing)
        self.start_spin = QSpinBox(); self.start_spin.setRange(0, 100_000_000)
        self.in_spin = ExactSourcePositionEdit()
        self.in_spin.setToolTip(tr('Exact source position in project-frame units. Decimals and fractions are accepted.'))
        self.duration_spin = QSpinBox(); self.duration_spin.setRange(1, 100_000_000)
        form.addRow(tr('Timeline start'), self.start_spin); form.addRow(tr('Source in'), self.in_spin); form.addRow(tr('Duration'), self.duration_spin)
        self.source_range_note = QLabel(); self.source_range_note.setWordWrap(True); form.addRow(self.source_range_note)
        timing_note = QLabel(tr('Changing source in or duration chooses a new source endpoint at the current playback speed. Moving only preserves the exact source range.')); timing_note.setWordWrap(True); form.addRow(timing_note)
        self.apply_timing_button = self._button(tr('Apply timing'), self.apply_timing); form.addRow(self.apply_timing_button)
        self.slip_source_edit = ExactSourcePositionEdit()
        self.slip_source_edit.setToolTip(tr('Absolute source position in project frames, not an offset. Slip keeps the exact selected source span, including a partial final frame.'))
        form.addRow(tr('Slip source in'), self.slip_source_edit)
        self.slip_range_note = QLabel(); self.slip_range_note.setWordWrap(True); form.addRow(self.slip_range_note)
        self.apply_slip_button = self._button(tr('Apply source slip'), self.apply_slip); form.addRow(self.apply_slip_button)
        self.slip_anchor_note = QLabel(tr('Slip changes only the selected source content. Timeline start, duration, speed and fades stay unchanged. Captions, stickers and separate audio clips do not follow the source.'))
        self.slip_anchor_note.setWordWrap(True); form.addRow(self.slip_anchor_note)
        self.slip_source_edit.textChanged.connect(self.refresh_slip_range)
        locale_events.changed.connect(self.refresh_slip_range)
        self.speed_edit = QLineEdit('1'); self.speed_edit.setPlaceholderText(tr('0.25–4 · decimal or fraction'))
        self.speed_edit.setToolTip(tr('Exact constant playback speed, from 1/4× to 4×. Apply explicitly; source selection is preserved. Rubber Band preserves audio pitch; at slow speeds, brief sounds may shift or soften. Build preview to check the result.'))
        form.addRow(tr('Playback speed · ×'), self.speed_edit)
        self.speed_duration_note = QLabel(); self.speed_duration_note.setWordWrap(True); form.addRow(self.speed_duration_note)
        self.apply_speed_button = self._button(tr('Apply speed'), self.apply_speed); form.addRow(self.apply_speed_button)
        self.speed_anchor_note = QLabel(tr('No ripple: other clips are not moved; an overlap is rejected. Existing captions, stickers and other voice clips keep their timeline positions. Playback speed is separate from voice synthesis speed.'))
        self.speed_anchor_note.setWordWrap(True); form.addRow(self.speed_anchor_note)
        self.speed_edit.textChanged.connect(self.refresh_speed_duration)
        locale_events.changed.connect(self.refresh_speed_duration)
        editing = QWidget(); edit_layout = QVBoxLayout(editing)
        edit_layout.setContentsMargins(0, 0, 0, 0)
        edit_layout.addWidget(timing)
        ripple = QGroupBox(tr('Current-track ripple delete')); ripple_layout = QVBoxLayout(ripple)
        self.ripple_note = QLabel(tr('Deletes this clip and shifts later clips on this track left by its duration. Other tracks, captions and stickers stay fixed. Ordinary Delete keeps the gap.'))
        self.ripple_note.setWordWrap(True); ripple_layout.addWidget(self.ripple_note)
        self.ripple_delete_button = self._button(tr('Ripple delete · this track'), self.ripple_delete_selected)
        ripple_layout.addWidget(self.ripple_delete_button)
        self.ripple_participants_button = self._button(tr('Ripple delete · choose participants…'),
                                                      lambda: open_ripple_delete(self))
        ripple_layout.addWidget(self.ripple_participants_button); edit_layout.addWidget(ripple)
        edit_layout.addWidget(self.build_transform_controls())
        roll = QGroupBox(tr('Roll adjacent cut')); self.roll_group = roll; roll_form = QFormLayout(roll)
        self.roll_cut_spin = QSpinBox(); self.roll_cut_spin.setRange(0, 2_147_483_647)
        self.roll_cut_spin.setToolTip(tr('Absolute timeline frame for the selected clip’s outgoing cut, not an offset. Only a touching right clip on the same track can roll.'))
        roll_form.addRow(tr('New cut · timeline frame'), self.roll_cut_spin)
        self.roll_range_note = plain_label(''); roll_form.addRow(self.roll_range_note)
        self.apply_roll_button = self._button(tr('Apply roll edit'), self.apply_roll); roll_form.addRow(self.apply_roll_button)
        self.cancel_roll_button = self._button(tr('Cancel roll draft'), self.cancel_roll_draft)
        self.cancel_roll_button.hide(); roll_form.addRow(self.cancel_roll_button)
        self.roll_anchor_note = QLabel(tr('Only these two clips change. Their combined span stays fixed; attached source audio follows. Captions, stickers and separate audio do not move. Existing fades are preserved; edits that cannot fit them are rejected.'))
        self.roll_anchor_note.setWordWrap(True); roll_form.addRow(self.roll_anchor_note)
        self.roll_cut_spin.valueChanged.connect(self.refresh_roll_range)
        locale_events.changed.connect(self.refresh_roll_range)
        edit_layout.addWidget(roll)
        edit_layout.addWidget(self.build_stabilization_controls())
        edit_layout.addWidget(self.build_restoration_controls())
        edit_layout.addStretch()
        self.inspector_tabs.addTab(editing, tr('Edit'))
        grade = QWidget(); colorlayout = QVBoxLayout(grade)
        self.grade_enabled = QCheckBox(tr('Enable selected clip correction')); self.grade_enabled.setChecked(True); self.grade_enabled.toggled.connect(self.apply_grade); colorlayout.addWidget(self.grade_enabled)
        colorlayout.addWidget(self.build_input_color_controls())
        colorlayout.addWidget(self.build_grade_transfer_controls())
        colorlayout.addWidget(self.build_white_balance_controls())
        self.frame_grade_preview_button = self._button(tr('Current-frame grade preview…'), self.open_frame_grade_preview)
        self.frame_grade_preview_button.setToolTip(tr('Explicit selected-clip still after the full grade and LUT, before effects and compositing. Refresh after adjustments; use Build preview for the full timeline.'))
        colorlayout.addWidget(self.frame_grade_preview_button)
        self.color_tabs = QTabWidget(); colorlayout.addWidget(self.color_tabs)
        basic = QWidget(); colorform = QFormLayout(basic); self.color_tabs.addTab(basic, tr('Basic'))
        self.grade_spins = {}
        specs = [('linear_exposure', tr('Linear exposure · stops'), -10, 10, .1), ('contrast', tr('Contrast'), 0, 4, .05), ('contrast_pivot', tr('Contrast pivot'), 0, 1, .01), ('saturation', tr('Saturation'), 0, 4, .05), ('temperature', tr('Warm / cool'), -1, 1, .05), ('tint', tr('Green / magenta'), -1, 1, .05), ('shadows', tr('Shadows'), -1, 1, .05), ('highlights', tr('Highlights'), -1, 1, .05)]
        for key, label, lo, hi, step in specs:
            spin = QDoubleSpinBox(); spin.setRange(lo, hi); spin.setSingleStep(step); spin.setDecimals(3 if key == 'contrast_pivot' else 2); spin.setKeyboardTracking(False); spin.valueChanged.connect(self.apply_grade)
            self.grade_spins[key] = spin; colorform.addRow(label, spin)
            if key == 'contrast_pivot':
                spin.setToolTip(tr('Encoded Rec.709 RGB after legacy code gain: (value − pivot) × contrast + pivot. Default pivot 0.5 preserves legacy contrast. At Contrast 1, pivot has no effect. Not linear-light or Log contrast.'))
                self.contrast_pivot_note = QLabel(tr('Encoded Rec.709 RGB, not linear light. Pivot has no effect at Contrast 1.'))
                self.contrast_pivot_note.setWordWrap(True); colorform.addRow(self.contrast_pivot_note)
        self.grade_spins['linear_exposure'].setToolTip(tr('Rec.709 inverse transfer → multiply linear-light RGB by 2^stops. SDR source, not camera RAW.'))
        wheels = QWidget(); wheel_layout = QVBoxLayout(wheels); self.color_tabs.addTab(wheels, tr('Wheels'))
        hint = QLabel(tr('Linear RGB: (value + lift) × gain, then signed power 1/gamma.\nNeutral lift / gamma / gain: 0 / 1 / 1.')); hint.setWordWrap(True); self.wheel_formula = hint
        self.wheel_choice = QComboBox(); self.wheel_choice.addItems([tr('Lift · black offset'), tr('Gamma · midtone exponent'), tr('Gain · linear multiplier')]); wheel_layout.addWidget(self.wheel_choice)
        self.wheel_controls = {}
        for key, lo, hi in [('lift', -1, 1), ('gamma', .1, 4), ('gain', 0, 4)]:
            control = WheelControl({'lift': tr('Lift'), 'gamma': tr('Gamma'), 'gain': tr('Gain')}[key], lo, hi); control.committed.connect(lambda values, key=key: self.apply_color_field(key, values)); self.wheel_controls[key] = control; wheel_layout.addWidget(control)
        self.wheel_choice.currentIndexChanged.connect(self.show_wheel); self.show_wheel(0)
        legacy = QDoubleSpinBox(); legacy.setRange(-10, 10); legacy.setSingleStep(.1); legacy.setDecimals(2); legacy.setKeyboardTracking(False); legacy.valueChanged.connect(self.apply_grade)
        legacy.setToolTip(tr('Compatibility control: 2^value gain on encoded RGB, after linear controls. Not linear exposure.'))
        self.grade_spins['exposure'] = legacy
        wheel_layout.addStretch()
        curves = QWidget(); curve_layout = QVBoxLayout(curves); self.color_tabs.addTab(curves, tr('Curves'))
        self.curve_choice = QComboBox(); self.curve_choice.addItems([tr('Master'), tr('Red'), tr('Green'), tr('Blue'), tr('Hue vs Hue'), tr('Hue vs Saturation')]); self.curve_choice.currentIndexChanged.connect(self.refresh_curve); curve_layout.addWidget(self.curve_choice)
        self.curve_editor = CurveEditor(); self.curve_editor.committed.connect(self.apply_curve); curve_layout.addWidget(self.curve_editor)
        self.selective_curve_control = SelectiveCurveControl(); self.selective_curve_control.committed.connect(self.apply_selective_curve); self.selective_curve_control.pick_requested.connect(self.open_color_picker); curve_layout.addWidget(self.selective_curve_control); self.selective_curve_control.hide()
        hint = QLabel(tr('Drag points · double-click to add\nRight-click / Delete to remove interior points.\nPiecewise linear; endpoints keep x = 0 and 1.')); hint.setWordWrap(True); curve_layout.addWidget(hint); self.custom_curve_hint = hint
        self.reset_curve_button = self._button(tr('Reset this curve'), self.reset_current_curve); curve_layout.addWidget(self.reset_curve_button); curve_layout.addStretch()
        self.stack_label = QLabel(tr('FIXED CORRECTION ORDER\n709 decode → neutral-reference white balance → linear exposure → RGB lift/gain, gamma → 709 encode → legacy gain / contrast around pivot / saturation / balance / tones → master + RGB curves → hue / saturation curves → joint qualifier → LUT (bounded domain) → clip / quantize\nSDR Rec.709 only · no HDR / OCIO management'))
        self.stack_label.setWordWrap(True); self.stack_label.setStyleSheet('color: #a2b3c4; font-size: 9pt;'); self.color_details = QWidget(); details_layout = QVBoxLayout(self.color_details); details_layout.addWidget(self.stack_label); details_layout.addWidget(self.wheel_formula)
        legacy_form = QFormLayout(); legacy_form.addRow(tr('Legacy code gain · log2'), legacy); details_layout.addLayout(legacy_form)
        self.color_details_toggle = QCheckBox(tr('Advanced · processing details')); self.color_details_toggle.toggled.connect(self.color_details.setVisible); colorlayout.addWidget(self.color_details_toggle); colorlayout.addWidget(self.color_details); self.color_details.hide()
        self.lut_controls = QWidget(); lut_layout = QVBoxLayout(self.lut_controls); lut_layout.setContentsMargins(0, 0, 0, 0)
        self.lut_label = QLabel(tr('No LUT')); self.lut_label.setWordWrap(True); lut_layout.addWidget(self.lut_label)
        lut_layout.addWidget(self._button(tr('Load .cube LUT…'), self.choose_lut))
        self.lut_strength = QDoubleSpinBox(); self.lut_strength.setRange(0, 1); self.lut_strength.setDecimals(2); self.lut_strength.setSingleStep(.05); self.lut_strength.setKeyboardTracking(False)
        self.lut_strength.setToolTip(tr('Blend after master, RGB and selective hue curves in encoded Rec.709 RGB: 0 keeps the pre-LUT image, 1 applies the full LUT. LUT input is bounded by its declared domain.'))
        self.lut_strength.valueChanged.connect(lambda value: self.apply_color_field('lut_strength', value))
        lut_form = QFormLayout(); lut_form.addRow(tr('LUT strength'), self.lut_strength); lut_layout.addLayout(lut_form)
        self.remove_lut_button = self._button(tr('Remove LUT'), lambda: self.apply_color_field('lut_path', ''))
        lut_layout.addWidget(self.remove_lut_button); colorlayout.addWidget(self.lut_controls)
        self.qualifier_button = self._button(tr("Hue / saturation / luma qualifier…"), self.open_qualifier)
        colorlayout.addWidget(self.qualifier_button)
        self.window_animation_button = self._button(tr("Window animation…"), self.open_window_animation)
        colorlayout.addWidget(self.window_animation_button)
        self.reset_color_button = self._button(tr('Reset color'), self.reset_grade); colorlayout.addWidget(self.reset_color_button)
        self.inspector_tabs.addTab(grade, tr('Color')); self.inspector_tabs.setCurrentIndex(1)
        audio = QGroupBox(tr('Clip audio')); aform = QFormLayout(audio)
        self.gain = QDoubleSpinBox(); self.gain.setRange(-96, 24); self.gain.setSuffix(' dB'); self.gain.setKeyboardTracking(False); self.gain.valueChanged.connect(self.apply_audio)
        self.mute = QCheckBox(tr('Mute this clip')); self.mute.toggled.connect(self.apply_audio)
        self.fade_in = QSpinBox(); self.fade_in.setRange(0, 100_000_000); self.fade_in.setKeyboardTracking(False); self.fade_in.valueChanged.connect(self.apply_audio)
        self.fade_out = QSpinBox(); self.fade_out.setRange(0, 100_000_000); self.fade_out.setKeyboardTracking(False); self.fade_out.valueChanged.connect(self.apply_audio)
        self.source_audio_status = plain_label(''); aform.addRow(self.source_audio_status)
        self.separate_source_audio_button = self._button(tr('Separate source audio…'), self.open_source_audio_separation)
        aform.addRow(self.separate_source_audio_button)
        self.audio_gain_button = self._button(tr('Clip volume keyframes…'), lambda: open_audio_gain(self))
        aform.addRow(self.audio_gain_button)
        self.speech_info_button = self._button(tr('Inspect voice provenance…'), self.inspect_speech)
        aform.addRow(self.speech_info_button)
        aform.addRow(tr('Gain'), self.gain); aform.addRow(self.mute); aform.addRow(tr('Fade in · frames'), self.fade_in); aform.addRow(tr('Fade out · frames'), self.fade_out); self.inspector_tabs.addTab(audio, tr('Audio'))
        self._roll_other_panels = tuple(edit_layout.itemAt(i).widget() for i in range(edit_layout.count())
            if edit_layout.itemAt(i).widget() is not None and edit_layout.itemAt(i).widget() is not roll) + (grade, audio)
        layout.addStretch(); return scroll

    def _build_menu(self):
        menus = {name: self.menuBar().addMenu(tr(name)) for name in ('File', 'Edit', 'Playback', 'Timeline', 'Settings')}
        callbacks = {
            'file.new': self.new_project, 'file.open': self.open_project,
            'file.save': self.save_project, 'file.save_as': lambda: self.save_project(True),
            'file.import': self.import_dialog, 'file.export': self.export_dialog,
            'edit.undo': self.undo, 'edit.redo': self.redo,
            'edit.copy_clips': self.copy_selected_clips, 'edit.paste_clips': self.paste_copied_clips,
            'edit.separate_audio': self.open_source_audio_separation,
            'edit.reattach_audio': self.open_source_audio_reattach,
            'edit.link_av': self.link_selected_av, 'edit.unlink_av': self.unlink_selected_av,
            'edit.joint_slip': lambda: self.open_linked_av_slip(),
            'edit.audio_roll': self.open_linked_audio_roll,
            'edit.split': self.split_selected, 'edit.delete': self.delete_active_selection,
            'edit.group': self.group_selected_clips, 'edit.ungroup': self.ungroup_selected_clips,
            'playback.toggle': self.toggle_play,
            'playback.previous_frame': lambda: self.seek_frame(self.timeline.playhead - 1),
            'playback.next_frame': lambda: self.seek_frame(self.timeline.playhead + 1),
            'playback.previous_five_frames': lambda: self.seek_frame(self.timeline.playhead - 5),
            'playback.next_five_frames': lambda: self.seek_frame(self.timeline.playhead + 5),
            'timeline.preview_in': lambda: self.set_preview_mark('in'),
            'timeline.preview_out': lambda: self.set_preview_mark('out'),
            'timeline.preview_selection': self.preview_selection,
            'timeline.preview_clear': lambda: self.set_preview_mark('clear'),
            'timeline.markers': self.open_timeline_markers,
            'timeline.add_marker': self.add_timeline_marker_at_playhead,
            'timeline.previous_marker': self.previous_timeline_marker,
            'timeline.next_marker': self.next_timeline_marker,
            'timeline.snapping': self.toggle_snapping,
            'timeline.master_gain': lambda: open_master_gain(self),
            'timeline.analyze_audio': lambda: open_audio_analysis(self),
            'app.shortcuts': self.shortcuts.open_dialog,
        }
        for command_id, command in COMMANDS.items():
            action = QAction(tr(command.label), self)
            action.triggered.connect(lambda checked=False, callback=callbacks[command_id]: callback())
            if command_id == 'timeline.snapping':
                action.setCheckable(True)
            self.shortcuts.register_action(command_id, action)
            menus[command.category].addAction(action)
        self.final_frame_preview_action = QAction(tr('Final composite still preview…'), self)
        self.final_frame_preview_action.triggered.connect(self.open_final_frame_preview)
        menus['Playback'].addAction(self.final_frame_preview_action)
        self.range_preview_action = QAction(tr('Preview around playhead'), self)
        self.range_preview_action.triggered.connect(self.open_range_preview)
        menus['Playback'].addAction(self.range_preview_action)
        range_menu = QtWidgets.QMenu(self.range_preview_button)
        range_menu.addAction(self.range_preview_action)
        range_menu.addSeparator()
        for command in ('timeline.preview_in', 'timeline.preview_out',
                        'timeline.preview_selection', 'timeline.preview_clear'):
            range_menu.addAction(self.shortcuts.actions[command])
        # One discoverable entry for both radius and explicitly marked previews.
        self.range_preview_button.clicked.disconnect()
        self.range_preview_button.setMenu(range_menu)
        self.range_preview_button.setText(tr('Range preview'))
        self.gap_close_action = QAction(tr('Close one gap…'), self)
        self.gap_close_action.triggered.connect(lambda: open_gap_close(self))
        menus['Timeline'].addAction(self.gap_close_action)
        self.recovery_action = QAction(tr('Recover unsaved projects'), self)
        self.recovery_action.triggered.connect(self.open_recovery)
        menus['File'].addAction(self.recovery_action)
        self.component_setup_action = QAction(tr("Local AI component setup"), self)
        self.component_setup_action.triggered.connect(lambda: open_component_setup(self))
        menus["Settings"].addAction(self.component_setup_action)
        menus['Edit'].setToolTipsVisible(True)
        for menu in menus.values():
            menu.aboutToShow.connect(self.shortcuts.update_enabled)
        for view in (self.caption_list, self.overlay_list, self.effect_list):
            view.currentItemChanged.connect(lambda *_: self.shortcuts.update_enabled())
        helpmenu = self.menuBar().addMenu(tr('Help'))
        action = QAction(tr('What this build supports'), self)
        action.triggered.connect(self.show_help)
        helpmenu.addAction(action)

    def toggle_snapping(self, *_):
        self.timeline.snapping_enabled = not self.timeline.snapping_enabled
        self.snapping_button.setChecked(self.timeline.snapping_enabled)
        self.snapping_button.setToolTip(tr('Snapping on') if self.timeline.snapping_enabled else tr('Snapping off'))
        if hasattr(self, 'shortcuts'):
            self.shortcuts.refresh_labels()

    def show_help(self):
        from .ui_help import HelpDialog
        dialog = HelpDialog(self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    @property
    def selection(self):
        """Compatibility anchor; assigning it selects the clip and its whole group."""
        return getattr(self, '_selection', '')

    @selection.setter
    def selection(self, value):
        self.set_selected_clip_ids((value,) if value else (), anchor=value)

    @property
    def selected_clip_ids(self):
        return getattr(self, '_selected_clip_ids', ())

    def set_selected_clip_ids(self, ids, *, anchor=None, keep_anchor=False):
        ids = reconcile_group_ids(self.project, ids)
        if anchor is None:
            anchor = self.selection if keep_anchor else (ids[0] if ids else '')
        if ids and anchor not in ids:
            anchor = ids[0]
        previous = (self.selected_clip_ids, self.selection)
        self._selected_clip_ids, self._selection = ids, anchor
        if previous != (ids, anchor):
            self.selection_generation = getattr(self, 'selection_generation', 0) + 1
            if hasattr(self, 'invalidate_review_selection') and hasattr(self, 'assistant_job'):
                self.invalidate_review_selection()
        if hasattr(self, 'timeline'):
            self.timeline.selected_clip_ids = ids
            self.timeline.selection = anchor
        if hasattr(self, 'shortcuts'):
            self.shortcuts.update_enabled()

    def clear_clip_selection(self, keep_anchor=False):
        self.set_selected_clip_ids((), keep_anchor=keep_anchor)

    def reconcile_clip_selection(self):
        # Undo replaces the Project too, but keeps this editor session. A new
        # editor/session must not inherit same-ID selections from another file.
        token = (id(self.editor), self.editor.session_id)
        if getattr(self, '_selection_editor_token', token) != token:
            self.clear_clip_selection()
            self.transition_selection = ''
        self._selection_editor_token = token
        existing = {c.id for c in self.project.clips}
        ids = tuple(cid for cid in self.selected_clip_ids if cid in existing)
        self.set_selected_clip_ids(ids, anchor=self.selection if self.selection in existing else None,
                                   keep_anchor=self.active_selection_type == 'effect')

    def selected_clip(self):
        if len(self.selected_clip_ids) != 1:
            return None
        return next((c for c in self.project.clips if c.id == self.selected_clip_ids[0]), None)

    def build_stabilization_controls(self):
        self._stabilization_draft = StabilizationSettings()
        self.stabilization_group = QGroupBox(tr('Picture stabilization'))
        form = QFormLayout(self.stabilization_group)
        self.stabilization_enabled = QCheckBox(tr('Enable stabilization'))
        self.stabilization_enabled.setToolTip(tr('Local CPU analysis covers the entire source, even after trimming. Limits: 600 seconds and 18,000 frames at the project frame rate. Temporary disk space is needed for a source copy and analysis proxies.'))
        form.addRow(self.stabilization_enabled)
        self.stabilization_strength = QDoubleSpinBox()
        self.stabilization_strength.setRange(0, 1)
        self.stabilization_strength.setDecimals(3); self.stabilization_strength.setSingleStep(.05)
        self.stabilization_strength.setKeyboardTracking(False)
        form.addRow(tr('Stabilization strength'), self.stabilization_strength)
        self.stabilization_note = QLabel(tr('Reduces camera shake using translation and rotation. Disabled by default. Apply settings, then build preview to check the result.'))
        self.stabilization_note.setWordWrap(True); form.addRow(self.stabilization_note)
        self.stabilization_advanced_toggle = QCheckBox(tr('Advanced · smoothing and borders'))
        form.addRow(self.stabilization_advanced_toggle)
        self.stabilization_advanced = QWidget()
        advanced = QFormLayout(self.stabilization_advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self.stabilization_smoothing = QSpinBox(); self.stabilization_smoothing.setRange(1, 120)
        self.stabilization_smoothing.setKeyboardTracking(False)
        self.stabilization_smoothing.setToolTip(tr('Radius in source-grid frames at the project frame rate. The same source-wide motion path is used after trim, split or playback-speed edits.'))
        advanced.addRow(tr('Smoothing radius · frames'), self.stabilization_smoothing)
        self.stabilization_max_zoom = QDoubleSpinBox(); self.stabilization_max_zoom.setRange(1, 1.5)
        self.stabilization_max_zoom.setDecimals(3); self.stabilization_max_zoom.setSingleStep(.01)
        self.stabilization_max_zoom.setKeyboardTracking(False)
        advanced.addRow(tr('Maximum crop zoom · ×'), self.stabilization_max_zoom)
        self.stabilization_border = QComboBox()
        self.stabilization_border.addItem(tr('Crop · bounded zoom'), 'crop')
        self.stabilization_border.addItem(tr('Black · retain frame'), 'black')
        advanced.addRow(tr('Stabilization borders'), self.stabilization_border)
        self.stabilization_border_note = QLabel(tr('Crop uses one fixed zoom across the entire source, including all shots. If the required zoom exceeds the cap, rendering stops: reduce strength, raise the cap or choose Black. Black fills exposed edges with black.'))
        self.stabilization_border_note.setWordWrap(True); advanced.addRow(self.stabilization_border_note)
        limits = QLabel(tr('No optical-flow interpolation, rolling-shutter repair or motion deblur. Source files are never changed.'))
        limits.setWordWrap(True); advanced.addRow(limits)
        self.stabilization_processing_note = QLabel(tr('Local CPU analysis covers the entire source, even after trimming. Limits: 600 seconds and 18,000 frames at the project frame rate. Temporary disk space is needed for a source copy and analysis proxies.'))
        self.stabilization_processing_note.setWordWrap(True); advanced.addRow(self.stabilization_processing_note)
        form.addRow(self.stabilization_advanced); self.stabilization_advanced.hide()
        self.stabilization_advanced_toggle.toggled.connect(self.stabilization_advanced.setVisible)
        self.apply_stabilization_button = self._button(tr('Apply stabilization'), self.apply_stabilization)
        self.reset_stabilization_button = self._button(tr('Reset stabilization'), self.reset_stabilization)
        form.addRow(self.apply_stabilization_button); form.addRow(self.reset_stabilization_button)
        self.stabilization_enabled.toggled.connect(lambda value: self.stage_stabilization('enabled', value))
        self.stabilization_strength.valueChanged.connect(lambda value: self.stage_stabilization('strength', value))
        self.stabilization_smoothing.valueChanged.connect(lambda value: self.stage_stabilization('smoothing', value))
        self.stabilization_max_zoom.valueChanged.connect(lambda value: self.stage_stabilization('max_zoom', value))
        self.stabilization_border.currentIndexChanged.connect(lambda _: self.stage_stabilization('border', self.stabilization_border.currentData()))
        return self.stabilization_group

    def refresh_stabilization_controls(self):
        c = self.selected_parameter_clip()
        media = next((m for m in self.project.media if c and m.id == c.media_id), None)
        available = bool(c and c.track < 2 and media and media.has_video)
        self.stabilization_group.setEnabled(available)
        self._stabilization_draft = copy.deepcopy(c.stabilization) if c else StabilizationSettings()
        settings = self._stabilization_draft
        self.stabilization_enabled.setChecked(settings.enabled)
        self.stabilization_strength.setValue(settings.strength)
        self.stabilization_smoothing.setValue(settings.smoothing)
        self.stabilization_max_zoom.setValue(settings.max_zoom)
        self.stabilization_border.setCurrentIndex(self.stabilization_border.findData(settings.border))
        self.update_stabilization_controls()

    def update_stabilization_controls(self):
        settings = self._stabilization_draft
        self.stabilization_strength.setEnabled(settings.enabled)
        self.stabilization_advanced.setEnabled(settings.enabled)
        self.stabilization_max_zoom.setEnabled(settings.enabled and settings.border == 'crop')
        c = self.selected_parameter_clip()
        self.apply_stabilization_button.setEnabled(bool(c and settings != c.stabilization))
        self.reset_stabilization_button.setEnabled(bool(c and (
            c.stabilization != StabilizationSettings() or settings != StabilizationSettings())))

    def stage_stabilization(self, field, value):
        if self.loading_controls or self.selected_parameter_clip() is None:
            return
        setattr(self._stabilization_draft, field, value)
        self.update_stabilization_controls()

    def apply_stabilization(self):
        c = self.selected_parameter_clip()
        if not c or self._stabilization_draft == c.stabilization:
            return
        self.edit(lambda: self.editor.update_clip(c.id, stabilization=self._stabilization_draft))

    def reset_stabilization(self):
        c = self.selected_parameter_clip()
        if not c:
            return
        if c.stabilization != StabilizationSettings():
            self.edit(lambda: self.editor.update_clip(c.id, stabilization=StabilizationSettings()))
        else:
            self.refresh_inspector()

    def build_restoration_controls(self):
        self._restoration_draft = RestorationSettings()
        self.restoration_group = QGroupBox(tr('Quality restoration'))
        form = QFormLayout(self.restoration_group)
        self.restoration_enabled = QCheckBox(tr('Enable quality restoration'))
        self.restoration_enabled.setToolTip(tr('Turn off to bypass restoration while retaining all three strengths. Apply to update preview and export.'))
        form.addRow(self.restoration_enabled)
        self.restoration_spins = {}
        labels = {'deblock': tr('Deblock'), 'denoise': tr('Denoise'), 'sharpen': tr('Sharpen')}
        hints = {
            'deblock': tr('Soften small compression-block edges. Start low to protect texture.'),
            'denoise': tr('Reduce fine noise within each frame. Higher values can soften texture.'),
            'sharpen': tr('Add gentle edge contrast. Higher values can emphasize noise and halos.'),
        }
        for name, label in labels.items():
            control = QDoubleSpinBox()
            control.setRange(0, 1); control.setDecimals(3); control.setSingleStep(.05)
            control.setKeyboardTracking(False); control.setToolTip(hints[name])
            control.setAccessibleName(label)
            self.restoration_spins[name] = control
            setattr(self, f'restoration_{name}', control)
            form.addRow(label, control)
            control.valueChanged.connect(lambda value, name=name: self.stage_restoration(name, value))
        self.restoration_note = QLabel(tr('Local, conservative processing of each frame: deblock, denoise, then sharpen. Disabled by default; 0 skips a step. Apply, then use Before / After preview.'))
        self.restoration_note.setWordWrap(True); form.addRow(self.restoration_note)
        self.restoration_warning_note = QLabel(tr('Deblock and denoise can soften real detail. Sharpening can amplify noise and halos. Start with low strengths.'))
        self.restoration_warning_note.setWordWrap(True); form.addRow(self.restoration_warning_note)
        self.restoration_limits_note = QLabel(tr('No AI, super-resolution or recovery of lost detail. This spatial method does not analyze motion or guarantee flicker-free video. Start low and check moving footage. Source files stay unchanged.'))
        self.restoration_limits_note.setWordWrap(True); form.addRow(self.restoration_limits_note)
        self.apply_restoration_button = self._button(tr('Apply restoration'), self.apply_restoration)
        self.reset_restoration_button = self._button(tr('Reset restoration'), self.reset_restoration)
        form.addRow(self.apply_restoration_button); form.addRow(self.reset_restoration_button)
        self.restoration_enabled.toggled.connect(lambda value: self.stage_restoration('enabled', value))
        return self.restoration_group

    def refresh_restoration_controls(self):
        c = self.selected_parameter_clip()
        media = next((m for m in self.project.media if c and m.id == c.media_id), None)
        available = bool(c and c.track < 2 and media and media.has_video)
        self.restoration_group.setEnabled(available)
        self._restoration_draft = copy.deepcopy(c.restoration) if c else RestorationSettings()
        self.restoration_enabled.setChecked(self._restoration_draft.enabled)
        for name, control in self.restoration_spins.items():
            control.setValue(getattr(self._restoration_draft, name))
        self.update_restoration_controls()

    def update_restoration_controls(self):
        settings = self._restoration_draft
        for control in self.restoration_spins.values():
            control.setEnabled(settings.enabled)
        c = self.selected_parameter_clip()
        self.apply_restoration_button.setEnabled(bool(c and settings != c.restoration))
        self.reset_restoration_button.setEnabled(bool(c and (
            c.restoration != RestorationSettings() or settings != RestorationSettings())))

    def stage_restoration(self, field, value):
        if self.loading_controls:
            return
        setattr(self._restoration_draft, field, value)
        self.update_restoration_controls()

    def apply_restoration(self):
        c = self.selected_parameter_clip()
        if not c or self._restoration_draft == c.restoration:
            return
        self.edit(lambda: self.editor.update_clip(c.id, restoration=self._restoration_draft))

    def reset_restoration(self):
        c = self.selected_parameter_clip()
        if not c:
            return
        if c.restoration != RestorationSettings():
            self.edit(lambda: self.editor.update_clip(c.id, restoration=RestorationSettings()))
        else:
            self.refresh_inspector()

    def build_transform_controls(self):
        self.transform_group = QGroupBox(tr('Picture transform'))
        form = QFormLayout(self.transform_group)
        self.transform_spins = {}
        for key, label, low, high, step, default in (
                ('x', 'Position X · px', -16384, 16384, 1, 0),
                ('y', 'Position Y · px', -16384, 16384, 1, 0),
                ('scale', 'Scale', .1, 4, .05, 1),
                ('rotation', 'Rotation · °', -180, 180, 1, 0)):
            spin = QDoubleSpinBox(); spin.setRange(low, high); spin.setDecimals(2)
            spin.setSingleStep(step); spin.setValue(default); spin.setKeyboardTracking(False)
            self.transform_spins[key] = spin; form.addRow(tr(label), spin)
            spin.valueChanged.connect(self.update_transform_buttons)
        self.transform_mirror_x = QCheckBox(tr('Mirror horizontal'))
        self.transform_mirror_y = QCheckBox(tr('Mirror vertical'))
        for control in (self.transform_mirror_x, self.transform_mirror_y):
            form.addRow(control); control.toggled.connect(self.update_transform_buttons)
        self.transform_note = QLabel(tr('Static, centered on the fitted canvas. X/Y move in project pixels; positive rotation is clockwise. Outside is black, not transparent by default. Enable compositing below to reveal lower video. Build preview to see changes.'))
        self.transform_note.setWordWrap(True); form.addRow(self.transform_note)
        self.apply_transform_button = self._button(tr('Apply transform'), self.apply_transform)
        self.reset_transform_button = self._button(tr('Reset transform'), self.reset_transform)
        form.addRow(self.apply_transform_button); form.addRow(self.reset_transform_button)
        self.composite_transparent_outside_transform = QCheckBox(tr('Transparent outside transform'))
        self.composite_transparent_outside_transform.setToolTip(tr('Reveal lower video outside transformed canvas'))
        form.addRow(self.composite_transparent_outside_transform)
        self.composite_opacity = QDoubleSpinBox()
        self.composite_opacity.setRange(0, 100); self.composite_opacity.setDecimals(1)
        self.composite_opacity.setSuffix(' %')
        self.composite_opacity.setSingleStep(5); self.composite_opacity.setValue(100)
        self.composite_opacity.setKeyboardTracking(False)
        form.addRow(tr('Picture opacity'), self.composite_opacity)
        self.composite_note = QLabel(tr('Transparency reveals only the transformed canvas exterior, not fit/pad black bars; source alpha is ignored. Opacity affects the whole picture, including at zero; audio stays unchanged.'))
        self.composite_note.setWordWrap(True); form.addRow(self.composite_note)
        self.composite_apply_note = QLabel(tr('Transform and compositing apply separately. Applying or resetting either refreshes other unapplied values.'))
        self.composite_apply_note.setWordWrap(True); form.addRow(self.composite_apply_note)
        self.apply_composite_button = self._button(tr('Apply compositing'), self.apply_composite)
        self.reset_composite_button = self._button(tr('Reset compositing'), self.reset_composite)
        form.addRow(self.apply_composite_button); form.addRow(self.reset_composite_button)
        self.composite_transparent_outside_transform.toggled.connect(self.update_composite_buttons)
        self.composite_opacity.valueChanged.connect(self._composite_opacity_changed)
        self.motion_button = self._button(tr('Animate picture…'), self.open_clip_motion)
        form.addRow(self.motion_button)
        return self.transform_group

    def open_clip_motion(self):
        from .ui_clip_motion import ClipMotionDialog
        clip = self._picture_transform_clip()
        if not self._picture_controls_current(clip):
            return
        previous = getattr(self, 'motion_dialog', None)
        if previous is not None:
            previous.reject()
        try:
            self.motion_dialog = ClipMotionDialog(self)
        except ValueError as exc:
            QMessageBox.warning(self, tr('Picture animation'), tr(str(exc)))
            return
        self.motion_dialog.show()

    def _picture_transform_clip(self):
        clip = self.selected_parameter_clip()
        if self.active_selection_type != 'clip' or not clip or clip.track >= 2:
            return None
        return clip if any(m.id == clip.media_id and m.has_video for m in self.project.media) else None

    def _picture_controls_token(self, clip):
        return (id(self.editor), self.editor.session_id, self.editor.revision,
                id(self.project), self.selection_generation, clip.id if clip else None)

    def _picture_controls_current(self, clip):
        return bool(clip and getattr(self, '_picture_display_token', None)
                    == self._picture_controls_token(clip))

    def staged_transform(self):
        c = self.selected_parameter_clip()
        displayed = getattr(self, '_transform_display_values', {})
        same_clip = c is not None and getattr(self, '_transform_display_clip', None) == c.id
        # Preserve loaded values beyond the control's display precision unless
        # that particular field changed. Toggling a mirror must not round scale.
        values = {key: (getattr(c.transform, key) if same_clip and spin.value() == displayed.get(key)
                        else spin.value()) for key, spin in self.transform_spins.items()}
        if c is not None and c.motion_keyframes:
            values.update({key: getattr(c.transform, key) for key in ('x', 'y', 'scale')})
        return ClipTransform(**values, mirror_x=self.transform_mirror_x.isChecked(),
                             mirror_y=self.transform_mirror_y.isChecked())

    def refresh_transform_controls(self):
        c = self.selected_parameter_clip()
        self.transform_group.setEnabled(self._picture_transform_clip() is not None)
        settings = c.transform if c else ClipTransform()
        for key, spin in self.transform_spins.items():
            spin.setValue(getattr(settings, key))
            spin.setEnabled(not (c and c.motion_keyframes and key in ('x', 'y', 'scale')))
        self.transform_mirror_x.setChecked(settings.mirror_x)
        self.transform_mirror_y.setChecked(settings.mirror_y)
        self._transform_display_clip = c.id if c else None
        self._transform_display_values = {key: spin.value() for key, spin in self.transform_spins.items()}
        self._picture_display_token = self._picture_controls_token(self._picture_transform_clip())
        self.motion_button.setToolTip(tr('Animated values are owned by picture keys. Rotation and mirrors remain static.') if c and c.motion_keyframes else '')
        self.refresh_composite_controls()
        self.update_transform_buttons()

    def update_transform_buttons(self, *_):
        if not hasattr(self, 'apply_transform_button'): return
        c = self._picture_transform_clip(); valid = self._picture_controls_current(c)
        staged = self.staged_transform()
        self.apply_transform_button.setEnabled(valid and staged != c.transform)
        reset = ClipTransform(x=c.transform.x, y=c.transform.y, scale=c.transform.scale) if valid and c.motion_keyframes else ClipTransform()
        self.reset_transform_button.setEnabled(valid and (staged != reset or c.transform != reset))

    def apply_transform(self):
        c = self._picture_transform_clip()
        if not self._picture_controls_current(c):
            self.refresh_transform_controls(); return
        settings = self.staged_transform()
        if settings != c.transform:
            self.edit(lambda: self.editor.update_clip(c.id, transform=settings))

    def reset_transform(self):
        c = self._picture_transform_clip()
        if not self._picture_controls_current(c):
            self.refresh_transform_controls(); return
        reset = ClipTransform(x=c.transform.x, y=c.transform.y, scale=c.transform.scale) if c.motion_keyframes else ClipTransform()
        if c.transform != reset:
            self.edit(lambda: self.editor.update_clip(c.id, transform=reset))
        else:
            self.refresh_transform_controls()

    def staged_composite(self):
        clip = self._picture_transform_clip()
        opacity = self.composite_opacity.value() / 100
        if (self._picture_controls_current(clip)
                and not getattr(self, '_composite_opacity_dirty', False)):
            # A checkbox change must not round an exact saved opacity to the
            # percentage display's single decimal place.
            opacity = clip.composite.opacity
        if clip and clip.motion_keyframes:
            opacity = clip.composite.opacity
        return ClipComposite(self.composite_transparent_outside_transform.isChecked(), opacity)

    def refresh_composite_controls(self):
        clip = self._picture_transform_clip()
        settings = clip.composite if clip else ClipComposite()
        self._refreshing_composite = True
        try:
            self.composite_opacity.setValue(settings.opacity * 100)
            self.composite_transparent_outside_transform.setChecked(settings.transparent_outside_transform)
        finally:
            self._refreshing_composite = False
        self.composite_opacity.setEnabled(not bool(clip and clip.motion_keyframes))
        self._composite_opacity_dirty = False
        self.update_composite_buttons()

    def _composite_opacity_changed(self, *_):
        if not getattr(self, '_refreshing_composite', False):
            self._composite_opacity_dirty = True
        self.update_composite_buttons()

    def update_composite_buttons(self, *_):
        if not hasattr(self, 'apply_composite_button'): return
        clip = self._picture_transform_clip()
        valid = self._picture_controls_current(clip)
        staged = self.staged_composite()
        self.apply_composite_button.setEnabled(valid and staged != clip.composite)
        reset = ClipComposite(opacity=clip.composite.opacity) if valid and clip.motion_keyframes else ClipComposite()
        self.reset_composite_button.setEnabled(valid and (staged != reset or clip.composite != reset))

    def apply_composite(self):
        clip = self._picture_transform_clip()
        if not self._picture_controls_current(clip):
            self.refresh_transform_controls(); return
        settings = self.staged_composite()
        if settings != clip.composite:
            self.edit(lambda: self.editor.update_clip(clip.id, composite=settings))

    def reset_composite(self):
        clip = self._picture_transform_clip()
        if not self._picture_controls_current(clip):
            self.refresh_transform_controls(); return
        reset = ClipComposite(opacity=clip.composite.opacity) if clip.motion_keyframes else ClipComposite()
        if clip.composite != reset:
            self.edit(lambda: self.editor.update_clip(clip.id, composite=reset))
        else:
            self.refresh_transform_controls()

    def refresh(self):
        from .ui_preview_selection import refresh_selection
        refresh_selection(self)
        self.refresh_clip_clipboard()
        self.refresh_source_audio_separation()
        self.refresh_source_audio_reattach()
        self.refresh_linked_av()
        self.reconcile_clip_selection()
        transition = self.selected_video_transition()
        self.timeline.transition_selection = transition.id if transition else ""
        self.refresh_media_browser()
        self.timeline.set_project(self.project, self.selection if self.active_selection_type in ("clip", "effect") else "", self.selected_clip_ids)
        self.format_label.setText(f'{self.project.width} × {self.project.height}  ·  {float(self.project.fps):g} fps  ·  Rec.709 SDR')
        self.scrub.blockSignals(True); self.scrub.setRange(0, max(0, self.project.duration_frames - 1)); self.scrub.blockSignals(False)
        self.seek_frame(self.timeline.playhead)
        self.refresh_inspector()
        self.refresh_captions()
        self.refresh_effects()
        self.refresh_ai()
        self.refresh_workspace_notice()
        if self.source_viewer_dialog is not None:
            self.source_viewer_dialog.check_current()
        if self.audio_analysis_dialog is not None:
            self.audio_analysis_dialog.check_current()
        if self.master_gain_dialog is not None:
            self.master_gain_dialog.check_current()
        if self.ripple_delete_dialog is not None:
            self.ripple_delete_dialog.check_current()
        if self.gap_close_dialog is not None:
            self.gap_close_dialog.check_current()
        self.refresh_timeline_markers()
        title = self.project_path.name if self.project_path else self.project.name
        self.setWindowTitle(f'{"● " if self.dirty else ""}{title} — {display_name()}')
        if getattr(self, 'range_preview_dialog', None) is not None:
            self.range_preview_dialog.check_current()
        self.update_playback_controls()

    def refresh_inspector(self):
        self.restore_track_lock_inspector_controls()
        self.loading_controls = True
        c = self.selected_parameter_clip()
        roll_only = self.prepare_linked_roll_inspector()
        self.inspector.setEnabled(c is not None or roll_only)
        for panel in self._roll_other_panels: panel.setEnabled(True)
        self.refresh_split_control()
        self.delete_clips_button.setEnabled(bool(self.selected_clip_ids) and self.active_selection_type == 'clip')
        if len(self.selected_clip_ids) > 1:
            grouped = any(clip_group(self.project, cid) for cid in self.selected_clip_ids)
            self.clip_name.setText(tr(
                'Selected {count} clips · Grouped\nUngroup to edit separately' if grouped
                else 'Selected {count} clips · Move and delete only', count=len(self.selected_clip_ids)))
            if self.can_reattach_source_audio():
                self.clip_name.setText(tr('Selected pair · Edit → Reattach source audio…\nPreview explicitly before deleting the selected audio clip.'))
        elif c is None:
            self.clip_name.setText(tr('Select a clip'))
        media = next((m for m in self.project.media if c and m.id == c.media_id), None)
        self.inspector_tabs.setTabEnabled(1, c is None or bool(media and media.has_video and c.track < 2))
        if c:
            m = next(m for m in self.project.media if m.id == c.media_id)
            self.clip_name.setText(tr('{name}\nInput: {space}', name=m.name, space=m.color_space or tr('unknown; assumed Rec.709')))
            self.start_spin.setValue(c.start); self.in_spin.setValue(c.source_in); self.duration_spin.setValue(c.duration)
            self.slip_source_edit.setValue(c.source_in)
            self.refresh_slip_range()
            self.roll_cut_spin.setValue(min(c.end, self.roll_cut_spin.maximum()))
            self.speed_edit.setText(exact_speed_text(c.speed))
            self.source_range_note.setText(tr('Selected source: [{source_in}, {source_out}) project frames', source_in=str(c.source_in), source_out=str(c.source_out)))
            self.refresh_speed_duration()
            self.grade_enabled.setChecked(c.grade.enabled)
            for key, spin in self.grade_spins.items(): spin.setValue(getattr(c.grade, key))
            for key, control in self.wheel_controls.items(): control.set_values(getattr(c.grade, key))
            self.refresh_curve()
            self.lut_label.setText(Path(c.grade.lut_path).name if c.grade.lut_path else tr('No LUT'))
            self.lut_strength.setValue(c.grade.lut_strength); self.lut_strength.setEnabled(bool(c.grade.lut_path)); self.remove_lut_button.setEnabled(bool(c.grade.lut_path))
            self.gain.setValue(c.gain_db); self.mute.setChecked(c.muted)
            self.fade_in.setMaximum(c.duration); self.fade_out.setMaximum(c.duration); self.fade_in.setValue(c.fade_in); self.fade_out.setValue(c.fade_out)
        if not c: self.refresh_curve()
        self.speech_info_button.setEnabled(bool(c and m.speech_provenance))
        self.refresh_roll_range()
        self.refresh_stabilization_controls()
        self.refresh_restoration_controls()
        self.refresh_transform_controls()
        self.ripple_delete_button.setEnabled(c is not None)
        self.ripple_participants_button.setEnabled(c is not None and self.active_selection_type == 'clip')
        self.loading_controls = False
        self.refresh_input_color_controls()
        self.refresh_white_balance_controls()

        self.refresh_grade_transfer_controls()
        self.window_animation_button.setEnabled(bool(c and self.active_selection_type == 'clip'
            and media and media.has_video and c.track < 2))
        self.refresh_source_audio_status()
        self.audio_gain_button.setEnabled(can_open_audio_gain(self))
        if self.audio_gain_dialog is not None: self.audio_gain_dialog.check_current()
        self.refresh_linked_av_controls()
        if roll_only:
            for panel in self._roll_other_panels: panel.setEnabled(False)
            self.inspector_tabs.setCurrentIndex(0)
            self.clip_name.setText(tr('Two linked A/V pairs · shared-cut roll only'))
        self.refresh_track_lock_controls()

    def update_playback_controls(self):
        valid = self.preview_revision == self.revision and self.loaded_preview_mode == self.preview_mode and self.project.duration_frames > 0
        self.scopes.set_stale(not valid or self.video.frame_provenance is None)
        if not valid:
            self.video.clear_current()
        self.update_reference_controls()
        self.play_button.setEnabled(valid)
        self.preview_button.setEnabled(bool(self.project.clips) and self.preview_job is None and self.export_job is None and self.audio_analysis_job is None)
        self.export_button.setEnabled(bool(self.project.clips) and self.export_job is None and self.preview_job is None and self.audio_analysis_job is None)
        self.cancel_button.setEnabled(bool(self.jobs))
        self.asr_button.setEnabled((self.asr_source.currentIndex() == 1 or self.selected_clip() is not None)
            and not any(getattr(self,n,None) for n in ("asr_job","tts_job","assistant_job","semantic_job")))
        self.tts_button.setEnabled(self.tts_job is None)
        self.tts_insert_button.setEnabled(self.pending_speech is not None and self.tts_job is None)
        if hasattr(self, "sticker_job"):
            self.add_original_sticker_button.setEnabled(self.sticker_job is None and self.sticker_choice.currentData() is not None)
            self.import_sticker_button.setEnabled(self.sticker_job is None)

        if hasattr(self, "source_audio_status"): self.refresh_source_audio_status()
        self.refresh_track_lock_controls()
        self.audio_gain_button.setEnabled(can_open_audio_gain(self))
        if self.audio_gain_dialog is not None: self.audio_gain_dialog.check_current()
        if hasattr(self, "shortcuts"): self.shortcuts.update_enabled()
        if hasattr(self, "update_tts_controls"): self.update_tts_controls()
        if hasattr(self, "sound_audition"): self.update_sound_controls()
        if hasattr(self, "ai_suggest_button"): self.update_ai_controls()

    def changed(self):
        proposal = getattr(self, "loudness_proposal_dialog", None)
        if proposal is not None:
            proposal.check_current()
        self.dirty = project_fingerprint(self.project) != self.saved_project_fingerprint
        self.revision += 1; self.player.pause()
        self.invalidate_preview_frame()
        self.preview_label.setText(tr('Preview out of date · Build preview to see all edits and hear the mix.'))
        self.refresh()

    def edit(self, function):
        try:
            before = (self.editor, self.editor.revision)
            result = function()
            if before != (self.editor, self.editor.revision):
                self.changed()
            return result
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc)); self.refresh_inspector(); self.refresh_effects(); return None

    def select_clip(self, cid):
        if hasattr(self, "invalidate_review_selection"): self.invalidate_review_selection()
        self.active_selection_type = "clip"
        self.timeline.transition_selection = ""
        self.caption_list.blockSignals(True); self.caption_list.setCurrentRow(-1); self.caption_list.blockSignals(False)
        self.timeline.caption_selection = ""; self.timeline.overlay_selection = ""
        self.selection = cid; self.timeline.selection = cid; self.timeline.update(); self.refresh_inspector(); self.refresh_clip_effects()
        self.update_playback_controls()

    def toggle_clip_selection(self, cid):
        if not any(c.id == cid for c in self.project.clips):
            return
        ids = list(self.selected_clip_ids) if self.active_selection_type == 'clip' else []
        members = reconcile_group_ids(self.project, (cid,))
        if set(members) <= set(ids):
            ids = [selected for selected in ids if selected not in members]
        else:
            ids.extend(members)
        self.active_selection_type = 'clip'
        self.caption_list.blockSignals(True); self.caption_list.setCurrentRow(-1); self.caption_list.blockSignals(False)
        self.timeline.caption_selection = ''; self.timeline.overlay_selection = ''
        self.set_selected_clip_ids(ids, anchor=self.selection if self.selection in ids else None)
        self.timeline.update(); self.refresh_inspector(); self.refresh_clip_effects()
        self.update_playback_controls()

    def _destructive_focus_blocked(self):
        app = QApplication.instance()
        if app.activeModalWidget() is not None or app.activePopupWidget() is not None:
            return True
        if getattr(getattr(self, 'shortcuts', None), '_composing', None) is not None:
            return True
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self.media_browser or self.media_browser.isAncestorOf(focus)):
            return True
        while focus is not None and focus is not self:
            if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit, QAbstractSpinBox, QtWidgets.QComboBox)):
                return True
            if focus.window() is not self:
                return True
            focus = focus.parentWidget()
        return False

    def delete_active_selection(self):
        if self._destructive_focus_blocked():
            return
        focus = QApplication.focusWidget()
        # Window-level shortcuts must not delete timeline content while the
        # user edits text/numbers or browses the media bin/keyframe table.
        if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit, QAbstractSpinBox, QtWidgets.QComboBox)):
            return
        if self.workspace_mode == 'simple' and (
            self.active_selection_type == 'effect' or focus is self.effect_list
            or focus is self.overlay_keyframes
            or (focus and self.overlay_keyframes.isAncestorOf(focus))
        ):
            self.statusBar().showMessage(tr('Switch to Professional to inspect or delete the selected effect / keyframe.'), 6000)
            return
        if focus is self.overlay_keyframes or (focus and self.overlay_keyframes.isAncestorOf(focus)):
            self.delete_overlay_keyframe(); return
        if isinstance(focus, QAbstractItemView):
            if focus is self.caption_list: self.delete_caption()
            elif focus is self.overlay_list: self.delete_overlay()
            elif focus is self.effect_list: self.delete_clip_effect()
            return
        if self.active_selection_type == "caption": self.delete_caption()
        elif self.active_selection_type == "overlay": self.delete_overlay()
        elif self.active_selection_type == "effect": self.delete_clip_effect()
        elif self.active_selection_type == "transition": self.remove_selected_video_transition()
        else: self.delete_selected()

    def add_selected_media(self):
        item = self.bin.currentItem()
        if not item or item.data(Qt.ItemDataRole.UserRole) not in self.selected_media_ids(): return
        c = self.edit(lambda: self.editor.add_clip(item.data(Qt.ItemDataRole.UserRole), self.track_choice.currentIndex()))
        if c: self.select_clip(c.id)

    def split_selected(self):
        focus = QApplication.focusWidget()
        if self._destructive_focus_blocked(): return
        ancestor = focus
        while ancestor is not None and ancestor is not self:
            if isinstance(ancestor, QAbstractItemView): return
            ancestor = ancestor.parentWidget()
        kind, _ = self.split_selection_qualification()
        if kind == 'linked':
            self.open_linked_av_split()
        elif kind == 'independent':
            self.edit(lambda: self.editor.split_clip(self.selection, self.timeline.playhead))

    def delete_selected(self):
        ids = self.selected_clip_ids
        if ids and self.active_selection_type == 'clip':
            self.edit(lambda: self.editor.delete_clips(ids))
            self.refresh()

    def ripple_delete_selected(self):
        c = self.selected_clip()
        if c:
            self.edit(lambda: self.editor.ripple_delete_clip(c.id))
            if self.selected_clip() is None: self.selection = ''
            self.refresh()

    def move_clip(self, cid, start, track):
        clip = next((c for c in self.project.clips if c.id == cid), None)
        if clip is not None and (clip.start, clip.track) != (start, track):
            self.edit(lambda: self.editor.move_clip(cid, start, track))

    def move_selected_clips(self, ids, delta):
        if delta and tuple(ids) == self.selected_clip_ids and self.active_selection_type == 'clip':
            self.edit(lambda: self.editor.move_clips(ids, delta))

    def trim_clip_edge(self, cid, edge, delta):
        # No-motion clicks must not dirty the project or invalidate playback.
        if delta:
            pair = av_link(self.project, cid)
            if pair is not None:
                if (self.active_selection_type != 'clip' or self.selection != cid
                        or linked_pair(self.project, self.selected_clip_ids) != pair):
                    return
                self.edit(lambda: self.editor.trim_linked_av(cid, edge, delta))
            else:
                self.edit(lambda: self.editor.trim_edge(cid, edge, delta))

    def apply_timing(self):
        c = self.selected_clip()
        if not c: return
        def apply():
            # Passing an unchanged duration would silently replace a partial
            # source endpoint with O + D*R, even for a move-only operation.
            requested = {'start': self.start_spin.value(), 'source_in': self.in_spin.value(),
                         'duration': self.duration_spin.value()}
            changed = {key: value for key, value in requested.items() if value != getattr(c, key)}
            # Explicit source-in entry is a duration-preserving edit with the displayed
            # output duration. It is checked against EOF, never clamped.
            if 'source_in' in changed:
                changed['duration'] = requested['duration']
            if changed:
                return self.editor.update_clip(c.id, **changed)
            return c
        try:
            unchanged = (self.start_spin.value() == c.start and self.in_spin.value() == c.source_in
                         and self.duration_spin.value() == c.duration)
        except ValueError:
            unchanged = False
        if not unchanged: self.edit(apply)

    def refresh_slip_range(self, *_):
        c = self.selected_parameter_clip()
        if not c:
            self.slip_range_note.setText(''); self.apply_slip_button.setEnabled(False); return
        try:
            origin = self.slip_source_edit.value()
            mapping = ClipTimeMap(origin, origin + c.source_out - c.source_in, c.speed)
            media = next(m for m in self.project.media if m.id == c.media_id)
            if mapping.source_out > self.project.media_frames(media):
                raise ValueError('Source slip exceeds the available media range.')
        except ValueError as exc:
            self.slip_range_note.setText(tr_error(exc)); self.apply_slip_button.setEnabled(False); return
        self.slip_range_note.setText(tr('Slip selects [{source_in}, {source_out}); timeline stays at {start} for {duration} frames.', source_in=str(origin), source_out=str(mapping.source_out), start=c.start, duration=c.duration))
        self.apply_slip_button.setEnabled(origin != c.source_in)

    def apply_slip(self):
        c = self.selected_parameter_clip()
        if not c: return
        try:
            origin = self.slip_source_edit.value()
        except ValueError as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc))
            self.refresh_inspector(); return
        if origin != c.source_in:
            if linked_pair(self.project, self.selected_clip_ids):
                self.open_linked_av_slip(origin)
            else:
                self.edit(lambda: self.editor.slip_clip(c.id, origin))

    def refresh_speed_duration(self, *_):
        c = self.selected_clip()
        if not c:
            self.speed_duration_note.setText(''); self.apply_speed_button.setEnabled(False); return
        try:
            speed = parse_speed(self.speed_edit.text().strip())
            frames = duration_frames(ClipTimeMap(c.source_in, c.source_out, speed))
        except (ValueError, TypeError, OverflowError) as exc:
            self.speed_duration_note.setText(tr_error(exc)); self.apply_speed_button.setEnabled(False); return
        if speed == c.speed:
            self.speed_duration_note.setText(tr('Current speed: {speed}× · duration {duration} frames. Enter a new speed, then apply.', speed=exact_speed_text(speed), duration=c.duration))
        else:
            self.speed_duration_note.setText(tr('Apply changes duration from {old} to {new} frames. Timeline start stays at {start}; the full selected source range is retained.', old=c.duration, new=frames, start=c.start))
        self.apply_speed_button.setEnabled(speed != c.speed)

    def refresh_roll_range(self, *_):
        if self.refresh_linked_roll_range(): return
        self.roll_range_note.setToolTip(''); self.roll_range_note.setAccessibleDescription('')
        self.apply_roll_button.setText(tr('Apply roll edit'))
        self.roll_anchor_note.setText(tr('Only these two clips change. Their combined span stays fixed; attached source audio follows. Captions, stickers and separate audio do not move. Existing fades are preserved; edits that cannot fit them are rejected.'))
        self.roll_cut_spin.setToolTip(tr('Absolute timeline frame for the selected clip’s outgoing cut, not an offset. Only a touching right clip on the same track can roll.'))
        c = self.selected_clip()
        if not c:
            self.roll_range_note.setText(''); self.apply_roll_button.setEnabled(False); return
        self.roll_cut_spin.setEnabled(c.end <= self.roll_cut_spin.maximum())
        try:
            if not self.roll_cut_spin.isEnabled():
                raise ValueError('This roll control supports timeline cuts up to 2147483647 frames.')
            left, right = self.editor.preview_roll_cut(c.id, self.roll_cut_spin.value())
        except ValueError as exc:
            self.roll_range_note.setText(tr_error(exc)); self.apply_roll_button.setEnabled(False); return
        media = next(m for m in self.project.media if m.id == right.media_id)
        self.roll_range_note.setText(tr('Right clip: {name}\nCut {old} → {new}; durations {left_duration} / {right_duration} frames. Combined span [{start}, {end}) stays fixed.\nSource ranges: [{left_in}, {left_out}) / [{right_in}, {right_out}).',
            name=media.name, old=c.end, new=left.end, left_duration=left.duration,
            right_duration=right.duration, start=left.start, end=right.end,
            left_in=str(left.source_in), left_out=str(left.source_out),
            right_in=str(right.source_in), right_out=str(right.source_out)))
        self.apply_roll_button.setEnabled(left.end != c.end)

    def apply_roll(self):
        if self.apply_linked_roll(): return
        c = self.selected_clip()
        if c and c.end <= self.roll_cut_spin.maximum() and self.roll_cut_spin.value() != c.end:
            self.edit(lambda: self.editor.roll_cut(c.id, self.roll_cut_spin.value()))

    def apply_speed(self):
        c = self.selected_clip()
        if not c: return
        try:
            rate = parse_speed(self.speed_edit.text().strip())
        except ValueError as exc:
            QMessageBox.warning(self, tr('Cannot make this edit'), tr_error(exc))
            self.refresh_inspector(); return
        if rate != c.speed:
            self.edit(lambda: self.editor.set_clip_speed(c.id, rate))

    def apply_grade(self, *_):
        c = self.selected_parameter_clip()
        if self.loading_controls or not c: return
        grade = copy.deepcopy(c.grade)
        sender = self.sender()
        if sender is self.grade_enabled:
            grade.enabled = self.grade_enabled.isChecked()
        else:
            # A changed control must not rewrite (or round) hidden values.
            fields = [key for key, spin in self.grade_spins.items() if spin is sender]
            if not fields:
                fields = BASIC_GRADE_FIELDS if self.workspace_mode == 'simple' else self.grade_spins
            for key in fields:
                setattr(grade, key, self.grade_spins[key].value())
        if grade == c.grade: return
        self.edit(lambda: self.editor.update_clip(c.id, grade=grade))

    def show_wheel(self, index):
        for i, control in enumerate(self.wheel_controls.values()): control.setVisible(i == index)

    def apply_color_field(self, key, value):
        c = self.selected_parameter_clip()
        if self.loading_controls or not c: return
        grade = copy.deepcopy(c.grade)
        if getattr(grade, key) == value: return
        setattr(grade, key, copy.deepcopy(value))
        self.edit(lambda: self.editor.update_clip(c.id, grade=grade))

    def curve_key(self): return ('master_curve', 'red_curve', 'green_curve', 'blue_curve', *SELECTIVE_CURVE_KEYS)[self.curve_choice.currentIndex()]

    def refresh_curve(self, *_):
        if not hasattr(self, 'selective_curve_control'): return
        c = self.selected_parameter_clip(); key = self.curve_key(); selective = key in SELECTIVE_CURVE_KEYS
        self.curve_editor.setVisible(not selective); self.custom_curve_hint.setVisible(not selective)
        self.selective_curve_control.setVisible(selective)
        if selective:
            points = getattr(c.grade if c else Grade(), key)
            self.selective_curve_control.set_curve(c.id if c else None, key, points)
        else:
            self.selective_curve_control.editor.cancel_gesture()
            self.selective_curve_control.context = None
            self.curve_editor.set_points(getattr(c.grade if c else Grade(), key))

    def apply_curve(self, points):
        if self.curve_key() not in SELECTIVE_CURVE_KEYS:
            self.apply_color_field(self.curve_key(), points)

    def open_qualifier(self):
        from .ui_qualifier import open_qualifier
        return open_qualifier(self)

    def open_window_animation(self):
        from .ui_window_animation import open_window_animation
        return open_window_animation(self)

    def open_final_frame_preview(self):
        from .ui_final_frame_preview import open_final_frame_preview
        return open_final_frame_preview(self)

    def set_preview_mark(self, kind):
        from .ui_preview_selection import set_mark
        set_mark(self, kind)

    def preview_selection(self):
        from .ui_preview_selection import preview_selection
        return preview_selection(self)

    def open_range_preview(self):
        from .ui_range_preview import open_range_preview
        return open_range_preview(self)

    def cancel_range_preview(self, *, close=False):
        if getattr(self, 'range_preview_dialog', None) is not None:
            from .ui_range_preview import cancel_range_previews
            cancel_range_previews(self, close=close)

    def open_frame_grade_preview(self):
        from .ui_frame_grade_preview import open_frame_grade_preview
        return open_frame_grade_preview(self)

    def open_color_picker(self):
        from .ui_color_picker import open_color_picker
        return open_color_picker(self)

    def apply_selective_curve(self, points):
        c = self.selected_parameter_clip(); key = self.curve_key()
        if c and self.active_selection_type == 'clip' and self.selective_curve_control.context == (c.id, key):
            self.apply_color_field(key, points)

    def reset_current_curve(self):
        self.selective_curve_control.editor.cancel_gesture()
        self.apply_color_field(self.curve_key(), getattr(Grade(), self.curve_key()))
        self.refresh_curve()

    def choose_lut(self):
        c = self.selected_parameter_clip()
        if not c: return
        path, _ = QFileDialog.getOpenFileName(self, tr('Choose Rec.709 .cube look LUT'), '', tr('Cube LUT (*.cube)'))
        if path:
            from .color import load_cube
            try:
                load_cube(path)
            except Exception as exc:
                QMessageBox.warning(self, tr('Could not load LUT'), tr_error(exc)); return
            grade = copy.deepcopy(c.grade); grade.lut_path = path; self.edit(lambda: self.editor.update_clip(c.id, grade=grade))

    def reset_grade(self):
        c = self.selected_parameter_clip()
        if not c: return
        grade = Grade()
        grade.input_encoding = c.grade.input_encoding
        if self.workspace_mode == 'simple':
            grade = copy.deepcopy(c.grade)
            for key in (*BASIC_GRADE_FIELDS, 'white_balance_enabled', 'white_balance_gains'):
                setattr(grade, key, getattr(Grade(), key))
        if grade != c.grade: self.edit(lambda: self.editor.update_clip(c.id, grade=grade))

    def apply_audio(self, *_):
        c = self.selected_parameter_clip()
        if self.loading_controls or not c: return
        self.edit(lambda: self.editor.update_clip(c.id, gain_db=self.gain.value(), muted=self.mute.isChecked(), fade_in=self.fade_in.value(), fade_out=self.fade_out.value()))

    def _restore_history(self, operation):
        self.refresh_source_audio_separation()
        self.refresh_source_audio_reattach()
        self.refresh_linked_av()
        from .source_overwrite import _signature
        audio_before = _signature(self.project) if self._source_audio_selection_history else None
        before = self.project.to_dict()
        if operation():
            after = self.project.to_dict()
            before.pop('track_edit_locks', None)
            after.pop('track_edit_locks', None)
            before.pop('timeline_markers', None)
            after.pop('timeline_markers', None)
            before.pop('av_links', None)
            after.pop('av_links', None)
            if before == after:
                self.marker_metadata_changed()
            else:
                self.changed()
            self.restore_source_audio_selection(audio_before)

    def undo(self):
        self._restore_history(self.editor.undo)

    def redo(self):
        self._restore_history(self.editor.redo)

    def zoom_timeline(self, value):
        self.timeline.cancel_gestures()
        self.timeline.scale = float(value)
        self.timeline.set_project(self.project, self.selection if self.active_selection_type in ('clip', 'effect') else '', self.selected_clip_ids)

    def seek_frame(self, frame):
        frame = max(0, min(max(0, self.project.duration_frames - 1), int(frame)))
        if frame != self.timeline.playhead:
            self._final_frame_seek_generation = getattr(self, '_final_frame_seek_generation', 0) + 1
        self.timeline.playhead = frame; self.timeline.update(); self.position_label.setText(timecode(frame, self.project))
        final_still = getattr(self, 'final_frame_preview_dialog', None)
        if final_still is not None:
            final_still.check_current()
        if self.clip_paste_dialog is not None: self.clip_paste_dialog.check_current()
        if self.linked_av_split_dialog is not None: self.linked_av_split_dialog.check_current()
        self.refresh_split_control()
        self.scrub.blockSignals(True); self.scrub.setValue(frame); self.scrub.blockSignals(False)
        if hasattr(self, 'shortcuts'): self.shortcuts.update_enabled()
        if self.preview_revision == self.revision and self.loaded_preview_mode == self.preview_mode:
            provenance = self.video.frame_provenance
            already_shown = (provenance is not None and provenance.timeline_frame == frame
                             and provenance.generation == self._preview_generation
                             and self._pending_seek_frame is None and not self._preview_ended)
            if not already_shown:
                self._pending_seek_frame = frame
                self._seek_target_frame = frame
                self._last_accepted_frame = None
                self._preview_ended = False
                self.video.clear_current()
                self.scopes.clear()
                self.update_reference_controls()
                # Do not round a fractional frame boundary down into the
                # preceding frame (e.g. 33.333 ms at 30 fps). Qt also
                # selects the previous frame on some exact boundaries: seek
                # one millisecond inside this CFR frame, not at its edge.
                self.request_preview_seek(math.ceil(Fraction(frame * 1000, 1) / self.project.fps) + (1 if frame else 0))

    def playback_position(self, milliseconds):
        # Position events are approximate and can precede the decoded target.
        # They must not move a just-requested seek back to the previous frame.
        if self._pending_seek_frame is not None:
            return
        frame = min(max(0, self.project.duration_frames - 1), round(milliseconds * float(self.project.fps) / 1000))
        if frame != self.timeline.playhead:
            self._final_frame_seek_generation = getattr(self, '_final_frame_seek_generation', 0) + 1
        self.timeline.playhead = frame; self.timeline.update(); self.position_label.setText(timecode(frame, self.project))
        final_still = getattr(self, 'final_frame_preview_dialog', None)
        if final_still is not None:
            final_still.check_current()
        if self.clip_paste_dialog is not None: self.clip_paste_dialog.check_current()
        if self.linked_av_split_dialog is not None: self.linked_av_split_dialog.check_current()
        self.refresh_split_control()
        self.scrub.blockSignals(True); self.scrub.setValue(frame); self.scrub.blockSignals(False)

    def open_source_viewer(self):
        return open_source_viewer(self)

    def toggle_play(self):
        stop_source_audition(self)
        self.sound_audition.stop()
        if self.preview_revision != self.revision or self.loaded_preview_mode != self.preview_mode: return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause(); self.play_button.setText(tr('Play'))
        else:
            if self._preview_ended:
                self.seek_frame(0)
            self.player.play(); self.play_button.setText(tr('Pause'))

    def video_frame(self, frame, generation=None):
        # Obsolete decoder callbacks cannot clear or overwrite a new image.
        if generation is not None and generation != self._preview_generation:
            return
        self.scope_counter += 1
        if not frame.isValid():
            if hasattr(self, 'video'):
                self.video.clear_current()
                self.update_reference_controls()
            self.scopes.clear()
            return
        valid = self.preview_revision == self.revision and self.loaded_preview_mode == self.preview_mode
        if not valid:
            if hasattr(self, 'video'):
                self.video.clear_current()
                self.update_reference_controls()
            self.scopes.set_stale(True)
            return
        # Keep the isolated scopes callback useful to non-player clients. Such
        # synthetic images never receive provenance or become capture eligible.
        if not hasattr(self, 'video'):
            self.scopes.set_stale(False)
            if self.scope_counter % 4 == 1:
                self.scopes.set_image(frame.toImage())
            return
        if (not self._preview_path or self.player.source() != QUrl.fromLocalFile(self._preview_path)
                or self._preview_ended or self._preview_failed):
            self.video.clear_current()
            self.scopes.clear()
            self.update_reference_controls()
            return
        provenance = frame_provenance(frame, self._preview_project, self.preview_revision,
                                      self._preview_path, self.loaded_preview_mode, self._preview_generation)
        if provenance is None:
            self.video.clear_current()
            self.scopes.clear()
            self.update_reference_controls()
            return
        if self._pending_seek_frame is not None and provenance.timeline_frame != self._pending_seek_frame:
            return
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if (not playing and self._seek_target_frame is not None
                and provenance.timeline_frame != self._seek_target_frame):
            return
        if (self._last_accepted_frame is not None
                and provenance.timeline_frame < self._last_accepted_frame):
            return
        try:
            image = preview_frame_image(frame)
        except (PreviewColorError, MemoryError) as error:
            # Never retain a previously accepted current frame/scopes on a
            # failed color conversion. The session-only reference is separate.
            self.video.clear_current()
            self.video.color_error = str(error)
            self.scopes.clear()
            self.update_reference_controls()
            self.statusBar().showMessage(tr('Preview color unavailable. Unsupported format or color metadata; no guessed conversion is displayed.'), 12000)
            return
        if image.isNull():
            self.video.clear_current()
            self.scopes.clear()
            self.update_reference_controls()
            return
        self._pending_seek_frame = None
        self._last_accepted_frame = provenance.timeline_frame
        self.video.set_image(image, provenance)
        self.scopes.set_stale(False)
        if self.scope_counter % 4 == 1 or self.scopes.data is None:
            self.scopes.set_image(image)
        self.update_reference_controls()

    def start_job(self, label, operation, success, kind, *, before_start=None, is_current=None):
        self._job_generation += 1
        job = Job(operation, self); self.jobs.add(job)
        setattr(self, kind + '_job', job)
        self.progress_label.setText(label); self.progress_bar.setValue(0)
        current = is_current or (lambda: True)
        job._is_current = current
        job.progress.connect(lambda p: self.progress_bar.setValue(max(0, min(100, round(p * 100)))) if current() and not job.cancel.is_set() and not self.closing else None)
        job.result.connect(lambda value: success(value) if not job.cancel.is_set() and not self.closing and current() else None)
        job.failed.connect(lambda msg: QMessageBox.warning(self, tr('{task} failed', task=label), tr_error(msg)) if not job.cancel.is_set() and not self.closing and current() else None)
        job.finished.connect(lambda: self.job_finished(job, kind))
        # Install caller-owned lifecycle state before even an immediate worker
        # result can emit finished. Late connections can miss that signal.
        if before_start is not None: before_start(job)
        job.start(); self.update_playback_controls(); return job

    def job_finished(self, job, kind):
        self.jobs.discard(job)
        current = getattr(job, '_is_current', lambda: True)()
        if getattr(self, kind + '_job') is job: setattr(self, kind + '_job', None)
        if current:
            self.progress_label.setText(tr('Canceled · sources unchanged') if job.cancel.is_set() else tr('Ready · local files only'))
        job.deleteLater(); self.update_playback_controls()
        if kind == "sound": self.load_sound_catalog()
        if self.closing and not self.jobs: self._job_close_timer.start(0)
        elif kind in ('preview', 'export') and self.preview_pending:
            self.preview_pending = False; QTimer.singleShot(0, self.build_preview)

    def cancel_jobs(self):
        self.cancel_range_preview()
        if getattr(self, 'final_frame_preview_dialog', None) is not None:
            from .ui_final_frame_preview import cancel_final_frame_previews
            cancel_final_frame_previews(self)
        self.cancel_clip_clipboard_work()
        self.cancel_source_audio_separation_work()
        self.cancel_source_audio_reattach_work()
        self.cancel_linked_av_work()
        invalidate_source_viewer(self)
        if self.closing and hasattr(self, "_media_status_job"):
            self.close_media_status()
        proposal = getattr(self, "loudness_proposal_dialog", None)
        if proposal is not None:
            proposal.cancel_work()
        if self.audio_analysis_dialog is not None:
            self.audio_analysis_dialog.cancel_analysis()
        animation = getattr(self, 'window_animation_dialog', None)
        if animation is not None and animation.isVisible():
            animation.cancel_refresh()
        if getattr(self, 'frame_grade_preview_dialog', None) is not None:
            from .ui_frame_grade_preview import cancel_frame_grade_previews
            cancel_frame_grade_previews(self)
        if hasattr(self, "cancel_controlled_edits"): self.cancel_controlled_edits()
        for job in self.jobs: job.cancel.set()
        self.progress_label.setText(tr('Canceling…'))

    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr('Import local media'), '', tr('Media (*.mp4 *.mov *.mkv *.webm *.avi *.m4v *.mp3 *.wav *.flac *.m4a *.aac *.ogg);;All files (*)'))
        if paths: self.import_paths(paths)

    def import_apple_log_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr('Import Apple Log 1 / BT.2020 (experimental)'), '', tr('Media (*.mp4 *.mov *.mkv);;All files (*)'))
        if paths:
            self.import_paths(paths, input_encoding='apple_log_bt2020')

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and all(u.isLocalFile() for u in event.mimeData().urls()): event.acceptProposedAction()

    def dropEvent(self, event): self.import_paths([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def change_preview_mode(self, index):
        self.preview_mode = 'bypass' if index else 'graded'
        self.invalidate_preview_frame(new_source=True)
        self.preview_request += 1
        self.player.pause()
        if self.preview_job:
            self.preview_job.cancel.set(); self.preview_pending = True
        cached = self.preview_paths.get((self.revision, self.preview_mode))
        if cached and Path(cached).exists():
            self.preview_pending = False
            self.activate_preview(cached, self.revision, self.preview_mode)
        else:
            self.preview_revision = -1
            self.video.clear_current()
            self.scopes.clear()
            self.preview_label.setText(tr('Before · restoration and grades bypassed') if index else tr('After · all corrections'))
            if self.export_job: self.preview_pending = True
            elif not self.preview_job: self.build_preview()
        self.update_playback_controls()

    def activate_preview(self, path, revision, mode):
        if revision != self.revision or mode != self.preview_mode:
            return False
        target_frame = self.timeline.playhead
        self.video.clear_current()
        self.scopes.clear()
        self.scope_counter = 0
        self.replace_preview_player()
        self._preview_path = str(Path(path).expanduser().absolute())
        self._preview_project = copy.deepcopy(self.project)
        self._preview_ended = False
        self._preview_failed = False
        self._pending_seek_frame = target_frame
        self._seek_target_frame = target_frame
        self._last_accepted_frame = None
        self.preview_revision = revision; self.loaded_preview_mode = mode
        # Bind all identity fields before starting this decoder. Each source
        # owns a distinct player/sink, so queued old-source events are rejected.
        self.player.setSource(QUrl.fromLocalFile(self._preview_path))
        label = tr('Before · ALL clip grades, LUTs and restoration bypassed') if mode == 'bypass' else tr('After · all clip corrections; 8-bit preview proxy')
        self.preview_label.setText(tr('{mode} · current Rec.709 SDR cache', mode=label))
        self.progress_bar.setValue(100); self.seek_frame(target_frame); self.update_playback_controls()
        return True

    def build_preview(self):
        if self.audio_analysis_job is not None:
            self.statusBar().showMessage(tr('Wait for audio analysis to finish or cancel it before preview or export.'), 5000)
            return
        if not self.project.clips or self.preview_job or self.export_job: return
        snapshot = copy.deepcopy(self.project); revision = self.revision; mode = self.preview_mode
        self.preview_request += 1; request = self.preview_request
        if mode == 'bypass':
            for clip in snapshot.clips:
                clip.grade.enabled = False
                clip.restoration.enabled = False
        target = self.cache / f'{uuid.uuid4().hex}.mp4'
        def operation(cancel, progress):
            engine = RenderEngine()
            try: return engine.render(snapshot, target, progress=progress, cancel=cancel, preview=True)
            finally: engine.close()
        def success(path):
            if revision != self.revision:
                self.preview_label.setText(tr('Edits changed while rendering. Build preview again for the latest version.')); return
            if request != self.preview_request or mode != self.preview_mode: return
            self.preview_paths[(revision, mode)] = path
            self.activate_preview(path, revision, mode)
        self.preview_label.setText(tr('Rendering BEFORE · bypass restoration and grades… Timeline stays editable.') if mode == 'bypass' else tr('Rendering AFTER · all corrections… Timeline stays editable.'))
        self.start_job(tr('Building timeline preview…'), operation, success, 'preview')

    def export_dialog(self):
        if self.audio_analysis_job is not None:
            self.statusBar().showMessage(tr('Wait for audio analysis to finish or cancel it before preview or export.'), 5000)
            return
        if self.export_job or self.preview_job or not self.project.clips: return
        profile = choose_export_profile(self.project, self)
        if profile is None: return
        high_bit = profile == 'prores_422_hq_10bit'
        suffix = '.mov' if high_bit else '.mp4'
        title = tr('Export new MOV · existing files are protected') if high_bit else tr('Export new MP4 · existing files are protected')
        file_filter = tr('QuickTime video (*.mov)') if high_bit else tr('MP4 video (*.mp4)')
        path, _ = QFileDialog.getSaveFileName(self, title, str(Path.home() / ('film' + suffix)), file_filter)
        if not path: return
        if not path.lower().endswith(suffix): path += suffix
        if Path(path).exists():
            QMessageBox.warning(self, tr('Choose a new filename'), tr('This editor does not overwrite exports or source files. Choose a new filename.')); return
        snapshot = copy.deepcopy(self.project)
        def operation(cancel, progress):
            engine = RenderEngine()
            try: return engine.render(snapshot, path, progress=progress, cancel=cancel, export_profile=profile)
            finally: engine.close()
        def success(result):
            self.progress_bar.setValue(100)
            QMessageBox.information(self, tr('Export complete'), tr('Your edited film is saved:\n{path}\n\n{frames} frames · {fps:g} fps', path=result, frames=snapshot.duration_frames, fps=float(snapshot.fps)))
        self.start_job(tr('Exporting project snapshot…'), operation, success, 'export')

    def confirm_discard(self):
        if not self.dirty: return True
        result = QMessageBox.question(self, tr('Save project changes?'), tr('Save your edits before continuing?'), QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if result == QMessageBox.StandardButton.Cancel: return False
        if result == QMessageBox.StandardButton.Save: return self.save_project()
        return True

    def new_project(self):
        self.cancel_range_preview(close=True)
        self.cancel_clip_clipboard_work(close=True)
        self.cancel_source_audio_separation_work(close=True)
        self.cancel_source_audio_reattach_work(close=True)
        self.cancel_linked_av_work(close=True)
        close_ripple_delete(self)
        close_gap_close(self)
        self.close_timeline_markers()
        invalidate_source_viewer(self)
        close_loudness_proposal(self)
        if self.jobs:
            QMessageBox.information(self, tr('Work in progress'), tr('Cancel or finish the current job before changing projects.')); return
        if not self.confirm_discard(): return
        choice, ok = QInputDialog.getItem(self, tr('New project format'), tr('Timeline format'), ['1280 × 720 · 30 fps', '1920 × 1080 · 24 fps', '1920 × 1080 · 25 fps', '1920 × 1080 · 30 fps', '1080 × 1920 · 30 fps', '1920 × 1080 · 29.97 fps'], 0, False)
        if not ok: return
        formats = {'1280 × 720 · 30 fps': (1280,720,30,1), '1920 × 1080 · 24 fps': (1920,1080,24,1), '1920 × 1080 · 25 fps': (1920,1080,25,1), '1920 × 1080 · 30 fps': (1920,1080,30,1), '1080 × 1920 · 30 fps': (1080,1920,30,1), '1920 × 1080 · 29.97 fps': (1920,1080,30000,1001)}
        w,h,n,d = formats[choice]
        invalidate_source_viewer(self, close=True)
        close_audio_analysis(self)
        close_master_gain(self)
        close_audio_gain(self)
        close_ripple_delete(self)
        close_gap_close(self)
        self.close_timeline_markers()
        self.finish_recovery_session()
        self.sound_audition.stop()
        self.invalidate_preview_frame(new_source=True, clear_reference=True)
        self.player.stop(); self.player.setSource(QUrl()); self.editor = Editor(Project(width=w,height=h,fps_num=n,fps_den=d)); self.project_path = None; self.selection = ''; self.dirty = False; self.revision += 1; self.preview_revision = -1; self.timeline.playhead = 0; self.refresh()
        self.reset_recovery_session()
        self.preview_paths.clear()
        self.scopes.clear()
        self.preview_label.setText(tr('Import and add a clip to begin.'))

    def open_project(self):
        self.cancel_range_preview(close=True)
        self.cancel_clip_clipboard_work(close=True)
        self.cancel_source_audio_separation_work(close=True)
        self.cancel_source_audio_reattach_work(close=True)
        self.cancel_linked_av_work(close=True)
        close_ripple_delete(self)
        close_gap_close(self)
        self.close_timeline_markers()
        invalidate_source_viewer(self)
        close_loudness_proposal(self)
        if self.jobs:
            QMessageBox.information(self, tr('Work in progress'), tr('Cancel or finish the current job before changing projects.')); return
        if not self.confirm_discard(): return
        path, _ = QFileDialog.getOpenFileName(self, tr('Open project'), '', tr('Lumen project (*.lumen);;JSON (*.json)'))
        if not path: return
        try: project = Project.load(path)
        except Exception as exc: QMessageBox.warning(self, tr('Could not open project'), tr_error(exc)); return
        invalidate_source_viewer(self, close=True)
        close_audio_analysis(self)
        close_master_gain(self)
        close_audio_gain(self)
        close_ripple_delete(self)
        close_gap_close(self)
        self.close_timeline_markers()
        self.finish_recovery_session()
        self.sound_audition.stop()
        self.invalidate_preview_frame(new_source=True, clear_reference=True)
        self.player.stop(); self.player.setSource(QUrl()); self.editor = Editor(project); self.project_path = Path(path); self.selection = ''; self.dirty = False; self.revision += 1; self.preview_revision = -1; self.timeline.playhead = 0; self.refresh()
        self.reset_recovery_session()
        self.preview_paths.clear()
        self.scopes.clear()
        self.preview_label.setText(tr('Project loaded · Build preview to view the edited timeline.'))
        if project.missing_media(): QMessageBox.warning(self, tr('Media needs relinking'), tr('Some source files have moved. Use Relink missing media before preview or export.'))

    def save_project(self, save_as=False):
        path = self.project_path
        if path is None or save_as:
            filename, _ = QFileDialog.getSaveFileName(self, tr('Save project'), str(path or Path.home() / 'Untitled.lumen'), tr('Lumen project (*.lumen)'))
            if not filename: return False
            path = Path(filename if filename.endswith('.lumen') else filename + '.lumen')
        try:
            # Project saves may replace only project files; never a source or LUT.
            protected = [Path(m.path).resolve() for m in self.project.media] + [Path(c.grade.lut_path).resolve() for c in self.project.clips if c.grade.lut_path]
            if path.resolve() in protected: raise ValueError(tr('Cannot replace source media or a LUT with a project file.'))
            self.project.save(path); self.project_path = path; self.dirty = False; self.recovery_saved(); self.refresh(); self.statusBar().showMessage(tr('Project saved atomically.'), 5000); return True
        except Exception as exc: QMessageBox.warning(self, tr('Could not save project'), tr_error(exc)); return False

    def relink_dialog(self):
        invalidate_source_viewer(self)
        missing = self.project.missing_media()
        if not missing:
            QMessageBox.information(self, tr('All media found'), tr('Every source file is available.')); return
        for media in missing:
            path, _ = QFileDialog.getOpenFileName(self, tr('Relink {name}', name=media.name), '', tr('All media (*)'))
            if not path: continue
            try:
                self.guard_source_editable(media.id)
                relink_editor = self.editor
                relink_revision = self.editor.revision
                replacement = (probe(path) if media.input_encoding == "rec709" else
                               probe(path, input_encoding=media.input_encoding))
                from .color_source import validate_input_assignment
                for clip in self.project.clips:
                    if clip.media_id == media.id and replacement.has_video:
                        validate_input_assignment(replacement, clip.grade.input_encoding)
                if replacement.has_video != media.has_video or replacement.has_audio != media.has_audio: raise ValueError(tr('Replacement media must have matching video/audio stream types.'))
                if media.sound_provenance is not None:
                    from .sound_library import verify_sound_file
                    provenance = media.sound_provenance
                    verify_sound_file(replacement.path, provenance.output_sha256,
                                      provenance.sample_rate, provenance.sample_count)
                    replacement.duration = media.duration
                if media.speech_provenance is not None and media.speech_provenance.sample_count is not None:
                    from .tts import _hash
                    if _hash(Path(replacement.path), None) != media.speech_provenance.output_sha256.lower():
                        raise ValueError(f'Generated speech source has changed: {media.name}. '
                                         'Restore the original audio or generate speech again.')
                    # Identical PCM retains its exact duration; ffprobe rounds
                    # to six decimal places, which is only display precision.
                    replacement.duration = media.duration
                self.guard_source_editable(media.id)
                if self.editor is not relink_editor or self.editor.revision != relink_revision:
                    raise ValueError(tr('Project changed while relinking. Choose the source again.'))
                project = copy.deepcopy(self.project)
                target = next(m for m in project.media if m.id == media.id)
                target.path = replacement.path; target.duration = replacement.duration; target.width = replacement.width; target.height = replacement.height; target.audio_offset = replacement.audio_offset; target.color_space = replacement.color_space; target.fps_num = replacement.fps_num; target.fps_den = replacement.fps_den
                target.color_transfer = replacement.color_transfer
                target.color_primaries = replacement.color_primaries
                target.color_range = replacement.color_range
                target.input_encoding = replacement.input_encoding
                target.decoded_video_end_num = replacement.decoded_video_end_num
                target.decoded_video_end_den = replacement.decoded_video_end_den
                project.validate()
                # A relink is a project boundary: remove stale undo snapshots that refer to old paths.
                self.editor = Editor(project); self.changed()
            except Exception as exc: QMessageBox.warning(self, tr('Cannot relink this source'), tr_error(exc))

    def deleteLater(self):
        if hasattr(self, '_media_status_job') and self.defer_media_status_delete():
            return
        super().deleteLater()

    def showEvent(self, event):
        self._job_close_timer.stop()
        if hasattr(self, "_media_status_closed"):
            self.reopen_media_status()
        if hasattr(self, "shortcuts"):
            self.shortcuts.attach()
        super().showEvent(event)

    def closeEvent(self, event):
        self.timeline.cancel_gestures()
        if not self.closing:
            if self.jobs and any(not getattr(job, '_source_cleanup', False) for job in self.jobs):
                answer = QMessageBox.question(self, tr('Cancel active work?'), tr('Cancel the current job and close after cleanup?'))
                if answer != QMessageBox.StandardButton.Yes: event.ignore(); return
            if not self.confirm_discard(): event.ignore(); return
        # Closing is approved, including repeated attempts while owned workers
        # drain. Retire native seeks before cleanup can reenter Qt or defer the
        # close; a declined close must leave the active transport untouched.
        self.cancel_preview_seek()
        if self.jobs:
            if not self.closing:
                self.sound_audition.stop()
                self.closing = True; self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
                self.cancel_range_preview(close=True)
                invalidate_source_viewer(self, close=True)
                self.cancel_jobs()
                close_audio_analysis(self)
                close_master_gain(self)
                close_audio_gain(self)
                close_ripple_delete(self)
                close_gap_close(self)
                self.close_timeline_markers()
                if getattr(self, 'final_frame_preview_dialog', None) is not None:
                    self.final_frame_preview_dialog.reject()
                if getattr(self, 'frame_grade_preview_dialog', None) is not None:
                    self.frame_grade_preview_dialog.reject()
            event.ignore(); return
        # The user has approved closing. Retain session marks and active source
        # work until that decision, then wait for owned worker-only cleanup.
        invalidate_source_viewer(self, close=True)
        self.cancel_range_preview(close=True)
        if self.jobs:
            self.closing = True
            self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
            event.ignore(); return
        if not self.close_media_status():
            self.closing = True
            self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
            event.ignore(); return
        self.cancel_clip_clipboard_work(close=True)
        self.cancel_source_audio_separation_work(close=True)
        self.cancel_source_audio_reattach_work(close=True)
        self.cancel_linked_av_work(close=True)
        self._clip_clipboard = None
        self._job_close_timer.stop()
        if getattr(self, 'final_frame_preview_dialog', None) is not None:
            from .ui_final_frame_preview import cancel_final_frame_previews
            cancel_final_frame_previews(self, close=True)
        if getattr(self, 'frame_grade_preview_dialog', None) is not None:
            from .ui_frame_grade_preview import cancel_frame_grade_previews
            cancel_frame_grade_previews(self)
            self.frame_grade_preview_dialog.reject()
        close_audio_analysis(self)
        close_master_gain(self)
        close_audio_gain(self)
        close_ripple_delete(self)
        close_gap_close(self)
        self.close_timeline_markers()
        self.timeline.waveform_refresh.stop()
        self.timeline.waveforms.close()
        self.recovery_timer.stop()
        self.finish_recovery_session()
        self.recovery_store.close()
        if self.recovery_dialog is not None: self.recovery_dialog.close()
        if hasattr(self, "close_tts_preview"): self.close_tts_preview()
        self.sound_audition.stop()
        self.shortcuts.detach()
        self.invalidate_preview_frame(new_source=True, clear_reference=True)
        self.player.stop(); self.player.setSource(QUrl()); shutil.rmtree(self.cache, ignore_errors=True); event.accept()
