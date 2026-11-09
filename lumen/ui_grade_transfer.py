"""Single-clip grade transfer, deliberately separate from source interpretation."""
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QMessageBox

from .grade_transfer import copy_grade, grade_for_paste, is_picture_clip
from .i18n import tr, tr_error
from .i18n_widgets import QWidget, QLabel, QPushButton


class GradeTransferPanelMixin:
    def build_grade_transfer_controls(self):
        self._copied_grade = None
        self.grade_transfer_controls = QWidget()
        layout = QVBoxLayout(self.grade_transfer_controls)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.copy_grade_button = QPushButton(tr('Copy grade'))
        self.copy_grade_button.clicked.connect(self.copy_selected_grade)
        self.paste_grade_button = QPushButton(tr('Paste grade'))
        self.paste_grade_button.clicked.connect(self.paste_selected_grade)
        row.addWidget(self.copy_grade_button)
        row.addWidget(self.paste_grade_button)
        layout.addLayout(row)
        self.grade_transfer_note = QLabel(tr(
            'Copies all creative controls, correction enable/bypass, curves and LUT strength. '
            'Source input normalization, restoration, stabilization, transforms, effects and audio are not copied.'))
        self.grade_transfer_note.setWordWrap(True)
        self.grade_animation_note = QLabel(tr('Window keys keep their clip-local timeline frames when pasted; no duration or speed scaling. Keys outside the target clip remain saved, with endpoint holds.'))
        self.grade_animation_note.setWordWrap(True)
        layout.addWidget(self.grade_transfer_note)
        layout.addWidget(self.grade_animation_note)
        self.grade_transfer_status = QLabel()
        self.grade_transfer_status.setWordWrap(True)
        layout.addWidget(self.grade_transfer_status)
        self.copy_grade_button.setToolTip(tr(
            'Keep the committed grade privately in this window, including across project changes. '
            'Nothing is sent to the system clipboard. Closing this window clears the copy.'))
        self.paste_grade_button.setToolTip(tr(
            'Replace one selected picture clip’s complete creative grade in one undo step. '
            'The target’s input normalization stays unchanged. Any assigned LUT must still be the same readable file.'))
        self.paste_grade_button.setAccessibleDescription(tr('Window keys keep their clip-local timeline frames when pasted; no duration or speed scaling. Keys outside the target clip remain saved, with endpoint holds.'))
        self.refresh_grade_transfer_controls()
        return self.grade_transfer_controls

    def _grade_transfer_clip(self):
        clip = self.selected_parameter_clip()
        if getattr(self, 'active_selection_type', 'clip') != 'clip':
            return None
        return clip if clip and is_picture_clip(self.project, clip.id) else None

    def refresh_grade_transfer_controls(self):
        if not hasattr(self, 'copy_grade_button'):
            return
        clip = self._grade_transfer_clip()
        copied = self._copied_grade
        try:
            animated = bool(copied and copied.grade_for_input(clip.grade.input_encoding if clip else 'rec709').qualifier.window_keyframes)
        except (ValueError, AttributeError):
            animated = False
        self.grade_animation_note.setVisible(animated or bool(clip and clip.grade.qualifier.window_keyframes))
        self.copy_grade_button.setEnabled(clip is not None)
        changed = False
        if clip and copied:
            try:
                changed = copied.grade_for_input(clip.grade.input_encoding) != clip.grade
            except ValueError:
                pass
        self.paste_grade_button.setEnabled(bool(clip and copied and changed))
        if copied is None:
            self.grade_transfer_status.setText(tr('No grade copied in this window.'))
        elif clip and not changed:
            self.grade_transfer_status.setText(tr('Selected clip already has this creative grade. Input normalization is unchanged.'))
        elif copied.lut_identity is not None:
            self.grade_transfer_status.setText(tr('Grade copied. The original LUT file must stay readable and unchanged, even when bypassed.'))
        else:
            self.grade_transfer_status.setText(tr('Grade copied privately in this window. Select a picture clip to paste.'))

    def copy_selected_grade(self):
        clip = self._grade_transfer_clip()
        if clip is None:
            self.refresh_grade_transfer_controls()
            return
        try:
            # Replace the last successful copy only after all validation succeeds.
            copied = copy_grade(self.project, clip.id)
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot copy grade'), tr_error(exc))
            return
        self._copied_grade = copied
        self.refresh_grade_transfer_controls()

    def paste_selected_grade(self):
        clip = self._grade_transfer_clip()
        if clip is None or self._copied_grade is None:
            self.refresh_grade_transfer_controls()
            return
        try:
            grade = grade_for_paste(self.project, clip.id, self._copied_grade)
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot paste grade'), tr_error(exc))
            return
        if grade != clip.grade:
            self.edit(lambda: self.editor.update_clip(clip.id, grade=grade))
        self.refresh_grade_transfer_controls()
