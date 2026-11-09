"""Offline sound browser; auditions are isolated from timeline playback/export."""
from __future__ import annotations
from pathlib import Path
from uuid import uuid4
from PySide6.QtCore import QObject, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QMediaDevices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QMessageBox, QVBoxLayout
from .i18n import get_locale, locale_events, tr, tr_error
from .i18n_widgets import (QComboBox, QFormLayout, QLabel, QLineEdit, QListWidgetItem,
                           QScrollArea, QSlider, QSpinBox, QWidget)
from .model import Media, SoundProvenance
from .sound_library import builtin_sounds, import_sound, local_sounds, search_sounds, verify_sound
from .sound_sources import SOURCE_FIELDS

CATEGORIES = [('all', 'All sounds'), ('ui', 'UI sounds'), ('transitions', 'Transitions'),
              ('impacts', 'Impacts'), ('notifications', 'Notifications'),
              ('atmosphere', 'Atmosphere'), ('foley', 'Foley'), ('local', 'My imports')]


class SoundAudition(QObject):
    """A fresh player per request prevents late events resurrecting old playback."""
    changed = Signal(str)
    failed = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.player = None
        self.audio = QAudioOutput(self)
        self.sound_id = ''
        self.generation = 0
        self.volume = .35

    def stop(self):
        self.generation += 1
        player = self.player
        self.player = None
        self.sound_id = ''
        if player:
            player.stop(); player.setAudioOutput(None); player.setSource(QUrl()); player.deleteLater()
        self.changed.emit('')

    def set_volume(self, value):
        self.volume = max(0., min(1., value))
        if self.audio:
            self.audio.setVolume(self.volume)

    def play(self, entry):
        self.stop()
        verify_sound(entry)
        generation = self.generation
        self.player = player = QMediaPlayer(self)
        self.audio.setVolume(self.volume); player.setAudioOutput(self.audio)
        self.sound_id = entry.id
        def status(value):
            if generation != self.generation or player is not self.player:
                return
            if value == QMediaPlayer.MediaStatus.EndOfMedia:
                self.stop()
        def error(*_):
            if generation == self.generation and player is self.player:
                message = player.errorString()
                self.stop(); self.failed.emit(message)
        player.mediaStatusChanged.connect(status)
        player.errorOccurred.connect(error)
        player.setSource(QUrl.fromLocalFile(str(entry.path)))
        self.changed.emit(entry.id)
        player.play()


