"""Headless Qt control/state tests; not native Windows/macOS acceptance."""
from copy import deepcopy
import json

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QGroupBox, QPushButton

from test_ui import app, window
from lumen.i18n import tr_error
from lumen.model import CaptionStyle, Project
from lumen.subtitles import format_srt


def appearance_form(window):
    return window.caption_safe_margin.parentWidget().layout()


def apply_button(window):
    group = window.caption_safe_margin.parentWidget()
    assert isinstance(group, QGroupBox)
    return next(button for button in group.findChildren(QPushButton)
                if button.text() in ('Apply appearance', '应用外观'))


@pytest.mark.parametrize('locale,margin_label,line_label', [
    ('en', 'Safe horizontal margin', 'Maximum lines'),
    ('zh-CN', '水平安全边距', '最大行数'),
])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_layout_controls_are_visible_bounded_and_localized(window, app, locale, margin_label, line_label, mode):
    window.set_ui_language(locale, persist=False)
    window.set_workspace_mode(mode, persist=False)
    window.show()
    window.side_tabs.setCurrentIndex(1)
    app.processEvents()
    form = appearance_form(window)
    assert form.labelForField(window.caption_safe_margin).text() == margin_label
    assert form.labelForField(window.caption_max_lines).text() == line_label
    assert window.caption_safe_margin.isVisible()
    assert window.caption_max_lines.isVisible()
    assert (window.caption_safe_margin.minimum(), window.caption_safe_margin.maximum()) == (0, 20)
    assert (window.caption_max_lines.minimum(), window.caption_max_lines.maximum()) == (1, 20)
    assert window.caption_safe_margin.value() == 5
    assert window.caption_max_lines.value() == 6
    assert '%' in window.caption_safe_margin.suffix()
    note = window.caption_wrap_note.text()
    assert ('Original caption/SRT text stays unchanged.' in note if locale == 'en'
            else '原始字幕和 SRT 文本保持不变。' in note)
    assert ('smaller type or shorter cues' in note if locale == 'en'
            else '减小字号或缩短字幕' in note)


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_staged_layout_survives_locale_mode_navigation_and_applies_one_undo(window, app, mode):
    w = window
    w.editor.add_caption(0, 60, '  Original English 中文\nUnchanged long original line  ')
    w.changed()
    w.select_caption(w.project.captions[0].id)
    w.set_workspace_mode(mode, persist=False)
    before = deepcopy(w.project)
    srt = format_srt(w.project.captions, w.project.fps)
    undo_count, revision = len(w.editor._undo), w.revision
    w.caption_safe_margin.setValue(12)
    w.caption_max_lines.setValue(4)
    w.caption_size.setValue(31)
    w.caption_margin.setValue(18)
    for locale in ('zh-CN', 'en', 'zh-CN', 'en'):
        w.set_ui_language(locale, persist=False)
        for workspace in ('simple', 'professional', mode):
            w.set_workspace_mode(workspace, persist=False)
            w.side_tabs.setCurrentIndex(0)
            w.side_tabs.setCurrentIndex(1)
            app.processEvents()
            assert w.caption_safe_margin.value() == 12
            assert w.caption_max_lines.value() == 4
            assert w.project == before
            assert w.revision == revision
            assert len(w.editor._undo) == undo_count
    QTest.mouseClick(apply_button(w), Qt.MouseButton.LeftButton)
    assert len(w.editor._undo) == undo_count + 1
    assert w.revision == revision + 1
    assert w.project.caption_style.safe_margin_percent == 12
    assert w.project.caption_style.max_lines == 4
    assert w.project.caption_style.font_size == 31
    assert w.project.caption_style.margin_bottom == 18
    assert w.project.captions == before.captions
    assert format_srt(w.project.captions, w.project.fps) == srt
    w.undo()
    assert w.project == before
    assert (w.caption_safe_margin.value(), w.caption_max_lines.value()) == (5, 6)
    w.redo()
    assert (w.caption_safe_margin.value(), w.caption_max_lines.value()) == (12, 4)
    assert w.caption_text.toPlainText() == before.captions[0].text
    assert not w.test_warnings


@pytest.mark.parametrize('mode', ['simple', 'professional'])
def test_open_layout_project_and_legacy_project_sync_controls_without_undo(window, app, monkeypatch, tmp_path, mode):
    w = window
    w.set_workspace_mode(mode, persist=False)
    saved = Project(caption_style=CaptionStyle(safe_margin_percent=18, max_lines=3))
    path = tmp_path / 'saved.lumen'
    saved.save(path)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(path), ''))
    w.open_project()
    app.processEvents()
    assert (w.caption_safe_margin.value(), w.caption_max_lines.value()) == (18, 3)
    assert not w.editor.can_undo and not w.dirty
    data = saved.to_dict()
    del data['caption_style']['safe_margin_percent']
    del data['caption_style']['max_lines']
    path.write_text(json.dumps(data), encoding='utf-8')
    w.open_project()
    app.processEvents()
    assert (w.caption_safe_margin.value(), w.caption_max_lines.value()) == (5, 6)
    assert not w.editor.can_undo and not w.dirty
    assert not w.test_warnings


def test_caption_wrap_note_reflows_after_locale_and_width_changes(window, app):
    note = window.caption_wrap_note
    for locale in ('en', 'zh-CN', 'en'):
        window.set_ui_language(locale, persist=False)
        for width in (650, 330, 280, 510, 650):
            note.resize(width, note.height())
            note._fit_wrapped_height()
            app.processEvents()
            flags = int(note.alignment()) | int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
            needed = note.fontMetrics().boundingRect(
                QRect(0, 0, note.contentsRect().width(), 100000), flags, note.text()).height()
            assert note.minimumHeight() >= needed


def test_layout_validation_errors_localize_without_translating_values(window):
    window.set_ui_language('zh-CN', persist=False)
    assert tr_error('caption safe horizontal margin must be an integer in 0..20') == '字幕水平安全边距必须为 0 至 20 范围内的整数'
    assert tr_error('caption maximum lines must be an integer in 1..20') == '字幕最大行数必须为 1 至 20 范围内的整数'
