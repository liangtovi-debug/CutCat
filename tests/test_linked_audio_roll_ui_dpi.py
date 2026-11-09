"""Fresh-process real-DPR EN/ZH audio-roll dialog geometry, not native QA.

Each subprocess retains four small ready-state PNGs plus a compact report.
The identity-only source is never decoded; no media worker or renderer runs.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


# Kept here rather than adding a second production/script entry point. Parent
# pytest owns process lifetime and retains its tmp_path on any failed assertion.
DRIVER = r'''
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

root, out, scale = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
sys.path.insert(0, str(root))
os.environ['QT_SCALE_FACTOR'] = str(scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QTextLayout
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import set_locale, tr
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.ui import MainWindow

out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
if os.environ.get('LUMEN_TEST_CJK_FONT_DIR'):
    from scripts.provision_test_font import verify_fixture
    fixture = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
    assert QFontDatabase.addApplicationFont(fixture['font_file']) >= 0
font = QFont('Noto Sans CJK SC', 10); app.setFont(font)
for text in ('Apply audio-only roll Align audio cut to picture Cancel', '应用仅音频滚动对齐画面剪辑点取消'):
    layout = QTextLayout(text, font); layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    runs = layout.glyphRuns()
    assert runs and all(run.rawFont().isValid() for run in runs)
    assert all(index != 0 for run in runs for index in run.glyphIndexes())
files = ('lumen/ui.py', 'lumen/ui_linked_av.py', 'lumen/ui_linked_audio_roll.py',
    'lumen/linked_av_audio_roll.py', 'lumen/model.py', 'lumen/shortcuts.py',
    'lumen/i18n_catalog_linked_av.py', 'lumen/i18n_catalog_linked_audio_roll.py',
    'lumen/i18n_catalog_errors.py', 'tests/test_linked_audio_roll_ui_dpi.py')
source_identity = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
source = out/'identity.mov'; source.write_bytes(b'Identity-only source: never decoded or rewritten')
source_before = (source.read_bytes(), source.stat().st_mtime_ns)
warnings, records = [], []
QMessageBox.warning = lambda *items: warnings.append(str(items[2]))
QMessageBox.information = lambda *_: None
w = MainWindow(workspace_settings=QSettings(str(out/'settings.ini'), QSettings.Format.IniFormat),
    ui_language='en', recovery_root=out/'recovery')
w.timeline.waveforms.request = lambda *_: None

def focus():
    w.show(); w.activateWindow(); QApplication.setActiveWindow(w)
    w.timeline.setFocus(); app.processEvents()

def make_project():
    project = Project(media=[Media('media', str(source), 'Picture <unsafe> & sound', 100, 64, 48, True, True)], clips=[
        Clip('lv', 'media', 0, 5, 20, 20, source_audio_enabled=False),
        Clip('la', 'media', 2, 5, 20, 20, fade_in=2, fade_out=3,
             audio_mix_anchor=AudioMixAnchor(0, 5, 'lv')),
        Clip('rv', 'media', 0, 25, 80, 20, source_audio_enabled=False),
        Clip('ra', 'media', 2, 25, 80, 20, fade_in=3, fade_out=2,
             audio_mix_anchor=AudioMixAnchor(0, 25, 'rv'))])
    w.editor = Editor(project)
    w.editor.link_av_clips('lv', 'la'); w.editor.link_av_clips('rv', 'ra')
    w.refresh(); w.active_selection_type = 'clip'
    w.set_selected_clip_ids(('lv', 'la', 'rv', 'ra'), anchor='ra'); w.refresh_inspector()
    return project

try:
    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        for geometry, width in (('normal', 780), ('narrow', 390)):
            make_project(); focus()
            before = deepcopy((w.project, w.editor._undo, w.editor._redo, w.editor.revision, w.revision))
            d = w.open_linked_audio_roll(); assert d is not None and not d.isModal()
            d.cut_edit.setText('28'); plan = d.plan
            assert plan.cut == 28 and d.apply_button.isEnabled() and not w.jobs
            d.fit_available_screen(QRect(0, 0, width, 720)); app.processEvents()
            assert d.devicePixelRatioF() == scale
            assert d.width() <= width-32 and d.height() <= 656
            assert d.scroll.verticalScrollBar().value() == 0
            input_rect = QRect(d.cut_edit.mapTo(d.scroll.viewport(), QPoint(0, 0)), d.cut_edit.size())
            assert d.scroll.viewport().rect().contains(input_rect), 'Initial cut editor must be fully visible'
            for button, title in ((d.align_button, 'Align audio cut to picture'),
                                  (d.apply_button, 'Apply audio-only roll'), (d.cancel_button, 'Cancel')):
                assert button.text() == tr(title)
                assert button.accessibleName() == button.text() and button.toolTip() == button.text()
                rect = QRect(button.mapTo(d, QPoint(0, 0)), button.size())
                assert d.rect().contains(rect) and button.width() > 80
            for label in (d.summary, d.impact, d.status, d.scope, d.limits):
                assert label.textFormat() == Qt.TextFormat.PlainText and label.wordWrap()
                if label.text():
                    assert label.width() <= d.scroll.viewport().width()
                    assert label.height() >= max(0, label.heightForWidth(label.width()))
            assert d.scroll.horizontalScrollBar().maximum() == 0
            assert '25' in d.summary.text() and '28' in d.summary.text() and 'L' in d.summary.text()
            for key in ('lv', 'la', 'rv', 'ra'): assert key in d.impact.text()
            name = f'{locale}-{geometry}-{scale:g}-ready.png'
            assert d.grab().save(str(out/name))
            # A pure draft and localized refresh retain the original plan.
            d.refresh_text(); d.check_current()
            assert d.plan is plan and (w.project, w.editor._undo, w.editor._redo, w.editor.revision, w.revision) == before
            d.cancel_button.click(); assert d._closed_flag and not d.timer.isActive()
            assert w.linked_audio_roll_dialog is None
            assert (w.project, w.editor._undo, w.editor._redo, w.editor.revision, w.revision) == before
            records.append({'locale': locale, 'geometry': geometry, 'available_width': width,
                'device_pixel_ratio': scale, 'dialog_size': [d.width(), d.height()],
                'localized_accessible_buttons': True, 'wrapped_plaintext_no_horizontal_scroll': True,
                'fixed_picture_and_requested_audio_visible': True, 'initial_cut_input_fully_visible': True,
                'no_worker_or_mutation': True,
                'cancel_releases_dialog': True, 'path': name})
    assert not warnings
finally:
    if w.linked_audio_roll_dialog is not None: w.linked_audio_roll_dialog.reject()
    w.cancel_jobs(); app.processEvents(); w.dirty = False; w.closing = True; w.close(); app.processEvents()
assert (source.read_bytes(), source.stat().st_mtime_ns) == source_before
assert source_identity == {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in files}
screenshot_bytes = sum(path.stat().st_size for path in out.glob('*.png'))
assert screenshot_bytes < 2*1024*1024
import_origins = {}
for name, module in tuple(sys.modules.items()):
    if name == 'lumen' or name.startswith(('lumen.', 'scripts.')):
        path = getattr(module, '__file__', None)
        if path is None: continue
        path = Path(path).resolve(); relative = path.relative_to(root)
        assert path.suffix == '.py' and relative.parts[0] in ('lumen', 'scripts')
        import_origins[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
assert 'lumen.ui_linked_audio_roll' in import_origins
report = {'status': 'passed', 'identity_only_fixture': True,
    'import_origins': import_origins,
    'actual_media_proof_claimed': False, 'native_windows_macos_validation': False,
    'font_family': QFontInfo(font).family(), 'source_identity': source_identity,
    'screenshot_bytes': screenshot_bytes, 'records': records}
(out/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'status': 'passed', 'records': len(records), 'screenshot_bytes': screenshot_bytes}))
'''


@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_audio_only_roll_bilingual_narrow_real_dpr(tmp_path, scale):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path/'audio-roll-screens'
    result = subprocess.run([sys.executable, '-c', DRIVER, str(root), str(out), str(scale)],
        cwd=root, env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    report = json.loads((out/'report.json').read_text())
    assert report['status'] == 'passed' and report['identity_only_fixture']
    assert not report['actual_media_proof_claimed'] and not report['native_windows_macos_validation']
    assert report['screenshot_bytes'] < 2*1024*1024 and len(report['records']) == 4
    import hashlib
    assert 'lumen.ui_linked_audio_roll' in report['import_origins']
    for row in report['import_origins'].values():
        path = Path(row['path']); assert path.is_relative_to(root)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
    for row in report['records']:
        assert row['device_pixel_ratio'] == scale and row['available_width'] in (390, 780)
        for flag in ('localized_accessible_buttons', 'wrapped_plaintext_no_horizontal_scroll',
                     'fixed_picture_and_requested_audio_visible', 'initial_cut_input_fully_visible',
                     'no_worker_or_mutation', 'cancel_releases_dialog'):
            assert row[flag]
        assert (out/row['path']).read_bytes().startswith(b'\x89PNG')
