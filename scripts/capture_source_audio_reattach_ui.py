"""Actual explicit-pair reattachment dialog, bilingual geometry and keyboard gate.

Only an identity-byte synthetic source is used. This proves Qt/worker behavior,
not decoding or native Windows/macOS or screen-reader support.
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
from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.ui import MainWindow
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project

out = args.output; out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
font_fixture_registered = False
if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
    from scripts.provision_test_font import verify_fixture
    verified = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
    font_fixture_registered = QFontDatabase.addApplicationFont(verified['font_file']) >= 0
    assert font_fixture_registered
root = Path(__file__).resolve().parents[1]
identity_files = ('lumen/ui.py', 'lumen/ui_source_audio_reattach.py', 'lumen/source_audio_reattach.py',
    'lumen/i18n_catalog_source_audio_reattach.py', 'lumen/model.py', 'lumen/shortcuts.py',
    'lumen/ui_source_audio_separation.py', 'scripts/capture_source_audio_reattach_ui.py')
def source_identity():
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in identity_files}
source_start = source_identity(); records = []


def snapshot(w):
    return (w.project.to_dict(), len(w.editor._undo), len(w.editor._redo), w.editor.revision)


def settle(w):
    end = time.monotonic() + 10
    while w.jobs and time.monotonic() < end:
        app.processEvents(); time.sleep(.005)
    for _ in range(5): app.processEvents()
    assert not w.jobs


def pair(w):
    w.active_selection_type = 'clip'; w.set_selected_clip_ids(('video-keep', 'audio-delete'))
    w.refresh_inspector(); w.update_playback_controls()


def fit(d, size):
    d.setMaximumSize(16777215, 16777215); d.resize(630, 530)
    d.fit_available_screen(QRect(0, 0, *size))
    for _ in range(5): app.processEvents()
    assert d.scroll.horizontalScrollBar().maximum() == 0
    for label in (d.scope, d.source, d.limits, d.check_note, d.impact, d.status):
        assert label.wordWrap() and label.width() <= d.scroll.viewport().width()
        assert label.height() >= label.heightForWidth(label.width()), (label.text(), label.height())
    for button in (d.preview_button, d.apply_button, d.cancel_button):
        assert button.isVisible() and button.accessibleName() and button.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert d.rect().contains(button.mapTo(d, button.rect().topLeft()))
        assert d.rect().contains(button.mapTo(d, button.rect().bottomRight()))
        assert button.fontMetrics().horizontalAdvance(button.text()) <= button.width() - 8


def shot(widget, tag, stage):
    path = f'{tag}-{stage}.png'; assert widget.grab().save(str(out / path)); return path


def keyboard(d, button):
    QApplication.setActiveWindow(d); d.activateWindow()
    button.setFocus(Qt.FocusReason.TabFocusReason); app.processEvents(); assert button.hasFocus()
    QTest.keyClick(button, Qt.Key.Key_Space); app.processEvents()


with tempfile.TemporaryDirectory(prefix='cutcat-reattach-ui-capture-') as raw:
    temp = Path(raw); source = temp / 'identity-av.mov'; source.write_bytes(b'Synthetic AV identity bytes only' * 100)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for locale in ('en', 'zh-CN'):
        for mode in ('simple', 'professional'):
            for geometry, size in (('normal', (1024, 800)), ('narrow', (420, 600))):
                tag = f'{locale}-{mode}-{geometry}-dpi{args.scale:g}'
                settings = QSettings(str(temp / f'{tag}.ini'), QSettings.Format.IniFormat); settings.setValue('mode', mode)
                w = MainWindow(workspace_settings=settings, ui_language=locale, recovery_root=temp / tag)
                w.editor = Editor(Project(media=[Media('shared-media', str(source), 'Synthetic shared source', 4, 64, 48, True, True)], clips=[
                    Clip('video-keep', 'shared-media', 0, 5, 12, 30, source_audio_enabled=False),
                    Clip('audio-delete', 'shared-media', 2, 5, 12, 30, gain_db=-6, muted=True, fade_in=2, fade_out=3,
                         audio_mix_anchor=AudioMixAnchor(0, 5, 'video-keep')),
                    Clip('other-audio-retain', 'shared-media', 3, 5, 12, 30)]))
                w.setFont(QFont('Noto Sans CJK SC', 10)); w.resize(1100, 760); w.refresh(); pair(w)
                w.show(); w.activateWindow(); QApplication.setActiveWindow(w); w.timeline.setFocus(); app.processEvents()
                before = snapshot(w); d = w.open_source_audio_reattach(); assert d is not None
                d.setFont(QFont('Noto Sans CJK SC', 10)); fit(d, size)
                assert d.preview_button.isEnabled() and not d.apply_button.isEnabled()
                assert 'video-keep' in d.source.text() and 'audio-delete' in d.source.text()
                d.scroll.ensureWidgetVisible(d.source); app.processEvents()
                paths = [shot(d, tag, 'explicit-keep-delete-pair')]
                keyboard(d, d.preview_button); settle(w)
                assert d.preview_receipt is not None and snapshot(w) == before
                reached = set()
                for _ in range(20):
                    reached.add(app.focusWidget()); QTest.keyClick(app.focusWidget() or d, Qt.Key.Key_Tab); app.processEvents()
                assert {d.preview_button, d.apply_button, d.cancel_button} <= reached
                fit(d, size); d.scroll.ensureWidgetVisible(d.impact); app.processEvents()
                paths.append(shot(d, tag, 'verified-delete-only-selected-audio'))
                logical_size = [d.width(), d.height()]
                keyboard(d, d.apply_button); settle(w)
                assert d._closed_flag and [c.id for c in w.project.clips] == ['video-keep', 'other-audio-retain']
                assert w.project.clips[0].source_audio_enabled and w.project.clips[0].muted
                assert len(w.editor._undo) == before[1] + 1 and w.selected_clip_ids == ('video-keep',)
                w.side_tabs.setCurrentWidget(w.inspector); w.inspector_tabs.setCurrentIndex(2); app.processEvents()
                panel = w.inspector_tabs.widget(2); w.inspector.ensureWidgetVisible(w.source_audio_status); app.processEvents()
                paths.append(shot(panel, tag, 'retained-video-audio-status'))
                after = snapshot(w); w.undo(); assert w.project.to_dict() == before[0]
                w.redo(); assert w.project.to_dict() == after[0]; w.undo(); pair(w)
                d = w.open_source_audio_reattach(); fit(d, size)
                keyboard(d, d.preview_button); settle(w)
                w.editor.set_track_edit_lock(2, True); w.changed(); d.check_current(); app.processEvents()
                assert d._stale and d.preview_receipt is None and not d.apply_button.isEnabled()
                d.scroll.ensureWidgetVisible(d.status); app.processEvents()
                if geometry == 'narrow': paths.append(shot(d, tag, 'lock-invalidates-preview'))
                expected = snapshot(w); QTest.keyClick(d, Qt.Key.Key_Escape); app.processEvents()
                assert d._closed_flag and snapshot(w) == expected
                pair(w); action = w.shortcuts.actions['edit.reattach_audio']
                assert not action.isEnabled() and action.toolTip()
                w.editor.set_track_edit_lock(2, False); w.changed(); pair(w)
                d = w.open_source_audio_reattach(); keyboard(d, d.preview_button); settle(w)
                expected = snapshot(w); QTest.keyClick(d, Qt.Key.Key_Escape); app.processEvents()
                assert d._closed_flag and snapshot(w) == expected
                assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
                records.append({'tag': tag, 'locale': locale, 'mode': mode, 'geometry': geometry,
                    'device_pixel_ratio': d.devicePixelRatioF(), 'logical_size': logical_size,
                    'horizontal_scroll': 0, 'footer_visible': True, 'keyboard_preview_apply_cancel': True,
                    'explicit_two_ids': True, 'other_candidate_retained': True, 'muted_transfer_preserved': True,
                    'preview_project_unchanged': True, 'single_atomic_apply_undo_redo': True,
                    'lock_stale_disabled': True, 'cancel_project_unchanged': True, 'source_unchanged': True,
                    'paths': paths})
                w.closing = True; w.dirty = False; w.close(); app.processEvents(); w.deleteLater(); app.processEvents()
assert source_identity() == source_start, 'Source changed while capturing UI evidence'
report = {'status': 'passed', 'source_files_sha256': source_start,
    'font_fixture_registered': font_fixture_registered, 'native_windows_macos_validation': False,
    'identity_only_fixture': True, 'records': records,
    'screenshot_bytes': sum(p.stat().st_size for p in out.glob('*.png'))}
assert report['screenshot_bytes'] < 7 * 1024 * 1024
(out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'status': 'passed', 'screenshots': sum(len(r['paths']) for r in records),
    'scale': args.scale, 'screenshot_bytes': report['screenshot_bytes']}))
