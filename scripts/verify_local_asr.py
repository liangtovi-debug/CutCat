#!/usr/bin/env python3
"""Run real local inference with explicit files; never downloads anything.

This is a development verification tool, not a TTS feature or model installer.
Output is JSON on stdout. Redirect stdout to a new evidence file if desired.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lumen.asr import ASRConfig, ASRError, transcribe


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, action='append', required=True,
                        help='Local audio/video fixture; may be repeated.')
    parser.add_argument('--language', default='auto')
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    config = ASRConfig(args.runtime, args.model, args.language, args.threads)
    result = {'runtime_sha256': sha256(args.runtime), 'model_sha256': sha256(args.model),
              'requested_language': args.language, 'threads': args.threads, 'fixtures': []}
    for fixture in args.fixture:
        began = time.monotonic()
        segments = transcribe(fixture, config)
        result['fixtures'].append({'filename': fixture.name, 'sha256': sha256(fixture),
                                   'elapsed_seconds': time.monotonic() - began,
                                   'segments': [asdict(s) for s in segments]})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ASRError, OSError) as exc:
        print(f'Local ASR verification failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