class SoundsPanelMixin:
    def build_sounds_panel(self):
        self.sound_job = None
        if self.sound_library_root is None:
            self.sound_library_root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'sound-library'
        self.sound_audition = SoundAudition(self)
        self._sound_was_playing = False
        self.sound_audition.changed.connect(self._sound_audition_changed)
        self.sound_audition.failed.connect(lambda error: self.show_sound_status(tr('Audition failed: {error}', error=error)))
        wrapper = QWidget(); wrapper.setObjectName('soundsPanel'); outer = QVBoxLayout(wrapper); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); self.sounds_scroll = scroll; outer.addWidget(scroll, 1)
        panel = QWidget(); scroll.setWidget(panel); layout = QVBoxLayout(panel)
        note = QLabel(tr('Original + external CC0 sounds · offline'))
        note.setToolTip(tr('Original synthesis and curated external CC0 sounds. Browse, audition, then insert a full sound on a music track.'))
        note.setWordWrap(True); layout.addWidget(note)
        self.sound_search = QLineEdit(); self.sound_search.setPlaceholderText(tr('Search sounds in Chinese or English…'))
        self.sound_search.setAccessibleName(tr('Search sounds'))
        self.sound_category = QComboBox()
        for key, label in CATEGORIES:
            self.sound_category.addItem(tr(label), key)
        search_row = QHBoxLayout(); search_row.addWidget(self.sound_search, 3); search_row.addWidget(self.sound_category, 2); layout.addLayout(search_row)
        self.sound_list = QListWidget(); self.sound_list.setMinimumHeight(90); self.sound_list.setMaximumHeight(160)
        layout.addWidget(self.sound_list, 1)
        self.sound_details = QLabel(); self.sound_details.setTextFormat(Qt.TextFormat.PlainText)
        self.sound_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sound_details.setWordWrap(True); layout.addWidget(self.sound_details)
        row = QHBoxLayout()
        self.sound_preview_button = self._button(tr('Audition'), self.preview_sound)
        self.sound_stop_button = self._button(tr('Stop audition'), self.sound_audition.stop)
        self.sound_insert_button = self._button(tr('Insert sound'), self.insert_sound, True)
        self.sound_insert_button.setToolTip(tr('No ripple or overwrite. Overlaps on the same track are rejected. Save the project and keep its .assets folder beside it to carry inserted sounds.'))
        row.addWidget(self.sound_preview_button); row.addWidget(self.sound_stop_button); row.addWidget(self.sound_insert_button)
        form = QFormLayout()
        self.sound_volume = QSlider(Qt.Orientation.Horizontal); self.sound_volume.setRange(0, 100); self.sound_volume.setValue(35)
        self.sound_volume.setToolTip(tr('Audition volume only. Timeline gain is unchanged.'))
        self.sound_volume.valueChanged.connect(lambda value: self.sound_audition.set_volume(value / 100))
        form.addRow(tr('Audition volume'), self.sound_volume)
        self.sound_track = QComboBox(); self.sound_track.addItem(tr('Music 1'), 2); self.sound_track.addItem(tr('Music 2'), 3)
        self.sound_track.setAccessibleName(tr('Target track')); self.sound_track.setToolTip(tr('Target track'))
        self.sound_position = QComboBox()
        self.sound_position.addItem(tr('At playhead'), 'playhead')
        self.sound_position.addItem(tr('Append to selected track'), 'append')
        self.sound_position.addItem(tr('At exact frame'), 'frame')
        self.sound_position.setAccessibleName(tr('Insert position')); self.sound_position.setToolTip(tr('Insert position'))
        target_row = QHBoxLayout(); target_row.addWidget(self.sound_track); target_row.addWidget(self.sound_position)
        outer.addLayout(target_row)
        self.sound_start = QSpinBox(); self.sound_start.setRange(0, 100_000_000); self.sound_start.setEnabled(False)
        self.sound_frame_row = QWidget(); frame_layout = QHBoxLayout(self.sound_frame_row); frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.addWidget(QLabel(tr('Start · project frame'))); frame_layout.addWidget(self.sound_start); self.sound_frame_row.hide()
        self.sound_position.currentIndexChanged.connect(lambda _: (self.sound_start.setEnabled(self.sound_position.currentData() == 'frame'), self.sound_frame_row.setVisible(self.sound_position.currentData() == 'frame')))
        outer.addWidget(self.sound_frame_row)
        outer.addLayout(row)
        layout.addLayout(form)
        self.sound_import_button = self._button(tr('Import local sound…'), self.import_sound_dialog); outer.addWidget(self.sound_import_button)
        self.sound_import_button.setToolTip(tr('Local audio only · up to 60 seconds / 64 MiB. Import makes a safe WAV copy; originals stay untouched. Only import audio you have rights to use.'))
        note = QLabel(tr('Local only · 60 s / 64 MiB max. Use audio you have rights to.'))
        note.setWordWrap(True); layout.addWidget(note)
        self.sound_status = QLabel(); self.sound_status.setWordWrap(True); self.sound_status.hide(); outer.addWidget(self.sound_status)
        self.sound_list.currentItemChanged.connect(self.select_sound)
        self.sound_search.textChanged.connect(self.filter_sounds)
        self.sound_category.currentIndexChanged.connect(self.filter_sounds)
        self.load_sound_catalog()
        locale_events.changed.connect(self.filter_sounds)
        return wrapper

    def show_sound_status(self, message):
        self.sound_status.setText(message)
        self.sound_status.setVisible(bool(message))

    def _sound_audition_changed(self, sound_id):
        if self._sound_was_playing and not sound_id:
            self.show_sound_status(tr('Audition stopped. The timeline is unchanged.'))
        self._sound_was_playing = bool(sound_id)
        self.update_sound_controls()

    def load_sound_catalog(self):
        self.sound_entries = []
        errors = []
        for load in (builtin_sounds, lambda: local_sounds(self.sound_library_root)):
            try:
                self.sound_entries.extend(load())
            except (ValueError, OSError) as exc:
                errors.append(tr_error(exc))
        if errors:
            self.show_sound_status(tr('Sound library unavailable: {error}', error='; '.join(errors)))
        self.filter_sounds()

    def selected_sound(self):
        item = self.sound_list.currentItem()
        sid = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((entry for entry in self.sound_entries if entry.id == sid), None)

    def filter_sounds(self, *_):
        old = self.selected_sound()
        self.sound_audition.stop()
        entries = self.sound_entries
        category = self.sound_category.currentData()
        if category == 'local':
            entries = [entry for entry in entries if entry.origin == 'user-imported']
            category = 'all'
        entries = search_sounds(entries, self.sound_search.text(), category)
        self.sound_list.blockSignals(True); self.sound_list.clear()
        for entry in entries:
            name = entry.name_zh if get_locale() == 'zh-CN' else entry.name_en
            item = QListWidgetItem(f'{name}  ·  {entry.duration:.2f}s')
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setToolTip(' / '.join(entry.tags))
            self.sound_list.addItem(item)
            if old and entry.id == old.id:
                self.sound_list.setCurrentItem(item)
        if self.sound_list.currentRow() < 0 and entries:
            self.sound_list.setCurrentRow(0)
        self.sound_list.blockSignals(False)
        self.select_sound()

    def select_sound(self, *_):
        self.sound_audition.stop()
        entry = self.selected_sound()
        if entry:
            if entry.origin == 'external-cc0':
                rights = tr('External sound · {license}\nAuthor: {author}\nSource: {source_name}',
                            license=entry.license, author=entry.author, source_name=entry.source_name)
                self.sound_details.setToolTip(tr('Source: {source_url}\nLicense: {license_url}\nOriginal file: {source_file}',
                    source_url=entry.source_url, license_url=entry.license_url, source_file=entry.source_file))
            else:
                rights = tr('Original procedural sound · CC0') if entry.origin == 'procedural' else tr('Your local import · rights not verified')
                self.sound_details.setToolTip('')
            self.sound_details.setText(tr('{count} sounds · {duration:.2f}s · {rate} Hz\n{rights}', count=self.sound_list.count(), duration=entry.duration, rate=entry.sample_rate, rights=rights))
        else:
            self.sound_details.setToolTip('')
            self.sound_details.setText(tr('No sounds match. Try another keyword or category.'))
        self.update_sound_controls()

    def update_sound_controls(self, *_):
        if not hasattr(self, 'sound_preview_button'):
            return
        present = self.selected_sound() is not None
        self.sound_preview_button.setEnabled(present)
        self.sound_stop_button.setEnabled(bool(self.sound_audition.sound_id))
        self.sound_insert_button.setEnabled(present)
        self.sound_import_button.setEnabled(self.sound_job is None)

    def preview_sound(self):
        entry = self.selected_sound()
        if entry is None:
            return
        self.sound_audition.stop()
        if not QMediaDevices.audioOutputs():
            self.show_sound_status(tr('No audio output device is available. Connect one to audition. Insertion and export still work.'))
            return
        self.player.pause()
        if hasattr(self, 'close_tts_preview'):
            self.close_tts_preview()
        try:
            self.sound_audition.play(entry)
            self.show_sound_status(tr('Audition only · not part of the timeline.'))
        except (ValueError, OSError) as exc:
            self.show_sound_status(tr('Audition failed: {error}', error=tr_error(exc)))

    def insert_sound(self):
        entry = self.selected_sound()
        if entry is None:
            return
        self.sound_audition.stop()
        try:
            verify_sound(entry)
            track = self.sound_track.currentData()
            mode = self.sound_position.currentData()
            start = (max((clip.end for clip in self.project.clips if clip.track == track), default=0) if mode == 'append'
                     else self.sound_start.value() if mode == 'frame' else self.timeline.playhead)
            name = entry.name_zh if get_locale() == 'zh-CN' else entry.name_en
            media = Media(uuid4().hex, str(entry.path), name, entry.duration, has_video=False, has_audio=True,
                          sound_provenance=SoundProvenance(entry.id, entry.origin, entry.license,
                              entry.source_sha256, entry.sha256, entry.sample_rate, entry.sample_count,
                              **{name: getattr(entry, name) for name in SOURCE_FIELDS}))
            clip = self.editor.add_sound_clip(media, track, start)
            self.changed(); self.select_clip(clip.id)
            self.show_sound_status(tr('Inserted at frame {frame}. Build preview to hear the mix.', frame=start))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, tr('Cannot insert sound'), tr_error(exc))

    def import_sound_dialog(self):
        if self.sound_job:
            return
        path, _ = QFileDialog.getOpenFileName(self, tr('Import local sound'), '', tr('Audio (*.wav *.flac *.mp3 *.m4a *.aac *.ogg *.opus *.aif *.aiff)'))
        if path:
            self.import_sound_path(path)

    def import_sound_path(self, path):
        if self.sound_job:
            return
        self.sound_audition.stop()
        root = self.sound_library_root
        def operation(cancel, progress):
            entry = import_sound(path, root, cancel)
            progress(1); return entry
        def success(entry):
            self.load_sound_catalog()
            self.sound_search.clear(); self.sound_category.setCurrentIndex(self.sound_category.findData('local'))
            for row in range(self.sound_list.count()):
                if self.sound_list.item(row).data(Qt.ItemDataRole.UserRole) == entry.id:
                    self.sound_list.setCurrentRow(row); break
            self.show_sound_status(tr('Imported a local WAV copy. Select Insert sound to add it to this project.'))
        self.start_job(tr('Importing sound…'), operation, success, 'sound')
