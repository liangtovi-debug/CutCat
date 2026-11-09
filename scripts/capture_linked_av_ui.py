"""Fresh-process bilingual linked A/V dialog and role QA at a real Qt DPR.

The tiny source is an identity-only fixture, never a media-rendering oracle.
Screens are Qt offscreen evidence, not native Windows/macOS validation.
"""
from pathlib import Path
from copy import deepcopy
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float, default=1)
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QFont, QFontInfo, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import tr
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.ui import MainWindow

out = args.output
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
font = QFont('Noto Sans CJK SC', 10)
app.setFont(font)
files = ('lumen/ui.py', 'lumen/ui_linked_av.py', 'lumen/linked_av.py',
         'lumen/i18n_catalog_linked_av.py', 'scripts/capture_linked_av_ui.py')
identity = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}
source_before = identity()
records = []
warnings = []
QMessageBox.warning = lambda *values: warnings.append(str(values[2]))


def events():
    for _ in range(5):
        app.processEvents()


def snapshot(w):
    return deepcopy((w.project, w.editor._undo, w.editor._redo,
                     w.editor.revision, w.revision, tuple(w.selected_clip_ids), w.selection))


def activate(window, widget):
    window.show(); window.activateWindow(); QApplication.setActiveWindow(window)
    widget.setFocus(Qt.FocusReason.TabFocusReason); events()
    assert widget.hasFocus()


def keyboard(dialog, button):
    activate(dialog, button)
    QTest.keyClick(button, Qt.Key.Key_Space); events()


def settle(w):
    deadline = time.monotonic() + 10
    while w.jobs and time.monotonic() < deadline:
        events(); time.sleep(.005)
    events()
    assert not w.jobs


def shot(widget, name, rect=None):
    pixmap = widget.grab(rect) if rect is not None else widget.grab()
    assert pixmap.save(str(out / name))
    return name


for sample in ('CutCatABCxyz0123456789', '联合源滑移当前角色取消关联不会恢复音频'):
    layout = QTextLayout(sample, font)
    layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    runs = layout.glyphRuns()
    assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index != 0 for run in runs for index in run.glyphIndexes())

