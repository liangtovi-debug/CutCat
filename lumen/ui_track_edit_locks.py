"""Track edit locks are editing guards, never playback mute/solo controls."""
from PySide6.QtCore import QObject, QEvent, Qt, QRect
from PySide6.QtWidgets import QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QSlider, QTabBar

from .i18n_widgets import QLabel
from .i18n import tr
from .ui_recovery import project_fingerprint

LOCK_REASON = 'A selected track is edit-locked. Unlock every affected track to edit; playback and export are unchanged.'
LOCK_NOTE = 'Editing locked\nUnlock all affected tracks\nto edit.\nPlayback and export\nare unchanged.'
SOURCE_LOCK_REASON = 'This source is used by an edit-locked track. Unlock all referencing tracks before replacing it.'


def locked_clips(project, ids):
    ids = set(ids)
    return any(c.id in ids and project.is_track_edit_locked(c.track) for c in project.clips)


class TrackLockNote(QLabel):
    """Explicit short lines plus measured height for mixed CJK font fallback."""
    def fit_text_height(self):
        width = max(1, self.contentsRect().width() - 2 * self.margin())
        flags = int(self.alignment()) | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
        needed = self.fontMetrics().boundingRect(QRect(0, 0, width, 100000), flags, self.text()).height()
        margins = self.contentsMargins()
        height = needed + margins.top() + margins.bottom() + 2 * self.margin()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_text_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit_text_height()

    def _refresh_translations(self, locale=None):
        super()._refresh_translations(locale)
        self.fit_text_height()


class _LockedEditorControls(QObject):
    """Disable editing leaves, keeping scroll areas and nested tabs navigable.

    Remember local availability, not inherited disabled state. Panel refreshes
    restore that state first, recompute normal availability, then reapply locks.
    EnabledChange also prevents late panel callbacks re-enabling locked inputs.
    """
    def __init__(self, window):
        super().__init__(window)
        self.controls = {}
        self.restoring = False

    def lock(self, controls):
        for control in controls:
            if control not in self.controls:
                self.controls[control] = not control.testAttribute(Qt.WidgetAttribute.WA_ForceDisabled)
                control.installEventFilter(self)
            control.setEnabled(False)

    def restore(self):
        self.restoring = True
        try:
            for control, enabled in self.controls.items():
                control.removeEventFilter(self)
                control.setEnabled(enabled)
            self.controls.clear()
        finally:
            self.restoring = False

    def eventFilter(self, control, event):
        if (not self.restoring and event.type() == QEvent.Type.EnabledChange
                and control in self.controls
                and not control.testAttribute(Qt.WidgetAttribute.WA_ForceDisabled)):
            control.setEnabled(False)
        return False


class TrackEditLocksMixin:
    def restore_track_lock_inspector_controls(self):
        guard = getattr(self, '_locked_editor_controls', None)
        if guard is not None:
            guard.restore()

    def _lock_inspector_editing_controls(self, locked):
        if not locked:
            self.restore_track_lock_inspector_controls()
            return
        if not hasattr(self, '_locked_editor_controls'):
            self._locked_editor_controls = _LockedEditorControls(self)
        inspection = {self.wheel_choice, self.curve_choice, self.color_details_toggle,
                      self.speech_info_button, self.copy_grade_button}
        types = (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit,
                 QPlainTextEdit, QTextEdit, QSlider)
        controls = [control for control in self.inspector.findChildren(QObject)
                    if isinstance(control, types) and control not in inspection
                    and not isinstance(control.parentWidget(), QTabBar)]
        controls.extend([self.curve_editor, self.selective_curve_control,
                         *self.wheel_controls.values()])
        self._locked_editor_controls.lock(controls)

    def selection_edit_locked(self):
        if self.active_selection_type == 'transition':
            transition = self.selected_video_transition()
            ids = (transition.left_clip_id, transition.right_clip_id) if transition else ()
        elif self.active_selection_type in ('clip', 'effect'):
            ids = self.selected_clip_ids or (self.selection,)
        else:
            ids = ()
        return locked_clips(self.project, ids)

    def guard_source_editable(self, media_id):
        if any(c.media_id == media_id and self.project.is_track_edit_locked(c.track)
               for c in self.project.clips):
            raise ValueError(SOURCE_LOCK_REASON)

    def explain_track_edit_lock(self):
        self._last_track_lock_notice = str(tr(LOCK_REASON))
        self.statusBar().showMessage(self._last_track_lock_notice, 6000)

    def toggle_track_edit_lock(self, track, locked):
        self.timeline.cancel_gestures()
        editor_revision = self.editor.revision
        self.editor.set_track_edit_lock(track, locked)
        if self.editor.revision != editor_revision:
            self.track_edit_locks_changed()
        else:
            self.timeline.refresh_track_lock_buttons()

    def track_edit_locks_changed(self):
        # Metadata changes invalidate staged edit contexts, not rendered media.
        self.timeline.cancel_gestures()
        old_revision = self.revision
        self.revision += 1
        if self.preview_revision == old_revision:
            self.preview_revision = self.revision
        self.dirty = project_fingerprint(self.project) != self.saved_project_fingerprint
        self.refresh()

    def refresh_track_lock_controls(self):
        if not hasattr(self, 'timeline'):
            return
        locked = self.selection_edit_locked()
        reason = tr(LOCK_REASON) if locked else ''
        self.track_lock_note.setText(tr(LOCK_NOTE) if locked else '')
        self.track_lock_note.fit_text_height()
        self.track_lock_note.setVisible(locked)
        self.track_lock_note.setAccessibleDescription(reason)
        # The linked-roll inspector is a four-role review shell, not a new
        # parameter-edit selection. Keep it navigable while the ordinary lock
        # guard below still disables every affected editing leaf.
        roll_inspection = getattr(self, 'can_inspect_linked_roll', lambda: False)()
        self.inspector.setEnabled(self.selected_parameter_clip() is not None or roll_inspection)
        self._lock_inspector_editing_controls(locked)
        self.clip_effects_panel.setEnabled(not locked)
        for control in (self.inspector, self.clip_effects_panel, self.split_clip_button,
                        self.delete_clips_button, self.video_transition_button):
            if not hasattr(control, '_unlocked_tooltip'):
                control._unlocked_tooltip = getattr(control, '_translation_bindings', {}).get('setToolTip', control.toolTip())
            control.setToolTip(reason if locked else control._unlocked_tooltip)
        self.split_clip_button.setEnabled(not locked and self.selected_clip() is not None
                                          and self.active_selection_type == 'clip')
        self.delete_clips_button.setEnabled(not locked and bool(self.selected_clip_ids)
                                            and self.active_selection_type == 'clip')
        self.video_transition_button.setEnabled(not locked)
        insert_locked = self.project.is_track_edit_locked(self.track_choice.currentIndex())
        self.add_media_button.setEnabled(not insert_locked)
        self.add_media_button.setToolTip(tr('The chosen track is edit-locked. Unlock it to insert media.') if insert_locked else tr('Add to\nselected track'))
        self.timeline.refresh_track_lock_buttons()
        notice = getattr(self, '_last_track_lock_notice', None)
        if notice and self.statusBar().currentMessage() == notice:
            self.explain_track_edit_lock()
