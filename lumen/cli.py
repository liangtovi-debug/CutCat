"""Headless validation and rendering using the exact desktop project engine."""
from __future__ import annotations
import argparse
import json
import signal
import sys
import threading
from .model import Project
from .media import probe
from .render import RenderEngine, RenderCancelled
from dataclasses import asdict


def main(argv=None):
    parser = argparse.ArgumentParser(description='Lumen Edit project tools (no GUI required)')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('validate', help='Validate project structure and media paths')
    check.add_argument('project')
    render = sub.add_parser('render', help='Render a project to a new MP4 or MOV')
    render.add_argument('project'); render.add_argument('output')
    inspect = sub.add_parser('probe', help='Inspect supported SDR media')
    inspect.add_argument('media')
    args = parser.parse_args(argv)
    try:
        if args.command == 'probe':
            print(json.dumps(asdict(probe(args.media)), indent=2)); return 0
        project = Project.load(args.project)
        missing = project.missing_media()
        if missing:
            raise ValueError('Missing media: ' + ', '.join(m.path for m in missing))
        if args.command == 'validate':
            print(f'Valid: {len(project.clips)} clips, {project.duration_frames} frames, {project.fps} fps.'); return 0
        cancel = threading.Event()
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda *_: cancel.set())
        engine = RenderEngine()
        last = -1
        def progress(value):
            nonlocal last
            percent = int(value * 100)
            if percent != last:
                print(f'\rRendering {percent:3d}%', end='', flush=True, file=sys.stderr); last = percent
        try:
            path = engine.render(project, args.output, cancel=cancel, progress=progress)
            print(str(path)); return 0
        finally:
            print(file=sys.stderr); signal.signal(signal.SIGINT, previous); engine.close()
    except RenderCancelled:
        print('Canceled; incomplete output removed.', file=sys.stderr); return 130
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
