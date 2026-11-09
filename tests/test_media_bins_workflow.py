"""Visible organization commands identify duplicate names by ID, never rows."""
from PySide6.QtWidgets import QInputDialog
from lumen.model import Media
from lumen.ui_media_bins import ALL_MEDIA, ROLE
from test_ui import app, window


def test_chinese_duplicate_names_visible_move_append_and_safe_cleanup(window, tmp_path, monkeypatch):
    for ident in ('a', 'b'):
        window.editor.add_media(Media(ident, str(tmp_path / ident / 'same.mov'), '同名画面', 2, 640, 360, True, True))
    names = iter(['旅行', '镜头'])
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: (next(names), True))
    window.create_media_bin_dialog()
    parent = window.project.media_bins[0]
    window.create_media_bin_dialog()
    child = window.project.media_bins[1]
    assert child.parent_id == parent.id
    window.media_scope = ALL_MEDIA; window.refresh_media_browser()
    window.bin.setCurrentRow(1)
    monkeypatch.setattr(window, '_choose_media_destination', lambda **kwargs: (True, child.id))
    window._media_action(1)
    assert window.project.media_bin_ids == {'b': child.id}
    window.media_scope = child.id; window.refresh_media_browser()
    window.media_search.setText('同名')
    assert window.bin.count() == 1 and window.bin.item(0).data(ROLE) == 'b'
    window.bin.setCurrentRow(0); window.add_selected_media()
    assert [c.media_id for c in window.project.clips] == ['b']
    window.media_scope = ALL_MEDIA; window.refresh_media_browser(); window.bin.selectAll()
    before = window.project.to_dict(); undo_depth = len(window.editor._undo)
    window._media_action(4)
    assert window.project.to_dict() == before and len(window.editor._undo) == undo_depth
    assert window.test_warnings
    window.bin.clearSelection(); window.bin.setCurrentRow(0)
    window._media_action(4)
    assert [m.id for m in window.project.media] == ['b']
    window.undo()
    assert [m.id for m in window.project.media] == ['a', 'b']
    assert window.project.media_bin_ids == {'b': child.id}


def test_bin_delete_and_reparent_commands_preserve_members(window, monkeypatch):
    parent = window.editor.create_media_bin('Duplicate')
    child = window.editor.create_media_bin('Duplicate', parent.id)
    window.media_scope = child.id; window.refresh_media_browser()
    monkeypatch.setattr(window, '_choose_media_destination', lambda **kwargs: (True, None))
    window._media_action(2)
    assert window.project.media_bins[1].parent_id is None
    window.media_scope = parent.id; window.refresh_media_browser(); window._media_action(3)
    assert [b.id for b in window.project.media_bins] == [child.id]
    assert window.media_scope == ALL_MEDIA
    window.undo()
    assert [b.id for b in window.project.media_bins] == [parent.id, child.id]
