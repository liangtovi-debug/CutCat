"""Auxiliary panel language switches preserve staged values and real user data."""
from test_ui import app, window, populate
from test_effects_ui import add_overlay, add_effect
from lumen.i18n import set_locale
from lumen.ui_ai import _translated_warnings
from lumen.i18n_widgets import QLabel


def test_effect_panels_switch_in_place_without_edits(window, app, tmp_path):
    w = window
    clip = populate(w, tmp_path)
    overlay = add_overlay(w, tmp_path)
    w.select_clip(clip.id)
    add_effect(w)
    w.overlay_start.setValue(41)
    w.effect_amount.setValue(4.5)
    before = w.project.to_dict()
    revision = w.revision
    widgets = (w.effect_kind, w.overlay_start, w.effect_amount, w.stickers_panel, w.clip_effects_panel)
    set_locale('zh-CN')
    assert w.side_tabs.tabText(w.side_tabs.indexOf(w.stickers_panel)) == '贴纸'
    assert w.effect_kind.itemText(0) == '高斯模糊'
    assert w.effect_kind.currentData() == 'blur'
    assert w.overlay_keyframes.horizontalHeaderItem(0).text() == '帧'
    assert '图层' in w.overlay_list.item(0).text()
    assert '高斯模糊' in w.effect_list.item(0).text()
    assert w.overlay_list.item(0).data(256) == overlay.id
    assert 'Sample scene' in w.effects_clip_label.text()
    assert w.overlay_start.value() == 41 and w.effect_amount.value() == 4.5
    assert w.project.to_dict() == before and w.revision == revision
    set_locale('en')
    assert w.side_tabs.tabText(w.side_tabs.indexOf(w.stickers_panel)) == 'Stickers'
    assert w.effect_kind.itemText(0) == 'Gaussian blur'
    assert 'layer' in w.overlay_list.item(0).text()
    assert widgets == (w.effect_kind, w.overlay_start, w.effect_amount, w.stickers_panel, w.clip_effects_panel)


def test_ai_controls_keep_paths_user_text_and_diff(window, app):
    w = window
    path = '/tmp/Choose… model 文件.gguf'
    w.ai_fields['subtitle-assistant']['model'].setText(path)
    w.ai_original.setPlainText('Original · IDs and times stay unchanged')
    w.ai_proposed.setPlainText('用户文本 English words')
    w.ai_query.setText('Search saved subtitles')
    w.refresh_ai_diff()
    set_locale('zh-CN')
    assert w.ai_suggest_button.text() == '建议更正'
    assert w.ai_fields['subtitle-assistant']['model'].text() == path
    assert '模型' in w.ai_fields['subtitle-assistant']['model'].placeholderText()
    assert w.ai_original.toPlainText() == 'Original · IDs and times stay unchanged'
    assert w.ai_proposed.toPlainText() == '用户文本 English words'
    assert w.ai_query.text() == 'Search saved subtitles'
    assert w.ai_diff.toPlainText().startswith('--- 原文\n+++ 已审核建议')
    set_locale('en')
    assert w.ai_diff.toPlainText().startswith('--- original\n+++ reviewed proposal')
    assert w.ai_fields['subtitle-assistant']['model'].text() == path
    w.clear_ai_suggestion()
    set_locale('zh-CN')
    assert w.ai_diff.toPlainText() == ''


def test_input_color_status_live_preserves_transfer_ids(window, tmp_path):
    w = window
    populate(w, tmp_path)
    w.refresh_input_color_controls()
    before = w.project.to_dict()
    set_locale('zh-CN')
    assert w.input_encoding_choice.currentData() == 'rec709'
    assert '源传递函数' in w.input_color_status.text()
    assert '显示器校准状态未验证' in w.input_color_status.text()
    assert w.input_color_group.title() == '输入色彩 · SDR 标准化'
    assert w.project.to_dict() == before
    set_locale('en')
    assert 'Source transfer' in w.input_color_status.text()
    assert 'Matrix:' in w.input_color_status.text()


def test_nested_ai_warnings_retranslate(app):
    label = QLabel(_translated_warnings((
        'AI suggestions can change meaning or miss errors. Review the complete diff before applying.',
        'This model differs from the validated Qwen3.5-0.8B Q4_0 file; compatibility and quality are unverified.',
    )))
    set_locale('zh-CN')
    assert '完整差异' in label.text() and '兼容性' in label.text()
    set_locale('en')
    assert label.text().startswith('AI suggestions')
