"""Real Qt profile selection, dismissal, validation and locale boundaries."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import ast
import copy
from pathlib import Path
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QScrollArea

from lumen.i18n import set_locale, tr_error
from lumen.model import Caption, Clip, ClipEffect, Media, Overlay, OverlayKeyframe, Project
from lumen.sticker_assets import load_sticker
from lumen.ui_color_export import ExportProfileDialog, choose_export_profile
import lumen.ui_color_export as export_ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def project():
    return Project(media=[Media('media', 'source.mp4', 'Source', 1, 64, 48)],
                   clips=[Clip('clip', 'media', 0, 0, 0, 30)])


def choose_high_bit(dialog):
    dialog.profile_choice.setCurrentIndex(dialog.profile_choice.findData('prores_422_hq_10bit'))


def test_default_is_legacy_h264_but_selection_requires_accept(app, project, monkeypatch):
    calls = []
    original = export_ui.validate_export_profile
    def validate(project, profile):
        calls.append(profile)
        return original(project, profile)
    monkeypatch.setattr(export_ui, 'validate_export_profile', validate)
    dialog = ExportProfileDialog(project)
    assert dialog.profile_choice.currentData() == 'h264_8bit'
    assert dialog.selected_profile is None and calls == []
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.continue_button.click()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_profile == 'h264_8bit'
    assert calls == ['h264_8bit'] and accepted == [True]


def test_profiles_describe_actual_delivery_and_proxy(app, project):
    dialog = ExportProfileDialog(project)
    assert '8-bit YUV 4:2:0' in dialog.profile_detail.text()
    choose_high_bit(dialog)
    assert 'SDR Rec.709 only' in dialog.profile_detail.text()
    assert 'RGB16' in dialog.profile_detail.text()
    assert '0–1' in dialog.profile_detail.text()
    assert '10-bit YUV 4:2:2' in dialog.profile_detail.text()
    assert 'not HDR' in dialog.profile_detail.text()
    limits = dialog.profile_limits.text()
    assert 'basic captions and verified static PNG stickers are supported' in limits
    assert 'without reducing the background video to 8-bit' in limits
    assert 'Gaussian blur and high-precision radial vignette are supported in native RGB16' in limits
    assert 'working 10-bit drawtext for captions, 10-bit overlay for stickers and RGB16 effects' in limits
    assert 'Failures block export; there is no silent fallback' in limits
    assert 'Animated sticker packs and unverified clip effects remain blocked' in limits
    assert 'remove captions' not in limits
    assert '8-bit H.264 proxy' in dialog.preview_note.text()
    assert 'not a 10-bit output or display check' in dialog.preview_note.text()
    assert 'calibration is not verified' in dialog.preview_note.text()


@pytest.mark.parametrize('dismissal', ['button', 'escape', 'close'])
def test_cancel_escape_and_close_expose_no_selection(app, project, dismissal):
    dialog = ExportProfileDialog(project)
    before = copy.deepcopy(project.to_dict())
    dialog.show()
    choose_high_bit(dialog)
    app.processEvents()
    if dismissal == 'button':
        dialog.cancel_button.click()
    elif dismissal == 'escape':
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()
    app.processEvents()
    assert not dialog.isVisible()
    assert dialog.selected_profile is None
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert project.to_dict() == before


def test_repeated_profile_switches_do_not_mutate_project(app, project):
    dialog = ExportProfileDialog(project)
    before = copy.deepcopy(project.to_dict())
    for _ in range(4):
        choose_high_bit(dialog)
        assert dialog.selected_profile is None
        dialog.profile_choice.setCurrentIndex(0)
        assert '8-bit YUV 4:2:0' in dialog.profile_detail.text()
    choose_high_bit(dialog)
    dialog.accept()
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert project.to_dict() == before


def test_unsupported_highbit_content_stays_in_dialog_then_can_use_h264(app, project):
    path = Path(__file__).resolve().parents[1] / 'lumen/assets/stickers/pulse_ring/sticker.json'
    # A JSON pack remains outside the bounded static-PNG highbit path.
    project.overlays = [Overlay('sticker', str(path), '0' * 64, 0, 10)]
    project.clips[0].effects = [ClipEffect('vignette', 'vignette', .5)]
    # Caption/static-PNG support must not bypass another unsupported branch.
    project.captions = [Caption('caption', 0, 10, 'User text stays untouched')]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    dialog.continue_button.click()
    assert dialog.isVisible()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.selected_profile is None
    assert not dialog.validation_notice.isHidden()
    assert 'sticker' in dialog.validation_notice.text()
    assert 'H.264' in dialog.validation_notice.text()
    assert project.to_dict() == before
    dialog.profile_choice.setCurrentIndex(0)
    assert dialog.validation_notice.isHidden()
    dialog.continue_button.click()
    assert dialog.selected_profile == 'h264_8bit'
    assert project.to_dict() == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('text', ['Existing captions', '原始字幕 <User content>\nSecond line'])
def test_highbit_captions_are_accepted_without_mutating_project(app, project, locale, text):
    set_locale(locale)
    project.captions = [Caption('caption', 0, 10, text)]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    assert dialog.selected_profile is None
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert dialog.validation_notice.isHidden()
    assert not dialog.isVisible()
    assert project.to_dict() == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_highbit_graphics_limits_remain_explicit_and_localized(app, project, locale):
    set_locale(locale)
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    limits = dialog.profile_limits.text()
    detail = dialog.profile_detail.text()
    if locale == 'zh-CN':
        assert '支持现有基础字幕和已验证的静态 PNG 贴纸' in limits
        assert '不会将背景视频降为 8 位' in limits
        assert '高斯模糊和高精度径向暗角支持原生 RGB16' in limits
        assert '10 位 drawtext' in limits and '10 位 overlay' in limits
        assert '检查失败会阻止导出，不会静默回退' in limits
        assert '动画贴纸包和未经验证的片段特效仍会阻止导出' in limits
        assert 'Qt 绘制到 8 位 RGBA 图层' in dialog.graphics_note.text()
        assert '不是线性光混合' in dialog.graphics_note.text()
        assert 'sRGB 至 Rec.709 传递函数转换' in dialog.graphics_note.text()
        assert 'ICC/HDR 色彩管理' in dialog.graphics_note.text()
        assert '仅支持 SDR Rec.709' in detail
        assert '不是 HDR' in detail
        assert '移除字幕' not in limits
    else:
        assert 'basic captions and verified static PNG stickers are supported' in limits
        assert 'without reducing the background video to 8-bit' in limits
        assert 'Gaussian blur and high-precision radial vignette are supported in native RGB16' in limits
        assert 'working 10-bit drawtext for captions, 10-bit overlay for stickers and RGB16 effects' in limits
        assert 'Failures block export; there is no silent fallback' in limits
        assert 'Animated sticker packs and unverified clip effects remain blocked' in limits
        assert 'Qt into an 8-bit RGBA layer' in dialog.graphics_note.text()
        assert 'not linear-light blending' in dialog.graphics_note.text()
        assert 'sRGB-to-Rec.709 transfer conversion' in dialog.graphics_note.text()
        assert 'ICC/HDR color management' in dialog.graphics_note.text()
        assert 'SDR Rec.709 only' in detail and 'not HDR' in detail
        assert 'remove captions' not in limits


def test_disabled_effect_and_sticker_do_not_block_highbit(app, project):
    project.clips[0].effects = [ClipEffect('effect', 'blur', 2, enabled=False)]
    project.overlays = [Overlay('sticker', 'private.png', '0' * 64, 0, 10, enabled=False)]
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    dialog.accept()
    assert dialog.selected_profile == 'prores_422_hq_10bit'


def test_live_language_switch_preserves_selection_and_translates_error(app, project):
    # A defensive profile guard still rejects unknown future/corrupt kinds.
    project.clips[0].effects = [ClipEffect('effect', 'unverified', .5)]
    project.captions = [Caption('caption', 0, 10, '<User content>')]
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    dialog.accept()
    assert 'supports Gaussian blur and radial vignette only' in dialog.validation_notice.text()
    set_locale('zh-CN')
    app.processEvents()
    assert dialog.windowTitle() == '选择导出格式'
    assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
    assert '10 位 SDR' in dialog.profile_choice.currentText()
    assert '仅支持高斯模糊和径向暗角' in dialog.validation_notice.text()
    assert '限制在 0–1' in dialog.profile_detail.text()
    assert '8 位 H.264' in dialog.preview_note.text()
    assert '基础字幕和已验证的静态 PNG 贴纸' in dialog.profile_limits.text()
    assert '10 位 drawtext' in dialog.profile_limits.text()
    assert '10 位 overlay' in dialog.profile_limits.text()
    assert 'Qt 绘制到 8 位 RGBA 图层' in dialog.graphics_note.text()
    assert dialog.selected_profile is None
    assert project.captions[0].text == '<User content>'
    set_locale('en')
    app.processEvents()
    assert 'supports Gaussian blur and radial vignette only' in dialog.validation_notice.text()
    assert dialog.continue_button.text() == 'Choose export filename…'
    assert 'basic captions and verified static PNG stickers' in dialog.profile_limits.text()
    assert 'Qt into an 8-bit RGBA layer' in dialog.graphics_note.text()


def test_error_is_plain_text_and_unknown_diagnostics_preserved(app, project, monkeypatch):
    def fail(*_):
        raise ValueError('<b>private-source.mp4</b> cannot be exported')
    monkeypatch.setattr(export_ui, 'validate_export_profile', fail)
    dialog = ExportProfileDialog(project)
    dialog.accept()
    assert dialog.validation_notice.textFormat() == Qt.TextFormat.PlainText
    assert '<b>private-source.mp4</b>' in dialog.validation_notice.text()
    set_locale('zh-CN')
    assert '<b>private-source.mp4</b>' in dialog.validation_notice.text()
    assert dialog.selected_profile is None


@pytest.mark.parametrize('accept', [False, True])
def test_helper_returns_only_explicitly_accepted_profile_without_filename_dialog(app, project, monkeypatch, accept):
    project.captions = [Caption('caption', 0, 10, 'Caption 原文')]
    before = copy.deepcopy(project.to_dict())
    def forbid_filename(*_):
        pytest.fail('Filename dialog must be opened by the caller only after a valid profile')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', forbid_filename)
    def finish():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, ExportProfileDialog)
        choose_high_bit(dialog)
        dialog.accept() if accept else dialog.reject()
    QTimer.singleShot(0, finish)
    result = choose_export_profile(project)
    assert result == ('prores_422_hq_10bit' if accept else None)
    assert project.to_dict() == before


def test_helper_invalid_profile_can_cancel_before_any_filename_selection(app, project, monkeypatch):
    project.clips[0].effects = [ClipEffect('vignette', 'vignette', .5)]
    path = Path(__file__).resolve().parents[1] / 'lumen/assets/stickers/pulse_ring/sticker.json'
    project.overlays = [Overlay('animated', str(path), '0' * 64, 0, 10)]
    project.captions = [Caption('caption', 0, 10, 'Caption')]
    before = copy.deepcopy(project.to_dict())
    def forbid_filename(*_):
        pytest.fail('Invalid profile must not reach filename selection')
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', forbid_filename)
    def finish():
        dialog = app.activeModalWidget()
        choose_high_bit(dialog)
        dialog.accept()
        assert dialog.isVisible() and dialog.selected_profile is None
        assert 'Animated frame-pack manifests' in dialog.validation_notice.text()
        dialog.reject()
    QTimer.singleShot(0, finish)
    assert choose_export_profile(project) is None
    assert project.to_dict() == before


def test_reopened_dialog_does_not_leak_prior_accepted_selection(app, project):
    project.clips[0].effects = [ClipEffect('vignette', 'vignette', .5)]
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    dialog.accept()
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    project.clips[0].effects.append(ClipEffect('effect', 'unverified', .5))
    dialog.show()
    assert dialog.selected_profile is None
    dialog.accept()
    assert dialog.isVisible() and dialog.selected_profile is None
    assert 'Other unverified clip effects are blocked' in dialog.validation_notice.text()
    dialog.reject()
    assert dialog.selected_profile is None


@pytest.mark.parametrize('source,chinese', [
    ('This FFmpeg build cannot burn captions directly into 10-bit Rec.709 video. Choose H.264 8-bit or install a compatible FFmpeg build.',
     '当前 FFmpeg 无法将字幕直接烧录到 10 位 Rec.709 视频。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。'),
    ('Render timed out checking high-bit caption support.',
     '检查高位深字幕支持时渲染超时。'),
    ('High-bit caption capability probe exceeded its output budget.',
     '高位深字幕能力检查的输出超出安全限制。'),
])
def test_highbit_caption_preflight_errors_are_localized(source, chinese):
    set_locale('en')
    assert tr_error(ValueError(source)) == source
    set_locale('zh-CN')
    assert tr_error(ValueError(source)) == chinese


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_highbit_limits_fit_scrollable_narrow_dialog(app, project, locale):
    set_locale(locale)
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    dialog.resize(dialog.minimumSize())
    dialog.show()
    app.processEvents()
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    assert dialog.profile_limits.width() <= scroll.viewport().width()
    scroll.ensureWidgetVisible(dialog.profile_limits)
    app.processEvents()
    assert dialog.continue_button.isVisible() and dialog.cancel_button.isVisible()
    assert dialog.rect().contains(dialog.buttons.geometry())
    assert dialog.profile_limits.wordWrap()
    assert dialog.graphics_note.wordWrap()
    scroll.ensureWidgetVisible(dialog.graphics_note)
    app.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    assert dialog.graphics_note.width() <= scroll.viewport().width()
    assert dialog.rect().contains(dialog.buttons.geometry())
    dialog.reject()


def test_highbit_caption_preflight_static_errors_have_catalog_entries():
    from lumen.highbit_captions import HIGHBIT_CAPTION_ERROR
    from lumen.i18n_catalog_errors import ZH_CN
    path = Path(__file__).resolve().parents[1] / 'lumen' / 'highbit_captions.py'
    sources = {HIGHBIT_CAPTION_ERROR}
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                sources.add(argument.value)
    assert sources <= ZH_CN.keys()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('captions', [False, True])
def test_verified_static_png_is_accepted_without_mutating_project(app, project, tmp_path, locale, captions):
    set_locale(locale)
    path = tmp_path / 'synthetic-static.png'
    image = QImage(32, 24, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#80ffbb66'))
    assert image.save(str(path))
    digest = load_sticker(path).digest
    project.overlays = [Overlay('sticker', str(path), digest, 2, 8,
                               keyframes=[OverlayKeyframe(0, 2, 3, .5, .4),
                                          OverlayKeyframe(7, 5, 6, 1, .9)])]
    if captions:
        project.captions = [Caption('caption', 2, 8, 'Caption 原始文字 <user>')]
    before = copy.deepcopy(project.to_dict())
    original_bytes = path.read_bytes()
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert dialog.validation_notice.isHidden()
    assert project.to_dict() == before
    assert path.read_bytes() == original_bytes


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_h264_hides_highbit_graphics_note_when_switching_back(app, project, locale):
    set_locale(locale)
    dialog = ExportProfileDialog(project)
    dialog.show()
    assert dialog.graphics_note.isHidden()
    for _ in range(3):
        choose_high_bit(dialog)
        assert not dialog.graphics_note.isHidden()
        assert 'Qt' in dialog.graphics_note.text()
        dialog.profile_choice.setCurrentIndex(0)
        assert dialog.graphics_note.isHidden()
        assert not dialog.graphics_note.text()
        assert 'H.264' in dialog.profile_detail.text()
    dialog.reject()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_animated_pack_rejection_is_localized_and_recoverable(app, project, locale):
    set_locale(locale)
    path = Path(__file__).resolve().parents[1] / 'lumen/assets/stickers/pulse_ring/sticker.json'
    # Profile eligibility rejects the declared JSON kind before file/render work.
    project.overlays = [Overlay('animated', str(path), '0' * 64, 0, 10)]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    dialog.accept()
    error = dialog.validation_notice.text()
    assert '动画贴纸包' in error if locale == 'zh-CN' else 'Animated frame-pack manifests' in error
    assert 'H.264' in error
    assert dialog.isVisible() and dialog.selected_profile is None
    dialog.profile_choice.setCurrentIndex(0)
    assert dialog.validation_notice.isHidden()
    dialog.accept()
    assert dialog.selected_profile == 'h264_8bit'
    assert project.to_dict() == before


def test_active_ui_copy_has_no_blanket_highbit_sticker_rejection():
    root = Path(__file__).resolve().parents[1]
    stale = ('does not yet support enabled stickers',
             'Active clip effects and enabled stickers remain blocked',
             '生效中的片段特效和已启用的贴纸仍会阻止导出')
    for relative in ('lumen/ui.py', 'lumen/ui_color_export.py',
                     'lumen/i18n_catalog_color_foundation.py'):
        source = (root / relative).read_text(encoding='utf-8')
        assert not any(text in source for text in stale), relative


@pytest.mark.parametrize('source,chinese', [('This FFmpeg build cannot composite stickers directly into 10-bit Rec.709 video. Choose H.264 8-bit or install a compatible FFmpeg build.', '当前 FFmpeg 无法将贴纸直接合成到 10 位 Rec.709 视频。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。'), ('High-bit sticker source identity is missing or unsupported. Verify a static PNG before rendering.', '高位深贴纸的源文件标识缺失或不受支持。请在渲染前验证静态 PNG。'), ('Render timed out checking high-bit sticker support.', '检查高位深贴纸支持时渲染超时。'), ('High-bit sticker capability probe exceeded its output budget.', '高位深贴纸能力检查的输出超出安全限制。')])
def test_highbit_sticker_preflight_errors_are_localized(source, chinese):
    set_locale('en')
    assert tr_error(ValueError(source)) == source
    set_locale('zh-CN')
    assert tr_error(ValueError(source)) == chinese


def test_highbit_sticker_preflight_static_errors_have_catalog_entries():
    from lumen.highbit_stickers import HIGHBIT_STICKER_ERROR, STATIC_PNG_ERROR
    from lumen.i18n_catalog_errors import ZH_CN
    path = Path(__file__).resolve().parents[1] / 'lumen' / 'highbit_stickers.py'
    sources = {HIGHBIT_STICKER_ERROR, STATIC_PNG_ERROR}
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                sources.add(argument.value)
    assert sources <= ZH_CN.keys()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('amount', [0, .25, 2, 30])
def test_gaussian_blur_profile_is_accepted_without_project_changes(app, project, locale, amount):
    set_locale(locale)
    project.clips[0].effects = [ClipEffect('blur', 'blur', amount)]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    assert not dialog.effect_note.isHidden()
    assert ('高斯模糊和高精度径向暗角支持原生 RGB16' if locale == 'zh-CN' else 'Gaussian blur and high-precision radial vignette are supported in native RGB16') in dialog.profile_limits.text()
    dialog.continue_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert dialog.validation_notice.isHidden() and not dialog.isVisible()
    assert project.to_dict() == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('enabled,amount', [(False, .5), (True, 0)])
def test_inactive_vignette_with_active_blur_is_eligible(app, project, locale, enabled, amount):
    set_locale(locale)
    project.clips[0].effects = [ClipEffect('blur', 'blur', 3),
                               ClipEffect('vignette', 'vignette', amount, enabled=enabled)]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    dialog.accept()
    assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert project.to_dict() == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('amount', [.001, .5, 1])
@pytest.mark.parametrize('blur_first', [False, True])
def test_active_vignette_with_blur_is_accepted_in_project_order(app, project, locale, amount, blur_first):
    set_locale(locale)
    project.clips[0].effects = [ClipEffect('blur', 'blur', 3),
                               ClipEffect('vignette', 'vignette', amount)]
    if not blur_first:
        project.clips[0].effects.reverse()
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    for _ in range(2):
        dialog.continue_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert not dialog.isVisible() and dialog.validation_notice.isHidden()
        assert dialog.selected_profile == 'prores_422_hq_10bit'
    assert project.to_dict() == before


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_vignette_dialog_switch_cancel_reopen_and_bypass_states(app, project, locale):
    set_locale(locale)
    effect = ClipEffect('vignette', 'vignette', .5)
    project.clips[0].effects = [effect]
    before = copy.deepcopy(project.to_dict())
    dialog = ExportProfileDialog(project)
    dialog.show()
    for _ in range(3):
        choose_high_bit(dialog)
        assert dialog.selected_profile is None
        assert not dialog.effect_note.isHidden()
        dialog.profile_choice.setCurrentIndex(0)
        assert dialog.effect_note.isHidden() and not dialog.effect_note.text()
        assert dialog.validation_notice.isHidden()
    choose_high_bit(dialog)
    dialog.cancel_button.click()
    assert not dialog.isVisible() and dialog.selected_profile is None
    assert project.to_dict() == before

    # A reused accepted or cancelled dialog must revalidate the current state;
    # neither an earlier choice nor the bypasses may leak an implicit accept.
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))
    for enabled, amount in ((True, .5), (False, .5), (True, 0), (True, 1)):
        effect.enabled, effect.amount = enabled, amount
        expected = copy.deepcopy(project.to_dict())
        dialog.show()
        assert dialog.selected_profile is None
        assert dialog.validation_notice.isHidden()
        assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
        dialog.continue_button.click()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.selected_profile == 'prores_422_hq_10bit'
        assert not dialog.isVisible()
        assert project.to_dict() == expected
    assert accepted == [True] * 4


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_unknown_effect_is_still_blocked_beside_supported_vignette(app, project, locale):
    set_locale(locale)
    project.clips[0].effects = [ClipEffect('vignette', 'vignette', .5),
                               ClipEffect('unknown', 'unverified', .25)]
    # The deliberately malformed kind cannot pass serialization validation.
    # Snapshot the object itself to prove the dialog does not repair/mutate it.
    before = copy.deepcopy(project)
    dialog = ExportProfileDialog(project)
    dialog.show()
    choose_high_bit(dialog)
    for _ in range(2):
        dialog.continue_button.click()
        assert dialog.isVisible() and dialog.selected_profile is None
        assert not dialog.validation_notice.isHidden()
        assert ('其他未经验证的片段特效' if locale == 'zh-CN'
                else 'Other unverified clip effects') in dialog.validation_notice.text()
        assert project == before
    dialog.reject()
    assert dialog.selected_profile is None


def test_live_effect_precision_copy_switches_languages_and_profiles(app, project):
    project.clips[0].effects = [ClipEffect('blur', 'blur', 2),
                               ClipEffect('vignette', 'vignette', .5)]
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    for locale in ('zh-CN', 'en', 'zh-CN', 'en'):
        set_locale(locale)
        app.processEvents()
        note = dialog.effect_note.text()
        assert dialog.profile_choice.currentData() == 'prores_422_hq_10bit'
        assert '0–30' in note and 'Sigma' in note
        if locale == 'zh-CN':
            assert '编码 Rec.709 RGB 码值' in note and '不是线性光或 HDR' in note
            assert '暗角强度为 0–1' in note
            assert '高精度径向暗角与旧版 8 位预览并非逐位一致' in note
            assert '零强度或禁用的特效直接旁路' in note
            assert '暂不支持片段特效关键帧' in note
            assert '保守 CPU 处理，导出速度可能较慢' in note
            assert '高精度径向暗角可能显著降低导出速度' in note
        else:
            assert 'encoded Rec.709 RGB code values' in note and 'not linear light or HDR' in note
            assert 'vignette amount: 0–1' in note
            assert 'high-precision radial vignette is not bit-identical to the legacy 8-bit preview' in note
            assert 'Zero or disabled effects are bypassed' in note
            assert 'Clip-effect keyframes are not supported' in note
            assert 'conservative CPU processing and may export more slowly' in note
            assert 'High-precision radial vignette can substantially slow export' in note
        dialog.profile_choice.setCurrentIndex(0)
        assert dialog.effect_note.isHidden() and not dialog.effect_note.text()
        choose_high_bit(dialog)
        assert not dialog.effect_note.isHidden()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('size', [(380, 360), (720, 820)])
@pytest.mark.parametrize('blocked', [False, True])
def test_effect_limits_and_rejection_fit_real_qt_layout(app, project, locale, size, blocked):
    set_locale(locale)
    project.clips[0].effects = [ClipEffect('blur', 'blur', 2),
                               ClipEffect('vignette', 'vignette', .5)]
    if blocked:
        project.clips[0].effects.append(ClipEffect('effect', 'unverified', .5))
    dialog = ExportProfileDialog(project)
    choose_high_bit(dialog)
    dialog.resize(*size)
    dialog.show()
    if blocked:
        dialog.continue_button.click()
    app.processEvents()
    scroll = dialog.findChild(QScrollArea)
    for note in (dialog.profile_limits, dialog.effect_note, dialog.graphics_note):
        scroll.ensureWidgetVisible(note)
        app.processEvents()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert note.wordWrap() and note.width() <= scroll.viewport().width()
        assert note.height() >= note.heightForWidth(note.width())
        assert dialog.rect().contains(dialog.buttons.geometry())
        assert dialog.continue_button.isVisible() and dialog.cancel_button.isVisible()
    if blocked:
        assert not dialog.validation_notice.isHidden()
        assert dialog.rect().contains(dialog.validation_notice.geometry())
        assert dialog.validation_notice.geometry().bottom() < dialog.buttons.geometry().top()
        assert dialog.validation_notice.height() >= dialog.validation_notice.heightForWidth(dialog.validation_notice.width())
    dialog.reject()
    assert dialog.selected_profile is None


@pytest.mark.parametrize('source,chinese', [
    ('ProRes 10-bit SDR export supports Gaussian blur and radial vignette only. Other unverified clip effects are blocked. Disable them or choose H.264 8-bit.',
     'ProRes 10 位 SDR 导出仅支持高斯模糊和径向暗角。其他未经验证的片段特效会被阻止。请禁用这些特效，或选择 H.264 8 位。'),
    ('This FFmpeg build cannot apply Gaussian blur directly to 16-bit RGB video. Choose H.264 8-bit or install a compatible FFmpeg build.',
     '当前 FFmpeg 无法直接对 16 位 RGB 视频应用高斯模糊。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。'),
    ('This FFmpeg build cannot apply radial vignette directly to 16-bit RGB video. Choose H.264 8-bit or install a compatible FFmpeg build.',
     '当前 FFmpeg 无法直接对 16 位 RGB 视频应用径向暗角。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。'),
    ('Render timed out checking high-bit effect support.', '检查高位深特效支持时渲染超时。'),
    ('High-bit effect capability probe exceeded its output budget.', '高位深特效能力检查的输出超出安全限制。'),
])
def test_highbit_effect_preflight_errors_are_localized(source, chinese):
    set_locale('en')
    assert tr_error(ValueError(source)) == source
    set_locale('zh-CN')
    assert tr_error(ValueError(source)) == chinese


def test_highbit_effect_preflight_static_errors_have_catalog_entries():
    from lumen.highbit_effects import HIGHBIT_EFFECT_ERROR, UNSUPPORTED_EFFECT_ERROR, VIGNETTE_ERROR
    from lumen.i18n_catalog_errors import ZH_CN
    path = Path(__file__).resolve().parents[1] / 'lumen' / 'highbit_effects.py'
    sources = {HIGHBIT_EFFECT_ERROR, UNSUPPORTED_EFFECT_ERROR, VIGNETTE_ERROR}
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
            argument = node.exc.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                sources.add(argument.value)
    assert sources <= ZH_CN.keys()


def test_active_ui_copy_has_no_blanket_highbit_effect_rejection():
    root = Path(__file__).resolve().parents[1]
    stale = ('does not yet support active clip effects',
             'Animated sticker packs and active clip effects remain blocked',
             '暂不支持生效中的片段特效',
             'supports Gaussian blur only',
             'active vignette and other unverified clip effects remain blocked',
             'Active vignette and other unverified clip effects are blocked',
             '生效中的暗角和其他未经验证的片段特效')
    for relative in ('lumen/ui.py', 'lumen/ui_color_export.py',
                     'lumen/i18n_catalog_color_foundation.py'):
        source = (root / relative).read_text(encoding='utf-8')
        assert not any(text in source for text in stale), relative
