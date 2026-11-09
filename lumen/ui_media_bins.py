"""In-memory project media organization and session-bound atomic imports."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem, QInputDialog, QMessageBox
from .i18n import tr, tr_error
from .i18n_widgets import QWidget, QLineEdit, QComboBox, QListWidget, QListWidgetItem, QLabel
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout

# Model IDs are strings; integer virtual scopes cannot collide with user IDs.
ALL_MEDIA = -1
UNFILED_MEDIA = -2
ROLE = Qt.ItemDataRole.UserRole


def local_import_path(path):
    """Reject URLs and network shares before any filesystem operation."""
    raw = os.fspath(path)
    if not isinstance(raw, str) or raw.startswith(('\\\\', '//')):
        raise ValueError(tr('Import supports local files only, not URLs or network shares.'))
    scheme = re.match(r'^([a-zA-Z][a-zA-Z0-9+.-]*):', raw)
    if scheme and not (len(scheme.group(1)) == 1 and len(raw) > 2 and raw[2] in '/\\'):
        raise ValueError(tr('Import supports local files only, not URLs or network shares.'))
    return Path(raw).expanduser()


def visible_media(project, scope=ALL_MEDIA, query='', kind='all', usage='all'):
    """Pure metadata query: never touch disk, decode, or mutate the project."""
    used = {c.media_id for c in project.clips}
    needle = query.casefold().strip()
    membership = project.media_bin_ids
    return [m for m in project.media
            if (scope == ALL_MEDIA or
                (scope == UNFILED_MEDIA and m.id not in membership) or
                membership.get(m.id) == scope)
            and (not needle or needle in m.name.casefold() or needle in m.path.casefold())
            and (kind == 'all' or (kind == 'video' and m.has_video) or
                 (kind == 'audio' and m.has_audio and not m.has_video))
            and (usage == 'all' or (usage == 'used') == (m.id in used))]


class MediaBinsMixin:
    def build_media_bins(self, layout):
        self.media_browser = QWidget()
        box = QVBoxLayout(self.media_browser); box.setContentsMargins(0, 0, 0, 0)
        self.media_scope = ALL_MEDIA
        self._media_browser_editor = (self.editor, self.editor.session_id)
        self._media_import_generation = 0
        self._media_status_job = None
        self._media_status_pending = None
        self._media_status_cache = {}
        self._media_status_waiting = set()
        self._media_status_uncertain = set()
        self._media_status_closed = False
        self._media_status_close_pending = False
        self._media_status_delete_pending = False
        self._media_status_close_timer = QTimer(self)
        self._media_status_close_timer.setSingleShot(True)
        self._media_status_close_timer.timeout.connect(self._finish_media_status_close)
        self.media_tree = QTreeWidget(); self.media_tree.setHeaderHidden(True)
        self.media_tree.setAccessibleName(tr('Media bins'))
        self.media_tree.setMinimumWidth(0); self.media_tree.setMaximumHeight(125)
        self.media_tree.currentItemChanged.connect(self._media_scope_changed)
        # Tree activation navigates only; it never appends a timeline clip.
        self.media_tree.itemDoubleClicked.connect(lambda item, _: self.media_tree.setCurrentItem(item))
        box.addWidget(self.media_tree)
        row = QVBoxLayout()
        for label, callback in [('New bin', self.create_media_bin_dialog), ('Rename bin', self.rename_media_bin_dialog)]:
            row.addWidget(self._button(tr(label), callback))
        box.addLayout(row)
        self.media_search = QLineEdit(); self.media_search.setPlaceholderText(tr('Search name or path'))
        self.media_search.setAccessibleName(tr('Search name or path')); box.addWidget(self.media_search)
        row = QVBoxLayout()
        self.media_type_filter = QComboBox(); self.media_usage_filter = QComboBox()
        for text, value in [('All types', 'all'), ('Video', 'video'), ('Audio only', 'audio')]:
            self.media_type_filter.addItem(tr(text), value)
        for text, value in [('All usage', 'all'), ('Used', 'used'), ('Unused', 'unused')]:
            self.media_usage_filter.addItem(tr(text), value)
        self.media_type_filter.setAccessibleName(tr('Media type')); self.media_usage_filter.setAccessibleName(tr('Media usage'))
        row.addWidget(self.media_type_filter); row.addWidget(self.media_usage_filter); box.addLayout(row)
        self.bin = QListWidget(); self.bin.setMinimumWidth(0)
        self.bin.setAccessibleName(tr('Media results'))
        self.bin.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.bin.itemDoubleClicked.connect(lambda _: self.add_selected_media())
        box.addWidget(self.bin, 1)
        self.media_count = QLabel(''); self.media_count.setWordWrap(True); box.addWidget(self.media_count)
        # Compact action menu works at narrow widths and avoids a tall button stack.
        self.media_actions = QComboBox(); self.media_actions.setAccessibleName(tr('Organize media'))
        for text in ['Organize media', 'Move selected media…', 'Move bin…', 'Delete empty bin', 'Remove selected unused media']:
            self.media_actions.addItem(tr(text))
        self.media_actions.activated.connect(self._media_action); box.addWidget(self.media_actions)
        self.media_actions.setToolTip(tr('Project organization only. Original files are never moved or deleted.'))
        layout.addWidget(self.media_browser, 1)
        self.media_search.textChanged.connect(self.refresh_media_results)
        self.media_type_filter.currentIndexChanged.connect(self.refresh_media_results)
        self.media_usage_filter.currentIndexChanged.connect(self.refresh_media_results)

    def selected_media_ids(self):
        valid = {m.id for m in visible_media(self.project, self.media_scope, self.media_search.text(),
                    self.media_type_filter.currentData(), self.media_usage_filter.currentData())}
        return [self.bin.item(i).data(ROLE) for i in range(self.bin.count())
                if self.bin.item(i).isSelected() and not self.bin.item(i).isHidden()
                and self.bin.item(i).data(ROLE) in valid]

    def refresh_media_browser(self):
        if self._media_browser_editor != (self.editor, self.editor.session_id):
            self._media_browser_editor = (self.editor, self.editor.session_id)
            self.media_scope = ALL_MEDIA
            self.bin.clear()
        selected = self.media_scope
        expanded = set()
        def remember(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.isExpanded(): expanded.add(child.data(0, ROLE))
                remember(child)
        remember(self.media_tree.invisibleRootItem())
        self.media_tree.blockSignals(True); self.media_tree.clear()
        nodes = {}
        for label, value in [('All media', ALL_MEDIA), ('Unfiled media', UNFILED_MEDIA)]:
            item = QTreeWidgetItem([tr(label)]); item.setData(0, ROLE, value)
            self.media_tree.addTopLevelItem(item); nodes[value] = item
        for entry in self.project.media_bins:
            item = QTreeWidgetItem([entry.name]); item.setData(0, ROLE, entry.id); nodes[entry.id] = item
        for entry in self.project.media_bins:
            item = nodes[entry.id]
            if entry.parent_id in nodes: nodes[entry.parent_id].addChild(item)
            else: self.media_tree.addTopLevelItem(item)
            item.setExpanded(entry.id in expanded)
        self.media_scope = selected if selected in nodes else ALL_MEDIA
        self.media_tree.setCurrentItem(nodes[self.media_scope])
        self.media_tree.blockSignals(False)
        self.request_media_status_scan()
        self.refresh_media_results()

    def retranslate_media_browser(self):
        # Language changes preserve Qt item identity, focus and staged selection.
        self.media_tree.setAccessibleName(tr('Media bins'))
        for index, label in ((0, 'All media'), (1, 'Unfiled media')):
            item = self.media_tree.topLevelItem(index)
            if item is not None: item.setText(0, tr(label))
        media = {m.id: m for m in self.project.media}
        for index in range(self.bin.count()):
            item = self.bin.item(index)
            source = media.get(item.data(ROLE))
            if source is not None: item.setText(self._media_result_text(source))

    def _media_result_text(self, media):
        details = tr('{duration:.2f}s · {width}×{height}', duration=media.duration, width=media.width, height=media.height) if media.has_video else tr('Audio · {duration:.2f}s', duration=media.duration)
        key = (self.editor, self.editor.session_id, media.id, media.path)
        missing = self._media_status_cache.get(key) is False
        checking = key in self._media_status_waiting
        unknown = key in self._media_status_uncertain or (key in self._media_status_cache and self._media_status_cache[key] is None)
        prefix = (str(tr('MISSING · ')) if missing else '') + (str(tr('Checking… · ')) if checking else str(tr('Unavailable status · ')) if unknown else '')
        saved_count = sum(entry.media_id == media.id for entry in self.project.source_ranges)
        if saved_count:
            details += ' · ' + tr('Saved ranges: {count}', count=saved_count)
        return f'{prefix}{media.name}\n{details}'

    def request_media_status_scan(self):
        """Coalesce actual browser refreshes; filter keystrokes never call this."""
        if self._media_status_closed or self.closing: return
        snapshot = (self.editor, self.editor.session_id,
                    tuple((m.id, m.path) for m in self.project.media))
        valid = {(snapshot[0], snapshot[1], mid, path) for mid, path in snapshot[2]}
        self._media_status_cache = {key: value for key, value in self._media_status_cache.items() if key in valid}
        self._media_status_uncertain.intersection_update(valid)
        active = self._media_status_job
        if active is not None:
            if snapshot == active.media_snapshot and not active.cancel.is_set(): return
            self._media_status_waiting = valid
            self._media_status_pending = snapshot
            active.cancel.set()
            return
        self._media_status_waiting = valid
        self._media_status_pending = None
        if snapshot[2]: self._start_media_status_batch(snapshot, snapshot[2])

    def _start_media_status_batch(self, snapshot, remaining):
        from .ui import Job
        batch, tail = remaining[:128], remaining[128:]
        def operation(cancel, progress):
            results = []
            for mid, path in batch:
                if cancel.is_set(): break
                try:
                    # Reject network strings before stat. Errors remain unknown,
                    # not a confident missing-source diagnosis.
                    exists = local_import_path(path).exists()
                except (OSError, RuntimeError, ValueError, TypeError):
                    exists = None
                results.append((mid, path, exists))
            return results
        job = Job(operation, self)
        job.media_snapshot = snapshot
        job.media_status_failed = False
        self._media_status_job = job
        def accept(results):
            if not isValid(self) or not isValid(job): return
            if (self._media_status_job is not job or job.cancel.is_set()
                    or self.closing or self._media_status_closed
                    or self.editor is not snapshot[0] or self.editor.session_id != snapshot[1]): return
            current = {m.id: m.path for m in self.project.media}
            for mid, path, exists in results:
                if current.get(mid) != path: continue
                key = (snapshot[0], snapshot[1], mid, path)
                self._media_status_cache[key] = exists
                self._media_status_waiting.discard(key)
                self._media_status_uncertain.discard(key)
            self.retranslate_media_browser()
        def finish():
            if not isValid(self) or not isValid(job): return
            if self._media_status_job is not job:
                job.deleteLater()
                return
            self._media_status_job = None
            job.deleteLater()
            if self._media_status_delete_pending:
                self._media_status_delete_pending = False
                self._media_status_close_pending = False
                self._media_status_close_timer.stop()
                self._job_close_timer.stop()
                self.close()
                self.deleteLater()
                return
            if self._media_status_close_pending:
                self._media_status_close_timer.start(0)
                return
            if self._media_status_closed or self.closing: return
            pending = self._media_status_pending
            self._media_status_pending = None
            if pending is not None:
                if pending[0] is self.editor and pending[1] == self.editor.session_id and pending[2]:
                    self._start_media_status_batch(pending, pending[2])
            elif (job.cancel.is_set() or job.media_status_failed):
                if self.editor is snapshot[0] and self.editor.session_id == snapshot[1]:
                    stale = {(snapshot[0], snapshot[1], mid, path) for mid, path in remaining}
                    stale.intersection_update(self._media_status_waiting)
                    self._media_status_waiting.difference_update(stale)
                    self._media_status_uncertain.update(stale)
                    self.retranslate_media_browser()
            elif (tail and self.editor is snapshot[0]
                    and self.editor.session_id == snapshot[1]):
                self._start_media_status_batch(snapshot, tail)
        job.result.connect(accept)
        job.failed.connect(lambda _: setattr(job, 'media_status_failed', True))
        job.finished.connect(finish)
        job.start()

    def close_media_status(self):
        """Return False while cleanup is pending; never block Qt's event loop."""
        self._media_status_closed = True
        self._media_status_waiting.clear()
        self._media_status_pending = None
        job = self._media_status_job
        if job is None or job.isFinished():
            self._media_status_close_pending = False
            self._media_status_close_timer.stop()
            if job is not None:
                job.cancel.set()
                self._media_status_job = None
            return True
        self._media_status_close_pending = True
        job.cancel.set()
        return False

    def defer_media_status_delete(self):
        """Retain only our background owner during explicit QObject disposal.

        Foreground task ownership remains the caller's existing responsibility;
        this must never turn an active foreground leak into a hidden shutdown.
        """
        if self.jobs: return False
        job = self._media_status_job
        if job is None: return False
        self.closing = True
        if job.isFinished():
            self.close_media_status()
            return False
        self._media_status_delete_pending = True
        self.centralWidget().setEnabled(False); self.menuBar().setEnabled(False)
        self.close_media_status()
        return True

    def _finish_media_status_close(self):
        if not isValid(self): return
        if (self._media_status_close_pending and self._media_status_closed
                and self.closing and self._media_status_job is None):
            self.close()

    def reopen_media_status(self):
        if self._media_status_delete_pending or not self._media_status_closed: return
        self._media_status_close_timer.stop()
        self._media_status_close_pending = False
        self._media_status_closed = False
        self.closing = False
        self.centralWidget().setEnabled(True); self.menuBar().setEnabled(True)
        self.request_media_status_scan()

    def refresh_media_results(self, *_):
        selected = {item.data(ROLE) for item in self.bin.selectedItems()}
        current = self.bin.currentItem().data(ROLE) if self.bin.currentItem() else None
        self.bin.clear()
        media = visible_media(self.project, self.media_scope, self.media_search.text(),
                              self.media_type_filter.currentData(), self.media_usage_filter.currentData())
        for m in media:
            item = QListWidgetItem(self._media_result_text(m)); item.setData(ROLE, m.id)
            item.setToolTip(m.path); self.bin.addItem(item)
            if m.id == current: self.bin.setCurrentItem(item, QtCoreSelection.NoUpdate)
            item.setSelected(m.id in selected)
        self.media_count.setText(tr('{shown} of {total} media', shown=len(media), total=len(self.project.media)))

    def _media_scope_changed(self, current, previous=None):
        self.media_scope = current.data(0, ROLE) if current else ALL_MEDIA
        self.refresh_media_results()

    def _real_media_bin(self):
        return self.media_scope if self.media_scope not in (ALL_MEDIA, UNFILED_MEDIA) else None

    def create_media_bin_dialog(self):
        name, ok = QInputDialog.getText(self, tr('New bin'), tr('Bin name'))
        if ok:
            created = self.edit(lambda: self.editor.create_media_bin(name, self._real_media_bin()))
            if created:
                self.media_scope = created.id; self.refresh_media_browser()

    def rename_media_bin_dialog(self):
        entry = next((b for b in self.project.media_bins if b.id == self._real_media_bin()), None)
        if entry is None: return
        name, ok = QInputDialog.getText(self, tr('Rename bin'), tr('Bin name'), text=entry.name)
        if ok: self.edit(lambda: self.editor.rename_media_bin(entry.id, name))

    def _choose_media_destination(self, *, bin_move=False):
        entries = list(self.project.media_bins)
        by_id = {b.id: b for b in entries}
        def label(entry):
            parts = [entry.name]; parent = entry.parent_id
            while parent in by_id:
                parts.insert(0, by_id[parent].name); parent = by_id[parent].parent_id
            return ' / '.join(parts) + ' [' + entry.id + ']'
        labels = [str(tr('Top level') if bin_move else tr('Unfiled media'))] + [label(b) for b in entries]
        chosen, ok = QInputDialog.getItem(self, tr('Choose destination'), tr('Destination'), labels, 0, False)
        if not ok: return False, None
        index = labels.index(chosen)
        return True, entries[index - 1].id if index else None

    def _media_action(self, index):
        self.media_actions.setCurrentIndex(0)
        if index == 1:
            ids = self.selected_media_ids()
            if not ids: return
            ok, target = self._choose_media_destination()
            if ok: self.edit(lambda: self.editor.move_media_to_bin(ids, target))
        elif index == 2:
            source = self._real_media_bin()
            if source is None: return
            ok, target = self._choose_media_destination(bin_move=True)
            if ok: self.edit(lambda: self.editor.reparent_media_bin(source, target))
        elif index == 3:
            source = self._real_media_bin()
            if source is not None: self.edit(lambda: self.editor.delete_media_bin(source))
        elif index == 4:
            ids = self.selected_media_ids()
            if ids: self.edit(lambda: self.editor.remove_unused_media(ids))

    def import_paths(self, paths, input_encoding='rec709'):
        if self.import_job or self.closing: return
        known = {os.path.normcase(m.path) for m in self.project.media}
        paths = tuple(p for p in paths if os.path.normcase(str(p)) not in known)
        if not paths: return
        editor = self.editor; session = editor.session_id
        self._media_import_generation += 1
        generation = self._media_import_generation
        destination = self._real_media_bin()
        # Snapshot strings only. Filesystem canonicalization/probing is worker-only.
        existing_paths = tuple(m.path for m in self.project.media)
        from . import ui
        def operation(cancel, progress):
            results, errors = [], []
            seen = set()
            for path in existing_paths:
                if cancel.is_set(): return [], []
                try: identity = str(local_import_path(path).resolve())
                except (OSError, RuntimeError, ValueError): identity = str(path)
                seen.add(os.path.normcase(identity))
            for i, path in enumerate(paths):
                if cancel.is_set(): break
                try:
                    canonical = str(local_import_path(path).resolve())
                    identity = os.path.normcase(canonical)
                    if identity not in seen:
                        media = ui.probe(canonical) if input_encoding == 'rec709' else ui.probe(canonical, input_encoding=input_encoding)
                        media.path = canonical
                        results.append(media); seen.add(identity)
                except Exception as exc:
                    errors.append(tr('{name}: {error}', name=Path(path).name, error=tr_error(exc)))
                progress((i + 1) / len(paths))
            return results, errors
        lifecycle = {}
        def current():
            job = lifecycle.get('job')
            return (job is not None and self.import_job is job and not self.closing
                    and self.editor is editor and editor.session_id == session
                    and generation == self._media_import_generation)
        def success(result):
            job = lifecycle.get('job')
            if lifecycle.get('accepted') or not current() or job.cancel.is_set(): return
            lifecycle['accepted'] = True
            media, errors = result
            errors = list(errors)
            if destination is not None and not any(b.id == destination for b in self.project.media_bins):
                self.statusBar().showMessage(tr('Import discarded: destination bin no longer exists.'), 8000)
                if errors: QMessageBox.warning(self, tr('Some media could not be imported'), '\n'.join(errors))
                return
            try:
                added = editor.import_media_batch(media, destination)
            except Exception as exc:
                added = []; errors.append(tr_error(exc))
            if added:
                self.changed()
                imported = {m.id for m in added}
                self.bin.clearSelection()
                for i in range(self.bin.count()):
                    item = self.bin.item(i)
                    if item.data(ROLE) in imported:
                        self.bin.setCurrentItem(item, QtCoreSelection.NoUpdate); item.setSelected(True)
            if errors: QMessageBox.warning(self, tr('Some media could not be imported'), '\n'.join(errors))
        self.start_job(tr('Inspecting media…'), operation, success, 'import',
                       before_start=lambda job: lifecycle.update(job=job), is_current=current)


from PySide6.QtCore import QItemSelectionModel
QtCoreSelection = QItemSelectionModel.SelectionFlag
