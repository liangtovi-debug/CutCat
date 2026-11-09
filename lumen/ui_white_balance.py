"""Known-neutral source sampling, independent from artistic warm/tint controls."""
from __future__ import annotations
import copy
from PySide6.QtWidgets import QVBoxLayout
from .i18n import tr, tr_error, locale_events
from .i18n_widgets import QCheckBox, QGroupBox, QLabel, QPushButton
from .model import Grade
from .ui_color_picker import ColorPickerDialog
from .color_picker_source import capture_picker_snapshot
from .white_balance import sample_white_balance

STAGE = ('Selected clip only · after input normalization and spatial restoration; before white balance and all creative grading.\n'
         'RGB16 decode → float32 sample; image display is clipped 8-bit. Not the composited preview.')
NOTE = ('Choose a surface you know is neutral gray or white under the scene light, not an arbitrary colored object. '
        'Linear Rec.709 RGB gains preserve the sampled neutral’s luminance, not whole-image brightness. '
        'No Kelvin, camera RAW, HDR or chromatic-adaptation claim.')


class WhiteBalanceDialog(ColorPickerDialog):
    def __init__(self, window):
        super().__init__(window)
        self.gains = None
        self.context = (id(window.project), window.revision, window.selection, None, window.timeline.playhead,
                        window.selected_clip_ids, window.selection_generation)
        self.setWindowTitle(tr('Neutral-reference white balance · static frame'))
        self.stage.setText(tr(STAGE))
        self.canvas.setAccessibleName(tr('Static source frame for neutral-reference white balance'))
        self.status.setText(tr('Click a known neutral gray or white surface. Dark, clipped, padding and excessive-gain samples are rejected.'))
        self.note.setText(tr(NOTE))
        self.apply_button.setText(tr('Apply sampled white balance'))
        locale_events.changed.connect(self.refresh_language)

    def refresh_language(self, *_):
        self.setWindowTitle(tr('Neutral-reference white balance · static frame'))
        self.canvas.setAccessibleName(tr('Static source frame for neutral-reference white balance'))
        stage = tr(STAGE)
        if self.snapshot is not None:
            stage += '\n' + tr('Spatial restoration: applied to active picture.' if self.snapshot.restoration_applied else 'Spatial restoration: bypassed (disabled or zero strength).')
        self.stage.setText(stage)

    def context_current(self):
        w = self.window
        return (self.isVisible() and not w.closing and w.active_selection_type == 'clip'
                and self.context == (id(w.project), w.revision, w.selection, None, w.timeline.playhead,
                                     w.selected_clip_ids, w.selection_generation)
                and w.selected_parameter_clip() is not None)

    def invalidate(self):
        self.gains = None
        super().invalidate()

    def accept_snapshot(self, snapshot):
        if not self.context_current(): return
        if snapshot.stage != 'white_balance':
            self.invalidate(); return
        super().accept_snapshot(snapshot)
        if self.snapshot is not None and self.current():
            self.refresh_language()

    def sample_pixel(self, x, y):
        self.gains = None; self.apply_button.setEnabled(False)
        self.canvas.marker = None; self.canvas.update()
        if not self.current(): self.invalidate(); return
        if self.snapshot is None: return
        frame = self.snapshot.frame
        if 0 <= y < frame.shape[0] and 0 <= x < frame.shape[1]:
            self.gains = sample_white_balance(frame[y, x], valid=bool(self.snapshot.valid_mask[y, x]))
        if self.gains is None:
            self.status.setText(tr('No usable neutral reference here: choose an unclipped, moderately bright gray or white picture pixel.')); return
        self.canvas.marker = (x, y); self.canvas.update()
        self.status.setText(tr('Linear RGB gains {gains} · pixel ({x}, {y}). No grade change yet.',
            gains=' / '.join(f'{g:.4f}' for g in self.gains), x=x, y=y))
        self.apply_button.setEnabled(True)

    def apply_sample(self):
        if not self.current(): self.invalidate(); return
        if self.gains is None: return
        gains = list(self.gains)
        self.accept()
        self.window.commit_white_balance(True, gains)

    def _closed(self, *_):
        self.gains = None
        super()._closed()


