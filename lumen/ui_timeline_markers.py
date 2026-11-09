"""Fixed-frame project annotations, independent of clips and rendered content.

The browser owns stable IDs, not row numbers. Editors are explicit local drafts:
no auto-save, no source I/O, and no rebase after any captured edit boundary.
"""
from __future__ import annotations

from html import escape
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QListWidgetItem, QSizePolicy

from .i18n import tr, tr_error, locale_events
from .i18n_widgets import (QDialog, QWidget, QScrollArea, QLineEdit, QPlainTextEdit,
                           QComboBox, QCheckBox, QFormLayout, QListWidget)
from .timeline_markers import (TIMELINE_MARKER_COLORS, MAX_TIMELINE_MARKER_NAME,
    MAX_TIMELINE_MARKER_COMMENT, MAX_TIMELINE_MARKER_FRAME)
from .ui_interval_preview import plain_label, action_button, fit_dialog
from .ui_recovery import project_fingerprint

MARKER_COLORS = {'red': '#ed6a6a', 'orange': '#ef9d5e', 'yellow': '#e9cc63',
                 'green': '#71c58a', 'cyan': '#68c8d1', 'blue': '#76a9f1',
                 'purple': '#b296ed', 'pink': '#e58dbd'}
COLOR_LABELS = {'red': 'Red', 'orange': 'Orange', 'yellow': 'Yellow', 'green': 'Green',
                'cyan': 'Cyan', 'blue': 'Blue', 'purple': 'Purple', 'pink': 'Pink'}
MARKER_SCOPE = ('Project markers are fixed project-frame annotations, not clip or source markers. '
                'Insert, ripple and clip changes do not move them. Changing FPS keeps the frame numbers '
                'and changes their clock times. Markers never extend playback or export duration.')
MARKER_HELP = ('Timeline → Timeline markers opens a searchable list of named point and range annotations. '
               'Add a point at the playhead or enter exact project-frame range bounds, then Apply. '
               'Names, comments and colors are persistent metadata with one Undo per edit. '
               'Previous/Next skips out-of-range markers and never wraps; same-frame markers remain individually accessible in the list. '
               'Markers stay at fixed frame numbers through clip edits and FPS changes and never change rendered duration. '
               'They are not clip-attached, source In/Out, chapters, range playback or range export.')
MARKER_BOUNDS = ('Point: one frame position. Range: [start, end), with an exclusive end greater than start. '
                 'Shortening the timeline retains out-of-range markers; navigation is unavailable until '
                 'their complete bounds fit. No range playback, range export or chapter output.')


def marker_record(project, marker_id):
    return next((m for m in project.timeline_markers if m.id == marker_id), None)


def validated_marker_in_bounds(marker, duration):
    """O(1) UI check for immutable records already validated by Project.

    Keep public/model validation strict. Painting and every playback-key enable
    update must not rescan up to two million characters of annotation text.
    """
    return marker.start_frame < duration and (marker.end_frame is None or marker.end_frame <= duration)


def marker_description(project, marker, *, duration=None):
    kind = tr('Point marker') if marker.end_frame is None else tr('Range marker')
    bounds = str(marker.start_frame) if marker.end_frame is None else f'[{marker.start_frame}, {marker.end_frame})'
    duration = project.duration_frames if duration is None else duration
    status = tr('In range') if validated_marker_in_bounds(marker, duration) else tr('Out of range · retained')
    return str(tr('{name}\n{kind} · {color} · frames {bounds}\n{status}\n{comment}',
        name=marker.name, kind=kind, color=tr(COLOR_LABELS[marker.color]), bounds=bounds,
        status=status, comment=marker.comment))


def marker_tooltip(project, marker, *, duration=None):
    # Qt tooltips can auto-detect HTML. Only this escaped wrapper is markup;
    # annotation text is never interpreted as markup or links.
    return '<qt>' + escape(marker_description(project, marker, duration=duration)).replace('\n', '<br>') + '</qt>'


