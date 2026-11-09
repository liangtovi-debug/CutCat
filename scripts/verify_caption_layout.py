"""Acceptance evidence from actual FFmpeg exports and Qt preview decoding.

Only synthetic text and an existing local font are used; no assets or models are
fetched. A new output directory retains MP4s, decoded PNGs, projects, SRTs and
source hashes. Screenshots are the actual editor, never a reconstructed mock.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lumen.caption_layout import layout_caption
from lumen.caption_render import font_identity, prepare_font
from lumen.media import ffmpeg_binary
from lumen.model import Caption, CaptionStyle, Project
from lumen.render import RenderEngine, RenderError
from lumen.subtitles import format_srt, read_srt, write_srt

ENGLISH = ('A long caption should wrap naturally at word boundaries and stay '
           'readable inside the safe area of every video frame.')
CHINESE = ('这是一段用来验证自动换行的中文字幕，横屏与竖屏都应完整显示。'
           '文字不能越过安全边距，预览和导出应保持一致。')


@dataclass(frozen=True)
class Case:
    name: str
    language: str
    width: int
    height: int
    text: str
    font_size: int = 26


CASES = tuple(Case(f'{language}-{orientation}', language, width, height, text)
              for language, text in (('en', ENGLISH), ('zh', CHINESE))
              for orientation, width, height in (('landscape', 640, 360),
                                                ('portrait', 360, 640))) + (
    Case('manual-break', 'en', 360, 640,
         'Manual break\nThe next long sentence wraps without changing the first line.'),
    Case('long-word-combining', 'en', 360, 640,
         'Supercalifragilisticexpialidocious' * 2 + ' Cafe\u0301' * 5, 24),
    Case('accented-boundary', 'en', 360, 640, 'e\u0301' * 40, 36),
    Case('stacked-accents', 'en', 360, 640, 'e\u0301\u0301\u0301' * 24, 36),
)


def make_project(case, family, directory='', *, duration=6):
    return Project(width=case.width, height=case.height,
                   captions=[Caption(case.name, 0, duration, case.text, case.language)],
                   caption_style=CaptionStyle(font_family=family,
                       font_directory=str(directory), font_size=case.font_size,
                       outline_width=3, margin_bottom=24,
                       safe_margin_percent=5, max_lines=6))


def decode_frames(path, width, height):
    decoded = subprocess.run([ffmpeg_binary(), '-v', 'error',
                              '-protocol_whitelist', 'file,pipe', '-i', str(path),
                              '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1'],
                             capture_output=True, check=True, timeout=60)
    return np.frombuffer(decoded.stdout, np.uint8).reshape(-1, height, width, 3)


def white_bounds(rgb):
    """Independent bright-neutral caption ink bounds, excluding codec ringing."""
    rows, columns = np.nonzero(np.min(rgb[:, :, :3], axis=2) > 180)
    if not len(rows):
        raise AssertionError('The real decoded frame has no white caption pixels')
    return [int(columns.min()), int(rows.min()),
            int(columns.max()) + 1, int(rows.max()) + 1]


def verify_bounds(rgb, project):
    left, top, right, bottom = white_bounds(rgb)
    margin = math.ceil(project.width * project.caption_style.safe_margin_percent / 100)
    assert left >= margin and right <= project.width - margin, \
        f'Caption ink crossed horizontal safe margins: {(left, right)}, {margin}'
    assert top >= 0 and bottom <= project.height - project.caption_style.margin_bottom, \
        f'Caption ink crossed vertical safe margins: {(top, bottom)}'
    return [left, top, right, bottom]


def project_layout(project, cache):
    style = project.caption_style
    font = prepare_font(style, project.captions, cache)
    horizontal = max(math.ceil(project.width * style.safe_margin_percent / 100),
                     style.outline_width + 1)
    bottom = max(style.margin_bottom, style.outline_width + 1)
    return layout_caption(project.captions[0].text, font, style.font_size,
                          project.width - 2 * horizontal,
                          project.height - bottom - style.outline_width - 1,
                          outline_width=style.outline_width, max_lines=style.max_lines)


def compare_frames(preview, exported):
    assert preview.shape == exported.shape
    delta = np.abs(preview.astype(np.int16) - exported.astype(np.int16))
    mean = float(delta.mean())
    foreground = np.maximum(preview.max(axis=2), exported.max(axis=2)) > 40
    ink_mean = float(delta[foreground].mean())
    # Presets and CRFs differ; compare decoded content rather than MP4 bytes.
    assert mean < 2.0 and ink_mean < 12.0, (mean, ink_mean)
    assert max(abs(a - b) for a, b in zip(white_bounds(preview), white_bounds(exported))) <= 2
    return {'whole_frame_mean_absolute_error': mean,
            'foreground_mean_absolute_error': ink_mean}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def qimage_rgb(image):
    from PySide6.QtGui import QImage
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    values = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    return values[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def capture_ui(project, preview_path, destination, locale):
    """Capture the actual editor and QMediaPlayer decode, using no painted mock."""
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    from lumen.i18n import get_locale, set_locale
    from lumen.model import Editor
    from lumen.ui import MainWindow
    app = QApplication.instance() or QApplication([])
    prior_locale = get_locale()
    font_id = QFontDatabase.addApplicationFont(font_identity(project.caption_style)[0])
    assert font_id >= 0, 'Qt must load the same existing font'
    settings = QSettings(str(destination.with_suffix('.ini')), QSettings.Format.IniFormat)
    window = MainWindow(workspace_settings=settings, shortcut_settings=settings,
                        ui_language=locale)
    try:
        window.editor = Editor(project)
        window.resize(1700, 1080)
        window.refresh()
        window.show()
        window.main_split.setSizes([190, 970, 540])
        window.select_caption(project.captions[0].id)
        assert window.caption_text.toPlainText() == project.captions[0].text
        window.side_tabs.widget(1).verticalScrollBar().setValue(0)
        window.activate_preview(preview_path, window.revision, 'graded')
        window.player.play()
        deadline = time.monotonic() + 15
        while window.video.image.isNull() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.02)
        assert not window.video.image.isNull(), 'Actual Qt player failed to decode the rendered preview'
        window.player.pause()
        image = qimage_rgb(window.video.image)
        assert image.shape[:2] == (project.height, project.width)
        bounds = verify_bounds(image, project)
        window.scopes.set_image(window.video.image)
        app.processEvents()
        assert window.grab().save(str(destination))
        qt_path = destination.with_name(destination.stem + '-decoded-frame.png')
        assert window.video.image.save(str(qt_path))
        return {'screenshot': destination.name, 'decoded_frame': qt_path.name,
                'white_ink_bounds': bounds, 'original_caption_text_unchanged': True,
                'qt_platform': app.platformName(), 'physical_desktop_verified': False}
    finally:
        window.dirty = False
        window.close()
        app.processEvents()
        QFontDatabase.removeApplicationFont(font_id)
        set_locale(prior_locale)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/caption-layout-acceptance')
    parser.add_argument('--font-family', default='Noto Sans CJK SC')
    parser.add_argument('--font-directory', default=os.environ.get('LUMEN_TEST_CJK_FONT_DIR', ''))
    parser.add_argument('--capture-ui', action='store_true')
    args = parser.parse_args(argv)
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    sources = ['lumen/caption_layout.py', 'lumen/caption_render.py', 'lumen/render.py',
               'lumen/model.py', 'lumen/ui_captions.py', 'lumen/ui.py',
               'scripts/verify_caption_layout.py', 'tests/test_caption_layout_render.py',
               'pyproject.toml']
    report = {'created_utc': datetime.now(timezone.utc).isoformat(),
              'platform': platform.platform(), 'python': sys.version,
              'ffmpeg': subprocess.run([ffmpeg_binary(), '-version'], capture_output=True,
                                       text=True, check=True, timeout=10).stdout.splitlines()[0],
              'source_sha256': {name: sha256(ROOT / name) for name in sources},
              'cases': [], 'synthetic_text_only': True, 'network_or_model_downloads': False,
              'physical_desktop_verified': False}
    engine = RenderEngine(timeout=120)
    try:
        for case in CASES:
            project = make_project(case, args.font_family, args.font_directory, duration=30)
            project_path = output / (case.name + '.lumen')
            project.save(project_path)
            reopened = Project.load(project_path)
            original_dict = reopened.to_dict()
            original_srt = format_srt(reopened.captions, reopened.fps)
            write_srt(output / (case.name + '.srt'), reopened.captions, reopened.fps)
            layout = project_layout(reopened, engine._cache_dir.name)
            assert len(layout.lines) > 1
            assert layout.text.replace('\n', '') == case.text.replace('\n', '')
            exported = engine.render(reopened, output / (case.name + '-export.mp4'))
            preview = engine.preview(reopened)
            retained_preview = output / (case.name + '-preview.mp4')
            shutil.copyfile(preview, retained_preview)
            export_frames = decode_frames(exported, case.width, case.height)
            preview_frames = decode_frames(retained_preview, case.width, case.height)
            assert len(export_frames) == len(preview_frames) == 30
            comparisons = compare_frames(preview_frames[0], export_frames[0])
            bounds = {}
            for kind, frames in (('export', export_frames), ('preview', preview_frames)):
                bounds[kind] = verify_bounds(frames[0], reopened)
                Image.fromarray(frames[0]).save(output / (case.name + '-' + kind + '.png'))
            assert reopened.to_dict() == original_dict
            assert format_srt(reopened.captions, reopened.fps) == original_srt
            assert read_srt(output / (case.name + '.srt'), reopened.fps)[0].text == case.text
            record = {'case': asdict(case), 'layout': asdict(layout),
                      'white_ink_bounds': bounds, 'preview_export_comparison': comparisons,
                      'project_and_srt_unchanged': True, 'frame_count': len(export_frames)}
            if args.capture_ui and case.name in ('en-landscape', 'zh-landscape'):
                record['qt'] = capture_ui(reopened, retained_preview,
                    output / (case.name + '-editor.png'), 'en' if case.language == 'en' else 'zh-CN')
            report['cases'].append(record)
            print(f'Passed {case.name}: {len(layout.lines)} lines; bounds {bounds}', flush=True)
        font = font_identity(reopened.caption_style)
        report['font'] = {'family': args.font_family, 'path': font[0],
                          'face_index': font[-1], 'sha256': sha256(font[0])}
        overflow = make_project(CASES[0], args.font_family, args.font_directory)
        overflow.caption_style.max_lines = 1
        refused = output / 'must-not-exist.mp4'
        try:
            engine.render(overflow, refused)
        except RenderError as error:
            report['overflow'] = {'error': str(error), 'output_published': refused.exists()}
            assert not refused.exists()
            assert not list(output.glob('.lumen-render-*'))
        else:
            raise AssertionError('Overflow must fail before output publication')
        assert report['source_sha256'] == {name: sha256(ROOT / name) for name in sources}, \
            'Source changed during acceptance; rerun against a stable checkout'
        report['files'] = {path.name: sha256(path) for path in sorted(output.iterdir()) if path.is_file()}
        report['passed'] = True
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Acceptance passed: {output / "report.json"}', flush=True)
    finally:
        engine.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
