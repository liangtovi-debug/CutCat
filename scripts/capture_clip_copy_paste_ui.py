"""Actual offscreen clip Paste geometry at an explicit per-process DPI.

Synthetic fixture + private dependency check; not native Windows/macOS or
screen-reader validation. No renderer/audio compiler is invoked.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float, default=1)
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QRect, QPoint
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.ui import MainWindow
from lumen.model import Project, Editor, Clip, Media
from lumen.i18n import set_locale

out = args.output; out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
font_fixture = None
if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
    from scripts.provision_test_font import verify_fixture, FONT_FAMILY
    verified = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
    font_id = QFontDatabase.addApplicationFont(verified['font_file'])
    assert font_id >= 0
    families = QFontDatabase.applicationFontFamilies(font_id)
    assert FONT_FAMILY in families
    font_fixture = {'font_sha256': verified['font_sha256'], 'families': families}
records = []
with tempfile.TemporaryDirectory(prefix='cutcat-copy-ui-') as raw:
    temp = Path(raw); source = temp / 'fixture.wav'; source.write_bytes(b'Identity-only synthetic fixture')
    for locale in ('en', 'zh-CN'):
        for mode in ('simple', 'professional'):
            for font_size in (10, 15):
                tag = f'{locale}-{mode}-font{font_size}-dpi{args.scale:g}'
                settings = QSettings(str(temp / f'{tag}.ini'), QSettings.Format.IniFormat)
                settings.setValue('mode', mode)
                w = MainWindow(workspace_settings=settings, ui_language=locale, recovery_root=temp / tag)
                w.editor = Editor(Project(media=[Media('m', str(source), 'Synthetic copied media', 3, has_video=False, has_audio=True)],
                    clips=[Clip('a', 'm', 2, 0, 0, 30), Clip('b', 'm', 3, 15, 0, 30)]))
                w.editor.group_clips(('a', 'b')); w.refresh(); w.select_clip('a')
                w.setFont(QFont('Noto Sans CJK SC', font_size)); w.show(); w.activateWindow()
                QApplication.setActiveWindow(w); w.timeline.setFocus(); app.processEvents()
                w.copy_selected_clips()
                until = time.monotonic() + 10
                while w.jobs and time.monotonic() < until:
                    app.processEvents(); time.sleep(.005)
                assert w._clip_clipboard and not w.jobs
                before = w.project.to_dict(); undo = len(w.editor._undo)
                d = w.paste_copied_clips(); assert d is not None
                d.destination_edit.setText('90'); d.setFont(QFont('Noto Sans CJK SC', font_size))
                d.fit_available_screen(QRect(0, 0, 392, 540))
                for _ in range(5): app.processEvents()
                assert d.width() <= 360 and d.height() <= 476
                assert d.scroll.horizontalScrollBar().maximum() == 0
                assert d.paste_button.isEnabled()
                footer_image = f'{tag}-footer.png'
                d.grab().save(str(out / footer_image))
                footer_rects = []
                footer = []
                for name, button in (('paste', d.paste_button), ('cancel', d.cancel_button)):
                    rect = QRect(button.mapTo(d, button.rect().topLeft()), button.size())
                    footer_rects.append(rect)
                    info = QFontInfo(button.font())
                    metrics = {'name': name, 'text': button.text(),
                        'text_width': button.fontMetrics().horizontalAdvance(button.text()),
                        'width': button.width(), 'height': button.height(),
                        'font_points': button.font().pointSizeF(), 'tag': tag,
                        'resolved_family': info.family(), 'resolved_pixels': info.pixelSize(),
                        'geometry': [button.x(), button.y(), button.width(), button.height()]}
                    with (out / 'footer-metrics.jsonl').open('a', encoding='utf-8') as evidence:
                        evidence.write(json.dumps(metrics, ensure_ascii=False) + '\n')
                    assert d.rect().contains(rect), metrics
                    assert button.visibleRegion().contains(button.rect()), metrics
                    if font_fixture is not None:
                        assert info.family() == FONT_FAMILY and info.pixelSize() > 0, metrics
                        assert all(button.fontMetrics().inFontUcs4(ord(c)) for c in button.text()), metrics
                    footer.append(metrics)
                    assert metrics['text_width'] <= button.width() - 8, metrics
                assert not footer_rects[0].intersects(footer_rects[1])
                for label in (d.scope, d.dependencies, d.private_note, d.impact, d.status):
                    assert label.wordWrap()
                    assert label.width() <= d.scroll.viewport().width()
                d.scroll.verticalScrollBar().setValue(0); app.processEvents()
                scope = f'{tag}-scope.png'; d.grab().save(str(out / scope))
                d.scroll.ensureWidgetVisible(d.destination_edit); app.processEvents()
                assert d.scroll.viewport().rect().contains(d.destination_edit.mapTo(d.scroll.viewport(), d.destination_edit.rect().center()))
                d.scroll.ensureWidgetVisible(d.capture_button, 0, 0); app.processEvents()
                assert d.scroll.viewport().rect().contains(d.capture_button.mapTo(d.scroll.viewport(), d.capture_button.rect().center()))
                top = f'{tag}-target.png'; d.grab().save(str(out / top))
                d.scroll.ensureWidgetVisible(d.status); app.processEvents()
                bottom = f'{tag}-confirmation.png'; d.grab().save(str(out / bottom))
                assert w.project.to_dict() == before and len(w.editor._undo) == undo
                records.append({'tag': tag, 'locale': locale, 'mode': mode, 'font_size': font_size,
                    'device_pixel_ratio': d.devicePixelRatioF(), 'logical_size': [d.width(), d.height()],
                    'horizontal_scroll': d.scroll.horizontalScrollBar().maximum(), 'footer_visible': True,
                    'project_unchanged': True, 'footer': footer, 'paths': [footer_image, scope, top, bottom]})
                d.reject(); w.closing = True; w.dirty = False; w.close(); app.processEvents(); w.deleteLater(); app.processEvents()
(out / 'report.json').write_text(json.dumps({'status':'passed', 'native_windows_macos_validation':False,
    'font_fixture': font_fixture, 'records':records}, indent=2), encoding='utf-8')
print(json.dumps({'status':'passed', 'screenshots':len(records)*4, 'scale':args.scale}))