with tempfile.TemporaryDirectory(prefix='linked-av-ui-capture-') as raw:
    temp = Path(raw)
    source = temp / 'identity.mov'; source.write_bytes(b'Identity-only source; never decoded' * 100)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for locale in ('en', 'zh-CN'):
        for geometry, mode, size in (('normal', 'simple', (1024, 850)),
                                     ('narrow', 'professional', (420, 600))):
            tag = f'{locale}-{geometry}-{args.scale:g}'
            w = MainWindow(workspace_settings=QSettings(str(temp / f'{tag}.ini'), QSettings.Format.IniFormat),
                           ui_language=locale, recovery_root=temp / tag)
            w.set_workspace_mode(mode, persist=False); w.setFont(font)
            w.editor = Editor(Project(media=[Media('media-id', str(source), 'Synthetic A/V', 4, 64, 48, True, True)], clips=[
                Clip('video-id', 'media-id', 0, 5, 12, 30, source_audio_enabled=False),
                Clip('audio-id', 'media-id', 2, 5, 12, 30, gain_db=-6, fade_in=2, fade_out=3,
                     audio_mix_anchor=AudioMixAnchor(0, 5, 'video-id'))]))
            w.refresh(); w.active_selection_type = 'clip'
            w.set_selected_clip_ids(('video-id', 'audio-id'), anchor='video-id')
            w.refresh_inspector(); w.show(); events(); activate(w, w.timeline)
            unlinked = deepcopy(w.project)
            w.link_selected_av(); events()
            assert len(w.project.av_links) == 1 and w.project.clips == unlinked.clips
            assert set(w.selected_clip_ids) == {'video-id', 'audio-id'}
            # A role is explicit even though the pair stays selected.
            keyboard(w, w.linked_audio_button)
            assert w.selection == 'audio-id' and w.selected_parameter_clip().id == 'audio-id'
            old_video = deepcopy(w.editor._clip('video-id'))
            w.gain.setValue(-8)
            assert w.editor._clip('audio-id').gain_db == -8 and w.editor._clip('video-id') == old_video
            keyboard(w, w.linked_video_button)
            assert w.selection == 'video-id' and set(w.selected_clip_ids) == {'video-id', 'audio-id'}
            assert not w.apply_timing_button.isEnabled() and not w.split_clip_button.isEnabled()
            parent = w.linked_av_note.parentWidget()
            top = w.linked_av_note.geometry().topLeft()
            bottom = w.linked_slip_button.geometry().bottomRight()
            paths = [shot(parent, f'{tag}-roles.png', QRect(top, bottom))]

            activate(w, w.timeline); d = w.open_linked_av_slip(); events()
            assert d is not None
            d.setFont(font); d.setMaximumSize(16777215, 16777215); d.resize(630, 590)
            d.fit_available_screen(QRect(0, 0, *size)); events()
            assert d.devicePixelRatioF() == args.scale
            assert d.scroll.horizontalScrollBar().maximum() == 0
            for label in (d.scope, d.source, d.origin_label, d.limits, d.check_note, d.impact, d.status):
                assert label.wordWrap() and label.width() <= d.scroll.viewport().width()
                assert label.height() >= label.heightForWidth(label.width()), (label.text(), label.height(), label.heightForWidth(label.width()))
            for button in (d.preview_button, d.apply_button, d.cancel_button):
                assert button.isVisible() and button.accessibleName()
                assert d.rect().contains(button.mapTo(d, button.rect().bottomRight()))
                assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 8
            assert d.origin.accessibleName()
            before = snapshot(w); d.origin.setText('41/2'); events()
            assert snapshot(w) == before and d.preview_button.isEnabled() and not d.apply_button.isEnabled()
            keyboard(d, d.preview_button); settle(w)
            assert snapshot(w) == before and d.preview_receipt is not None and d.apply_button.isEnabled()
            d.scroll.verticalScrollBar().setValue(0); events()
            paths.append(shot(d, f'{tag}-top.png'))
            d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum()); events()
            paths.append(shot(d, f'{tag}-verified.png'))
            keyboard(d, d.apply_button); settle(w)
            assert d._closed_flag and str(w.editor._clip('video-id').source_in) == '41/2'
            assert w.editor._clip('video-id').timing == w.editor._clip('audio-id').timing
            assert len(w.editor._undo) == len(before[1]) + 1
            slipped = deepcopy(w.project); w.undo(); assert w.project == before[0]
            w.redo(); assert w.project == slipped
            activate(w, w.timeline); d2 = w.open_linked_av_slip(); d2.origin.setText('23')
            before_cancel = snapshot(w); keyboard(d2, d2.cancel_button)
            assert d2._closed_flag and snapshot(w) == before_cancel
            activate(w, w.timeline); w.unlink_selected_av(); events()
            assert not w.project.av_links and w.project.clips == slipped.clips
            assert not w.editor._clip('video-id').source_audio_enabled
            assert not warnings and not w.jobs
            records.append({'locale': locale, 'geometry': geometry, 'mode': mode,
                'logical_size': [d.width(), d.height()], 'device_pixel_ratio': d.devicePixelRatioF(),
                'horizontal_scroll': d.scroll.horizontalScrollBar().maximum(),
                'explicit_role_parameter_edit': True, 'pair_selection': True, 'exact_rational_input': True,
                'keyboard_preview_apply_undo_redo': True, 'draft_cancel_no_commit': True,
                'link_unlink_metadata_only': True, 'footer_visible': True, 'accessible_controls': True,
                'source_unchanged': hashlib.sha256(source.read_bytes()).hexdigest() == digest, 'paths': paths})
            w.dirty = False; w.closing = True; w.close(); events(); w.deleteLater(); events()

assert identity() == source_before
report = {'status': 'passed', 'identity_only_fixture': True, 'native_windows_macos_validation': False,
          'font_family': QFontInfo(font).family(), 'source_identity': source_before,
          'records': records, 'screenshot_bytes': sum(path.stat().st_size for path in out.glob('*.png'))}
(out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'status': 'passed', 'records': len(records), 'bytes': report['screenshot_bytes']}))
