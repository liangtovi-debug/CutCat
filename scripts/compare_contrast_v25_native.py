"""Strict same-runtime v25/current oracle; retain historical golden bytes and identity."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import runpy
import stat
import subprocess
import sys
import tempfile
import zipfile

ZIP_SHA = '9f05082da94a9387966b9175f496d0672ad71d00a244039ce257bde4fe0da9d2'
GOLDEN_SHA = '2060811035977fd6781de6679efec97740820539b2b0f643450c6c4520923951'
ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')

def module_origins(repository):
    origins = {}
    for name, module in list(sys.modules.items()):
        if name == 'lumen' or name.startswith('lumen.'):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(repository / 'lumen'), (name, 'mixed implementation import')
            origins[name] = {'relative_path': str(path.relative_to(repository)), 'sha256': digest(path)}
    return origins

def native_capture(archive, output):
    """Capture only the verified old implementation using this process's CPU mode.

    Missing/changed sources are errors. The historical golden is immutable input
    and provenance, not a universal cross-CPU numerical output oracle.
    """
    import numpy as np
    from numpy._core import _multiarray_umath as core
    archive, output = Path(archive), Path(output)
    assert archive.is_file(), 'Verified v25 archive is required; run scripts/fetch_contrast_v25_archive.py'
    golden = ROOT / 'tests/fixtures/contrast_v25_golden.npz'
    assert digest(golden) == GOLDEN_SHA, 'Historical golden identity mismatch'
    provenance = json.loads((ROOT / 'tests/fixtures/contrast_v25_golden.provenance.json').read_text())
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as td:
        old, manifest = unpack(archive, Path(td), provenance)
        captured = output / 'native-v25.npz'
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        # Inherit NPY_DISABLE_CPU_FEATURES exactly; match the already-loaded
        # parent's effective dispatch below, so late environment edits fail.
        with (output / 'capture.log').open('w', encoding='utf-8') as log:
            subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--child', 'old',
                            str(old), str(captured), str(golden)], cwd=td, env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
        runtime = json.loads(captured.with_suffix('.runtime.json').read_text())
        expected_runtime = {'python': sys.version, 'numpy': np.__version__,
                            'numpy_binary_sha256': digest(core.__file__),
                            'cpu_features': core.__cpu_features__,
                            'power_dispatch': np.lib.introspect.opt_func_info(func_name='power')}
        for key, expected in expected_runtime.items():
            assert runtime[key] == expected, ('Old/current runtime mismatch', key)
        for name, item in runtime['origins'].items():
            relative = Path(item['relative_path']).as_posix()
            assert item['sha256'] == manifest['lumen-edit/' + relative], ('Old import identity mismatch', name)
        assert all('lumen.' + name[:-3] in runtime['origins'] for name in provenance['source_sha256'])
        with np.load(captured, allow_pickle=False) as old_data, np.load(golden, allow_pickle=False) as historical:
            assert json.loads(str(old_data['manifest'])) == json.loads(str(historical['manifest']))
            for key in ('rgb', 'cube', 'grades'):
                assert old_data[key].dtype == historical[key].dtype
                assert old_data[key].shape == historical[key].shape
                assert old_data[key].tobytes() == historical[key].tobytes(), ('Historical input identity mismatch', key)
            assert old_data['outputs'].shape == (18, 17, 19, 3)
            assert old_data['outputs'].dtype == np.dtype('float32')
            write(output / 'historical-comparison.json', {
                'zip_sha256': ZIP_SHA, 'golden_sha256': GOLDEN_SHA,
                'old_golden_bit_differences': [int((old_data['outputs'][i].view(np.uint32) != historical['outputs'][i].view(np.uint32)).sum()) for i in range(18)],
                'interpretation': 'Historical Linux capture comparison is diagnostic; current product must equal verified native v25 bit for bit.'})
    return captured

def unpack(archive, destination, provenance):
    assert digest(archive) == ZIP_SHA, 'Original ZIP identity mismatch'
    manifest = {}
    with zipfile.ZipFile(archive) as z:
        assert len(z.infolist()) <= 2000
        assert sum(i.file_size for i in z.infolist()) <= 150_000_000
        seen = set()
        for item in z.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            assert '\\' not in name and ':' not in name and not name.startswith('/')
            assert parts and '..' not in parts and name.casefold() not in seen
            assert str(PurePosixPath(name)) == name.rstrip('/'), 'Noncanonical ZIP path'
            seen.add(name.casefold())
            mode = item.external_attr >> 16
            assert not stat.S_ISLNK(mode)
            assert stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR)
            path = destination.joinpath(*parts)
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                data = z.read(item)
                path.write_bytes(data)
                manifest[name] = hashlib.sha256(data).hexdigest()
    old = destination / 'lumen-edit'
    for name, sha in provenance['source_sha256'].items():
        assert digest(old / 'lumen' / name) == sha, name
    return old, manifest

def child(mode, repository, output, golden):
    import numpy as np
    assert np.__version__ == '2.2.4'
    repository = Path(repository).resolve()
    output = Path(output).resolve()
    # -I removes cwd/PYTHONPATH; insert only the selected implementation.
    sys.path.insert(0, str(repository))
    if mode == 'old':
        sys.argv = ['capture_contrast_v25.py', str(repository), str(output)]
        runpy.run_path(str(ROOT / 'tests/fixtures/capture_contrast_v25.py'), run_name='__main__')
    else:
        from lumen.color import apply_grade
        from lumen.model import Media, Clip, Project
        sys.path.insert(0, str(ROOT / 'tests'))
        from clip_composite_legacy_fixture import strip_clip_composite_field
        with np.load(golden, allow_pickle=False) as f, tempfile.TemporaryDirectory() as td:
            cube = Path(td) / 'synthetic.cube'
            cube.write_text(str(f['cube']), encoding='utf-8')
            outputs = []
            for record in json.loads(str(f['grades'])):
                if record['lut_path']:
                    record['lut_path'] = str(cube)
                media = Media('golden', 'synthetic-golden.mov', 'golden', 1.)
                payload = Project(media=[media], clips=[Clip('clip', media.id, 0, 0, 0, 1)]).to_dict()
                payload['version'] = 25
                strip_clip_composite_field(payload)
                payload['media'][0].pop('input_encoding')
                payload['clips'][0]['grade'] = record
                grade = Project.from_dict(payload).clips[0].grade
                assert grade.contrast_pivot == .5
                outputs.append(apply_grade(f['rgb'], grade))
            np.savez_compressed(output, rgb=f['rgb'], cube=f['cube'], grades=f['grades'], outputs=np.stack(outputs))
    origins = module_origins(repository)
    from numpy._core import _multiarray_umath as core
    write(output.with_suffix('.runtime.json'), {
        'python': sys.version, 'numpy': np.__version__, 'numpy_binary_sha256': digest(core.__file__),
        'disabled': os.environ.get('NPY_DISABLE_CPU_FEATURES', ''),
        'cpu_features': core.__cpu_features__,
        'power_dispatch': np.lib.introspect.opt_func_info(func_name='power'), 'origins': origins})

def compare(archive, output):
    import numpy as np
    output.mkdir(parents=True, exist_ok=False)
    golden = ROOT / 'tests/fixtures/contrast_v25_golden.npz'
    assert digest(golden) == GOLDEN_SHA
    provenance = json.loads((ROOT / 'tests/fixtures/contrast_v25_golden.provenance.json').read_text())
    report = {'success': False, 'zip_sha256': ZIP_SHA, 'golden_sha256': GOLDEN_SHA,
              'historical_golden_bytes_unchanged': True,
              'compatibility_oracle': 'same_runtime_verified_v25',
              'historical_output_comparison': 'diagnostic_cross_environment_evidence',
              'configurations': []}
    write(output / 'summary.json', report)
    with tempfile.TemporaryDirectory() as td:
        old, manifest = unpack(archive, Path(td), provenance)
        write(output / 'archive-manifest.json', manifest)
        from numpy._core import _multiarray_umath as core
        configurations = [('default', '')]
        if core.__cpu_features__.get('AVX512F'):
            configurations.append(('no_avx512', 'AVX512F'))
        else:
            report['restricted_configuration_not_run'] = 'AVX512F not available; default already lacks it'
        for label, disabled in configurations:
            env = dict(os.environ)
            env.pop('PYTHONPATH', None)
            env.pop('NPY_DISABLE_CPU_FEATURES', None)
            if disabled:
                env['NPY_DISABLE_CPU_FEATURES'] = disabled
            for mode, repository in [('old', old), ('current', ROOT)]:
                path = output / f'{label}-{mode}.npz'
                with (output / f'{label}-{mode}.log').open('w', encoding='utf-8') as log:
                    subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--child', mode,
                                    str(repository), str(path), str(golden)], env=env, cwd=td,
                                   stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            a_runtime = json.loads((output / f'{label}-old.runtime.json').read_text())
            b_runtime = json.loads((output / f'{label}-current.runtime.json').read_text())
            for key in ('python', 'numpy', 'numpy_binary_sha256', 'disabled', 'cpu_features', 'power_dispatch'):
                assert a_runtime[key] == b_runtime[key], ('runtime mismatch', key)
            with np.load(output / f'{label}-old.npz', allow_pickle=False) as a, np.load(output / f'{label}-current.npz', allow_pickle=False) as b, np.load(golden, allow_pickle=False) as g:
                for key in ('rgb', 'cube', 'grades'):
                    assert a[key].dtype == g[key].dtype and np.array_equal(a[key], g[key]), ('capture inputs', key)
                    assert np.array_equal(b[key], g[key]), ('candidate inputs', key)
                assert a['outputs'].shape == b['outputs'].shape == g['outputs'].shape == (18, 17, 19, 3)
                assert a['outputs'].dtype == b['outputs'].dtype == np.dtype('float32')
                scenes = []
                for i in range(18):
                    old_bits = a['outputs'][i].view(np.uint32)
                    current_bits = b['outputs'][i].view(np.uint32)
                    mismatch = old_bits != current_bits
                    scenes.append({'scene': i, 'old_current_different_bits': int(mismatch.sum()),
                                   'first_old_current_mismatch': None if not mismatch.any() else np.argwhere(mismatch)[0].tolist(),
                                   'old_current_max_abs_float_difference': float(np.max(np.abs(a['outputs'][i] - b['outputs'][i]))),
                                   'old_golden_max_uint32_distance': int(np.max(np.abs(old_bits.astype(np.int64) - g['outputs'][i].view(np.uint32).astype(np.int64)))),
                                   'old_golden_max_abs_float_difference': float(np.max(np.abs(a['outputs'][i] - g['outputs'][i]))),
                                   'old_golden_different_bits': int((old_bits != g['outputs'][i].view(np.uint32)).sum()),
                                   'current_golden_different_bits': int((current_bits != g['outputs'][i].view(np.uint32)).sum())})
                entry = {'configuration': label, 'scenes': scenes, 'exact_equal': all(s['old_current_different_bits'] == 0 for s in scenes)}
                report['configurations'].append(entry)
                write(output / 'summary.json', report)
                assert entry['exact_equal'], 'Old/current float32 bit mismatch; see synthetic arrays'
    assert digest(golden) == GOLDEN_SHA
    report['success'] = True
    write(output / 'summary.json', report)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--child':
        child(*sys.argv[2:])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--archive', type=Path, required=True)
        parser.add_argument('--output', type=Path, required=True)
        args = parser.parse_args()
        compare(args.archive.resolve(), args.output.resolve())