def _project_token(window):
    return id(window.editor), window.editor.session_id


def _draft_token(window):
    return (*_project_token(window), id(window.project), window.editor.revision, window.revision)


def _frame_value(text):
    if not re.fullmatch(r'[0-9]{1,16}', text) or int(text) > MAX_TIMELINE_MARKER_FRAME:
        raise ValueError('Marker frame must be a non-negative integer up to 9007199254740991')
    return int(text)


def marker_comment_text(editor):
    # toPlainText() normalizes NBSP to ordinary spaces. Raw document text
    # preserves code points; Qt paragraph separators represent the schema's
    # allowed LF. Literal U+2029 is forbidden in persisted marker comments.
    return editor.document().toRawText().replace('\u2029', '\n')


def set_marker_comment_text(editor, text):
    # Qt interprets a leading U+FEFF as an encoding signature in setPlainText.
    # Prefix before loading, then remove only that known code point through
    # the document API, preserving even a comment consisting solely of BOM.
    editor.setPlainText(' ' + text)
    cursor = QTextCursor(editor.document())
    cursor.setPosition(0); cursor.deleteChar(); editor.setTextCursor(cursor)
    editor.document().clearUndoRedoStacks()
    editor.document().setModified(False)


def set_marker_name_text(editor, text):
    # QLineEdit.setText also consumes a leading BOM. Remove a known temporary
    # prefix with native editing, then reset the unchanged maximum to clear
    # that initialization-only undo step without re-encoding the string.
    maximum = editor.maxLength()
    editor.setMaxLength(maximum + 1)
    editor.setText(' ' + text)
    editor.setSelection(0, 1); editor.del_()
    editor.setMaxLength(maximum)
    editor.setModified(False)


