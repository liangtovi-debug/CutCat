"""Independent real-Qt matte-level transactions, pixels, and localized layouts."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import numpy as np
import pytest
from PySide6.QtCore import Qt, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QGroupBox
from lumen.i18n import set_locale, tr, tr_error
from lumen.qualifier import QualifierSettings, qualifier_mask
from test_qualifier_ui import app, loaded, window
from test_qualifier_detail_ui import install_source, zoom
from test_qualifier_overlay_ui import overlay_reference


def focus(widget):
    widget.setFocus(); QTest.qWait(70)
    assert widget.hasFocus()
    assert widget.visibleRegion().boundingRect() == widget.rect()


def levels(dialog, black, white):
    dialog.controls['matte_black'].setValue(black)
    dialog.controls['matte_white'].setValue(white)
    dialog.render_preview()


def test_matte_cancel_noop_reset_and_single_apply_undo_redo(window, app):
    before = deepcopy(window.project)
    dialog = loaded(window)
    levels(dialog, 20, 80)
    QTest.mouseClick(dialog.close_button, Qt.MouseButton.LeftButton)
    assert window.project == before and not window.editor.can_undo
    assert dialog.snapshot is None and dialog.matte is None and dialog.canvas.image.isNull()
    dialog = loaded(window)
    levels(dialog, 20, 80)
    focus(dialog.reset_matte_button)
    QTest.mouseClick(dialog.reset_matte_button, Qt.MouseButton.LeftButton)
    assert (dialog.controls['matte_black'].value(), dialog.controls['matte_white'].value()) == (0, 100)
    assert dialog.settings == window.selected_clip().grade.qualifier
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert window.project == before and not window.editor.can_undo
    dialog = loaded(window); levels(dialog, 20, 80)
    expected = deepcopy(dialog.settings)
    assert window.project == before
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert window.selected_clip().grade.qualifier == expected and len(window.editor._undo) == 1
    after = deepcopy(window.project)
    dialog = loaded(window)
    assert dialog.controls['matte_black'].value() == 20 and dialog.controls['matte_white'].value() == 80
    QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
    assert len(window.editor._undo) == 1
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after


@pytest.mark.parametrize('black,white', [(50,50),(80,20),(100,100),(0,0)])
@pytest.mark.parametrize('view', ['matte','overlay'])
def test_transient_invalid_disarms_clears_then_repair_and_reset(window, app, black, white, view):
    dialog = loaded(window)
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(view))
    QTest.mouseClick(dialog.pick_button, Qt.MouseButton.LeftButton)
    assert dialog.canvas.picking
    before = deepcopy(window.project)
    levels(dialog, black, white)
    assert not dialog.apply_button.isEnabled() and not dialog.pick_button.isEnabled()
    assert dialog.matte is None and dialog.canvas.image.isNull() and not dialog.canvas.picking
    assert 'strictly below' in dialog.status.text()
    invalid = deepcopy(dialog.settings)
    dialog.pick_sample(16,16); dialog.apply_settings()
    assert dialog.settings == invalid and window.project == before and not window.editor.can_undo
    levels(dialog, 10,90)
    assert dialog.apply_button.isEnabled() and dialog.pick_button.isEnabled()
    assert dialog.matte is not None and not dialog.canvas.image.isNull()
    focus(dialog.reset_matte_button)
    QTest.mouseClick(dialog.reset_matte_button, Qt.MouseButton.LeftButton)
    assert (dialog.settings.matte_black,dialog.settings.matte_white)==(0,1)
    assert not window.editor.can_undo


@pytest.mark.parametrize('change', ['revision','selection','frame','grade','dimensions'])
def test_changed_snapshot_rejects_matte_reset_apply_and_pick(window, change):
    dialog = loaded(window); levels(dialog,20,80)
    if change == 'revision': window.revision += 1
    elif change == 'selection': window.select_clip('other')
    elif change == 'frame': window.timeline.playhead += 1
    elif change == 'grade': window.selected_clip().grade.exposure = .3
    else: window.project.height += 2
    before=deepcopy(window.project); settings=deepcopy(dialog.settings)
    dialog.reset_matte_levels(); dialog.pick_sample(16,16); dialog.apply_settings()
    assert dialog.settings == settings and window.project == before
    assert not dialog.reset_matte_button.isEnabled() and not dialog.apply_button.isEnabled()
    assert not dialog.pick_button.isEnabled() and not window.editor.can_undo


def test_live_remap_matte_and_overlay_have_independent_exact_pixels(window):
    dialog=loaded(window)
    pixels=np.array([[[.5,0,0],[0,.5,0],[.5,.375,0]]],np.float32); pixels.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot,frame=pixels,valid_mask=np.ones((1,3),bool)))
    dialog.settings=QualifierSettings(hue_width=.1,hue_softness=.05)
    levels(dialog,25,100)
    np.testing.assert_allclose(dialog.matte,[[1,0,1/3]],atol=1e-6)
    assert dialog.canvas.image.pixelColor(2,0).red()==85
    dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData('overlay'))
    for x,w in enumerate([1,0,1/3]):
        assert dialog.canvas.image.pixelColor(x,0).getRgb()[:3]==overlay_reference(pixels[0,x],w)
    assert dialog.snapshot.frame is pixels and not pixels.flags.writeable


def test_full_resolution_crop_uses_global_window_coordinates_after_remap(window,app):
    dialog=loaded(window); frame,_=install_source(dialog,(1080,1920),(997,541))
    dialog.settings=QualifierSettings(hue_width=.1,hue_softness=.2,matte_black=.1,matte_white=.8,
        window_shape='ellipse',window_center_x=.53,window_center_y=.51,window_width=.37,window_height=.42,window_feather=.4)
    full=qualifier_mask(frame,replace(dialog.settings,enabled=True))
    zoom(dialog,8); dialog.set_detail_center(997,541); app.processEvents()
    for x,y in [(0,0),(10,10),(dialog.preview_shape[1]//2,dialog.preview_shape[0]//2)]:
        sx,sy=dialog.viewport.source_at(x,y)
        assert dialog.matte[y,x]==pytest.approx(full[sy,sx],abs=1e-6)
    assert dialog.snapshot.frame is frame and not window.editor.can_undo


@pytest.mark.parametrize('locale',['en','zh-CN'])
@pytest.mark.parametrize('width',[320,360,680])
def test_localized_matte_focus_text_and_screenshot(window,app,locale,width):
    set_locale(locale); dialog=loaded(window); dialog.resize(width,800); QTest.qWait(100)
    assert dialog.width()==width
    for key in ['matte_black','matte_white']:
        spin=dialog.controls[key]; focus(spin)
        label=dialog.control_labels[spin]
        assert label.visibleRegion().boundingRect()==label.rect()
        bounds=label.fontMetrics().boundingRect(QRect(0,0,label.width(),10000),int(Qt.TextFlag.TextWordWrap),label.text())
        assert bounds.height()<=label.height()
        assert spin.lineEdit().fontMetrics().horizontalAdvance(spin.text())<=spin.lineEdit().width()
    focus(dialog.reset_matte_button)
    assert dialog.reset_matte_button.fontMetrics().horizontalAdvance(dialog.reset_matte_button.text())+16<=dialog.reset_matte_button.width()
    assert dialog.scroll.horizontalScrollBar().maximum()==0
    group=dialog.reset_matte_button.parentWidget()
    assert isinstance(group,QGroupBox)
    assert group.fontMetrics().horizontalAdvance(group.title())+20<=group.width()
    note=next(label for label in group.findChildren(QLabel) if ('weighted 3×3' in label.text() or '3×3 加权' in label.text()))
    assert note.hasHeightForWidth() and note.height()>=note.heightForWidth(note.width())
    if locale=='zh-CN':
        assert '蒙版' in group.title() and '重置' in dialog.reset_matte_button.text()
        assert all('蒙版' in dialog.controls[k].accessibleName() for k in ['matte_black','matte_white'])
    directory=os.getenv('MATTE_SCREENSHOT_DIR')
    if directory:
        path=Path(directory);path.mkdir(parents=True,exist_ok=True)
        assert dialog.grab().save(str(path/f'matte-{locale}-{width}-dpi{os.getenv("QT_SCALE_FACTOR","1")}.png'))


@pytest.mark.parametrize('message',[
    'Matte black point must be strictly below white point',
    'Qualifier matte_black must be finite and in 0..1',
    'Qualifier matte_white must be finite and in 0..1',
])
def test_matte_errors_are_directly_localized(message):
    try:
        set_locale('zh-CN');translated=str(tr_error(message))
        assert translated!=message and '技术错误详情' not in translated
    finally: set_locale('en')


def test_all_new_matte_strings_have_direct_chinese_translation():
    import ast
    from lumen.ui_qualifier import __file__ as source
    tree=ast.parse(Path(source).read_text())
    messages=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            text=node.value
            if any(word in text for word in ['Matte refinement','Matte black','Matte white','Reset matte','Remap black/white','refined color-matte','then matte levels']):
                messages.append(text)
    assert len(messages)>=9
    try:
        set_locale('zh-CN')
        for text in messages:
            translated=str(tr(text))
            assert translated!=text, text
            assert any('\u4e00'<=ch<='\u9fff' for ch in translated),text
    finally: set_locale('en')


def test_keyboard_numeric_commit_does_not_apply_and_reset_preserves_other_fields(window,app):
    dialog=loaded(window)
    dialog.controls['exposure'].setValue(.25)
    before=deepcopy(window.project)
    for key,value in [('matte_black','60'),('matte_white','60')]:
        spin=dialog.controls[key]; focus(spin)
        spin.selectAll(); QTest.keyClicks(spin,value); QTest.keyClick(spin,Qt.Key.Key_Return)
    assert dialog.isVisible() and not dialog.apply_button.isEnabled()
    assert dialog.settings.matte_black==dialog.settings.matte_white==.6
    assert window.project==before and not window.editor.can_undo
    focus(dialog.reset_matte_button); QTest.keyClick(dialog.reset_matte_button,Qt.Key.Key_Space)
    assert (dialog.settings.matte_black,dialog.settings.matte_white)==(0,1)
    assert dialog.settings.exposure==.25 and dialog.apply_button.isEnabled()
    assert window.project==before and not window.editor.can_undo


def test_report_non_neutral_matte_overlay_and_feathered_window(window,app):
    import colorsys
    set_locale('zh-CN'); dialog=loaded(window); dialog.resize(680,900)
    row=np.asarray([colorsys.hsv_to_rgb((x/511*.45-.1)%1,.85,.85) for x in range(512)],np.float32)
    pixels=np.repeat(row[None,:,:],256,axis=0)
    pixels*=np.linspace(.35,1,256,dtype=np.float32)[:,None,None]
    pixels.setflags(write=False)
    dialog.accept_snapshot(replace(dialog.snapshot,frame=pixels,valid_mask=np.ones((256,512),bool)))
    dialog.controls['hue_width'].setValue(30)
    dialog.controls['hue_softness'].setValue(45)
    levels(dialog,20,80)
    dialog.window_shape.setCurrentIndex(dialog.window_shape.findData('ellipse'))
    for key,value in [('window_center_x',48),('window_width',60),('window_height',65),('window_feather',40)]:
        dialog.controls[key].setValue(value)
    dialog.render_preview();QTest.qWait(100)
    assert np.any((dialog.matte>0)&(dialog.matte<1)) and np.any(dialog.matte==0) and np.any(dialog.matte==1)
    before=deepcopy(window.project)
    for mode in ['matte','overlay']:
        dialog.preview_choice.setCurrentIndex(dialog.preview_choice.findData(mode))
        focus(dialog.reset_matte_button)
        directory=os.getenv('MATTE_SCREENSHOT_DIR')
        if directory:
            path=Path(directory);path.mkdir(parents=True,exist_ok=True)
            assert dialog.grab().save(str(path/f'report-zh-CN-680-black20-white80-feathered-window-{mode}.png'))
    assert window.project==before and not window.editor.can_undo
