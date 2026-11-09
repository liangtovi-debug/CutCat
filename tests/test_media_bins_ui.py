"""Headless media-browser behavior and deferred import acceptance contracts."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from lumen.model import Editor, Media, Project
from lumen.ui_media_bins import ALL_MEDIA, UNFILED_MEDIA, ROLE, visible_media
from test_ui import app, window, populate, pump_until


def seed(window, tmp_path):
    a = Media('a', str(tmp_path / 'a.mov'), 'Forest', 2, 640, 360, True, True)
    b = Media('b', str(tmp_path / 'b.wav'), 'Rain', 3, 0, 0, False, True)
    window.editor.add_media(a); window.editor.add_media(b)
    folder = window.editor.create_media_bin('Nature')
    window.editor.move_media_to_bin(['a'], folder.id)
    window.refresh()
    return a, b, folder


def deferred(window, monkeypatch):
    captured = {}
    def start(label, operation, success, kind, *, before_start=None, is_current=None):
        job = SimpleNamespace(cancel=threading.Event())
        captured.update(operation=operation, success=success, job=job, is_current=is_current)
        window.import_job = job
        if before_start: before_start(job)
        return job
    monkeypatch.setattr(window, 'start_job', start)
    return captured


def test_scope_search_usage_and_no_filesystem_per_keystroke(window, tmp_path, monkeypatch):
    a, b, folder = seed(window, tmp_path)
    window.editor.add_clip(a.id, 0)
    window.refresh()
    assert [m.id for m in visible_media(window.project, folder.id)] == ['a']
    assert [m.id for m in visible_media(window.project, UNFILED_MEDIA)] == ['b']
    assert [m.id for m in visible_media(window.project, usage='used')] == ['a']
    assert [m.id for m in visible_media(window.project, kind='audio')] == ['b']
    def forbidden(*args, **kwargs): raise AssertionError('filesystem access during filtering')
    monkeypatch.setattr(Path, 'exists', forbidden)
    monkeypatch.setattr(Path, 'resolve', forbidden)
    window.media_search.setText('b.wav')
    assert window.bin.count() == 1 and window.bin.item(0).data(ROLE) == 'b'
    window.media_type_filter.setCurrentIndex(1)
    assert window.bin.count() == 0


def test_hidden_selection_cannot_append_move_or_remove(window, tmp_path):
    seed(window, tmp_path)
    window.bin.setCurrentRow(0)
    assert window.selected_media_ids() == ['a']
    window.media_search.setText('Rain')
    assert window.selected_media_ids() == []
    window.add_selected_media(); window._media_action(4)
    assert not window.project.clips and len(window.project.media) == 2
    window.media_search.clear()
    assert window.selected_media_ids() == []


def test_selection_is_stable_id_and_nested_tree_expansion(window, tmp_path):
    a, b, folder = seed(window, tmp_path)
    child = window.editor.create_media_bin('Child', folder.id)
    window.editor.create_media_bin('Grandchild', child.id)
    window.refresh()
    window.bin.setCurrentRow(1)
    root = window.media_tree.topLevelItem(2)
    root.setExpanded(True); root.child(0).setExpanded(True)
    window.refresh()
    assert window.selected_media_ids() == ['b']
    root = window.media_tree.topLevelItem(2)
    assert root.isExpanded() and root.child(0).isExpanded()


def test_tree_activation_only_navigates_and_focus_blocks_delete(window, tmp_path, app):
    clip = populate(window, tmp_path)
    folder = window.editor.create_media_bin('Bin'); window.refresh()
    window.show(); app.processEvents()
    node = window.media_tree.topLevelItem(2)
    window.media_tree.itemDoubleClicked.emit(node, 0)
    assert len(window.project.clips) == 1
    for control in (window.media_tree, window.media_tree.viewport(), window.bin, window.bin.viewport(), window.media_search):
        control.setFocus(); app.processEvents()
        window.delete_active_selection()
        assert [c.id for c in window.project.clips] == [clip.id]


def test_import_deduplicates_atomic_undo_preserves_errors_and_selected_ids(window, tmp_path, monkeypatch):
    captured = deferred(window, monkeypatch)
    a = tmp_path / 'a.mov'; b = tmp_path / 'b.mov'
    calls = []
    def probe(path):
        calls.append(path)
        if path.endswith('bad'): raise ValueError('broken fixture')
        return Media(Path(path).stem, path, Path(path).name, 2, 640, 360, True, True)
    monkeypatch.setattr('lumen.ui.probe', probe)
    window.import_paths([str(a), str(a), str(b), str(tmp_path / 'bad')])
    result = captured['operation'](captured['job'].cancel, lambda p: None)
    assert len(calls) == 3
    before = len(window.editor._undo)
    captured['success'](result)
    assert len(window.project.media) == 2 and len(window.editor._undo) == before + 1
    assert window.selected_media_ids() == ['a', 'b']
    assert 'broken fixture' in str(window.test_warnings)
    captured['success'](result)
    assert len(window.editor._undo) == before + 1
    window.import_job = None
    window.undo(); assert not window.project.media
    window.redo(); assert len(window.project.media) == 2


@pytest.mark.parametrize('invalidate', ['editor', 'session', 'generation', 'cancel', 'closing', 'job', 'destination'])
def test_import_rejects_stale_lifecycle_without_root_fallback(window, tmp_path, monkeypatch, invalidate):
    folder = window.editor.create_media_bin('Destination')
    window.media_scope = folder.id
    captured = deferred(window, monkeypatch)
    window.import_paths([str(tmp_path / 'a.mov')])
    media = Media('new', str(tmp_path / 'a.mov'), 'New', 2, 640, 360, True, True)
    if invalidate == 'editor': window.editor = Editor(Project())
    elif invalidate == 'session': window.editor.project = Project()
    elif invalidate == 'generation': window._media_import_generation += 1
    elif invalidate == 'cancel': captured['job'].cancel.set()
    elif invalidate == 'closing': window.closing = True
    elif invalidate == 'job': window.import_job = object()
    elif invalidate == 'destination': window.editor.delete_media_bin(folder.id)
    before = window.project.to_dict(); depth = len(window.editor._undo)
    captured['success'](([media], []))
    assert window.project.to_dict() == before and len(window.editor._undo) == depth
    window.import_job = None


def test_import_rechecks_current_duplicates_and_captured_destination(window, tmp_path, monkeypatch):
    folder = window.editor.create_media_bin('Destination'); window.media_scope = folder.id
    captured = deferred(window, monkeypatch)
    path = str(tmp_path / 'a.mov')
    window.import_paths([path])
    window.media_scope = ALL_MEDIA
    window.editor.add_media(Media('existing', path, 'Already added', 2, 640, 360, True, True))
    media = Media('new', path, 'Duplicate', 2, 640, 360, True, True)
    depth = len(window.editor._undo)
    captured['success'](([media], []))
    assert len(window.project.media) == 1 and len(window.editor._undo) == depth
    assert not window.project.media_bin_ids
    window.import_job = None


def test_import_destination_and_filters_do_not_select_hidden_result(window, tmp_path, monkeypatch):
    folder = window.editor.create_media_bin('Destination'); window.media_scope = folder.id
    captured = deferred(window, monkeypatch)
    path = str(tmp_path / 'a.mov'); window.import_paths([path])
    window.media_scope = UNFILED_MEDIA
    media = Media('new', path, 'New', 2, 640, 360, True, True)
    captured['success'](([media], []))
    assert window.project.media_bin_ids == {'new': folder.id}
    assert window.selected_media_ids() == [] and window.bin.count() == 0
    window.import_job = None


@pytest.mark.parametrize('action', ['new', 'open'])
def test_late_import_after_new_or_open_cannot_modify_project(window, tmp_path, monkeypatch, action):
    from PySide6.QtWidgets import QInputDialog, QFileDialog
    captured = deferred(window, monkeypatch)
    path = str(tmp_path / 'a.mov'); window.import_paths([path])
    monkeypatch.setattr(window, 'confirm_discard', lambda: True)
    if action == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('1280 × 720 · 30 fps', True))
        window.new_project()
    else:
        project_path = tmp_path / 'opened.lumen'; Project(name='Opened').save(project_path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(project_path), ''))
        window.open_project()
    before = window.project.to_dict()
    captured['success'](([Media('new', path, 'New', 2, 640, 360, True, True)], []))
    assert window.project.to_dict() == before
    assert not captured['is_current']()
    window.import_job = None


def test_real_worker_canceled_import_has_no_partial_commit(window, tmp_path, monkeypatch, app):
    entered = threading.Event(); release = threading.Event()
    def probe(path):
        entered.set(); release.wait(5)
        return Media('new', path, 'New', 2, 640, 360, True, True)
    monkeypatch.setattr('lumen.ui.probe', probe)
    window.import_paths([str(tmp_path / 'a.mov')]); assert entered.wait(3)
    window.cancel_jobs(); release.set()
    pump_until(app, lambda: not window.jobs)
    assert not window.project.media and not window.editor.can_undo


def test_real_worker_late_error_does_not_warn_replacement_project(window, tmp_path, monkeypatch, app):
    entered = threading.Event(); release = threading.Event()
    def probe(path):
        entered.set(); release.wait(5); raise ValueError('old project error')
    monkeypatch.setattr('lumen.ui.probe', probe)
    window.import_paths([str(tmp_path / 'a.mov')]); assert entered.wait(3)
    window.editor = Editor(Project()); window.refresh()
    release.set(); pump_until(app, lambda: not window.jobs)
    assert not window.project.media and not window.test_warnings


@pytest.mark.parametrize('path', ['https://example.invalid/video.mp4', 'file:///remote.mov', '//server/share/a.mov', r'\\server\share\a.mov'])
def test_import_rejects_network_paths_before_resolve_or_probe(window, monkeypatch, path):
    captured = deferred(window, monkeypatch)
    window.import_paths([path])
    def forbidden(*args, **kwargs): raise AssertionError('network filesystem must not be touched')
    monkeypatch.setattr(Path, 'resolve', forbidden)
    monkeypatch.setattr('lumen.ui.probe', forbidden)
    media, errors = captured['operation'](captured['job'].cancel, lambda p: None)
    assert not media and errors
    window.import_job = None


def test_compact_media_style_is_scoped_and_actions_use_visible_ids(window, tmp_path, monkeypatch):
    a, b, folder = seed(window, tmp_path)
    assert window.media_type_filter.property('compactMediaControl') is True
    assert not window.preview_mode_choice.property('compactMediaControl')
    window.bin.setCurrentRow(1)
    monkeypatch.setattr(window, '_choose_media_destination', lambda **kwargs: (True, folder.id))
    window._media_action(1)
    assert window.project.media_bin_ids == {'a': folder.id, 'b': folder.id}
    window.media_search.setText('Rain')
    window._media_action(4)
    assert [m.id for m in window.project.media] == ['a']
    window.undo(); assert [m.id for m in window.project.media] == ['a', 'b']


def test_media_tree_accessible_name_retranslates_without_state_changes(window, tmp_path):
    from lumen.i18n import tr
    seed(window, tmp_path)
    window.bin.setCurrentRow(1)
    item = window.bin.currentItem()
    tree_item = window.media_tree.currentItem()
    selected = window.selected_media_ids()
    project = window.project.to_dict()
    revision = window.editor.revision
    for locale in ('en', 'zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        assert window.media_tree.accessibleName() == str(tr('Media bins'))
        assert window.bin.currentItem() is item
        assert window.media_tree.currentItem() is tree_item
        assert window.selected_media_ids() == selected
        assert window.project.to_dict() == project
        assert window.editor.revision == revision


def test_media_browser_live_locale_audit_keeps_user_state(window, tmp_path, app):
    from PySide6.QtWidgets import QPushButton
    from lumen.i18n import tr
    seed(window, tmp_path)
    window.media_search.setText('Rain')
    window.bin.setCurrentRow(0)
    window.media_actions.setCurrentIndex(1)
    selected_item = window.bin.currentItem()
    selected_tree = window.media_tree.currentItem()
    selected_ids = window.selected_media_ids()
    project = window.project.to_dict()
    revision = window.editor.revision
    history = (len(window.editor._undo), len(window.editor._redo))
    named = [(window.media_tree, 'Media bins'), (window.media_search, 'Search name or path'),
             (window.media_type_filter, 'Media type'), (window.media_usage_filter, 'Media usage'),
             (window.bin, 'Media results'), (window.media_actions, 'Organize media')]
    menus = [(window.media_type_filter, ['All types', 'Video', 'Audio only']),
             (window.media_usage_filter, ['All usage', 'Used', 'Unused']),
             (window.media_actions, ['Organize media', 'Move selected media…', 'Move bin…',
                                     'Delete empty bin', 'Remove selected unused media'])]
    buttons = window.media_scroll.findChildren(QPushButton)
    for locale in ('en', 'zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        app.processEvents(); app.processEvents()
        for widget, source in named:
            assert widget.accessibleName() == str(tr(source))
        assert window.media_search.placeholderText() == str(tr('Search name or path'))
        for widget, sources in menus:
            assert [widget.itemText(i) for i in range(widget.count())] == [str(tr(source)) for source in sources]
        assert str(tr('Project organization only. Original files are never moved or deleted.')) in window.media_actions.toolTip()
        for button in buttons:
            if button is window.import_apple_log_button: continue
            source = button._translation_bindings['setText'].source
            assert button.toolTip() == str(tr(source))
        assert window.media_tree.topLevelItem(0).text(0) == str(tr('All media'))
        assert window.media_tree.topLevelItem(1).text(0) == str(tr('Unfiled media'))
        assert window.media_tree.topLevelItem(2).text(0) == 'Nature'
        assert window.bin.currentItem() is selected_item
        assert window.media_tree.currentItem() is selected_tree
        assert window.media_search.text() == 'Rain'
        assert window.media_actions.currentIndex() == 1
        assert window.selected_media_ids() == selected_ids
        assert window.project.to_dict() == project and window.editor.revision == revision
        assert (len(window.editor._undo), len(window.editor._redo)) == history


def test_raw_media_destination_dialog_uses_current_locale_without_changing_project(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from lumen.i18n import tr
    seed(window, tmp_path)
    project = window.project.to_dict()
    captured = []
    def get_item(parent, title, label, values, *args):
        captured.append((str(title), str(label), list(values)))
        return '', False
    monkeypatch.setattr(QInputDialog, 'getItem', get_item)
    for locale in ('en', 'zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        assert window._choose_media_destination() == (False, None)
        title, label, values = captured[-1]
        assert title == str(tr('Choose destination'))
        assert label == str(tr('Destination'))
        assert values[0] == str(tr('Unfiled media'))
        assert 'Nature' in values[1]
        assert window.project.to_dict() == project