class MarkerEditDialog(QDialog):
    """One revision-bound Add/Edit draft, consumed by a successful Apply."""
    def __init__(self, window, marker_id=None, *, range_marker=False):
        super().__init__(window)
        self.window = window
        self.marker_id = marker_id
        self.record = marker_record(window.project, marker_id) if marker_id is not None else None
        if marker_id is not None and self.record is None:
            raise ValueError('Timeline marker no longer exists')
        self.context = _draft_token(window)
        self._closed_flag = self._stale = self._applying = False
        self.setModal(False)
        self.setWindowTitle(tr('Edit timeline marker') if marker_id is not None else tr('Add timeline marker'))
        self.resize(580, 670)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        body.addWidget(plain_label(tr(MARKER_SCOPE)))
        form = QFormLayout(); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit()
        # Qt counts UTF-16 units; the schema counts Unicode code points. Keep
        # enough draft headroom for over-limit input to be rejected, never
        # silently truncated to a valid 128-emoji name. Even a draft truncated
        # at 1024 Qt units still exceeds the 128-code-point schema limit.
        self.name.setMaxLength(MAX_TIMELINE_MARKER_NAME * 8)
        self.name.setAccessibleName(tr('Marker name')); self.name.setMinimumWidth(0)
        self.comment = QPlainTextEdit(); self.comment.setMinimumWidth(0); self.comment.setFixedHeight(105)
        self.comment.setAccessibleName(tr('Marker comment'))
        self.comment.setToolTip(tr('Plain text, up to 4096 characters; line breaks are allowed.'))
        self.color = QComboBox(); self.color.setAccessibleName(tr('Marker color'))
        for color in TIMELINE_MARKER_COLORS: self.color.addItem(tr(COLOR_LABELS[color]), color)
        # Draft headroom prevents Qt truncating an invalid 17-digit paste into
        # a different valid 16-digit frame. Exact parsing remains bounded.
        self.start = QLineEdit(); self.start.setMaxLength(128); self.start.setAccessibleName(tr('Start project frame'))
        self.end = QLineEdit(); self.end.setMaxLength(128); self.end.setAccessibleName(tr('Exclusive end project frame'))
        for control in (self.start, self.end):
            control.setMinimumWidth(0); control.setToolTip(tr('Exact project frame number, not seconds or source In/Out.'))
        self.range = QCheckBox(tr('Range marker')); self.range.setAccessibleName(tr('Range marker'))
        form.addRow(tr('Marker name'), self.name); form.addRow(tr('Marker comment'), self.comment)
        form.addRow(tr('Marker color'), self.color); form.addRow(tr('Start project frame'), self.start)
        form.addRow('', self.range); form.addRow(tr('Exclusive end project frame'), self.end)
        body.addLayout(form)
        self.capture_start_button = action_button('Use playhead as start', self.capture_start, body)
        self.capture_end_button = action_button('Use playhead as exclusive end', self.capture_end, body)
        self.capture_end_button.setText(tr('Use playhead as\nexclusive end'))
        body.addWidget(plain_label(tr(MARKER_BOUNDS)))
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.apply_button = action_button('Apply', self.apply_marker, row)
        self.cancel_button = action_button('Cancel', self.reject, row)
        layout.addLayout(row)
        record = self.record
        set_marker_name_text(self.name, record.name if record is not None else '')
        set_marker_comment_text(self.comment, record.comment if record is not None else '')
        self.color.setCurrentIndex(self.color.findData(record.color if record is not None else 'yellow'))
        start = record.start_frame if record is not None else window.timeline.playhead
        end = record.end_frame if record is not None else None
        self.start.setText(str(start)); self.end.setText(str(end if end is not None else min(MAX_TIMELINE_MARKER_FRAME, start + 1)))
        self.range.setChecked(end is not None or range_marker)
        self.range.toggled.connect(self.check_current)
        self.finished.connect(self._closed)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.check_current); self.timer.start()
        self.check_current()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def check_current(self, *_):
        if self._closed_flag or self._applying: return
        if self.window.closing: self.reject(); return
        record_changed = (self.marker_id is not None
                          and marker_record(self.window.project, self.marker_id) != self.record)
        self._stale = self._stale or self.context != _draft_token(self.window) or record_changed
        for control in (self.name, self.comment, self.color, self.start, self.range, self.capture_start_button):
            control.setEnabled(not self._stale)
        self.end.setEnabled(not self._stale and self.range.isChecked())
        self.capture_end_button.setEnabled(not self._stale and self.range.isChecked())
        self.apply_button.setEnabled(not self._stale)
        if self._stale:
            self.status.setText(tr('Project changed. Close and reopen the marker editor before applying.'))

    def capture_start(self):
        self.check_current()
        if not self._stale: self.start.setText(str(self.window.timeline.playhead))

    def capture_end(self):
        self.check_current()
        if not self._stale and self.range.isChecked(): self.end.setText(str(self.window.timeline.playhead))

    def apply_marker(self):
        self.check_current()
        if self._closed_flag or self._stale or self._applying: return
        try:
            start = _frame_value(self.start.text())
            end = _frame_value(self.end.text()) if self.range.isChecked() else None
            if len(self.name.text()) > MAX_TIMELINE_MARKER_NAME:
                raise ValueError('Invalid timeline marker name')
            comment = marker_comment_text(self.comment)
            if len(comment) > MAX_TIMELINE_MARKER_COMMENT:
                raise ValueError('Timeline marker comment must contain at most 4096 plain-text characters')
            fields = dict(name=self.name.text(), comment=comment,
                          color=self.color.currentData(), start_frame=start, end_frame=end)
            self._applying = True
            if self.marker_id is None:
                result = self.window.editor.add_timeline_marker(**fields)
            else:
                result = self.window.editor.update_timeline_marker(self.marker_id, **fields)
            # The editor owns no-op semantics. Do not dirty or refresh a no-op.
            if self.context[-2] != self.window.editor.revision:
                self.window.marker_metadata_changed()
            self.window.selected_timeline_marker_id = result.id
            self.window.refresh_timeline_markers()
            self.accept()
        except Exception as exc:
            self.status.setText(tr('Marker edit blocked:\n{error}', error=tr_error(exc)))
        finally:
            self._applying = False

    def _closed(self, *_):
        self._closed_flag = True; self.timer.stop()


