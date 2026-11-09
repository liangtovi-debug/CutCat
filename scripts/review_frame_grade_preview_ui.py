"""Capture real Qt grade-preview geometry in fresh DPI-specific processes.

Linux/offscreen evidence is not native macOS/Windows playback validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    runs = []
    for scale in ('1', '1.25', '1.5', '2'):
        folder = output / ('dpi-' + scale); folder.mkdir(exist_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=scale,
                   FRAME_GRADE_SCREENSHOTS=str(folder))
        command = [sys.executable, '-m', 'pytest', 'tests/test_frame_grade_preview_ui.py',
                   '-k', 'narrow_dpi_geometry_locale_and_state_preservation', '-q']
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        (folder / 'pytest.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        captures = [{'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sorted(folder.glob('*.png'))]
        runs.append(dict(scale=scale, returncode=result.returncode, captures=captures))
        print(f'DPI {scale}: exit {result.returncode}; {len(captures)} actual Qt captures', flush=True)
    (output / 'manifest.json').write_text(json.dumps(dict(platform='Linux/offscreen',
        native_validation=False, dialog_widths=[360, 760], locales=['en', 'zh-CN'],
        checks='Real RGB16 source and full float grade snapshot; all labels and controls visible; narrow geometry unchanged; localization preserves current snapshot, project and history',
        runs=runs), indent=2), encoding='utf-8')
    return int(any(run['returncode'] or len(run['captures']) != 4 for run in runs))


if __name__ == '__main__':
    raise SystemExit(main())
