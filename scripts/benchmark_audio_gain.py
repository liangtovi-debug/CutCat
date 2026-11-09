"""Small reproducible synthetic production-mixer benchmark; never user media.

Usage: python scripts/benchmark_audio_gain.py --output /owned/evidence.json
Timings are observations, not a realtime promise or cross-platform threshold.
No input files are created; each bounded FFmpeg process emits float32 to null.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lumen.audio_gain import AudioGainKeyframe, AUDIO_GAIN_ALGORITHM_ID
from lumen.audio_mix import AudioMixBuilder
from lumen.media import ffmpeg_binary
from lumen.model import Clip, Media, Project


def measure(seconds=10, repeats=3):
    if type(seconds) is not int or not 1 <= seconds <= 30 or type(repeats) is not int or not 1 <= repeats <= 5:
        raise ValueError('Benchmark duration/repeat request exceeds its bounded synthetic scope')
    measurements = []
    for count in (0, 1, 16, 128):
        keys = [AudioGainKeyframe(i*seconds*48000//max(1, count-1),
                -96+120*(i % 2), 'linear' if i % 3 else 'hold') for i in range(count)]
        media = Media('m', '/synthetic-input-unused', 'synthetic', seconds,
                      has_video=False, has_audio=True)
        project = Project(media=[media], clips=[Clip('c', 'm', 2, 0, 0,
                          seconds*30, gain_db=-3, audio_gain_keyframes=keys)])
        project.validate()
        builder = AudioMixBuilder(project)
        fragment = builder.clip_fragment(0, media)
        graph = ';'.join((builder.silence_fragment, fragment.fragment,
                          builder.final_fragment([fragment.label])))
        command = [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
            '-threads', '1', '-filter_complex_threads', '1', '-f', 'lavfi', '-i',
            f'aevalsrc=0.2*sin(2*PI*431*t)|0.13*sin(2*PI*991*t):s=48000:d={seconds}',
            '-filter_complex', graph, '-map', '[aout]', '-c:a', 'pcm_f32le', '-f', 'null', '-']
        walls = []
        for _ in range(repeats):
            began = time.monotonic()
            result = subprocess.run(command, capture_output=True, timeout=30, check=False)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-2000:])
            walls.append(time.monotonic()-began)
        measurements.append({'keys': count, 'audio_seconds': seconds,
            'stereo_output_samples': builder.end_sample, 'filter_chars': len(graph),
            'wall_seconds': walls, 'best_audio_seconds_per_wall_second': seconds/min(walls)})
    return {'algorithm': AUDIO_GAIN_ALGORITHM_ID, 'platform': platform.platform(),
        'python': platform.python_version(),
        'ffmpeg': subprocess.check_output([ffmpeg_binary(), '-version'], text=True).splitlines()[0],
        'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            for name in ('lumen/audio_gain.py', 'lumen/audio_mix.py', 'scripts/benchmark_audio_gain.py')},
        'measurements': measurements,
        'limitations': 'One synthetic clip, one machine, process startup included. No realtime or native-package claim.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    text = json.dumps(measure(args.seconds, args.repeats), indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text, end='')
