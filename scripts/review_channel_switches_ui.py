"""Run actual Qt focus/reveal screenshots in fresh processes for each DPI.

Linux/offscreen screenshots are not native macOS or Windows acceptance.
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
    for dpi in ('1', '1.25', '1.5', '2'):
        folder = output / ('dpi-' + dpi); folder.mkdir(exist_ok=True)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=dpi,
                   QUALIFIER_CHANNEL_SCREENSHOTS=str(folder))
        command = [sys.executable, '-m', 'pytest', 'tests/test_qualifier_channel_ui.py',
                   '-k', 'focus_scroll_layout', '-q']
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        (folder/'pytest.log').write_text(result.stdout + result.stderr)
        captures = [{'name': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in sorted(folder.glob('*.png'))]
        runs.append(dict(scale=dpi, returncode=result.returncode, captures=captures))
        print(f'DPI {dpi}: exit {result.returncode}; {len(captures)} actual Qt captures', flush=True)
    (output/'manifest.json').write_text(json.dumps(dict(platform='Linux/offscreen', native_validation=False,
        viewport_widths=[320, 760], locales=['en', 'zh-CN'], target_ranges=['base', 'inclusion', 'exclusion'],
        checks='Focus from bottom adjustment to every checkbox; full visible region; all enabled numeric controls and labels focus-revealed; no horizontal scrolling; pending toggle and restore without project commits',
        runs=runs), indent=2))
    return int(any(run['returncode'] for run in runs))


if __name__ == '__main__':
    raise SystemExit(main())
