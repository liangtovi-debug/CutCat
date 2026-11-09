"""Actual bilingual separation UI geometry, source-worker and keyboard gate.

Identity-only synthetic AV fixture; no decoding, extracted asset or native
Windows/macOS/screen-reader validation is claimed. Each process owns one DPI.
"""
from pathlib import Path
import argparse
import hashlib
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

from PySide6.QtCore import QSettings, QRect, QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.ui import MainWindow
from lumen.model import Project, Editor, Clip, Media

out = args.output; out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
font_fixture_registered = False
if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
    from scripts.provision_test_font import verify_fixture
    verified = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
    font_fixture_registered = QFontDatabase.addApplicationFont(verified['font_file']) >= 0
    assert font_fixture_registered
source_root = Path(__file__).resolve().parents[1]
identity_files = ('lumen/ui.py', 'lumen/ui_source_audio_separation.py',
    'lumen/source_audio_separation.py', 'lumen/i18n_catalog_source_audio_separation.py',
    'lumen/model.py', 'lumen/audio_components.py', 'lumen/ui_interval_preview.py',
    'scripts/capture_source_audio_separation_ui.py')
def source_identity():
    return {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in identity_files}
source_start = source_identity()
records = []


def settle(window):
    end = time.monotonic() + 10
    while window.jobs and time.monotonic() < end:
        app.processEvents(); time.sleep(.005)
    for _ in range(5): app.processEvents()
    assert not window.jobs, 'A separation source worker did not terminate'


def snapshot(window):
    return (window.project.to_dict(), len(window.editor._undo), len(window.editor._redo), window.editor.revision)


def fit(dialog, size):
    dialog.setMaximumSize(16777215, 16777215); dialog.resize(630, 530)
    dialog.fit_available_screen(QRect(0, 0, *size))
    for _ in range(5): app.processEvents()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    for label in (dialog.scope, dialog.source, dialog.track_label, dialog.limits,
                  dialog.check_note, dialog.impact, dialog.status):
        assert label.wordWrap() and label.width() <= dialog.scroll.viewport().width()
        assert label.height() >= label.heightForWidth(label.width()), (label.text(), label.height())
    for button in (dialog.preview_button, dialog.apply_button, dialog.cancel_button):
        assert button.isVisible()
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        if button.fontMetrics().horizontalAdvance(button.text()) > button.width() - 8:
            dialog.grab().save(str(out / f'FAIL-{tag}-footer.png'))
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.width() - 8, (
            button.text(), button.width(), button.fontMetrics().horizontalAdvance(button.text()))
        assert button.accessibleName() and button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert dialog.track_label.buddy() is dialog.track and dialog.track.accessibleName()


def shot(widget, tag, state, rect=None):
    name = f'{tag}-{state}.png'
    pixmap = widget.grab() if rect is None else widget.grab(rect)
    assert pixmap.save(str(out / name))
    return name


def keyboard_click(dialog, button):
    QApplication.setActiveWindow(dialog); dialog.activateWindow()
    button.setFocus(Qt.FocusReason.TabFocusReason); app.processEvents()
    assert button.hasFocus(), button.text()
    QTest.keyClick(button, Qt.Key.Key_Space); app.processEvents()


