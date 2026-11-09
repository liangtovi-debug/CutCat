"""Explicit source transfer assignment; separate from creative grade controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QFormLayout, QComboBox, QLabel, QMessageBox

from .i18n import tr, tr_error
from .i18n_widgets import QComboBox, QFormLayout, QGroupBox, QLabel

class InputColorPanelMixin:
    def build_input_color_controls(self):
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        self.input_color_group = QGroupBox(tr('Input color · SDR normalization'))
        form = QFormLayout(self.input_color_group)
        self.input_encoding_choice = QComboBox()
        for label, value in [(tr('Rec.709 encoded RGB'), 'rec709'),
                             (tr('sRGB encoded RGB · Rec.709 primaries'), 'srgb'),
                             (tr('Linear RGB · Rec.709 primaries'), 'linear_rec709'),
                             (tr('Apple Log 1 / BT.2020 (experimental)'), 'apple_log_bt2020')]:
            self.input_encoding_choice.addItem(label, value)
        self.input_encoding_choice.setToolTip(
            tr('Assign source encoding, not a creative look. Apple Log 1 requires explicit experimental import and strict metadata; other camera Log, wide-gamut and HDR inputs remain unsupported.'))
        self.input_encoding_choice.currentIndexChanged.connect(self.apply_input_encoding)
        form.addRow(tr('Source transfer'), self.input_encoding_choice)
        note = QLabel(tr('Normalized to the SDR Rec.709 working space before grading. Before/After and correction bypass keep this normalization.'))
        note.setWordWrap(True)
        form.addRow(note)
        layout.addWidget(self.input_color_group)
        self.input_color_status = QLabel()
        self.input_color_status.setWordWrap(True)
        self.input_color_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.input_color_status)
        self.color_boundary_note = QLabel(tr('SDR pipeline: RGB16 decode → float32 grade → clipped RGB16 → delivery. Preview/scopes use an 8-bit H.264 proxy, not a 10-bit monitor. Untagged input is an assumption, not Log detection.'))
        self.color_boundary_note.setWordWrap(True)
        layout.addWidget(self.color_boundary_note)
        self.apple_log_note = QLabel(tr('Apple Log 1 / BT.2020 is experimental and tested with synthetic inputs only. No Apple Log 2, camera compatibility, Apple LUT or Resolve matching claim. Output remains clipped SDR Rec.709; no HDR mastering.'))
        self.apple_log_note.setToolTip(tr('Requires BT.2020 primaries and unknown transfer tags. Only full-range RGB16, or verified 10-bit YUV with BT.2020 non-constant matrix and explicit range.'))
        self.apple_log_note.setWordWrap(True)
        layout.addWidget(self.apple_log_note)
        self.apple_log_stabilization_note = QLabel(tr('Experimental Apple Log input does not support active stabilization.'))
        self.apple_log_stabilization_note.setWordWrap(True)
        layout.addWidget(self.apple_log_stabilization_note)
        return outer

    def refresh_input_color_controls(self):
        from .color_source import (validate_input_assignment, matching_input_encoding,
                                   INPUT_LABELS, UNKNOWN_TAGS, input_encoding_status)
        clip = self.selected_parameter_clip()
        self.input_encoding_choice.blockSignals(True)
        try:
            self.input_encoding_choice.setEnabled(clip is not None)
            if clip is None:
                self.input_color_status.setText(tr('Select a picture clip to inspect source color.'))
                return
            media = next(m for m in self.project.media if m.id == clip.media_id)
            self.input_encoding_choice.setCurrentIndex(self.input_encoding_choice.findData(clip.grade.input_encoding))
            if not media.has_video:
                self.input_encoding_choice.setEnabled(False)
                self.input_color_status.setText(tr('Audio-only clip: no input color conversion.'))
                return
            validate_input_assignment(media, clip.grade.input_encoding)
            if clip.grade.input_encoding == 'apple_log_bt2020':
                status = tr(input_encoding_status(media, clip.grade.input_encoding))
            else:
                known = matching_input_encoding(media.color_transfer)
                encoding = tr(INPUT_LABELS[known or clip.grade.input_encoding])
                transfer = (tr('Source transfer: {encoding}.', encoding=encoding) if known else
                            tr('Source transfer untagged; assigned {encoding}.', encoding=encoding))
                primaries = (tr('Rec.709 primaries assumed.') if media.color_primaries in UNKNOWN_TAGS else
                             tr('Primaries: {primaries}.', primaries=media.color_primaries))
                status = tr('{transfer} {primaries} Output: SDR Rec.709.', transfer=transfer, primaries=primaries)
            tags = tr('Matrix: {matrix}; range: {range}; transfer: {transfer}; primaries: {primaries}.', matrix=media.color_space, range=media.color_range, transfer=media.color_transfer, primaries=media.color_primaries)
            self.input_color_status.setText(tr('{status}\n{tags}\nInput assignment is available in Professional mode. Display calibration is not verified.', status=status, tags=tags))
        except ValueError as exc:
            self.input_color_status.setText(tr('Input color needs attention: {error}\nUse Professional mode to review source transfer. Unsupported HDR/gamut needs an external, verified SDR conversion.', error=tr_error(exc)))
        finally:
            self.input_encoding_choice.blockSignals(False)

    def apply_input_encoding(self, *_):
        if self.loading_controls or self.selected_parameter_clip() is None:
            return
        value = self.input_encoding_choice.currentData()
        if value:
            clip = self.selected_parameter_clip()
            media = next(m for m in self.project.media if m.id == clip.media_id)
            if 'apple_log_bt2020' in (value, media.input_encoding):
                from .color_source import validate_input_assignment
                try:
                    validate_input_assignment(media, value)
                except ValueError as exc:
                    QMessageBox.warning(self, tr('Input color'), tr_error(exc))
                    self.refresh_input_color_controls()
                    return
            self.apply_color_field('input_encoding', value)
