"""Optional local two-checkout default-gain regression evidence.

Run with the already provisioned test Python, --baseline PATH --candidate PATH
--output PATH. Each checkout is imported in an isolated child interpreter. The
baseline creates schema-33 documents; the candidate loads those exact bytes.
Graph, PCM, decoded AAC and decoded picture hashes are compared in the same
FFmpeg environment. This does not claim cross-version encoder bit identity.
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


def _worker(checkout, root, label):
    sys.path.insert(0, str(checkout))
    from fractions import Fraction
    from lumen.media import ffmpeg_binary, probe
    from lumen.model import Clip, Project
    from lumen.render import RenderEngine
    from lumen.timing import ClipTimeMap, duration_frames
    from lumen.video_transition import VideoTransition

    binary = ffmpeg_binary()
    def run(args):
        return subprocess.run([binary, '-v', 'error', '-nostdin', *args], check=True,
                              capture_output=True, timeout=120).stdout
    def digest(data):
        return hashlib.sha256(data).hexdigest()

    paths = [root / 'first.mkv', root / 'second.mkv']
    if label == 'baseline':
        for index, path in enumerate(paths):
            run(['-f', 'lavfi', '-i',
                 f'testsrc2=size=64x48:rate=30000/1001:duration=4,hue=h={index * 70}',
                 '-f', 'lavfi', '-i',
                 f'aevalsrc=0.23*sin(2*PI*{997 + 100*index}*t)|0.13*sin(2*PI*3001*t):s=48000:d=4',
                 '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'ffv1', '-c:a', 'pcm_f32le',
                 '-color_primaries', 'bt709', '-color_trc', 'bt709', str(path)])
        media = [probe(path) for path in paths]
        mapping = ClipTimeMap(Fraction(5, 3), Fraction(127, 2), Fraction(3, 2))
        edited = Project(width=64, height=48, fps_num=30000, fps_den=1001, media=media, clips=[
            Clip('retime', media[0].id, 0, 0, mapping.source_in, duration_frames(mapping),
                 time_map=mapping, gain_db=2.75, fade_in=7, fade_out=9),
            Clip('picture', media[1].id, 1, 5, 3, 30, gain_db=-4.125, fade_in=3, fade_out=5),
            Clip('sound', media[0].id, 2, 13, 11, 27, gain_db=-6, fade_in=3, fade_out=5),
            Clip('muted-tail', media[0].id, 3, 45, 0, 9, muted=True)])
        transition = Project(width=64, height=48, fps_num=30000, fps_den=1001, media=media, clips=[
            Clip('left', media[0].id, 0, 0, 4, 12, fade_in=3),
            Clip('right', media[1].id, 0, 12, 14, 12, fade_out=4),
            Clip('overlap', media[0].id, 2, 7, 3, 10, gain_db=-4, fade_in=2, fade_out=2)],
            video_transitions=[VideoTransition('dissolve', 'left', 'right', 5)])
        for name, project in [('retime-overlap-fades', edited), ('cross-dissolve', transition)]:
            data = project.to_dict()
            assert data['version'] == 33 and 'master_gain_db' not in data
            (root / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')

    rows = []
    for name in ['retime-overlap-fades', 'cross-dissolve']:
        path = root / f'{name}.json'
        original = path.read_bytes()
        project = Project.from_dict(json.loads(original))
        assert getattr(project, 'master_gain_db', 0.0) == 0.0
        assert path.read_bytes() == original
        for mode in ['preview', 'h264', 'prores']:
            engine = RenderEngine(timeout=120)
            captures = []
            compile_original = engine._compile
            def capture(*args, **kwargs):
                compiled = compile_original(*args, **kwargs)
                captures.append(compiled)
                return compiled
            engine._compile = capture
            try:
                if mode == 'preview':
                    output = engine.preview(project)
                else:
                    output = root / f'{label}-{name}-{mode}.{ "mov" if mode == "prores" else "mp4"}'
                    engine.render(project, output, export_profile=(
                        'prores_422_hq_10bit' if mode == 'prores' else 'h264_8bit'))
                assert len(captures) == 1
                inputs, graph, duration = captures[0]
                pcm = run(['-filter_complex_threads', '1', *inputs, '-filter_complex', graph,
                           '-map', '[aout]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_f32le',
                           '-f', 'f32le', 'pipe:1', '-map', '[vout]', '-an', '-f', 'null', os.devnull])
                audio = run(['-i', str(output), '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '2',
                             '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'])
                video = run(['-i', str(output), '-map', '0:v:0', '-an', '-pix_fmt', 'rgb48le',
                             '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'])
                normalize = lambda value: value.replace(engine._cache_dir.name, '<CACHE>')
                normalized_graph = normalize(graph)
                # Transition cache keys include the whole serialized project,
                # so schema migration legitimately changes their filenames.
                # Compare their actual decoded content, not that cache salt.
                normalized_inputs = []
                for value in inputs:
                    if value.startswith(engine._cache_dir.name):
                        cached_picture = run([
                            '-i', value, '-map', '0:v:0', '-an', '-pix_fmt', 'rgb48le',
                            '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1'])
                        normalized_inputs.append('<CACHE_RGB48_SHA256:' + digest(cached_picture) + '>')
                    else:
                        normalized_inputs.append(value)
                rows.append({'case': name, 'mode': mode, 'schema33_sha256': digest(original),
                             'duration_seconds': duration, 'timeline_frames': project.duration_frames,
                             'graph_sha256': digest(graph.encode()),
                             'cache_prefix_normalized_graph_sha256': digest(normalized_graph.encode()),
                             'raw_inputs': inputs,
                             'raw_inputs_sha256': digest(json.dumps(inputs).encode()),
                             'cache_prefix_normalized_inputs': [normalize(value) for value in inputs],
                             'content_normalized_inputs_sha256': digest(json.dumps(normalized_inputs).encode()),
                             'preencoder_pcm_sha256': digest(pcm), 'preencoder_pcm_frames': len(pcm) // 8,
                             'decoded_aac_sha256': digest(audio), 'decoded_aac_frames': len(audio) // 8,
                             'decoded_rgb48_sha256': digest(video),
                             'decoded_video_frames': len(video) // (64 * 48 * 6)})
            finally:
                engine.close()
    print(json.dumps({'checkout': str(checkout), 'ffmpeg': run(['-version']).decode().splitlines()[0],
                      'rows': rows}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=['baseline', 'candidate'])
    parser.add_argument('--workspace', type=Path)
    args = parser.parse_args()
    if args.worker:
        _worker(args.baseline if args.worker == 'baseline' else args.candidate,
                args.workspace, args.worker)
        return
    if not args.baseline or not args.candidate or not args.output:
        parser.error('--baseline, --candidate and --output are required')
    with tempfile.TemporaryDirectory(prefix='cutcat-master-gain-baseline-') as directory:
        results = {}
        for label in ['baseline', 'candidate']:
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                     '--baseline', str(args.baseline.resolve()),
                                     '--candidate', str(args.candidate.resolve()),
                                     '--worker', label, '--workspace', directory],
                                    capture_output=True, text=True, timeout=600)
            if result.returncode:
                raise RuntimeError(f'{label} worker failed:\n{result.stderr}')
            results[label] = json.loads(result.stdout)
        path_fields = {'raw_inputs', 'raw_inputs_sha256', 'cache_prefix_normalized_inputs'}
        comparisons = []
        for baseline, candidate in zip(results['baseline']['rows'], results['candidate']['rows']):
            stable_baseline = {key: value for key, value in baseline.items() if key not in path_fields}
            stable_candidate = {key: value for key, value in candidate.items() if key not in path_fields}
            comparisons.append({'case': baseline['case'], 'mode': baseline['mode'],
                                'graph_pcm_aac_video_and_content_normalized_inputs_equal':
                                    stable_baseline == stable_candidate,
                                'raw_input_arguments_equal': baseline['raw_inputs'] == candidate['raw_inputs'],
                                'cache_prefix_normalized_input_arguments_equal':
                                    baseline['cache_prefix_normalized_inputs'] == candidate['cache_prefix_normalized_inputs']})
        rows_equal = all(row['graph_pcm_aac_video_and_content_normalized_inputs_equal'] for row in comparisons)
        results['comparisons'] = comparisons
        results['all_six_default_public_routes_equal'] = rows_equal
        results['scope'] = ('Same-FFmpeg isolated-checkout regression: frozen schema-33 documents '
                            'migrated by candidate; exact graph, preencoder float PCM, decoded AAC '
                            'and decoded video identity. Owned temporary video input filenames '
                            'are normalized by their decoded RGB48 content hash, not cache keys. '
                            'Raw input arguments are retained and differ: transition-cache keys '
                            'include the serialized schema and new default master field. This '
                            'is not a claim of raw command-byte identity. Full final filter '
                            'graph hashes are compared without path normalization. '
                            'AAC identity is deterministic regression, '
                            'not equality to preencoder PCM or a losslessness claim.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'all_six_default_public_routes_equal': rows_equal,
                          'output': str(args.output), 'routes': len(results['baseline']['rows'])}))
        if not rows_equal:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
