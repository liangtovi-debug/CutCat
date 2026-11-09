"""Real offscreen screen bounds and input events, not native desktop proof."""
import argparse
import json
import os
from pathlib import Path
import sys


def run(output, scale, width, height):
    output.mkdir(parents=True, exist_ok=True)
    config = output / 'screen.json'
    config.write_text(json.dumps({'screens': [{'name': 'Help test', 'width': round(width * scale), 'height': round(height * scale)}]}))
    os.environ['QT_QPA_PLATFORM'] = f'offscreen:configfile={config.resolve()}'
    os.environ['QT_SCALE_FACTOR'] = str(scale)
    os.environ['XDG_CACHE_HOME'] = str(output / 'cache')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QTextLayout, QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from lumen.i18n import set_locale
    from lumen.ui_help import HelpDialog, help_text
    app = QApplication([])
    records = []
    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        for font_size in (10, 16, 20):
            font = QFont('Noto Sans CJK SC', font_size)
            probe = QTextLayout('剪辑猫 CutCat 0123456789', font)
            probe.beginLayout(); probe.createLine().setLineWidth(1000); probe.endLayout()
            assert probe.glyphRuns() and all(i != 0 for r in probe.glyphRuns() for i in r.glyphIndexes())
            app.setFont(font)
            d = HelpDialog(); d.show(); QTest.qWait(30)
            screen = d.screen().availableGeometry()
            assert screen.width() == width and screen.height() == height
            assert d.devicePixelRatioF() == scale
            assert screen.contains(d.frameGeometry()), (screen, d.frameGeometry())
            assert d.close_button.visibleRegion().boundingRect() == d.close_button.rect()
            assert d.close_button.height() >= d.close_button.sizeHint().height()
            assert d.body.toPlainText() == help_text()
            assert d.body.horizontalScrollBar().maximum() == 0
            assert d.body.font().pointSize() == font_size
            top = f'{locale}-{font_size}-top.png'; bottom = f'{locale}-{font_size}-bottom.png'
            assert d.grab().save(str(output / top))
            d.body.setFocus(); QTest.keyClick(d.body, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier); app.processEvents()
            assert d.body.textCursor().atEnd()
            assert d.body.cursorRect().bottom() <= d.body.viewport().height()
            d.body.verticalScrollBar().setValue(d.body.verticalScrollBar().maximum())
            assert d.grab().save(str(output / bottom))
            QTest.keyClick(d.body, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            assert d.body.textCursor().selectedText().replace('\u2029', '\n') == help_text()
            QTest.keyClicks(d.body, 'cannot edit')
            assert d.body.toPlainText() == help_text()
            QTest.keyClick(d.body, Qt.Key.Key_Tab); app.processEvents()
            assert app.focusWidget() is d.close_button
            QTest.keyClick(d.close_button, Qt.Key.Key_Backtab); app.processEvents()
            assert app.focusWidget() is d.body
            QTest.keyClick(d.body, Qt.Key.Key_Return); app.processEvents(); assert not d.isVisible()
            d.show(); app.processEvents(); QTest.keyClick(d.body, Qt.Key.Key_Escape); assert not d.isVisible()
            d.show(); app.processEvents(); QTest.mouseClick(d.close_button, Qt.MouseButton.LeftButton); assert not d.isVisible()
            records.append(dict(locale=locale,font_size=font_size,screen=[width,height],size=[d.width(),d.height()],frame_inside_screen=True,dpr=scale,keyboard=True,selectable_readonly=True,horizontal_scroll=0,top=top,bottom=bottom))
            d.deleteLater(); app.processEvents()
    (output / 'report.json').write_text(json.dumps(dict(status='passed',native_validation=False,records=records),indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--scale',type=float,required=True);p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True);a=p.parse_args();run(a.output,a.scale,a.width,a.height)
