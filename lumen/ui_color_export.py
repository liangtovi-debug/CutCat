"""Explicit, non-destructive delivery-profile selection before the filename.

The chooser never starts a render or changes project data.  A profile is exposed
only after backend validation and an explicit accept; closing/canceling exposes
no selection.  Preview quality is deliberately described separately.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QVBoxLayout

from .color_output import H264_8BIT, PRORES_10BIT, validate_export_profile
from .i18n import locale_events, tr, tr_error
# Keep the optional catalog visible to frozen-app dependency collection.
from .i18n_catalog_color_foundation import VALIDATION_MESSAGES
from .i18n_widgets import (QComboBox, QDialog, QGroupBox, QLabel, QPushButton,
                           QScrollArea, QWidget)
from .ui_style import apply_ui_layout_tokens


def _note(text='', *, secondary=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if secondary:
        label.setProperty('secondaryText', True)
    return label


class ExportProfileDialog(QDialog):
    """Choose an SDR export; ``selected_profile`` is None until accepted."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.selected_profile: str | None = None
        self._finished = False
        self._validation_error: ValueError | None = None
        self.setWindowTitle(tr('Choose export format'))
        self.setModal(True)
        self.setMinimumSize(380, 360)
        self.resize(640, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        heading = _note(tr('Export color and quality'))
        heading.setProperty('sectionHeading', True)
        layout.addWidget(heading)
        layout.addWidget(_note(tr('Choose the delivery format, then choose a new filename. Your project and source files stay unchanged.'), secondary=True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(12)
        group = QGroupBox(tr('Delivery format'))
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(10)
        self.profile_choice = QComboBox()
        self.profile_choice.setObjectName('exportProfileChoice')
        self.profile_choice.setAccessibleName(tr('Delivery format'))
        self.profile_choice.addItem(tr('H.264 · 8-bit SDR · MP4 (default)'), H264_8BIT.key)
        self.profile_choice.addItem(tr('ProRes 422 HQ · 10-bit SDR · MOV'), PRORES_10BIT.key)
        self.profile_choice.setCurrentIndex(0)
        group_layout.addWidget(self.profile_choice)
        self.profile_detail = _note()
        group_layout.addWidget(self.profile_detail)
        self.profile_limits = _note()
        group_layout.addWidget(self.profile_limits)
        self.effect_note = _note(secondary=True)
        self.effect_note.setObjectName('exportEffectPrecision')
        group_layout.addWidget(self.effect_note)
        self.vignette_shape_note = _note(tr('Vignette radius / feather: 0.25–2; defaults 1 / 1 keep the original shape. Larger radius protects a wider center; higher feather spreads darkening inward. Preview / H.264 keep the legacy 8-bit path at defaults and use RGB8 for changed shapes; ProRes uses RGB16. These paths are not bit-identical. Shape processing is CPU-based and can be slow.'), secondary=True)
        self.vignette_shape_note.setObjectName('exportVignetteShape')
        self.vignette_shape_note.setVisible(any(effect.kind == 'vignette'
            for clip in project.clips for effect in clip.effects))
        group_layout.addWidget(self.vignette_shape_note)
        self.graphics_note = _note(secondary=True)
        self.graphics_note.setObjectName('exportGraphicsPrecision')
        group_layout.addWidget(self.graphics_note)
        body.addWidget(group)

        self.preview_note = _note(tr('Preview is an 8-bit H.264 proxy, even when exporting 10-bit. It is not a 10-bit output or display check; display calibration is not verified.'), secondary=True)
        body.addWidget(self.preview_note)
        self.validation_notice = _note()
        self.validation_notice.setObjectName('exportProfileValidation')
        self.validation_notice.setAccessibleName(tr('Export validation'))
        self.validation_notice.hide()
        body.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        # Keep a failed validation next to Continue, not below a scroll fold.
        layout.addWidget(self.validation_notice)

        self.buttons = QDialogButtonBox()
        self.continue_button = QPushButton(tr('Choose export filename…'))
        self.continue_button.setObjectName('primary')
        self.continue_button.setDefault(True)
        self.cancel_button = QPushButton(tr('Cancel'))
        self.cancel_button.setAutoDefault(False)
        self.buttons.addButton(self.continue_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.profile_choice.currentIndexChanged.connect(self._profile_changed)
        locale_events.changed.connect(self._refresh_error)
        self._profile_changed()
        apply_ui_layout_tokens(self)

    def _profile_changed(self, *_):
        self.selected_profile = None
        self._validation_error = None
        self.validation_notice.clear()
        self.validation_notice.hide()
        if self.profile_choice.currentData() == PRORES_10BIT.key:
            self.profile_detail.setText(tr('SDR Rec.709 only. The graded RGB16 cache clamps values to 0–1, then the export converts to 10-bit YUV 4:2:2. This is not HDR or an unbounded floating-point master.'))
            self.profile_limits.setText(tr('Existing basic captions and verified static PNG stickers are supported without reducing the background video to 8-bit. Gaussian blur and high-precision radial vignette are supported in native RGB16. Runtime checks require working 10-bit drawtext for captions, 10-bit overlay for stickers and RGB16 effects. Failures block export; there is no silent fallback. Animated sticker packs and unverified clip effects remain blocked. Disable them or choose H.264. ProRes files are substantially larger.'))
            self.effect_note.setText(tr('Blur and radial vignette use encoded Rec.709 RGB code values, not linear light or HDR. Blur Sigma: 0–30 project pixels; vignette amount: 0–1. The high-precision radial vignette is not bit-identical to the legacy 8-bit preview. Zero or disabled effects are bypassed. Clip-effect keyframes are not supported. High-bit blur uses conservative CPU processing and may export more slowly. High-precision radial vignette can substantially slow export.'))
            self.effect_note.show()
            self.graphics_note.setText(tr('Sticker artwork is still rendered by Qt into an 8-bit RGBA layer. Legacy gamma-coded SDR numeric appearance is retained, not linear-light blending, sRGB-to-Rec.709 transfer conversion, or ICC/HDR color management.'))
            self.graphics_note.show()
        else:
            self.effect_note.clear()
            self.effect_note.hide()
            self.graphics_note.clear()
            self.graphics_note.hide()
            self.profile_detail.setText(tr('Standard SDR Rec.709 delivery: H.264, 8-bit YUV 4:2:0 in MP4. Smaller files for everyday sharing.'))
            self.profile_limits.setText(tr('Uses the existing effects, sticker and caption pipeline. Higher-bit-depth sources are delivered as 8-bit; this profile is not HDR.'))

    def _refresh_error(self, *_):
        if self._validation_error is None:
            return
        error = str(self._validation_error)
        detail = tr(error) if error in VALIDATION_MESSAGES else tr_error(self._validation_error)
        self.validation_notice.setText(tr('This export format cannot be used:\n{error}', error=detail))
        self.validation_notice.show()

    def accept(self):
        if self._finished:
            return
        profile = self.profile_choice.currentData()
        try:
            validate_export_profile(self.project, profile)
        except ValueError as exc:
            self.selected_profile = None
            self._validation_error = exc
            self._refresh_error()
            return
        self.selected_profile = profile
        self._finished = True
        super().accept()

    def reject(self):
        self.selected_profile = None
        self._finished = True
        super().reject()

    def showEvent(self, event):
        # A reused dialog must not leak an earlier accepted selection.
        self.selected_profile = None
        self._finished = False
        self._validation_error = None
        self.validation_notice.clear()
        self.validation_notice.hide()
        super().showEvent(event)


def choose_export_profile(project, parent=None) -> str | None:
    """Return a validated profile key, or None on Cancel/Escape/window close.

    Call this before any save-filename dialog or render job.  The render engine
    also validates its snapshot, protecting non-UI callers and later changes.
    """
    dialog = ExportProfileDialog(project, parent)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selected_profile
        return None
    finally:
        dialog.deleteLater()
