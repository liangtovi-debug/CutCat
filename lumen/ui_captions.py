"""Caption editing and opt-in, offline transcription controls for the Qt window."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, is_dataclass
from fractions import Fraction
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSettings, QUrl, QRect, QEvent
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QSpinBox,
    QDoubleSpinBox, QFileDialog, QMessageBox, QScrollArea, QSizePolicy)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaDevices
from .i18n import tr, tr_error
from .i18n_widgets import (QFormLayout, QLabel, QLineEdit, QPlainTextEdit,
                           QComboBox, QGroupBox, QCheckBox)
from .model import Caption, validate_captions
from .subtitles import read_srt, write_srt
from .timing import round_half_up, source_to_local, duration_frames


def tts_pack_label(pack):
    """Keep proper pack names and persisted keys separate from UI labels."""
    if pack.experimental:
        return tr('{name} · experimental · documented voices: {count}',
                  name=pack.display_name, count=len(pack.voices))
    return tr('{name} · documented voices: {count}',
              name=pack.display_name, count=len(pack.voices))


def tts_pack_notice(pack):
    if pack.experimental:
        return tr('Experimental local pack · {rate} kHz · at most {limit} characters per audition or saved take. Listening quality is not certified. Unsupported characters stop generation; edit the reading text explicitly or choose another pack. No automatic text rewriting. Long or slow takes may use several GiB of memory; split long reading text.',
                  rate=pack.sample_rate / 1000, limit=pack.max_text_chars)
    return tr('Local English/Chinese synthesis · {rate} kHz. Listen to an audition before saving a take.',
              rate=pack.sample_rate / 1000)


def _caption_snapshot(caption):
    return (caption.id, caption.start, caption.duration, caption.text, caption.language)


@dataclass(frozen=True)
class _PendingSpeechContext:
    """Bind a saved take to one edit session and immutable source assumptions.

    Editor identity survives undo/redo, whose snapshots replace Project objects.
    The tuple identity also prevents a reentrant dialog from consuming a newer take.
    """
    editor: object
    fps: Fraction
    pending: tuple
    caption: tuple
    media: str


def segments_to_captions(segments, fps, *, source_in=0, duration=None, start=0,
                         time_map=None, collapsed=None):
    """Intersect exact source times, inverse-map, then quantize each edge once.

    ``collapsed`` optionally receives nonempty source cues discarded because
    both output boundaries round to the same frame. Legacy/media-bin calls
    retain their 1x mapping. No existing timeline captions are retimed.
    """
    try:
        if isinstance(fps, bool):
            raise ValueError
        rate = Fraction(str(fps)) if isinstance(fps, float) else Fraction(fps)
        if not Fraction(1, 1000) <= rate <= 1000:
            raise ValueError
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('Frame rate must be finite and in 0.001..1000') from exc
    origin = time_map.source_in if time_map is not None else Fraction(source_in)
    endpoint = (time_map.source_out if time_map is not None else
                origin + duration if duration is not None else None)
    output_duration = duration_frames(time_map) if time_map is not None else duration
    def position(seconds):
        if isinstance(seconds, bool):
            raise ValueError('Caption time must be finite and nonnegative')
        try:
            value = Fraction(str(seconds)) if isinstance(seconds, float) else Fraction(seconds)
            if value < 0:
                raise ValueError
            return value * rate
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
            raise ValueError('Caption time must be finite and nonnegative') from exc
    captions = []
    source_ranges = []
    for segment in segments:
        left = max(origin, position(segment.start_seconds))
        right = position(segment.end_seconds)
        if endpoint is not None:
            right = min(endpoint, right)
        if right <= left or not segment.text.strip():
            continue
        source_ranges.append((left, right))
        local_left = source_to_local(time_map, left) if time_map is not None else left - origin
        local_right = source_to_local(time_map, right) if time_map is not None else right - origin
        first = max(0, round_half_up(local_left))
        last = max(0, round_half_up(local_right))
        if output_duration is not None:
            first = min(output_duration, first); last = min(output_duration, last)
        if last <= first:
            if collapsed is not None: collapsed.append(segment)
            continue
        language = segment.language if segment.language not in ('auto', '', None) else 'und'
        captions.append(Caption(uuid4().hex, start + first, last - first,
                                segment.text, language))
    ordered_ranges = sorted(source_ranges)
    if any(left[1] > right[0] for left, right in zip(ordered_ranges, ordered_ranges[1:])):
        raise ValueError('Recognition cues overlap in source timings; review the transcript before importing.')
    validate_captions(captions)
    return captions


class _WrappedCaptionNote(QLabel):
    """Keep a spanning form note tall enough after scroll/form width changes.

    QFormLayout may retain the old height-for-width result when switching tabs
    after a resize. An explicit content-height minimum follows the final width,
    and is lowered again when the form widens; it does not fix a pixel height.
    """
    def _fit_wrapped_height(self):
        if not self.wordWrap():
            return
        rect = self.contentsRect()
        margin = self.margin()
        width = max(1, rect.width() - 2 * margin)
        flags = int(self.alignment()) | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
        needed = self.fontMetrics().boundingRect(QRect(0, 0, width, 100000), flags, self.text()).height()
        margins = self.contentsMargins()
        height = needed + 2 * margin + margins.top() + margins.bottom()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_wrapped_height()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._fit_wrapped_height()

    def _refresh_translations(self, locale=None):
        super()._refresh_translations(locale)
        self._fit_wrapped_height()


class VoiceComboBox(QComboBox):
    """Documented IDs only; value/setValue retain old programmatic UI callers."""
    def value(self):
        return self.currentData()

    def setValue(self, voice_id):
        index = self.findData(voice_id)
        if index < 0:
            raise ValueError('Voice is not in the currently displayed verified catalog.')
        self.setCurrentIndex(index)


class CaptionPanelMixin:
    def build_caption_panel(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        panel = QWidget(); layout = QVBoxLayout(panel); scroll.setWidget(panel)
        note = QLabel(tr('Subtitles · independent timeline track\nClip edits do not move captions. Build preview after editing.')); note.setWordWrap(True); layout.addWidget(note)
        self.caption_list = QListWidget(); self.caption_list.setMinimumHeight(110)
        self.caption_list.currentItemChanged.connect(self.load_caption); layout.addWidget(self.caption_list)
        form = QFormLayout(); layout.addLayout(form)
        self.caption_start = QSpinBox(); self.caption_start.setRange(0, 100_000_000)
        self.caption_duration = QSpinBox(); self.caption_duration.setRange(1, 100_000_000); self.caption_duration.setValue(60)
        self.caption_text = QPlainTextEdit(); self.caption_text.setPlaceholderText(tr('Caption text')); self.caption_text.setMaximumHeight(90)
        self.caption_language = QLineEdit('und')
        form.addRow(tr('Start · frames'), self.caption_start); form.addRow(tr('Duration · frames'), self.caption_duration)
        form.addRow(tr('Text'), self.caption_text); form.addRow(tr('Language code'), self.caption_language)
        row = QHBoxLayout()
        for title, action in [(tr('Add'), self.add_caption), (tr('Update'), self.update_caption), (tr('Delete'), self.delete_caption)]:
            row.addWidget(self._button(title, action))
        layout.addLayout(row)
        row = QHBoxLayout(); row.addWidget(self._button(tr('Import SRT…'), self.import_srt)); row.addWidget(self._button(tr('Export SRT…'), self.export_srt)); layout.addLayout(row)
        style = QGroupBox(tr('Subtitle appearance')); sf = QFormLayout(style)
        self.caption_font = QLineEdit(); self.caption_size = QSpinBox(); self.caption_size.setRange(1, 1000)
        self.caption_margin = QSpinBox(); self.caption_margin.setRange(0, 16384)
        self.caption_safe_margin = QSpinBox(); self.caption_safe_margin.setRange(0, 20); self.caption_safe_margin.setValue(5); self.caption_safe_margin.setSuffix(' %')
        self.caption_max_lines = QSpinBox(); self.caption_max_lines.setRange(1, 20); self.caption_max_lines.setValue(6)
        self.caption_font_directory = QLineEdit(); self.caption_font_directory.setPlaceholderText(tr('Optional local font folder'))
        font_row = QHBoxLayout(); font_row.addWidget(self.caption_font_directory); font_row.addWidget(self._button(tr('Choose…'), self.choose_caption_font_directory))
        sf.addRow(tr('Font folder'), font_row)
        sf.addRow(tr('Font family'), self.caption_font); sf.addRow(tr('Size · output pixels'), self.caption_size); sf.addRow(tr('Bottom margin'), self.caption_margin)
        sf.addRow(tr('Safe horizontal margin'), self.caption_safe_margin)
        sf.addRow(tr('Maximum lines'), self.caption_max_lines)
        self.caption_wrap_note = _WrappedCaptionNote(tr('Wrapping affects preview/export only. Original caption/SRT text stays unchanged. Text that cannot fit requires smaller type or shorter cues.'))
        self.caption_wrap_note.setWordWrap(True); sf.addRow(self.caption_wrap_note)
        sf.addRow(self._button(tr('Apply appearance'), self.apply_caption_style)); layout.addWidget(style)
        asr = QGroupBox(tr('Transcribe locally')); af = QFormLayout(asr)
        note = QLabel(tr('Choose your existing whisper.cpp executable and model. No downloads or cloud services. Review recognition before export.')); note.setWordWrap(True); af.addRow(note)
        self.asr_runtime = QLineEdit(); self.asr_model = QLineEdit()
        for label, field in [(tr('Runtime'), self.asr_runtime), (tr('Model'), self.asr_model)]:
            row = QHBoxLayout(); row.addWidget(field); row.addWidget(self._button(tr('Choose…'), lambda checked=False, f=field: self.choose_asr_file(f))); af.addRow(label, row)
        self.asr_source = QComboBox(); self.asr_source.addItems([tr('Selected timeline clip · trim + speed + position'), tr('Selected media-bin source · start at zero')])
        self.asr_source.currentIndexChanged.connect(lambda *_: self.update_playback_controls())
        self.asr_language = QLineEdit('auto'); self.asr_language.setPlaceholderText('auto, en, es, zh…')
        af.addRow(tr('Source'), self.asr_source); af.addRow(tr('Language'), self.asr_language)
        self.asr_button = self._button(tr('Generate captions'), self.transcribe_selected, True); af.addRow(self.asr_button); layout.addWidget(asr)
        speech = QGroupBox(tr('Voice · subtitle reading')); tf = QFormLayout(speech)
        tf.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        note = _WrappedCaptionNote(tr('Choose and verify an existing local TTS pack. No downloads or cloud calls. Voice origins are upstream labels; every listed voice can receive English or Chinese text. Auditions never change the timeline.')); note.setWordWrap(True); note.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum); tf.addRow(note)
        from .voice_catalog import PACKS
        self.tts_settings = QSettings('Lumen Edit', 'Local voice browser')
        raw_favorites = self.tts_settings.value('favorites', '[]')
        try:
            saved = json.loads(raw_favorites) if isinstance(raw_favorites, str) else []
            allowed = {v.key for pack in PACKS.values() for v in pack.voices}
            self.tts_favorites = {key for key in saved if isinstance(key, str) and key in allowed} if isinstance(saved, list) else set()
        except (ValueError, TypeError): self.tts_favorites = set()
        self._tts_verified_pack = None
        self._tts_verify_request = 0
        self._tts_preview_task = None
        self.tts_preview_path = None
        self._tts_preview_request = 0
        self.tts_preview_player = QMediaPlayer(self)
        self.tts_preview_audio = QAudioOutput(self); self.tts_preview_audio.setVolume(.8)
        self.tts_preview_player.setAudioOutput(self.tts_preview_audio)
        self.tts_preview_player.errorOccurred.connect(self.tts_preview_error)
        self.tts_pack = QComboBox()
        self.tts_pack.setMinimumContentsLength(12)
        self.tts_pack.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for version, pack in PACKS.items(): self.tts_pack.addItem(tts_pack_label(pack), version)
        tf.addRow(tr('Pack version'), self.tts_pack)
        self.tts_pack_info = _WrappedCaptionNote(); self.tts_pack_info.setWordWrap(True); tf.addRow(self.tts_pack_info)
        self.tts_model = QLineEdit(); self.tts_model.setPlaceholderText(tr('Existing local TTS model-pack folder'))
        self.tts_choose_button = self._button(tr('Choose…'), self.choose_tts_model)
        row = QHBoxLayout(); row.addWidget(self.tts_model); row.addWidget(self.tts_choose_button); tf.addRow(tr('Local pack'), row)
        self.tts_verify_button = self._button(tr('Verify pack and runtime'), self.verify_tts_pack); tf.addRow(self.tts_verify_button)
        self.tts_pack_status = QLabel(tr('Select a folder and verify it before choosing a voice.')); self.tts_pack_status.setWordWrap(True); tf.addRow(self.tts_pack_status)
        self.tts_origin = QComboBox(); self.tts_language = QComboBox()
        self.tts_language.addItem(tr('English + Chinese text'), ''); self.tts_language.addItem(tr('English text'), 'en'); self.tts_language.addItem(tr('Chinese text'), 'zh')
        self.tts_language.setToolTip(tr('Text-language capability is pack-wide. This filter never hides a bilingual voice based on its origin.'))
        tf.addRow(tr('Voice origin'), self.tts_origin); tf.addRow(tr('Text language'), self.tts_language)
        self.tts_favorites_only = QCheckBox(tr('Favorites only')); tf.addRow(self.tts_favorites_only)
        self.tts_voice = VoiceComboBox(); self.tts_voice.setMinimumContentsLength(12)
        self.tts_voice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.tts_voice.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.tts_favorite_button = self._button(tr('☆ Favorite'), self.toggle_tts_favorite)
        row = QHBoxLayout(); row.addWidget(self.tts_voice, 1); row.addWidget(self.tts_favorite_button); tf.addRow(tr('Voice'), row)
        self.tts_voice_name = QLabel(); self.tts_voice_name.setWordWrap(True); tf.addRow(self.tts_voice_name)
        self.tts_speed = QDoubleSpinBox(); self.tts_speed.setRange(.5, 2.); self.tts_speed.setSingleStep(.05); self.tts_speed.setValue(1.); self.tts_speed.setSuffix(' ×')
        tf.addRow(tr('Synthesis speed'), self.tts_speed)
        self.tts_pause = QSpinBox(); self.tts_pause.setRange(20, 100); self.tts_pause.setSingleStep(10); self.tts_pause.setValue(20); self.tts_pause.setSuffix(' %')
        self.tts_pause.setToolTip(tr('Preserve 20–100% of engine-detected pauses. Higher values leave more breathing room; this does not add a pause at every punctuation mark or change pronunciation. 20% preserves older behavior.'))
        tf.addRow(tr('Pause preservation'), self.tts_pause)
        self.tts_preview_text = QPlainTextEdit('Hello. This is a local voice preview. 你好，这是本地语音试听。')
        self.tts_preview_text.setMaximumHeight(85); tf.addRow(tr('Audition text · full'), self.tts_preview_text)
        self.tts_copy_preview_button = self._button(tr('Use selected subtitle for audition'), self.copy_caption_to_audition); tf.addRow(self.tts_copy_preview_button)
        self.tts_preview_button = self._button(tr('Generate audition'), self.preview_tts_voice)
        self.tts_preview_play_button = self._button(tr('Replay'), self.play_tts_preview)
        self.tts_preview_stop_button = self._button(tr('Stop / cancel'), self.stop_tts_preview)
        tf.addRow(self.tts_preview_button)
        row = QHBoxLayout(); row.addWidget(self.tts_preview_play_button); row.addWidget(self.tts_preview_stop_button); tf.addRow(row)
        self.tts_preview_status = QLabel(tr('Auditions are temporary local WAVs, separate from saved takes.')); self.tts_preview_status.setWordWrap(True); tf.addRow(self.tts_preview_status)
        self.tts_track = QComboBox(); self.tts_track.addItem(tr('Choose audio lane…'), None); self.tts_track.addItem(tr('Music 1 · audio lane 2'), 2); self.tts_track.addItem(tr('Music 2 · audio lane 3'), 3)
        tf.addRow(tr('Destination lane'), self.tts_track)
        self.tts_button = self._button(tr('Generate voice to new WAV…'), self.speak_selected, True); tf.addRow(self.tts_button)
        self.tts_insert_button = self._button(tr('Insert saved full voice'), self.insert_pending_speech); self.tts_insert_button.setEnabled(False); tf.addRow(self.tts_insert_button)
        self.tts_status = QLabel(tr('Full voice is never shortened automatically.')); self.tts_status.setWordWrap(True); self.tts_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); tf.addRow(self.tts_status)
        self.tts_pack.currentIndexChanged.connect(self.tts_pack_changed)
        self.tts_model.textChanged.connect(self.invalidate_tts_pack)
        self.tts_origin.currentIndexChanged.connect(self.refresh_tts_voices)
        self.tts_language.currentIndexChanged.connect(self.refresh_tts_voices)
        self.tts_favorites_only.toggled.connect(self.refresh_tts_voices)
        self.tts_voice.currentIndexChanged.connect(self.tts_voice_changed)
        self.tts_speed.valueChanged.connect(self.invalidate_tts_audition)
        self.tts_pause.valueChanged.connect(self.invalidate_tts_audition)
        self.tts_preview_text.textChanged.connect(self.invalidate_tts_audition)
        self.tts_pack_changed()
        layout.addWidget(speech)
        from .ui_online_voices import open_online_voice_directory
        self.online_voice_directory_button = self._button(tr('Online voice directory…'),
            lambda: open_online_voice_directory(self))
        layout.addWidget(self.online_voice_directory_button)
        layout.addStretch(); return scroll

    def selected_caption(self):
        item = self.caption_list.currentItem()
        cid = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((c for c in self.project.captions if c.id == cid), None)

    def refresh_captions(self):
        selected = self.selected_caption()
        cid = selected.id if selected else None
        self.caption_list.blockSignals(True); self.caption_list.clear()
        for caption in sorted(self.project.captions, key=lambda c: c.start):
            item = QListWidgetItem(f'f{caption.start}–{caption.end}  ·  {caption.text.replace(chr(10), " / ")}')
            item.setData(Qt.ItemDataRole.UserRole, caption.id); self.caption_list.addItem(item)
            if caption.id == cid: self.caption_list.setCurrentItem(item)
        self.caption_list.blockSignals(False)
        style = self.project.caption_style
        self.caption_font_directory.setText(style.font_directory)
        self.caption_font.setText(style.font_family); self.caption_size.setValue(style.font_size); self.caption_margin.setValue(style.margin_bottom)
        self.caption_safe_margin.setValue(style.safe_margin_percent); self.caption_max_lines.setValue(style.max_lines)
        if cid: self.load_caption()
        else: self.timeline.caption_selection = ""

    def load_caption(self, *_):
        caption = self.selected_caption()
        if caption:
            self.caption_start.setValue(caption.start); self.caption_duration.setValue(caption.duration)
            self.caption_text.setPlainText(caption.text); self.caption_language.setText(caption.language)
            self.active_selection_type = "caption"; self.clear_clip_selection()
            self.refresh_inspector(); self.refresh_clip_effects()
            self.timeline.caption_selection = caption.id; self.timeline.update()
            self.update_playback_controls()

    def select_caption(self, cid):
        self.side_tabs.setCurrentIndex(1)
        for index in range(self.caption_list.count()):
            item = self.caption_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == cid:
                self.caption_list.setCurrentItem(item); self.load_caption(); break

    def add_caption(self):
        caption = self.edit(lambda: self.editor.add_caption(self.caption_start.value(), self.caption_duration.value(), self.caption_text.toPlainText(), self.caption_language.text().strip() or 'und'))
        if caption: self.select_caption(caption.id)

    def update_caption(self):
        caption = self.selected_caption()
        if caption:
            self.edit(lambda: self.editor.update_caption(caption.id, start=self.caption_start.value(), duration=self.caption_duration.value(), text=self.caption_text.toPlainText(), language=self.caption_language.text().strip() or 'und'))

    def delete_caption(self):
        caption = self.selected_caption()
        if caption: self.edit(lambda: self.editor.delete_caption(caption.id))

    def apply_caption_style(self):
        self.edit(lambda: self.editor.update_caption_style(
            font_family=self.caption_font.text().strip(), font_size=self.caption_size.value(),
            margin_bottom=self.caption_margin.value(), font_directory=self.caption_font_directory.text().strip(),
            safe_margin_percent=self.caption_safe_margin.value(), max_lines=self.caption_max_lines.value()))

    def choose_caption_font_directory(self):
        path = QFileDialog.getExistingDirectory(self, tr("Choose local caption fonts"))
        if path: self.caption_font_directory.setText(path)

    def import_caption_batch(self, captions):
        if not captions:
            QMessageBox.information(self, tr('No captions'), tr('No caption cues were found.')); return
        validate_captions(captions)
        replace = False
        try:
            validate_captions([*self.project.captions, *captions])
        except ValueError as exc:
            # Never infer that overlapping imported text can replace user work.
            if not self.project.captions: raise
            answer = QMessageBox.question(self, tr('Replace subtitle track?'), tr('{error}\n\nReplace ALL existing captions with this import? This is one undoable edit.', error=tr_error(exc)), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes: return
            replace = True
        self.edit(lambda: self.editor.import_captions(captions, replace=replace))

    def import_srt(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Import UTF-8 subtitles'), '', 'SubRip (*.srt)')
        if not path: return
        try: self.import_caption_batch(read_srt(path, self.project.fps))
        except Exception as exc: QMessageBox.warning(self, tr('Cannot import subtitles'), tr_error(exc))

    def export_srt(self):
        path, _ = QFileDialog.getSaveFileName(self, tr('Export subtitles to a new file'), '', 'SubRip (*.srt)')
        if not path: return
        try:
            write_srt(path, self.project.captions, self.project.fps)
            self.statusBar().showMessage(tr('Subtitles saved: {path}', path=path), 12000)
        except Exception as exc: QMessageBox.warning(self, tr('Cannot export subtitles'), tr_error(exc))

    def choose_asr_file(self, field):
        path, _ = QFileDialog.getOpenFileName(self, tr('Choose existing local file'))
        if path: field.setText(path)

    def transcribe_selected(self):
        if self.asr_job: return
        try:
            from .asr import ASRConfig, transcribe
            runtime = Path(self.asr_runtime.text().strip()).expanduser()
            model = Path(self.asr_model.text().strip()).expanduser()
            if not runtime.is_file() or not model.is_file():
                raise ValueError('Choose an existing whisper.cpp runtime and model file first. Nothing is downloaded automatically.')
            clip = copy.deepcopy(self.selected_clip()) if self.asr_source.currentIndex() == 0 else None
            if self.asr_source.currentIndex() == 0:
                if clip is None: raise ValueError('Select a timeline clip to transcribe.')
                media_id = clip.media_id
            else:
                item = self.bin.currentItem()
                if not item: raise ValueError('Select a source in the media bin.')
                media_id = item.data(Qt.ItemDataRole.UserRole)
            media = next(m for m in self.project.media if m.id == media_id)
            if not media.has_audio: raise ValueError('This source has no audio stream to transcribe.')
            config = ASRConfig(runtime_path=str(runtime), model_path=str(model), language=self.asr_language.text().strip() or 'auto', threads=4)
            source_duration = self.project.media_frames(media)
            if source_duration < 1:
                raise ValueError("Source is shorter than one full project frame.")
            revision = self.revision; fps = self.project.fps; source = media.path
            selection_context = (self.selected_clip_ids, self.selection_generation)
            settings_key = (self.asr_runtime.text(), self.asr_model.text(),
                            self.asr_language.text(), self.asr_source.currentIndex())
            def operation(cancel, progress):
                bounds = ({'source_start': clip.source_in / fps,
                           'source_end': clip.source_out / fps} if clip else {})
                segments = transcribe(source, config, progress=progress, cancel=cancel, **bounds)
                collapsed = []
                captions = segments_to_captions(segments, fps, time_map=clip.timing if clip else None,
                                               duration=clip.duration if clip else source_duration,
                                               start=clip.start if clip else 0, collapsed=collapsed)
                return captions, len(collapsed)
            def success(result):
                if self.revision != revision:
                    QMessageBox.warning(self, tr('Transcript is stale'), tr('The project changed during transcription. No captions were added. Run transcription again for the current edit.')); return
                if clip is not None and (self.active_selection_type != 'clip' or
                        selection_context != (self.selected_clip_ids, self.selection_generation)):
                    QMessageBox.warning(self, tr('Transcript is stale'), tr('The project changed during transcription. No captions were added. Run transcription again for the current edit.')); return
                current_settings = (self.asr_runtime.text(), self.asr_model.text(),
                                    self.asr_language.text(), self.asr_source.currentIndex())
                if settings_key != current_settings:
                    QMessageBox.warning(self, tr('Transcript is stale'), tr('Transcription settings changed. No captions were added. Run transcription again with the current settings.')); return
                captions, collapsed_count = result
                try:
                    if captions or not collapsed_count:
                        self.import_caption_batch(captions)
                    if collapsed_count:
                        QMessageBox.information(self, tr('Recognition timing'), tr('{count} recognition cue(s) were shorter than one output frame after speed mapping and were omitted. Review the generated captions.', count=collapsed_count))
                except Exception as exc: QMessageBox.warning(self, tr('Cannot add transcript'), tr_error(exc))
            self.start_job(tr('Transcribing locally…'), operation, success, 'asr')
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot transcribe'), tr_error(exc))

    def _tts_pack_key(self):
        return str(Path(self.tts_model.text().strip()).expanduser().resolve()), self.tts_pack.currentData()

    def invalidate_tts_pack(self, *_):
        self._tts_verified_pack = None
        self._tts_verify_request += 1
        self.close_tts_preview()
        self.tts_pack_status.setText(tr('Select a folder and verify it before choosing a voice.'))
        self.update_tts_controls()

    def tts_pack_changed(self, *_):
        from .voice_catalog import get_pack
        self.invalidate_tts_pack()
        pack = get_pack(self.tts_pack.currentData())
        self.tts_pack_info.setText(tts_pack_notice(pack))
        self.tts_pack_info._fit_wrapped_height()
        self.tts_voice.blockSignals(True); self.tts_voice.clear(); self.tts_voice.blockSignals(False)
        self.tts_origin.blockSignals(True); self.tts_origin.clear(); self.tts_origin.addItem(tr('All voice origins'), '')
        for origin in dict.fromkeys(v.origin for v in pack.voices): self.tts_origin.addItem(tr(origin), origin)
        self.tts_origin.blockSignals(False)
        self.refresh_tts_voices()

    def refresh_tts_voices(self, *_):
        from .voice_catalog import filter_voices
        previous = self.tts_voice.currentData()
        voices = filter_voices(self.tts_pack.currentData(), origin=self.tts_origin.currentData() or '',
                               text_language=self.tts_language.currentData() or '', favorites=self.tts_favorites,
                               favorites_only=self.tts_favorites_only.isChecked())
        self.tts_voice.blockSignals(True); self.tts_voice.clear()
        for voice in voices: self.tts_voice.addItem(tr('{favorite}{name} · {origin} · #{voice_id}', favorite='★ ' if voice.key in self.tts_favorites else '', name=voice.name, origin=tr(voice.origin), voice_id=voice.voice_id), voice.voice_id)
        index = self.tts_voice.findData(previous)
        self.tts_voice.setCurrentIndex(index if index >= 0 else (0 if voices else -1))
        self.tts_voice.blockSignals(False); self.tts_voice_changed()

    def tts_voice_changed(self, *_):
        from .voice_catalog import get_voice
        self.close_tts_preview()
        voice_id = self.tts_voice.currentData()
        if voice_id is None:
            self.tts_voice_name.setText(tr('No voices match these filters.'))
            self.tts_favorite_button.setText(tr('☆ Favorite'))
        else:
            voice = get_voice(self.tts_pack.currentData(), voice_id)
            self.tts_voice_name.setText(tr('{version} / {name} · accepts English and Chinese text', version=voice.pack_version, name=voice.name))
            self.tts_favorite_button.setText(tr('★ Unfavorite') if voice.key in self.tts_favorites else tr('☆ Favorite'))
        self.update_tts_controls()

    def toggle_tts_favorite(self):
        from .voice_catalog import get_voice
        if self.tts_voice.currentData() is None: return
        voice = get_voice(self.tts_pack.currentData(), self.tts_voice.currentData())
        if voice.key in self.tts_favorites: self.tts_favorites.remove(voice.key)
        else: self.tts_favorites.add(voice.key)
        self.tts_settings.setValue('favorites', json.dumps(sorted(self.tts_favorites)))
        self.tts_settings.sync(); self.refresh_tts_voices()

    def update_tts_controls(self):
        if not hasattr(self, 'tts_preview_button'): return
        idle = self.tts_job is None
        inference_idle = idle and not any(getattr(self, key, None) for key in ('assistant_job', 'semantic_job', 'asr_job'))
        valid = self._tts_verified_pack == self._tts_pack_key()
        selected = self.tts_voice.currentData() is not None
        for widget in (self.tts_model, self.tts_choose_button, self.tts_pack): widget.setEnabled(idle)
        self.tts_verify_button.setEnabled(idle and bool(self.tts_model.text().strip()))
        for widget in (self.tts_origin, self.tts_language, self.tts_favorites_only, self.tts_voice): widget.setEnabled(idle and valid)
        self.tts_favorite_button.setEnabled(idle and valid and selected)
        self.tts_preview_text.setEnabled(idle); self.tts_speed.setEnabled(idle); self.tts_pause.setEnabled(idle); self.tts_copy_preview_button.setEnabled(idle)
        self.tts_preview_button.setEnabled(inference_idle and valid and selected)
        self.tts_button.setEnabled(inference_idle and valid and selected)
        self.tts_preview_play_button.setEnabled(idle and self.tts_preview_path is not None)
        self.tts_preview_stop_button.setEnabled(self._tts_preview_task is not None or self.tts_preview_path is not None)

    def choose_tts_model(self):
        path = QFileDialog.getExistingDirectory(self, tr('Choose an existing local TTS pack'))
        if path: self.tts_model.setText(path)

    def verify_tts_pack(self):
        if self.tts_job: return
        from .tts import validate_pack
        from .voice_catalog import get_pack
        key = self._tts_pack_key()
        if not self.tts_model.text().strip(): return
        self.invalidate_tts_pack()
        request = self._tts_verify_request
        self.tts_pack_status.setText(tr('Verifying the complete local pack and optional runtime…'))
        def operation(cancel, progress):
            progress(0)
            result = validate_pack(*key, cancel=cancel)
            progress(1); return result
        def success(result):
            if request != self._tts_verify_request or result != self._tts_pack_key(): return
            self._tts_verified_pack = result
            self.tts_pack_status.setText(tr('Verified local {name} assets and optional runtime. No assets were downloaded. Listening quality is not certified.', name=get_pack(result[1]).display_name))
        job = self.start_job(tr('Verifying local voice pack…'), operation, success, 'tts')
        job.failed.connect(lambda _: self.tts_pack_status.setText(tr('Verification failed. Check the folder, version and installed runtime.')) if request == self._tts_verify_request else None)
        job.finished.connect(lambda: self.tts_pack_status.setText(tr('Verification canceled. Voices remain disabled.')) if request == self._tts_verify_request and job.cancel.is_set() else None)

    def _tts_config(self):
        from .tts import TTSConfig
        if not self.tts_model.text().strip() or not Path(self.tts_model.text().strip()).expanduser().is_dir():
            raise ValueError('Choose an existing local TTS model-pack folder. Nothing is downloaded.')
        if self._tts_verified_pack != self._tts_pack_key():
            raise ValueError('Verify the selected local pack and runtime first.')
        if self.tts_voice.currentData() is None: raise ValueError('Choose a documented voice in the browser.')
        return TTSConfig(model_dir=self._tts_pack_key()[0], voice_id=self.tts_voice.currentData(),
                         speed=self.tts_speed.value(), threads=2, pack_version=self.tts_pack.currentData(), silence_scale=self.tts_pause.value() / 100)

    def copy_caption_to_audition(self):
        self.tts_preview_text.setPlainText(self.caption_text.toPlainText())

    def invalidate_tts_audition(self, *_):
        self.close_tts_preview()
        self.tts_preview_status.setText(tr('Audition settings changed. Generate a new audition to hear them.'))
        self.update_tts_controls()

    def close_tts_preview(self):
        if not hasattr(self, 'tts_preview_player'): return
        self._tts_preview_request += 1
        if self._tts_preview_task is not None: self._tts_preview_task.cancel.set()
        self.tts_preview_player.stop(); self.tts_preview_player.setSource(QUrl())
        if self.tts_preview_path is not None:
            # Only this owned audition path is removed; user-saved takes are never touched.
            path = Path(self.tts_preview_path)
            if path.parent == self.cache / 'voice-auditions':
                try: path.unlink(missing_ok=True)
                except OSError: pass  # Some native players release their file handle asynchronously.
            self.tts_preview_path = None

    def stop_tts_preview(self):
        if self._tts_preview_task is not None: self._tts_preview_task.cancel.set()
        self.tts_preview_player.stop()
        self.tts_preview_status.setText(tr('Audition stopped. The timeline is unchanged.'))

    def tts_preview_error(self, *_):
        self.tts_preview_status.setText(tr('Local audition playback: {error}', error=tr_error(self.tts_preview_player.errorString())))

    def play_tts_preview(self):
        if hasattr(self, "sound_audition"): self.sound_audition.stop()
        if self.tts_preview_path is None or not Path(self.tts_preview_path).is_file(): return
        if not QMediaDevices.audioOutputs():
            self.tts_preview_status.setText(tr('Full audition is ready, but no local audio output device is available. Connect a device, then replay.'))
            return
        self.player.pause()
        self.tts_preview_player.setSource(QUrl.fromLocalFile(str(self.tts_preview_path)))
        self.tts_preview_player.setPosition(0); self.tts_preview_player.play()

    def review_tts_text(self, text):
        from .tts_text_review import reading_warnings
        from .voice_catalog import get_pack
        pack = get_pack(self.tts_pack.currentData())
        if len(text) > pack.max_text_chars:
            QMessageBox.warning(self, tr('Text exceeds this pack’s limit'),
                tr('{name} accepts at most {limit} characters per audition or saved take. Shorten the reading text explicitly or choose another pack. Nothing was truncated or generated.',
                   name=pack.display_name, limit=pack.max_text_chars))
            return False
        warnings = reading_warnings(text)
        if not warnings:
            return True
        message = '\n\n'.join(tr(warning) for warning in warnings)
        message += '\n\n' + tr('Continue with the original reading text? No text or subtitles will be changed automatically.')
        return QMessageBox.question(self, tr('Review speech pronunciation'), message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def preview_tts_voice(self):
        if self.tts_job: return
        try:
            from .tts import synthesize
            from .model import normalize_caption_text
            config = self._tts_config()
            text = normalize_caption_text(self.tts_preview_text.toPlainText())
            if not self.review_tts_text(text): return
            self.close_tts_preview()
            request = self._tts_preview_request
            directory = self.cache / 'voice-auditions'; directory.mkdir(exist_ok=True)
            target = directory / (uuid4().hex + '.wav')
            self.tts_preview_status.setText(tr('Generating the full audition locally…'))
            completed = []
            def operation(cancel, progress):
                try: return synthesize(text, config, str(target), progress=progress, cancel=cancel)
                except Exception:
                    target.unlink(missing_ok=True); raise
            def success(result):
                if request != self._tts_preview_request: return
                completed.append(True); self.tts_preview_path = target
                self.tts_preview_status.setText(tr('Full audition: {duration:.3f}s · {version} / voice {voice_id}. Timeline unchanged.', duration=result.duration, version=config.pack_version, voice_id=config.voice_id))
                self.play_tts_preview()
            def before_start(job):
                self._tts_preview_task = job
                def finished():
                    if self._tts_preview_task is job: self._tts_preview_task = None
                    if not completed:
                        target.unlink(missing_ok=True)
                        self.tts_preview_status.setText(tr('Audition canceled.') if job.cancel.is_set() else tr('Audition failed. Check the reported error and try again.'))
                    self.update_tts_controls()
                job.finished.connect(finished)
            self.start_job(tr('Auditioning local voice…'), operation, success, 'tts', before_start=before_start)
            self.update_tts_controls()
        except Exception as exc:
            self.tts_preview_status.setText(tr_error(exc))
            QMessageBox.warning(self, tr('Cannot audition voice'), tr_error(exc))

    def speak_selected(self):
        if self.tts_job: return
        try:
            from .tts import TTSConfig, synthesize
            from .model import Media, SpeechProvenance, normalize_caption_text
            caption = copy.deepcopy(self.selected_caption())
            if caption is None: raise ValueError('Select a subtitle to read first.')
            text = normalize_caption_text(self.caption_text.toPlainText())
            model = Path(self.tts_model.text().strip()).expanduser()
            if not self.tts_model.text().strip() or not model.is_dir():
                raise ValueError('Choose an existing local TTS model-pack folder. Nothing is downloaded.')
            track = self.tts_track.currentData()
            if track not in (2, 3): raise ValueError('Choose Music 1 or Music 2 as the destination audio lane.')
            config = self._tts_config()
            if not self.review_tts_text(text): return
            path, _ = QFileDialog.getSaveFileName(self, tr('Save generated voice to a NEW WAV'), '', tr('Wave audio (*.wav)'))
            if not path: return
            target = Path(path).expanduser().resolve()
            if target.suffix.lower() != '.wav': raise ValueError('Choose a new filename ending in .wav.')
            protected = {Path(m.path).resolve() for m in self.project.media}
            if self.project_path: protected.add(Path(self.project_path).resolve())
            if target in protected or target.exists(): raise ValueError('Voice output must be a new file. Existing files and project sources cannot be overwritten.')
            if target.is_relative_to(self.cache.resolve()): raise ValueError('Choose a durable media folder outside the temporary preview cache.')
            revision = self.revision
            editor, fps = self.editor, self.project.fps
            def operation(cancel, progress):
                result = synthesize(text, config, str(target), progress=progress, cancel=cancel)
                details = asdict(result.provenance) if is_dataclass(result.provenance) else dict(result.provenance)
                details.update(source_caption_id=caption.id, source_start=caption.start, source_duration=caption.duration)
                provenance = SpeechProvenance(**details)
                return Media(uuid4().hex, str(result.path), target.name, result.duration, has_video=False, has_audio=True, speech_provenance=provenance)
            def success(media):
                self.tts_status.setText(tr('Saved full voice: {path}', path=media.path))
                if self.revision != revision or self.editor is not editor or self.project.fps != fps:
                    QMessageBox.warning(self, tr('Voice saved; project changed'), tr('The project changed during synthesis. No audio was inserted. The full WAV remains saved at:\n{path}\nGenerate a new take for the current caption.', path=media.path)); return
                self.pending_speech = (media, caption, revision)
                self._pending_speech_context = _PendingSpeechContext(
                    editor, fps, self.pending_speech, _caption_snapshot(caption),
                    json.dumps(asdict(media), sort_keys=True))
                self.insert_pending_speech(track=track)
            self.start_job(tr('Generating local voice…'), operation, success, 'tts')
        except Exception as exc:
            QMessageBox.warning(self, tr('Cannot generate voice'), tr_error(exc))

    def _pending_speech_matches(self, pending):
        context = getattr(self, '_pending_speech_context', None)
        if (self.pending_speech is not pending or context is None or context.pending is not pending
                or self.editor is not context.editor or self.project.fps != context.fps):
            return False
        media, caption, _ = pending
        current = next((cue for cue in self.project.captions if cue.id == caption.id), None)
        return (current is not None and _caption_snapshot(current) == context.caption
                and _caption_snapshot(caption) == context.caption
                and json.dumps(asdict(media), sort_keys=True) == context.media)

    def _discard_pending_speech(self, pending):
        # Never discard a newer take created while a modal dialog was open.
        if self.pending_speech is pending:
            self.pending_speech = None
            self._pending_speech_context = None
        self.update_playback_controls()

    def _verify_pending_speech_file(self, media):
        from .tts import _hash
        try:
            if _hash(Path(media.path), None) == media.speech_provenance.output_sha256.lower():
                return True
        except (OSError, ValueError):
            pass
        QMessageBox.warning(self, tr('Cannot insert saved voice'), tr('The saved WAV is missing, changed or cannot be verified. No audio was inserted. Restore the original WAV and retry:\n{path}', path=media.path))
        self.update_playback_controls()
        return False

    def insert_pending_speech(self, checked=False, *, track=None):
        pending = self.pending_speech
        if pending is None: return
        media, caption, original_revision = pending
        if not self._pending_speech_matches(pending):
            QMessageBox.warning(self, tr('Saved voice is stale'), tr('The project session, frame rate or original caption changed. No audio was inserted. The WAV is preserved at:\n{path}\nGenerate a new take for the current caption.', path=media.path))
            self._discard_pending_speech(pending); return
        revision = self.revision
        rechecked = revision != original_revision
        track = self.tts_track.currentData() if track is None else track
        if track not in (2, 3):
            QMessageBox.warning(self, tr('Choose audio lane'), tr('Choose Music 1 or Music 2 before inserting the saved full voice.')); return
        if not self._verify_pending_speech_file(media): return
        if rechecked:
            self.tts_status.setText(tr('Saved voice rechecked after a timeline edit. The original caption and full WAV are unchanged.\n{path}', path=media.path))
        frames = self.project.media_frames(media)
        end = caption.start + frames
        conflicts = [c for c in self.project.clips if c.track == track and c.start < end and caption.start < c.end]
        summary = tr('Voice: {duration:.6f} seconds ({frames} timeline frames). Caption: {caption_frames} frames ({caption_seconds:.6f} seconds).', duration=media.duration, frames=frames, caption_frames=caption.duration, caption_seconds=caption.duration / float(self.project.fps))
        if conflicts:
            ranges = ', '.join(f'f{c.start}–f{c.end}' for c in conflicts)
            QMessageBox.warning(self, tr('Audio lane overlap'), tr('{summary}\nFull voice f{start}–f{end} overlaps lane {track}: {ranges}.\nNothing was inserted. Choose a free audio lane, or move/trim the blocking clip without changing this caption, then click Insert saved full voice. The full WAV remains saved.\n{path}', summary=summary, start=caption.start, end=end, track=track, ranges=ranges, path=media.path))
            self.update_playback_controls(); return
        if frames > caption.duration:
            answer = QMessageBox.question(self, tr('Keep the full voice?'), tr('{summary}\nThe voice extends {extra_frames} frames beyond this caption. Keep the FULL audio on lane {track}? The caption stays unchanged. No truncation or automatic speed change.\nNo cancels insertion; the WAV remains saved.\n{path}', summary=summary, extra_frames=frames - caption.duration, track=track, path=media.path), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                self.update_playback_controls(); return
        if self.revision != revision or not self._pending_speech_matches(pending):
            QMessageBox.warning(self, tr('Saved voice is stale'), tr('The project changed while confirming insertion. The WAV remains saved at:\n{path}', path=media.path))
            self._discard_pending_speech(pending); return
        # Modal consent can outlive filesystem changes without any project edit.
        if frames > caption.duration and not self._verify_pending_speech_file(media): return
        clip = self.edit(lambda: self.editor.add_speech_clip(media, caption.id, track, start=caption.start))
        if clip:
            self._discard_pending_speech(pending); self.select_clip(clip.id); self.inspector_tabs.setCurrentIndex(2)
            if rechecked:
                self.tts_status.setText(tr('Inserted rechecked full voice after a timeline edit ({duration:.6f}s): {path}', duration=media.duration, path=media.path))
            else:
                self.tts_status.setText(tr('Inserted full voice ({duration:.6f}s): {path}', duration=media.duration, path=media.path))
        self.update_playback_controls()

    def inspect_speech(self):
        clip = self.selected_clip()
        if not clip: return
        media = next(m for m in self.project.media if m.id == clip.media_id)
        if media.speech_provenance:
            QMessageBox.information(self, tr('Immutable voice provenance'), json.dumps(asdict(media.speech_provenance), ensure_ascii=False, indent=2))