with tempfile.TemporaryDirectory(prefix='cutcat-separation-ui-') as raw:
    temp = Path(raw); source = temp / 'fixture.mov'
    source.write_bytes(b'Identity-only synthetic embedded AV fixture' * 100)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for locale in ('en', 'zh-CN'):
        for mode in ('simple', 'professional'):
            for geometry, size in (('normal', (1024, 800)), ('narrow', (420, 600))):
                tag = f'{locale}-{mode}-{geometry}-dpi{args.scale:g}'
                settings = QSettings(str(temp / f'{tag}.ini'), QSettings.Format.IniFormat)
                settings.setValue('mode', mode)
                w = MainWindow(workspace_settings=settings, ui_language=locale, recovery_root=temp / tag)
                w.editor = Editor(Project(media=[Media('av', str(source), 'Synthetic AV source', 3, 64, 48, True, True)],
                    clips=[Clip('v0', 'av', 0, 5, 12, 30, gain_db=-6, fade_in=2, fade_out=3)]))
                w.setFont(QFont('Noto Sans CJK SC', 10)); w.resize(1100, 760)
                w.refresh(); w.select_clip('v0'); w.show(); w.activateWindow()
                QApplication.setActiveWindow(w); w.timeline.setFocus(); app.processEvents()
                before = snapshot(w)
                d = w.open_source_audio_separation(); assert d is not None
                d.setFont(QFont('Noto Sans CJK SC', 10)); fit(d, size)
                assert not d.preview_button.isEnabled() and not d.apply_button.isEnabled()
                QApplication.setActiveWindow(d); d.activateWindow(); d.track.setFocus()
                QTest.keyClick(d.track, Qt.Key.Key_End); app.processEvents()
                assert d.track.currentData() == 3 and d.preview_button.isEnabled()
                # Real keyboard tab chain, not focusPolicy alone.
                reached = set()
                for _ in range(20):
                    reached.add(app.focusWidget())
                    QTest.keyClick(app.focusWidget() or d, Qt.Key.Key_Tab); app.processEvents()
                assert {d.track, d.preview_button, d.cancel_button} <= reached
                d.scroll.ensureWidgetVisible(d.track); app.processEvents()
                paths = [shot(d, tag, 'draft-explicit-A1')]
                keyboard_click(d, d.preview_button); settle(w)
                assert d.preview_receipt is not None and d.apply_button.isEnabled()
                assert snapshot(w) == before
                reached = set()
                for _ in range(20):
                    reached.add(app.focusWidget())
                    QTest.keyClick(app.focusWidget() or d, Qt.Key.Key_Tab); app.processEvents()
                assert {d.preview_button, d.apply_button, d.cancel_button} <= reached
                d.scroll.ensureWidgetVisible(d.status); app.processEvents()
                paths.append(shot(d, tag, 'verified-preview'))
                fit(d, size)
                verified_size = [d.width(), d.height()]
                keyboard_click(d, d.apply_button); settle(w)
                assert d._closed_flag and len(w.project.clips) == 2
                assert len(w.editor._undo) == before[1] + 1
                audio_id = w.project.clips[-1].id
                w.side_tabs.setCurrentWidget(w.inspector); w.inspector_tabs.setCurrentIndex(2)
                audio_panel = w.inspector_tabs.widget(2)
                for selected, state, phrase in (('v0', 'suppressed-video',
                    'stays silent' if locale == 'en' else '静音'), (audio_id, 'independent-audio',
                    'Independent source audio' if locale == 'en' else '独立')):
                    w.select_clip(selected); w.refresh_inspector(); app.processEvents()
                    assert phrase in w.source_audio_status.text(), w.source_audio_status.text()
                    assert w.source_audio_status.isVisible() and w.source_audio_status.wordWrap()
                    assert w.source_audio_status.height() >= w.source_audio_status.heightForWidth(w.source_audio_status.width())
                    w.inspector.ensureWidgetVisible(w.source_audio_status); app.processEvents()
                    bottom = w.fade_out.mapTo(audio_panel, QPoint(0, w.fade_out.height())).y() + 16
                    paths.append(shot(audio_panel, tag, state, QRect(0, 0, audio_panel.width(), bottom)))
                assert not w.project.clips[0].source_audio_enabled
                w.undo(); w.select_clip('v0'); app.processEvents()
                assert w.project.to_dict() == before[0]
                # A locked explicit destination has a real visible rejection.
                w.editor.set_track_edit_lock(3, True); w.refresh()
                d = w.open_source_audio_separation(); d.track.setCurrentIndex(2); fit(d, size)
                assert d.plan is None and not d.preview_button.isEnabled()
                assert d._last_error
                d.scroll.ensureWidgetVisible(d.impact); app.processEvents()
                if geometry == 'narrow': paths.append(shot(d, tag, 'locked-destination'))
                d.reject(); w.editor.set_track_edit_lock(3, False); w.refresh(); w.select_clip('v0')
                d = w.open_source_audio_separation(); d.track.setCurrentIndex(2); fit(d, size)
                keyboard_click(d, d.preview_button); settle(w)
                assert d.preview_receipt is not None
                w.editor.update_clip('v0', gain_db=-7); w.refresh(); d.check_current(); app.processEvents()
                assert d._stale and not d.apply_button.isEnabled() and d.preview_receipt is None
                d.scroll.ensureWidgetVisible(d.status); app.processEvents()
                if geometry == 'narrow': paths.append(shot(d, tag, 'stale-preview'))
                stale_state = snapshot(w)
                QTest.keyClick(d, Qt.Key.Key_Escape); app.processEvents()
                assert d._closed_flag and snapshot(w) == stale_state
                # Cancel a fresh, successfully verified preview by keyboard.
                w.select_clip('v0'); d = w.open_source_audio_separation(); d.track.setCurrentIndex(2)
                keyboard_click(d, d.preview_button); settle(w); cancel_state = snapshot(w)
                assert d.preview_receipt is not None
                QTest.keyClick(d, Qt.Key.Key_Escape); app.processEvents()
                assert d._closed_flag and snapshot(w) == cancel_state
                assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
                records.append({'tag': tag, 'locale': locale, 'mode': mode, 'geometry': geometry,
                    'device_pixel_ratio': d.devicePixelRatioF(), 'logical_size': verified_size,
                    'horizontal_scroll': d.scroll.horizontalScrollBar().maximum(),
                    'footer_visible': True, 'keyboard_target_preview_apply_cancel': True,
                    'preview_project_unchanged': True, 'single_atomic_apply': True,
                    'stale_and_locked_apply_disabled': True, 'cancel_project_unchanged': True,
                    'source_unchanged': True, 'paths': paths})
                w.closing = True; w.dirty = False; w.close(); app.processEvents(); w.deleteLater(); app.processEvents()
assert source_identity() == source_start, 'UI source changed while capturing evidence'
report = {'status': 'passed', 'source_files_sha256': source_start,
          'font_fixture_registered': font_fixture_registered, 'native_windows_macos_validation': False,
          'identity_only_fixture': True, 'records': records,
          'screenshot_bytes': sum(p.stat().st_size for p in out.glob('*.png'))}
assert report['screenshot_bytes'] < 7 * 1024 * 1024
(out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'status': 'passed', 'screenshots': sum(len(r['paths']) for r in records),
                  'scale': args.scale, 'screenshot_bytes': report['screenshot_bytes']}))
