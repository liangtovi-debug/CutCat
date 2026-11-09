"""Bounded fixed-field startup evidence; never include paths or source traceback."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def diagnose(output):
    from lumen import caption_runtime
    directory = caption_runtime.runtime_directory()
    report = {'platform': sys.platform, 'pillow_available': importlib.util.find_spec('PIL') is not None,
              'receipt_exists': (directory/'receipt.json').is_file(),
              'dll_exists': (directory/'fribidi.dll').is_file(),
              'probe': 'Original unconditional session-start caption initializer; separate diagnostic process'}
    try:
        caption_runtime.initialize()
    except caption_runtime.CaptionRuntimeError as error:
        report.update(initializer='failed', error_type=type(error).__name__,
                      cause_type=type(error.__cause__).__name__ if error.__cause__ else None)
    else:
        report['initializer'] = 'succeeded'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    diagnose(parser.parse_args().output)
