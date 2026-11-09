"""Real, frame-based sticker and ordered-effect controls for the Qt editor.

Controls stage edits until Apply. Every command is one model transaction and
therefore one undo step; loading a selection never mutates the project.
"""
from __future__ import annotations

import copy
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QScrollArea, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .i18n import tr
from .i18n_widgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QListWidgetItem, QTableWidget

from .model import ClipEffect, Overlay, OverlayKeyframe


class EffectsPanelMixin:
    def build_effects_panels(self):
        """Build independent top-level panels, keeping their edit state separate."""
        self.overlay_selection = ''
        self.effect_selection = ''
        self._effect_panel_clip_id = ''
        self._effect_panel_session = None
        self._effect_panel_snapshot = None
        self._loaded_effect_context = None
        self.sticker_job = None
        self.stickers_panel = self._build_stickers()
        self.stickers_panel.setObjectName('stickersPanel')
        self.clip_effects_panel = self._build_clip_effects()
        self.clip_effects_panel.setObjectName('clipEffectsPanel')

    @staticmethod
    def _frame_spin(minimum=0, maximum=100_000_000):
        spin = QSpinBox(); spin.setRange(minimum, maximum)
        spin.setKeyboardTracking(False)
        return spin

    @staticmethod
    def _transform_spins():
        fields = {}
        for key, low, high, value, suffix in [
            ('x', -16384, 16384, 0, ' px'),
            ('y', -16384, 16384, 0, ' px'),
            ('scale', .01, 16, 1, ' ×'),
            ('opacity', 0, 1, 1, ''),
        ]:
            spin = QDoubleSpinBox(); spin.setRange(low, high)
            spin.setDecimals(3 if key in ('scale', 'opacity') else 1)
            spin.setSingleStep(.05 if key in ('scale', 'opacity') else 1)
            spin.setValue(value); spin.setSuffix(suffix); spin.setKeyboardTracking(False)
            fields[key] = spin
        return fields

    def _build_stickers(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)
        note = QLabel(tr('Original local stickers · no downloads\nStickers sit above picture, below subtitles. Build preview after edits.'))
        note.setWordWrap(True); layout.addWidget(note)
        self.sticker_choice = QComboBox(); self.sticker_choice.setIconSize(QSize(28, 28))
        self._populate_original_stickers()
        layout.addWidget(self.sticker_choice)
        self.add_original_sticker_button = self._button(tr('Add original sticker'), self.add_original_sticker, True)
        layout.addWidget(self.add_original_sticker_button)
        self.import_sticker_button = self._button(tr('Import PNG / animation pack…'), self.import_sticker_dialog)
        layout.addWidget(self.import_sticker_button)
        self.overlay_list = QListWidget(); self.overlay_list.setMinimumHeight(105)
        self.overlay_list.setMaximumHeight(150)
        self.overlay_list.currentItemChanged.connect(self.load_overlay)
        layout.addWidget(self.overlay_list)
        self.overlay_asset_label = QLabel(tr('Choose a sticker or import a local asset.'))
        self.overlay_asset_label.setWordWrap(True); layout.addWidget(self.overlay_asset_label)
        self.overlay_controls = QWidget(); layout.addWidget(self.overlay_controls)
        controls = QVBoxLayout(self.overlay_controls); controls.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout(); controls.addLayout(form)
        self.overlay_start = self._frame_spin()
        self.overlay_duration = self._frame_spin(1); self.overlay_duration.setValue(60)
        self.overlay_z = self._frame_spin(-(2**31), 2**31 - 1)
        self.overlay_enabled = QCheckBox(tr('Show sticker')); self.overlay_enabled.setChecked(True)
        form.addRow(tr('Start · project frame'), self.overlay_start)
        form.addRow(tr('Duration · frames'), self.overlay_duration)
        form.addRow(tr('Layer · higher is on top'), self.overlay_z)
        form.addRow(self.overlay_enabled)
        row = QHBoxLayout()
        row.addWidget(self._button(tr('Apply timing / layer'), self.apply_overlay_timing))
        self.delete_overlay_button = self._button(tr('Delete sticker'), self.delete_overlay)
        row.addWidget(self.delete_overlay_button); controls.addLayout(row)
        base = QGroupBox(tr('First-frame transform')); base_form = QFormLayout(base)
        self.overlay_transform = self._transform_spins()
        for key, label in [('x', tr('Left')), ('y', tr('Top')), ('scale', tr('Scale')), ('opacity', tr('Opacity'))]:
            base_form.addRow(label, self.overlay_transform[key])
        base_form.addRow(self._button(tr('Apply first-frame transform'), self.apply_overlay_transform))
        controls.addWidget(base)
        self.keyframe_toggle = QCheckBox(tr('Advanced · transform keyframes'))
        self.keyframe_toggle.setToolTip(tr('Animate sticker position, uniform scale and opacity over relative frames.'))
        controls.addWidget(self.keyframe_toggle)
        self.keyframe_panel = QGroupBox(tr('Sticker animation'))
        advanced = QVBoxLayout(self.keyframe_panel)
        note = QLabel(tr('Values interpolate linearly between keys. Frame 0 is the starting pose; the final pose holds to the end.'))
        note.setWordWrap(True); advanced.addWidget(note)
        self.overlay_keyframes = QTableWidget(0, 5)
        self.overlay_keyframes.setHorizontalHeaderLabels([tr('Frame'), tr('Left'), tr('Top'), tr('Scale'), tr('Opacity')])
        self.overlay_keyframes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.overlay_keyframes.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.overlay_keyframes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.overlay_keyframes.verticalHeader().hide()
        self.overlay_keyframes.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.overlay_keyframes.setMinimumHeight(115); self.overlay_keyframes.setMaximumHeight(165)
        self.overlay_keyframes.itemSelectionChanged.connect(self.load_overlay_keyframe)
        advanced.addWidget(self.overlay_keyframes)
        keyform = QFormLayout(); advanced.addLayout(keyform)
        self.overlay_keyframe_frame = self._frame_spin()
        keyform.addRow(tr('Relative frame'), self.overlay_keyframe_frame)
        self.overlay_keyframe_values = self._transform_spins()
        for key, label in [('x', tr('Left')), ('y', tr('Top')), ('scale', tr('Scale')), ('opacity', tr('Opacity'))]:
            keyform.addRow(label, self.overlay_keyframe_values[key])
        row = QHBoxLayout()
        row.addWidget(self._button(tr('Add / replace key'), self.apply_overlay_keyframe))
        self.delete_keyframe_button = self._button(tr('Delete key'), self.delete_overlay_keyframe)
        row.addWidget(self.delete_keyframe_button); advanced.addLayout(row)
        controls.addWidget(self.keyframe_panel)
        self.keyframe_toggle.toggled.connect(self.keyframe_panel.setVisible)
        self.keyframe_panel.hide()
        layout.addStretch(); return scroll

    def _populate_original_stickers(self):
        # Resource discovery stays in the asset module so installed apps and
        # source checkouts resolve the same owned, packaged resources.
        try:
            from .sticker_assets import builtin_stickers
            entries = builtin_stickers()
        except (ImportError, OSError):
            entries = []
        for entry in entries:
            if isinstance(entry, dict):
                title, path = entry['name'], entry['path']
            else:
                title, path = entry
            from .sticker_assets import load_sticker
            try:
                thumbnail = QIcon(str(load_sticker(path).frames[0]))
            except ValueError:
                thumbnail = QIcon()
            self.sticker_choice.addItem(thumbnail, title, str(path))
        if not self.sticker_choice.count():
            self.sticker_choice.addItem(tr('No bundled assets available'), None)

    def sticker_display_name(self, path):
        index = self.sticker_choice.findData(str(path))
        return self.sticker_choice.itemText(index) if index >= 0 else Path(path).stem

    def _build_clip_effects(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)
        self.effects_clip_label = QLabel(tr('Select a picture clip on the timeline.'))
        self.effects_clip_label.setWordWrap(True); layout.addWidget(self.effects_clip_label)
        note = QLabel(tr('Effects run top to bottom after clip color correction, before picture compositing. Reorder to change the result.'))
        note.setWordWrap(True); layout.addWidget(note)
        self.effects_controls = QWidget(); layout.addWidget(self.effects_controls)
        controls = QVBoxLayout(self.effects_controls); controls.setContentsMargins(0, 0, 0, 0)
        self.effect_kind = QComboBox()
        self.effect_kind.addItem(tr('Gaussian blur'), 'blur'); self.effect_kind.addItem(tr('Vignette'), 'vignette')
        controls.addWidget(self.effect_kind)
        self.effect_add_button = self._button(tr('Add effect to selected clip'), self.add_clip_effect, True)
        controls.addWidget(self.effect_add_button)
        self.effect_list = QListWidget(); self.effect_list.setMinimumHeight(130)
        self.effect_list.currentItemChanged.connect(self.load_clip_effect)
        self.effect_list.itemClicked.connect(self.load_clip_effect)
        self.effect_list.itemActivated.connect(self.load_clip_effect)
        controls.addWidget(self.effect_list)
        self.effect_editor = QGroupBox(tr('Selected effect')); form = QFormLayout(self.effect_editor)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.effect_amount_label = QLabel(tr('Amount'))
        self.effect_amount = QDoubleSpinBox(); self.effect_amount.setKeyboardTracking(False)
        self.effect_amount.setDecimals(2)
        self.effect_enabled = QCheckBox(tr('Enable effect'))
        form.addRow(self.effect_amount_label, self.effect_amount)
        self.effect_radius_label = QLabel(tr('Vignette radius'))
        self.effect_feather_label = QLabel(tr('Vignette feather'))
        self.effect_radius = QDoubleSpinBox(); self.effect_feather = QDoubleSpinBox()
        for spin in (self.effect_radius, self.effect_feather):
            spin.setRange(.25, 2); spin.setDecimals(3); spin.setSingleStep(.05)
            spin.setValue(1); spin.setKeyboardTracking(False)
        self._staged_effect_fields = set()
        for name, spin in (('amount', self.effect_amount), ('radius', self.effect_radius),
                           ('feather', self.effect_feather)):
            spin.valueChanged.connect(lambda _value, name=name: self._staged_effect_fields.add(name))
            # Explicitly retyping the displayed value can reset an imported
            # full-precision value that rounds to the same visible number.
            spin.lineEdit().textEdited.connect(lambda _text, name=name: self._staged_effect_fields.add(name))
        radius_tip = tr('Larger radius protects a wider center; smaller radius darkens more of the image. 1.000 preserves the original radius.')
        feather_tip = tr('Higher feather spreads darkening inward; lower feather keeps more of the center clear. 1.000 preserves the original falloff.')
        for widget in (self.effect_radius_label, self.effect_radius): widget.setToolTip(radius_tip)
        for widget in (self.effect_feather_label, self.effect_feather): widget.setToolTip(feather_tip)
        form.addRow(self.effect_radius_label, self.effect_radius)
        form.addRow(self.effect_feather_label, self.effect_feather)
        self.effect_vignette_note = QLabel(tr('Stage settings, then Apply and build preview.'))
        self.effect_vignette_note.setWordWrap(True)
        self.effect_vignette_note.setToolTip(tr('Centered mathematical vignette, not DaVinci Resolve parity or lens simulation. Shape changes use per-pixel CPU processing and can slow preview/export. No live preview or animated controls. Default shape preserves legacy rendering; changing shape switches preview/H.264 to RGB processing and can change color.'))
        form.addRow(self.effect_vignette_note)
        form.addRow(self.effect_enabled)
        self.effect_apply_button = self._button(tr('Apply effect settings'), self.apply_clip_effect)
        form.addRow(self.effect_apply_button)
        row = QHBoxLayout()
        self.effect_up_button = self._button(tr('Move up'), lambda: self.move_clip_effect(-1))
        self.effect_down_button = self._button(tr('Move down'), lambda: self.move_clip_effect(1))
        row.addWidget(self.effect_up_button); row.addWidget(self.effect_down_button); form.addRow(row)
        form.addRow(self._button(tr('Delete effect'), self.delete_clip_effect))
        controls.addWidget(self.effect_editor)
        layout.addStretch(); return scroll

    def selected_overlay(self):
        return next((o for o in self.project.overlays if o.id == self.overlay_selection), None)

    def selected_effect(self):
        # The independent panel may retain a read-only selection while a
        # caption or sticker is active. Mutations must also use effect_clip().
        if len(self.selected_clip_ids) > 1 and self.selected_parameter_clip() is None:
            return None
        clip = self._effect_panel_clip()
        return next((e for e in clip.effects if e.id == self.effect_selection), None) if clip else None

    def _effect_panel_clip(self):
        if self._effect_panel_session != (id(self.editor), self.editor.session_id):
            return None
        return next((c for c in self.project.clips if c.id == self._effect_panel_clip_id), None)

    def effect_clip(self):
        """An effect owns an explicit clip anchor, not a clip selection.

        Never use the primary anchor of a multi-selection as an implicit edit
        target. The retained anchor is valid only while an effect is active.
        """
        if self.active_selection_type not in ('clip', 'effect'):
            return None
        panel_clip = self._effect_panel_clip()
        if panel_clip is None:
            return None
        clip = self.selected_parameter_clip()
        if clip is not None and clip.id == panel_clip.id:
            return clip
        if self.active_selection_type == 'effect' and not self.selected_clip_ids:
            return panel_clip if panel_clip.id == self.selection else None
        return None

    def _refresh_effect_enabled(self):
        clip = self._effect_panel_clip()
        media = next((m for m in self.project.media if clip and m.id == clip.media_id), None)
        self.effects_controls.setEnabled(bool(clip and clip.track < 2 and media and media.has_video)
                                         and (len(self.selected_clip_ids) <= 1 or self.selected_parameter_clip() is not None))
        editable = self._picture_clip_selected()
        self.effect_kind.setEnabled(editable); self.effect_add_button.setEnabled(editable)
        self.effect_editor.setEnabled(editable and self.selected_effect() is not None)

    def _picture_clip_selected(self):
        clip = self.effect_clip()
        media = next((m for m in self.project.media if clip and m.id == clip.media_id), None)
        return bool(clip and clip.track < 2 and media and media.has_video)

    def refresh_effects(self):
        """Reconcile IDs after undo, delete or project replacement without edits."""
        overlay = self.selected_overlay()
        if not overlay: self.overlay_selection = ''
        self.overlay_list.blockSignals(True); self.overlay_list.clear()
        for item in sorted(self.project.overlays, key=lambda o: (o.start, o.z_order, o.id)):
            text = tr('{name}{hidden}\nf{start}–{end} · layer {layer}', name=self.sticker_display_name(item.asset_path), hidden=tr(' · hidden') if not item.enabled else '', start=item.start, end=item.start + item.duration, layer=item.z_order)
            row = QListWidgetItem(text); row.setData(Qt.ItemDataRole.UserRole, item.id)
            row.setToolTip(item.asset_path); self.overlay_list.addItem(row)
            if item.id == self.overlay_selection: self.overlay_list.setCurrentItem(row)
        self.overlay_list.blockSignals(False)
        self.load_overlay(activate=False)
        self.refresh_clip_effects()
        busy = self.sticker_job is not None
        self.add_original_sticker_button.setEnabled(not busy and self.sticker_choice.currentData() is not None)
        self.import_sticker_button.setEnabled(not busy)

    def refresh_clip_effects(self):
        session = (id(self.editor), self.editor.session_id)
        if self._effect_panel_session != session:
            self._effect_panel_clip_id = ''; self.effect_selection = ''
            self._effect_panel_session = session
        selected = self.selected_parameter_clip()
        if selected is not None:
            self._effect_panel_clip_id = selected.id
        clip = self._effect_panel_clip()
        if clip is None:
            self._effect_panel_clip_id = ''; self.effect_selection = ''
        self._refresh_effect_enabled()
        if clip:
            media = next((m for m in self.project.media if m.id == clip.media_id), None)
            picture = clip.track < 2 and media and media.has_video
            self.effects_clip_label.setText(tr('Clip: {name}\n{description}', name=media.name if media else clip.id, description=tr('Ordered picture effects') if picture else tr('Choose a picture clip; audio clips have no image effects.')))
        else:
            self.effects_clip_label.setText(tr('Select a picture clip on the timeline.'))
        snapshot = (session, clip.id if clip else '', clip.effects if clip else [])
        if snapshot == self._effect_panel_snapshot:
            return  # Navigation never discards an unchanged panel's draft.
        self._effect_panel_snapshot = copy.deepcopy(snapshot)
        if not clip or not any(e.id == self.effect_selection for e in clip.effects):
            self.effect_selection = ''
        self.effect_list.blockSignals(True); self.effect_list.clear()
        for index, effect in enumerate(clip.effects if clip else []):
            label = tr('Gaussian blur') if effect.kind == 'blur' else tr('Vignette')
            amount = f'{effect.amount:g} px' if effect.kind == 'blur' else f'{effect.amount:g}'
            item = QListWidgetItem(tr('{index}. {label} · {amount}{bypassed}', index=index + 1, label=label, amount=amount, bypassed=tr(' · bypassed') if not effect.enabled else ''))
            item.setData(Qt.ItemDataRole.UserRole, effect.id); self.effect_list.addItem(item)
            if effect.id == self.effect_selection: self.effect_list.setCurrentItem(item)
        self.effect_list.blockSignals(False); self.load_clip_effect(activate=False)

    def _clear_caption_selection(self):
        self.caption_list.blockSignals(True); self.caption_list.setCurrentRow(-1); self.caption_list.blockSignals(False)
        self.timeline.caption_selection = ''

    def load_overlay(self, *_args, activate=True):
        if activate:
            item = self.overlay_list.currentItem()
            self.overlay_selection = item.data(Qt.ItemDataRole.UserRole) if item else ''
        overlay = self.selected_overlay(); self.overlay_controls.setEnabled(overlay is not None)
        if overlay:
            if activate:
                self.active_selection_type = 'overlay'; self._clear_caption_selection()
                self.clear_clip_selection()
                self.refresh_inspector(); self.refresh_clip_effects()
                self.timeline.selection = ''; self.timeline.overlay_selection = overlay.id
                self.update_playback_controls()
            self.overlay_start.setValue(overlay.start); self.overlay_duration.setValue(overlay.duration)
            self.overlay_z.setValue(overlay.z_order); self.overlay_enabled.setChecked(overlay.enabled)
            self.overlay_asset_label.setText(f'{self.sticker_display_name(overlay.asset_path)} · {Path(overlay.asset_path).name}')
            self.overlay_asset_label.setToolTip(overlay.asset_path)
            first = overlay.keyframes[0]
            for key, spin in self.overlay_transform.items(): spin.setValue(getattr(first, key))
            self.overlay_keyframe_frame.setMaximum(overlay.duration - 1)
            previous = self.overlay_keyframe_frame.value()
            self.overlay_keyframes.blockSignals(True); self.overlay_keyframes.setRowCount(len(overlay.keyframes))
            selected_row = 0
            for row, keyframe in enumerate(overlay.keyframes):
                for col, key in enumerate(('frame', 'x', 'y', 'scale', 'opacity')):
                    cell = QTableWidgetItem(f'{getattr(keyframe, key):g}')
                    cell.setData(Qt.ItemDataRole.UserRole, keyframe.frame)
                    self.overlay_keyframes.setItem(row, col, cell)
                if keyframe.frame == previous: selected_row = row
            self.overlay_keyframes.selectRow(selected_row); self.overlay_keyframes.blockSignals(False)
            self.load_overlay_keyframe()
        else:
            self.overlay_keyframes.setRowCount(0)
            self.overlay_asset_label.setText(tr('Choose a sticker or import a local asset.'))
            self.overlay_asset_label.setToolTip('')
        self.timeline.overlay_selection = self.overlay_selection if self.active_selection_type == 'overlay' else ''
        self.timeline.update()

    def select_overlay(self, overlay_id):
        self.overlay_selection = overlay_id
        self.side_tabs.setCurrentWidget(self.stickers_panel)
        for index in range(self.overlay_list.count()):
            item = self.overlay_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == overlay_id:
                self.overlay_list.setCurrentItem(item); self.load_overlay(); return
        self.load_overlay(activate=False)

    def load_caption(self, *_args):
        super().load_caption(*_args)
        if self.selected_caption():
            self.timeline.overlay_selection = ''; self.timeline.update()

    def add_original_sticker(self):
        path = self.sticker_choice.currentData()
        if path: self.import_sticker_path(path)

    def import_sticker_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Choose a local sticker'), '', tr('Stickers (*.png *.json)'))
        if path: self.import_sticker_path(path)

    def import_sticker_path(self, path):
        if self.sticker_job: return
        from .sticker_assets import load_sticker
        revision = self.revision; start = self.timeline.playhead
        duration = max(1, round(float(self.project.fps) * 2))
        width, height = self.project.width, self.project.height
        path = str(Path(path).expanduser().absolute())
        def operation(cancel, progress):
            asset = load_sticker(path, cancel=cancel); progress(1); return asset
        def success(asset):
            if self.revision != revision:
                QMessageBox.warning(self, tr('Sticker import is stale'), tr('The project changed while the sticker was loading. No sticker was added. Import it again into the current edit.')); return
            scale = max(.01, min(1., width * .28 / asset.width, height * .28 / asset.height))
            key = OverlayKeyframe(frame=0, x=round(width * .06), y=round(height * .08), scale=scale, opacity=1.)
            overlay = Overlay(id=uuid4().hex, asset_path=path, asset_sha256=asset.digest,
                              start=start, duration=duration,
                              z_order=max((o.z_order for o in self.project.overlays), default=-1) + 1,
                              enabled=True, keyframes=[key])
            before = self.revision
            self.edit(lambda: self.editor.add_overlay(overlay))
            if self.revision != before: self.select_overlay(overlay.id)
        self.start_job(tr('Loading local sticker…'), operation, success, 'sticker')

    def apply_overlay_timing(self):
        overlay = self.selected_overlay()
        if not overlay: return
        values = dict(start=self.overlay_start.value(), duration=self.overlay_duration.value(),
                      z_order=self.overlay_z.value(), enabled=self.overlay_enabled.isChecked())
        if max(k.frame for k in overlay.keyframes) >= values['duration']:
            QMessageBox.warning(self, tr('Keep animation keys inside the sticker'), tr('The new duration would leave animation keys outside the sticker. Move or delete those keys first; your animation has been preserved.'))
            self.load_overlay(activate=False); return
        if all(getattr(overlay, key) == value for key, value in values.items()): return
        self.edit(lambda: self.editor.update_overlay(overlay.id, **values)); self.load_overlay(activate=False)

    def apply_overlay_transform(self):
        overlay = self.selected_overlay()
        if not overlay: return
        keys = copy.deepcopy(overlay.keyframes)
        if keys[0].frame != 0:
            keys.insert(0, copy.deepcopy(keys[0])); keys[0].frame = 0
        for key, spin in self.overlay_transform.items(): setattr(keys[0], key, spin.value())
        if keys == overlay.keyframes: return
        self.edit(lambda: self.editor.update_overlay(overlay.id, keyframes=keys)); self.load_overlay(activate=False)

    def delete_overlay(self):
        overlay = self.selected_overlay()
        if overlay: self.edit(lambda: self.editor.delete_overlay(overlay.id))

    def load_overlay_keyframe(self):
        overlay = self.selected_overlay(); row = self.overlay_keyframes.currentRow()
        if not overlay or not 0 <= row < len(overlay.keyframes):
            self.delete_keyframe_button.setEnabled(False); return
        keyframe = overlay.keyframes[row]
        self.overlay_keyframe_frame.setValue(keyframe.frame)
        for key, spin in self.overlay_keyframe_values.items(): spin.setValue(getattr(keyframe, key))
        self.delete_keyframe_button.setEnabled(keyframe.frame != 0 and len(overlay.keyframes) > 1)

    def apply_overlay_keyframe(self):
        overlay = self.selected_overlay()
        if not overlay: return
        keyframe = OverlayKeyframe(frame=self.overlay_keyframe_frame.value(),
                                  **{key: spin.value() for key, spin in self.overlay_keyframe_values.items()})
        keys = [copy.deepcopy(key) for key in overlay.keyframes if key.frame != keyframe.frame] + [keyframe]
        keys.sort(key=lambda key: key.frame)
        if keys == overlay.keyframes: return
        self.edit(lambda: self.editor.update_overlay(overlay.id, keyframes=keys))

    def delete_overlay_keyframe(self):
        overlay = self.selected_overlay(); row = self.overlay_keyframes.currentRow()
        if not overlay or not 0 <= row < len(overlay.keyframes): return
        frame = overlay.keyframes[row].frame
        if frame == 0 or len(overlay.keyframes) <= 1: return
        keys = [copy.deepcopy(key) for key in overlay.keyframes if key.frame != frame]
        self.edit(lambda: self.editor.update_overlay(overlay.id, keyframes=keys))

    def load_clip_effect(self, *_args, activate=True):
        if activate:
            if (len(self.selected_clip_ids) > 1 and self.selected_parameter_clip() is None) or self._effect_panel_clip() is None:
                return
            item = self.effect_list.currentItem()
            self.effect_selection = item.data(Qt.ItemDataRole.UserRole) if item else ''
        clip = self._effect_panel_clip()
        # Loading disabled controls is read-only even during multi-selection.
        # If undo changes the model then, never revive the preceding draft.
        effect = next((e for e in clip.effects if e.id == self.effect_selection), None) if clip else None
        if activate and effect:
            self.active_selection_type = 'effect'; self._clear_caption_selection()
            self.set_selected_clip_ids((), anchor=clip.id)
            self.refresh_inspector()
            self.timeline.overlay_selection = ''; self.timeline.update()
            self.update_playback_controls()
        self._refresh_effect_enabled()
        for widget in (self.effect_radius_label, self.effect_radius,
                       self.effect_feather_label, self.effect_feather, self.effect_vignette_note):
            widget.setVisible(effect is not None and effect.kind == 'vignette')
        if effect:
            index = next(i for i, entry in enumerate(clip.effects) if entry.id == effect.id)
            self.effect_up_button.setEnabled(index > 0)
            self.effect_down_button.setEnabled(index < len(clip.effects) - 1)
        context = (self._effect_panel_session, clip.id, effect) if effect else None
        if effect and context == self._loaded_effect_context:
            return  # Explicitly returning to this effect preserves staged values.
        self._loaded_effect_context = copy.deepcopy(context)
        self._loaded_effect_values = {}
        self._staged_effect_fields = set()
        if effect:
            blur = effect.kind == 'blur'
            self.effect_amount_label.setText(tr('Blur radius · sigma') if blur else tr('Edge darkening'))
            self.effect_amount.setRange(0, 30 if blur else 1)
            self.effect_amount.setSingleStep(.5 if blur else .05)
            self.effect_amount.setSuffix(' px' if blur else '')
            self.effect_amount.setValue(effect.amount); self.effect_enabled.setChecked(effect.enabled)
            self.effect_radius.setValue(effect.radius); self.effect_feather.setValue(effect.feather)
            # Imported values retain their full precision unless that control
            # actually changes. Applying another field must not round them.
            self._loaded_effect_values = {
                'amount': self.effect_amount.value(), 'radius': self.effect_radius.value(),
                'feather': self.effect_feather.value(),
            }
            self._staged_effect_fields.clear()

    def select_clip_effect(self, effect_id):
        clip = self._effect_panel_clip()
        if (len(self.selected_clip_ids) > 1 and self.selected_parameter_clip() is None) or clip is None or not any(e.id == effect_id for e in clip.effects):
            return
        self.effect_selection = effect_id
        if self.workspace_mode == 'professional':
            self.side_tabs.setCurrentWidget(self.clip_effects_panel)
        for index in range(self.effect_list.count()):
            item = self.effect_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == effect_id:
                self.effect_list.setCurrentItem(item); self.load_clip_effect(); return

    def add_clip_effect(self):
        if not self._picture_clip_selected(): return
        clip = self.effect_clip(); kind = self.effect_kind.currentData()
        effect = ClipEffect(id=uuid4().hex, kind=kind, amount=3. if kind == 'blur' else .4, enabled=True)
        revision = self.revision
        self.edit(lambda: self.editor.add_effect(clip.id, effect))
        if self.revision != revision: self.select_clip_effect(effect.id)

    def apply_clip_effect(self):
        clip = self.effect_clip(); effect = self.selected_effect()
        if not clip or not effect: return
        changes = {'enabled': self.effect_enabled.isChecked()}
        for name, spin in (('amount', self.effect_amount), ('radius', self.effect_radius),
                           ('feather', self.effect_feather)):
            if effect.kind == 'blur' and name != 'amount': continue
            if name in self._staged_effect_fields or spin.value() != self._loaded_effect_values.get(name):
                changes[name] = spin.value()
        if all(getattr(effect, name) == value for name, value in changes.items()): return
        self.edit(lambda: self.editor.update_effect(clip.id, effect.id, **changes))

    def move_clip_effect(self, delta):
        clip = self.effect_clip(); effect = self.selected_effect()
        if not clip or not effect: return
        index = next(i for i, entry in enumerate(clip.effects) if entry.id == effect.id) + delta
        if 0 <= index < len(clip.effects):
            self.edit(lambda: self.editor.move_effect(clip.id, effect.id, index))

    def delete_clip_effect(self):
        clip = self.effect_clip(); effect = self.selected_effect()
        if clip and effect: self.edit(lambda: self.editor.delete_effect(clip.id, effect.id))
