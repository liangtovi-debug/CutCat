"""Capture bounded synthetic Qt PIP evidence, never native platform acceptance.

Usage: python scripts/review_clip_composite_ui.py --output /path/to/evidence/ui
Uses only a short locally generated source; never opens user projects/media.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from lumen.clip_composite import ClipComposite
from lumen.clip_transform import ClipTransform
from lumen.i18n import set_locale
from lumen.media import ffmpeg_binary
from lumen.model import Clip, Editor, Media, Project
from lumen.ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    captures = []
    with tempfile.TemporaryDirectory(prefix='cutcat-pip-ui-') as folder:
        temp = Path(folder); source = temp / 'synthetic-owned.mkv'
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=size=96x64:rate=30:duration=2', '-an', '-c:v', 'ffv1', str(source)],
            check=True, capture_output=True, timeout=30)
        settings = QSettings(str(temp / 'workspace.ini'), QSettings.Format.IniFormat)
        settings.setValue('mode', 'professional')
        window = MainWindow(workspace_settings=settings, ui_language='en', recovery_root=temp / 'recovery')
        try:
            media = Media('synthetic', str(source), 'Synthetic picture · 合成画面', 2, 96, 64, True, False)
            window.editor = Editor(Project(width=960, height=640, media=[media], clips=[
                Clip('base', media.id, 0, 0, 0, 60),
                Clip('pip', media.id, 1, 0, 0, 60,
                     transform=ClipTransform(x=180, y=100, scale=.4, rotation=12),
                     composite=ClipComposite(True, .875))]))
            window.refresh(); window.resize(1100, 800); window.show(); app.processEvents()
            for language in ('en', 'zh-CN'):
                set_locale(language)
                for mode in ('simple', 'professional'):
                    window.set_workspace_mode(mode)
                    for multiple in (False, True):
                        window.select_clip('pip')
                        if multiple: window.toggle_clip_selection('base')
                        window.inspector_tabs.setCurrentIndex(0)
                        window.inspector.ensureWidgetVisible(window.reset_composite_button)
                        app.processEvents()
                        assert window.transform_group.isEnabled() is not multiple
                        assert not window.apply_composite_button.isEnabled()
                        assert window.reset_composite_button.isEnabled() is not multiple
                        assert window.composite_note.wordWrap()
                        assert window.inspector.horizontalScrollBar().maximum() == 0
                        suffix = 'multi-disabled' if multiple else 'single'
                        stem = f'clip-composite-{language}-{mode}-{suffix}'
                        for label, widget in (('window', window), ('inspector', window.inspector.viewport()),
                                              ('transform', window.transform_group)):
                            path = output / f'{stem}-{label}.png'
                            assert widget.grab().save(str(path))
                            captures.append({'name': path.name,
                                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'locale': language, 'workspace': mode, 'multiple': multiple,
                                'width': widget.width(), 'height': widget.height()})
            assert all(c.composite == (ClipComposite(True, .875) if c.id == 'pip' else ClipComposite())
                       for c in window.project.clips)
        finally:
            window.cancel_jobs()
            for job in list(window.jobs):
                assert job.wait(5000), 'Synthetic UI worker did not stop within five seconds'
            window.dirty = False; window.closing = True; window.close(); app.processEvents()
    manifest = dict(platform='Linux/offscreen', native_validation=False,
                    source='Locally generated 96x64 FFV1 testsrc2, two seconds, no user media',
                    window_size=[1100, 800], locales=['en', 'zh-CN'],
                    workspaces=['simple', 'professional'], captures=captures)
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Saved {len(captures)} offscreen screenshots and manifest to {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
