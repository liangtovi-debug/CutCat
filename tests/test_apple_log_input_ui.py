"""Explicit Apple Log import remains asynchronous and relinking fails closed."""
import copy
from pathlib import Path
import pytest
from test_ui import app, window, pump_until
from lumen.model import Media
from lumen.i18n import set_locale
from PySide6.QtWidgets import QFileDialog


def source(path, **kwargs):
    return Media('log', str(path), 'Synthetic Apple Log', 1, 64, 64,
                 color_space='gbr', color_range='pc', color_primaries='bt2020',
                 input_encoding='apple_log_bt2020', **kwargs)


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_explicit_import_and_inspector_do_not_autodetect(window, app, tmp_path, monkeypatch, locale):
    import lumen.ui as ui
    set_locale(locale)
    path = tmp_path / 'synthetic.mkv'; path.write_bytes(b'synthetic')
    calls = []
    def inspect(path, **kwargs):
        calls.append(kwargs)
        return source(path)
    monkeypatch.setattr(ui, 'probe', inspect)
    window.import_paths([str(path)], input_encoding='apple_log_bt2020')
    pump_until(app, lambda: not window.jobs)
    assert calls == [{'input_encoding': 'apple_log_bt2020'}]
    window.bin.setCurrentRow(0); window.add_selected_media()
    assert window.selected_clip().grade.input_encoding == 'apple_log_bt2020'
    assert 'Apple Log' in window.input_color_status.text()
    assert 'BT.2020' in window.input_color_status.text()
    before = copy.deepcopy(window.project)
    window.input_encoding_choice.setCurrentIndex(window.input_encoding_choice.findData('rec709'))
    assert window.project == before and window.test_warnings
    assert window.input_encoding_choice.currentData() == 'apple_log_bt2020'
    window.refresh_input_color_controls()
    assert window.project == before


def test_default_media_cannot_be_reinterpreted_as_apple_by_dropdown(window, tmp_path):
    path=tmp_path/'sdr';path.write_bytes(b'sdr')
    window.editor.add_media(Media('sdr',str(path),'SDR',1,64,64))
    window.refresh();window.bin.setCurrentRow(0);window.add_selected_media()
    before=copy.deepcopy(window.project)
    window.input_encoding_choice.setCurrentIndex(window.input_encoding_choice.findData('apple_log_bt2020'))
    assert window.project==before and window.test_warnings
    assert window.input_encoding_choice.currentData()=='rec709'


def test_relink_preserves_explicit_profile_and_rejects_conflicting_tags(window,tmp_path,monkeypatch):
    import lumen.ui as ui
    original=tmp_path/'missing.mkv'
    window.editor.add_media(source(original));window.refresh();window.bin.setCurrentRow(0);window.add_selected_media()
    replacement=tmp_path/'replacement.mkv';replacement.write_bytes(b'fixture')
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a:(str(replacement),''))
    calls=[]
    def inspect(path, **kwargs):
        calls.append(kwargs)
        return source(path,color_transfer='bt709')
    monkeypatch.setattr(ui,'probe',inspect)
    before=copy.deepcopy(window.project)
    window.relink_dialog()
    assert window.project==before and window.test_warnings
    assert calls==[{'input_encoding':'apple_log_bt2020'}]
    monkeypatch.setattr(ui,'probe',lambda path,**kwargs:source(path))
    window.relink_dialog()
    assert window.project.media[0].path==str(replacement)
    assert window.project.media[0].input_encoding=='apple_log_bt2020'
    assert window.project.media[0].color_transfer=='unknown'
    assert window.project.clips[0].grade.input_encoding=='apple_log_bt2020'
    assert not window.editor.can_undo


def test_apple_log_errors_are_localized_without_raw_diagnostics():
    from lumen.i18n import tr_error
    set_locale('zh-CN')
    text = tr_error('Experimental Apple Log input does not support active stabilization.')
    assert '防抖' in text and 'Technical' not in text
    text = tr_error('Cannot inspect media: Experimental Apple Log RGB requires full-range RGB16 and RGB matrix metadata.')
    assert '全范围' in text and 'requires' not in text


def test_import_action_keeps_experimental_badge_and_tooltips_on_language_switch(window):
    for locale in ('en', 'zh-CN', 'en'):
        set_locale(locale)
        assert '\n' not in window.import_apple_log_button.text()
        assert 'Apple Log 1' in window.import_apple_log_button.text()
        assert 'BT.2020' in window.apple_log_import_badge.text()
        assert window.apple_log_import_badge.wordWrap()
        assert window.apple_log_import_badge.toolTip() == window.import_apple_log_button.toolTip()
        expected = 'experimental' if locale == 'en' else '实验性'
        assert expected in window.apple_log_import_badge.text()
