"""Capture real offscreen Qt recovery states using synthetic project metadata.

No media is decoded, uploaded, or bundled. These are not native OS screenshots.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--language', choices=('zh-CN', 'en'), default='zh-CN')
    parser.add_argument('--scale', default='1')
    args = parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ['QT_SCALE_FACTOR'] = args.scale
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtGui import QFont, QFontDatabase, QTextLayout
    from PySide6.QtWidgets import QApplication, QPushButton
    from lumen.model import Project, Caption, Media, Clip
    from lumen.project_recovery import RecoveryStore
    from lumen.ui import MainWindow
    app = QApplication([])
    fontfile = Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
    if fontfile.exists():
        handle = QFontDatabase.addApplicationFont(str(fontfile))
        font = QFont(QFontDatabase.applicationFontFamilies(handle)[0], 10)
        app.setFont(font)
    shape = QTextLayout('剪辑猫恢复项目 Restore 123', app.font())
    shape.beginLayout(); shape.createLine().setLineWidth(1000); shape.endLayout()
    assert shape.glyphRuns() and all(0 not in run.glyphIndexes() for run in shape.glyphRuns()), 'Missing glyphs'
    root = out / 'runtime' / 'recovery'
    sample = Project(name='街景短片 · 恢复演示' if args.language == 'zh-CN' else 'Street scene · recovery demo',
                     media=[Media('fixture', str(out / 'synthetic-scene.mp4'), 'Synthetic scene', 5, 640, 360, True, True)],
                     clips=[Clip('clip', 'fixture', 0, 0, 0, 150)],
                     captions=[Caption('caption', 10, 90, '恢复的字幕 / Recovered captions')])
    with RecoveryStore(root) as store:
        entry = store.write(sample, str(uuid.uuid4()), out / 'scene-original.lumen')
    settings = QSettings(str(out / 'runtime' / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'simple')
    w = MainWindow(workspace_settings=settings, ui_language=args.language, recovery_root=root)
    w.resize(1366, 768); w.show(); app.processEvents()
    results = []
    def capture(widget, filename):
        app.processEvents()
        image = widget.grab()
        assert image.save(str(out / filename))
        results.append({'file': filename, 'physical_size': [image.width(), image.height()],
                        'logical_size': [widget.width(), widget.height()]})
    capture(w, 'recovery-available.png')
    w.open_recovery(); app.processEvents()
    d = w.recovery_dialog; d.items.setCurrentRow(0); app.processEvents()
    violations = []
    for b in d.findChildren(QPushButton):
        if b.isVisible() and b.width() < b.sizeHint().width(): violations.append(b.text())
    assert not violations, violations
    capture(d, 'recovery-manager.png')
    d.restore_selected(); app.processEvents()
    assert w.dirty and w.project_path is None and w.project.to_dict() == sample.to_dict()
    capture(w, 'recovery-restored.png')
    w.dirty = False; w.closing = True; w.close(); app.processEvents()
    (out / 'capture.json').write_text(json.dumps({'platform': 'offscreen Qt; not native Windows/macOS',
        'language': args.language, 'scale': args.scale, 'fixture': 'synthetic project metadata; not user media',
        'glyph_validation': True, 'button_geometry_violations': violations, 'captures': results}, ensure_ascii=False, indent=2))
    print(out / 'capture.json')


if __name__ == '__main__': main()
