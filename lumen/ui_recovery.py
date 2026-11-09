"""Local project recovery UI. Recovery is a choice, never an automatic open."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import uuid

from PySide6.QtCore import QStandardPaths, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QLabel, QPushButton, QMessageBox, QPlainTextEdit

from .i18n import tr, tr_error, locale_events
from .model import Editor
from .project_recovery import RecoveryStore


def project_fingerprint(project):
    return hashlib.sha256(json.dumps(project.to_dict(), sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()


class RecoveryDialog(QDialog):
    """Nonmodal browser; closing it never discards any snapshots or current edits."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        self.help = QLabel(); self.help.setWordWrap(True); layout.addWidget(self.help)
        self.items = QListWidget(); layout.addWidget(self.items)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); self.details.setFixedHeight(70)
        layout.addWidget(self.details)
        self.notice = QLabel(); self.notice.setWordWrap(True); self.notice.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.notice)
        row = QHBoxLayout(); layout.addLayout(row)
        self.restore = QPushButton(); self.discard = QPushButton(); self.refresh_button = QPushButton(); self.close_button = QPushButton()
        for button in (self.restore, self.discard, self.refresh_button, self.close_button): row.addWidget(button)
        self.restore.clicked.connect(self.restore_selected)
        self.discard.clicked.connect(self.discard_selected)
        self.refresh_button.clicked.connect(self.reload)
        self.close_button.clicked.connect(self.close)
        self.items.currentRowChanged.connect(self.update_buttons)
        locale_events.changed.connect(self.retranslate)
        self.retranslate(); self.reload()

    def retranslate(self, *_):
        self.setWindowTitle(tr('Recover unsaved projects'))
        self.help.setText(tr('Snapshots stay on this computer. Restore opens an unsaved copy; originals and media are never replaced. Close keeps snapshots for later.'))
        self.restore.setText(tr('Restore copy'))
        self.discard.setText(tr('Discard snapshot'))
        self.refresh_button.setText(tr('Refresh'))
        self.close_button.setText(tr('Close'))

    def update_buttons(self, *_):
        row = self.items.currentRow()
        selected = 0 <= row < self.items.count()
        self.restore.setEnabled(0 <= row < len(getattr(self, 'entries', [])) and not self.owner.jobs)
        self.discard.setEnabled(selected)
        if 0 <= row < len(getattr(self, 'entries', [])):
            entry = self.entries[row]
            self.details.setPlainText((entry.original_path or tr('Untitled project')) + '\n' + entry.created_utc)
        else:
            self.details.clear()

    def reload(self):
        self.entries, warnings = self.owner.recovery_entries()
        self.items.clear()
        for entry in self.entries:
            label = entry.original_path or tr('Untitled project')
            timestamp = datetime.fromisoformat(entry.created_utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %z')
            self.items.addItem(f'{entry.project_name} · {timestamp}')
            self.items.item(self.items.count() - 1).setToolTip(label)
        self.unreadable = list(getattr(self.owner.recovery_store, 'unrecoverable_identities', ()))
        for identity in self.unreadable:
            self.items.addItem(tr('Unreadable recovery session · {identity}', identity=identity))
        self.notice.setText(tr('Some recovery files could not be read: {error}', error='; '.join(warnings)[:500]) if warnings else
                            (tr('Select a snapshot to restore or discard.') if self.entries else tr('No recoverable projects. Active sessions are not listed.')))
        self.update_buttons()

    def restore_selected(self):
        index = self.items.currentRow()
        if 0 <= index < len(self.entries) and self.owner.restore_recovery(self.entries[index]):
            self.close()

    def discard_selected(self):
        index = self.items.currentRow()
        if not 0 <= index < self.items.count(): return
        identity = self.entries[index].identity if index < len(self.entries) else self.unreadable[index - len(self.entries)]
        answer = QMessageBox.question(self, tr('Discard recovery snapshot?'),
            tr('Discard all recovery snapshots for this session? This does not delete the original project or media.'),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Discard: return
        try:
            self.owner.recovery_store.discard(identity)
        except Exception as exc:
            self.notice.setText(tr('Recovery needs attention: {error}', error=tr_error(exc))); return
        finally:
            self.owner.recovery_store.release(identity)
        self.reload(); self.owner.refresh_recovery_status()


class RecoveryMixin:
    def init_recovery(self, root=None):
        root = Path(root) if root is not None else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'project-recovery-v1'
        self.recovery_store = RecoveryStore(root)
        self.recovery_identity = str(uuid.uuid4())
        self.recovery_original_path = None
        self.recovery_last_fingerprint = None
        self.saved_project_fingerprint = project_fingerprint(self.project)
        self.recovery_dialog = None
        self.recovery_message = 'Local recovery · every 30 seconds'
        self.recovery_error = ''
        self.recovery_button = QPushButton(self)
        self.recovery_button.setMaximumWidth(380)
        self.recovery_button.clicked.connect(self.open_recovery)
        self.statusBar().addPermanentWidget(self.recovery_button)
        self.recovery_timer = QTimer(self)
        self.recovery_timer.setInterval(30_000)
        self.recovery_timer.timeout.connect(self.autosave_recovery)
        self.recovery_timer.start()
        locale_events.changed.connect(self.refresh_recovery_status)
        self.refresh_recovery_status()

    def recovery_entries(self):
        try:
            return self.recovery_store.list_entries()
        except Exception as exc:
            return [], [tr_error(exc)]

    def refresh_recovery_status(self, *_):
        entries, warnings = self.recovery_entries()
        error = self.recovery_error or '; '.join(warnings)
        if error:
            text = tr('Recovery needs attention')
            detail = tr('Recovery needs attention: {error}', error=error)
        elif entries:
            text = tr('Recovery · {count} available', count=len(entries)); detail = text
        else:
            text = tr(self.recovery_message); detail = text
        self.recovery_button.setText(text)
        self.recovery_button.setToolTip(detail + '\n' + tr('Open recovery manager. Snapshots do not replace Save.'))

    def open_recovery(self):
        if self.recovery_dialog is None:
            self.recovery_dialog = RecoveryDialog(self)
        self.recovery_dialog.reload()
        self.recovery_dialog.show(); self.recovery_dialog.raise_(); self.recovery_dialog.activateWindow()

    def autosave_recovery(self):
        if self.closing: return
        try:
            fingerprint = project_fingerprint(self.project)
            # Compare content, not UI revision: selection, playback and undo to
            # the saved baseline must not produce duplicate/empty snapshots.
            if fingerprint == self.saved_project_fingerprint:
                if self.recovery_last_fingerprint is not None:
                    self.recovery_store.discard(self.recovery_identity)
                    self.recovery_last_fingerprint = None
                    self.recovery_message = 'Local recovery · every 30 seconds'
                    self.recovery_error = ''
                    self.refresh_recovery_status()
                return
            if not self.dirty or fingerprint == self.recovery_last_fingerprint: return
            self.recovery_store.write(self.project, self.recovery_identity,
                                      self.project_path or self.recovery_original_path)
            self.recovery_last_fingerprint = fingerprint
            self.recovery_message = 'Recovery snapshot saved locally'
            self.recovery_error = ''
        except Exception as exc:
            self.recovery_error = tr_error(exc)
        self.refresh_recovery_status()

    def finish_recovery_session(self):
        """Called only after an actual save, accepted replacement or close."""
        try:
            self.recovery_store.discard(self.recovery_identity)
            self.recovery_error = ''
        except Exception as exc:
            self.recovery_error = tr_error(exc)
        finally:
            self.recovery_store.release(self.recovery_identity)
        self.recovery_last_fingerprint = None

    def reset_recovery_session(self):
        self.recovery_identity = str(uuid.uuid4())
        self.recovery_original_path = self.project_path
        self.recovery_last_fingerprint = None
        self.saved_project_fingerprint = project_fingerprint(self.project)
        self.recovery_message = 'Local recovery · every 30 seconds'
        self.refresh_recovery_status()

    def recovery_saved(self):
        self.finish_recovery_session()
        self.reset_recovery_session()

    def restore_recovery(self, entry):
        if entry.identity == self.recovery_identity:
            return False  # Ignore a queued second activation of the same row.
        if self.jobs:
            self.statusBar().showMessage(tr('Finish or cancel active work before restoring.'), 8000); return False
        # Load/validate and claim before asking to replace current work. A stale
        # selection or corrupt file must never throw away the current session.
        try:
            project = self.recovery_store.load(entry)
        except Exception as exc:
            self.recovery_error = tr_error(exc); self.refresh_recovery_status(); return False
        if not self.confirm_discard():
            self.recovery_store.release(entry.identity); return False
        self.finish_recovery_session()
        self.invalidate_preview_frame(new_source=True, clear_reference=True)
        self.sound_audition.stop(); self.player.stop()
        from PySide6.QtCore import QUrl
        self.player.setSource(QUrl())
        self.editor = Editor(project)  # No undo history crosses project boundary.
        self.project_path = None       # Save must ask for a destination.
        self.clear_clip_selection(); self.active_selection_type = 'clip'
        self.dirty = True; self.revision += 1; self.preview_revision = -1
        self.timeline.playhead = 0; self.preview_paths.clear()
        self.recovery_identity = entry.identity
        self.recovery_original_path = entry.original_path
        self.saved_project_fingerprint = None
        self.recovery_last_fingerprint = project_fingerprint(project)
        self.recovery_message = 'Recovered copy · Save As to keep it'
        self.refresh(); self.refresh_recovery_status()
        self.preview_label.setText(tr('Recovered copy · Build preview, then Save As to keep your edits.'))
        if project.missing_media():
            self.statusBar().showMessage(tr('Some recovered media is missing. Use Relink missing media.'), 15000)
        return True