class TimelineMarkersDialog(QDialog):
    """Live metadata browser; no list operation reads source files or renders."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window; self.context = _project_token(window)
        self._closed_flag = False; self._observed = None
        self.selected_id = getattr(window, 'selected_timeline_marker_id', None)
        self.setWindowTitle(tr('Timeline markers')); self.setModal(False); self.resize(630, 720)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); body = QVBoxLayout(content)
        body.addWidget(plain_label(tr(MARKER_SCOPE)))
        self.search = QLineEdit(); self.search.setAccessibleName(tr('Search marker names and comments'))
        self.search.setPlaceholderText(tr('Search marker names and comments')); self.search.setMinimumWidth(0)
        body.addWidget(self.search)
        self.color_filter = QComboBox(); self.color_filter.setAccessibleName(tr('Filter marker color'))
        self.color_filter.addItem(tr('All marker colors'), None)
        for color in TIMELINE_MARKER_COLORS: self.color_filter.addItem(tr(COLOR_LABELS[color]), color)
        body.addWidget(self.color_filter)
        self.marker_list = QListWidget(); self.marker_list.setAccessibleName(tr('Timeline marker list'))
        self.marker_list.setMinimumWidth(0); self.marker_list.setMinimumHeight(145)
        self.marker_list.setWordWrap(True); self.marker_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.marker_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.marker_list)
        self.details = plain_label(''); self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        body.addWidget(self.details)
        self.point_button = action_button('Add point at playhead…', window.add_timeline_marker_at_playhead, body)
        self.range_button = action_button('Add range marker…', lambda: window.edit_timeline_marker(range_marker=True), body)
        self.edit_button = action_button('Edit selected marker…', self.edit_selected, body)
        self.delete_button = action_button('Delete selected marker', self.delete_selected, body)
        self.navigate_button = action_button('Go to marker start', self.navigate_selected, body)
        self.previous_button = action_button('Previous timeline marker', self.previous_marker, body)
        self.next_button = action_button('Next timeline marker', self.next_marker, body)
        body.addWidget(plain_label(tr(MARKER_BOUNDS)))
        self.status = plain_label(''); body.addWidget(self.status); body.addStretch()
        self.scroll.setWidget(content); layout.addWidget(self.scroll, 1)
        self.close_button = action_button('Close', self.reject, layout)
        self.search.textChanged.connect(self.refresh_records); self.color_filter.currentIndexChanged.connect(self.refresh_records)
        self.marker_list.currentItemChanged.connect(self.selection_changed)
        self.marker_list.itemDoubleClicked.connect(lambda *_: self.navigate_selected())
        locale_events.changed.connect(self.refresh_records)
        self.finished.connect(self._closed)
        self.timer = QTimer(self); self.timer.setInterval(150); self.timer.timeout.connect(self.check_current); self.timer.start()
        self.refresh_records()

    def showEvent(self, event):
        super().showEvent(event); self.fit_available_screen()

    def fit_available_screen(self, available=None):
        fit_dialog(self, available)

    def check_current(self):
        if self._closed_flag: return
        if self.window.closing or self.context != _project_token(self.window): self.reject(); return
        observed = (self.window.editor.revision, self.window.revision, self.window.project.duration_frames,
                    self.window.project.fps, tuple(self.window.project.timeline_markers))
        if observed != self._observed:
            self._observed = observed; self.refresh_records()

    def selected_record(self):
        item = self.marker_list.currentItem()
        return marker_record(self.window.project, item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def refresh_records(self, *_):
        if self._closed_flag: return
        if self.context != _project_token(self.window): self.reject(); return
        selected = self.selected_id
        needle = self.search.text().casefold(); color = self.color_filter.currentData()
        records = sorted(self.window.project.timeline_markers, key=lambda m: (m.start_frame, m.id))
        duration = self.window.project.duration_frames
        records = [m for m in records if (color is None or m.color == color)
                   and (not needle or needle in m.name.casefold() or needle in m.comment.casefold())]
        self.marker_list.blockSignals(True); self.marker_list.clear()
        for record in records:
            kind = tr('Point marker') if record.end_frame is None else tr('Range marker')
            bounds = str(record.start_frame) if record.end_frame is None else f'[{record.start_frame}, {record.end_frame})'
            status = '' if validated_marker_in_bounds(record, duration) else str(tr(' · OUT OF RANGE'))
            text = str(tr('{bounds} · {kind} · {color}{status}\n{name}', bounds=bounds, kind=kind,
                          color=tr(COLOR_LABELS[record.color]), status=status, name=record.name))
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, record.id)
            item.setToolTip(marker_tooltip(self.window.project, record, duration=duration)); self.marker_list.addItem(item)
            if record.id == selected: self.marker_list.setCurrentItem(item)
        self.marker_list.blockSignals(False)
        self.selection_changed()
        self.status.setText(tr('{shown} of {total} markers · navigation uses all in-range markers, without wrapping.',
            shown=len(records), total=len(self.window.project.timeline_markers)))

    def selection_changed(self, *_):
        record = self.selected_record()
        if record is not None:
            self.selected_id = record.id; self.window.selected_timeline_marker_id = record.id
        self.details.setText(marker_description(self.window.project, record) if record is not None else tr('Select a marker to inspect, edit, delete or navigate.'))
        for button in (self.edit_button, self.delete_button): button.setEnabled(record is not None)
        self.navigate_button.setEnabled(record is not None and validated_marker_in_bounds(record, self.window.project.duration_frames))
        if hasattr(self.window.timeline, 'marker_selection'):
            self.window.timeline.marker_selection = record.id if record is not None else ''; self.window.timeline.update()

    def select_marker_id(self, marker_id):
        self.selected_id = marker_id
        current = self.marker_list.currentItem()
        if current is not None and current.data(Qt.ItemDataRole.UserRole) == marker_id: return
        item = next((self.marker_list.item(row) for row in range(self.marker_list.count())
                     if self.marker_list.item(row).data(Qt.ItemDataRole.UserRole) == marker_id), None)
        self.marker_list.setCurrentItem(item)
        self.selection_changed()

    def edit_selected(self):
        self.check_current(); record = self.selected_record()
        if not self._closed_flag and record is not None: self.window.edit_timeline_marker(record.id)

    def delete_selected(self):
        self.check_current(); record = self.selected_record()
        if self._closed_flag or record is None: return
        try:
            before = self.window.editor.revision
            self.window.editor.delete_timeline_marker(record.id)
            if before != self.window.editor.revision: self.window.marker_metadata_changed()
            self.selected_id = None; self.window.selected_timeline_marker_id = None; self.refresh_records()
        except Exception as exc:
            self.status.setText(tr('Marker edit blocked:\n{error}', error=tr_error(exc)))

    def navigate_selected(self):
        self.check_current(); record = self.selected_record()
        if not self._closed_flag and record is not None: self.window.navigate_timeline_marker(record.id)

    def previous_marker(self):
        self.window.previous_timeline_marker(); self.selected_id = getattr(self.window, 'selected_timeline_marker_id', None); self.refresh_records()

    def next_marker(self):
        self.window.next_timeline_marker(); self.selected_id = getattr(self.window, 'selected_timeline_marker_id', None); self.refresh_records()

    def _closed(self, *_):
        self._closed_flag = True; self.timer.stop()


class TimelineMarkersMixin:
    def marker_metadata_changed(self):
        self.timeline.cancel_gestures()
        old_revision = self.revision; self.revision += 1
        # Only a previously activated, current completed preview is carried.
        # Worker callbacks retain their captured old revision and reject normally.
        if self.preview_revision == old_revision and self.loaded_preview_mode == self.preview_mode:
            self.preview_revision = self.revision
        self.dirty = project_fingerprint(self.project) != self.saved_project_fingerprint
        self.refresh()

    def refresh_timeline_markers(self):
        selected = getattr(self, 'selected_timeline_marker_id', None)
        if marker_record(self.project, selected) is None:
            self.selected_timeline_marker_id = None
        if hasattr(self.timeline, 'marker_selection'):
            self.timeline.marker_selection = self.selected_timeline_marker_id or ''; self.timeline.update()
        browser = getattr(self, 'timeline_markers_dialog', None)
        if browser is not None and not browser._closed_flag:
            browser.selected_id = self.selected_timeline_marker_id; browser.check_current()
            if not browser._closed_flag: browser.select_marker_id(self.selected_timeline_marker_id)
        editor = getattr(self, 'marker_edit_dialog', None)
        if editor is not None: editor.check_current()

    def open_timeline_markers(self):
        if self.closing: return None
        dialog = getattr(self, 'timeline_markers_dialog', None)
        if dialog is not None and not dialog._closed_flag:
            dialog.check_current()
            if not dialog._closed_flag:
                dialog.show(); dialog.raise_(); dialog.activateWindow(); return dialog
        if dialog is not None: dialog.deleteLater()
        dialog = TimelineMarkersDialog(self); self.timeline_markers_dialog = dialog; dialog.show(); return dialog

    def edit_timeline_marker(self, marker_id=None, *, range_marker=False):
        if self.closing: return None
        previous = getattr(self, 'marker_edit_dialog', None)
        if previous is not None: previous.reject(); previous.deleteLater()
        if marker_id is not None and marker_record(self.project, marker_id) is None: return None
        dialog = MarkerEditDialog(self, marker_id, range_marker=range_marker)
        self.marker_edit_dialog = dialog; dialog.show(); return dialog

    def add_timeline_marker_at_playhead(self):
        return self.edit_timeline_marker()

    def navigate_timeline_marker(self, marker_id):
        if self.closing: return False
        record = marker_record(self.project, marker_id)
        if record is None: return False
        self.selected_timeline_marker_id = record.id
        self.refresh_timeline_markers()
        if not validated_marker_in_bounds(record, self.project.duration_frames):
            self.statusBar().showMessage(tr('Marker is outside the current timeline. Its original frame bounds are retained; no seek was made.'), 6000)
            return False
        self.seek_frame(record.start_frame)
        return True

    def adjacent_timeline_marker(self, direction):
        frame = self.timeline.playhead
        duration = self.project.duration_frames
        records = (m for m in self.project.timeline_markers if validated_marker_in_bounds(m, duration)
                   and (m.start_frame > frame if direction > 0 else m.start_frame < frame))
        choose = min if direction > 0 else max
        return choose(records, key=lambda m: (m.start_frame, m.id), default=None)

    def _adjacent_timeline_marker(self, direction):
        marker = self.adjacent_timeline_marker(direction)
        if marker is None:
            self.statusBar().showMessage(tr('No next in-range marker; navigation does not wrap.') if direction > 0
                                        else tr('No previous in-range marker; navigation does not wrap.'), 5000)
            return False
        return self.navigate_timeline_marker(marker.id)

    def previous_timeline_marker(self):
        return self._adjacent_timeline_marker(-1)

    def next_timeline_marker(self):
        return self._adjacent_timeline_marker(1)

    def close_timeline_markers(self):
        for name in ('timeline_markers_dialog', 'marker_edit_dialog'):
            dialog = getattr(self, name, None)
            if dialog is not None: dialog.reject()