def open_white_balance(window):
    clip = window.selected_parameter_clip()
    media = next((m for m in window.project.media if clip and m.id == clip.media_id), None)
    if clip is None or window.active_selection_type != 'clip' or not media or not media.has_video or clip.track >= 2:
        return None
    existing = getattr(window, 'white_balance_dialog', None)
    if existing is not None and existing.isVisible():
        existing.raise_(); existing.activateWindow(); return existing
    window.player.pause()
    window.selective_curve_control.editor.cancel_gesture()
    dialog = WhiteBalanceDialog(window); window.white_balance_dialog = dialog
    project = copy.deepcopy(window.project); frame = window.timeline.playhead
    dialog.show()
    def operation(cancel, progress):
        return capture_picker_snapshot(project, clip.id, frame, cancel=cancel, stage='white_balance')
    dialog.job = window.start_job(tr('Reading neutral-reference frame…'), operation, dialog.accept_snapshot, 'white_balance_picker')
    def failed(message):
        if dialog.context_current(): dialog.status.setText(tr_error(message))
    dialog.job.failed.connect(failed)
    return dialog


class WhiteBalancePanelMixin:
    def build_white_balance_controls(self):
        self.white_balance_group = QGroupBox(tr('Neutral-reference white balance'))
        layout = QVBoxLayout(self.white_balance_group)
        self.white_balance_enabled = QCheckBox(tr('Enable white balance'))
        self.white_balance_enabled.toggled.connect(self.apply_white_balance_enabled)
        layout.addWidget(self.white_balance_enabled)
        row = QVBoxLayout()
        self.white_balance_pick = QPushButton(tr('Pick known neutral gray / white…'))
        self.white_balance_pick.clicked.connect(self.open_white_balance)
        self.white_balance_reset = QPushButton(tr('Reset white balance'))
        self.white_balance_reset.clicked.connect(self.reset_white_balance)
        row.addWidget(self.white_balance_pick); row.addWidget(self.white_balance_reset); layout.addLayout(row)
        self.white_balance_status = QLabel(); self.white_balance_status.setWordWrap(True); layout.addWidget(self.white_balance_status)
        note = QLabel(tr('Source neutral correction before exposure. Warm / cool and Green / magenta remain separate artistic controls.'))
        note.setWordWrap(True); layout.addWidget(note)
        locale_events.changed.connect(self.refresh_white_balance_controls)
        return self.white_balance_group

    def refresh_white_balance_controls(self, *_):
        c = self.selected_parameter_clip()
        grade = c.grade if c else Grade()
        self.white_balance_enabled.blockSignals(True)
        self.white_balance_enabled.setChecked(grade.white_balance_enabled)
        self.white_balance_enabled.blockSignals(False)
        media = next((m for m in self.project.media if c and m.id == c.media_id), None)
        self.white_balance_group.setEnabled(bool(c and media and media.has_video and c.track < 2 and self.active_selection_type == 'clip'))
        self.white_balance_status.setText(tr('Linear RGB gains: {gains}', gains=' / '.join(f'{g:.4f}' for g in grade.white_balance_gains)))
        self.white_balance_reset.setEnabled(grade.white_balance_enabled or grade.white_balance_gains != [1., 1., 1.])

    def commit_white_balance(self, enabled, gains):
        c = self.selected_parameter_clip()
        if self.loading_controls or c is None or self.active_selection_type != 'clip': return
        grade = copy.deepcopy(c.grade)
        grade.white_balance_enabled = enabled
        grade.white_balance_gains = list(gains)
        if grade != c.grade:
            self.edit(lambda: self.editor.update_clip(c.id, grade=grade))

    def apply_white_balance_enabled(self, enabled):
        c = self.selected_parameter_clip()
        if c: self.commit_white_balance(enabled, c.grade.white_balance_gains)

    def reset_white_balance(self):
        self.commit_white_balance(False, [1., 1., 1.])

    def open_white_balance(self):
        return open_white_balance(self)
