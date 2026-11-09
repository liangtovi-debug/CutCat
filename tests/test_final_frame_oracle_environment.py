"""Fail-closed isolation contracts for the final-frame historical-code oracle."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lumen.media import ffmpeg_available
from scripts import benchmark_final_frame_preview as fixture
from scripts import final_frame_code_oracle as oracle
from scripts import benchmark_range_preview as runtime


def receipt():
    return {'runtime': {'tools': {'ffmpeg': {'version': 'ffmpeg version 7.1.1', 'sha256': 'verified'},
                                 'ffprobe': {'version': 'ffprobe version 7.1.1', 'sha256': 'verified'}},
                        'shaping': {'raqm': 'same'}, 'caption_bootstrap_sha256': 'same'},
            'font': {'path': '/same/font.otf', 'face': 0, 'sha256': 'same'}}


@pytest.mark.parametrize('mutation', ['version', 'tool_hash', 'shaping', 'bootstrap', 'font', 'face'])
def test_cross_runtime_or_font_receipts_are_not_equality_evidence(mutation):
    before = receipt(); changed = deepcopy(before)
    if mutation == 'version': changed['runtime']['tools']['ffmpeg']['version'] = 'ffmpeg version 9.0.2'
    elif mutation == 'tool_hash': changed['runtime']['tools']['ffprobe']['sha256'] = 'different'
    elif mutation == 'shaping': changed['runtime']['shaping']['raqm'] = 'different'
    elif mutation == 'bootstrap': changed['runtime']['caption_bootstrap_sha256'] = 'different'
    elif mutation == 'font': changed['font']['sha256'] = 'different'
    else: changed['font']['face'] = 1
    oracle.require_same_runtime(before, deepcopy(before))
    with pytest.raises(AssertionError, match='mismatch'):
        oracle.require_same_runtime(before, changed)


@pytest.mark.parametrize('missing', ['FFMPEG', 'FFPROBE'])
def test_final_frame_children_require_both_reference_tools_on_windows(monkeypatch, tmp_path, missing):
    monkeypatch.setattr(sys, 'platform', 'win32')
    for name in ('FFMPEG', 'FFPROBE'):
        monkeypatch.setenv('LUMEN_REFERENCE_' + name, 'reference-' + name)
    monkeypatch.delenv('LUMEN_REFERENCE_' + missing)
    with pytest.raises(RuntimeError, match='Provision'):
        oracle.oracle_child_environment(tmp_path, fixture.ROOT)


def test_code_oracle_environment_does_not_change_parent_or_performance_scope(monkeypatch, tmp_path):
    monkeypatch.setenv('LUMEN_FFMPEG', '/production/9/ffmpeg')
    monkeypatch.setenv('LUMEN_FFPROBE', '/production/9/ffprobe')
    monkeypatch.setenv('LUMEN_REFERENCE_FFMPEG', '/reference/7/ffmpeg')
    monkeypatch.setenv('LUMEN_REFERENCE_FFPROBE', '/reference/7/ffprobe')
    before = dict(os.environ)
    isolated = oracle.oracle_child_environment(tmp_path, fixture.ROOT)
    performance = fixture.frozen_environment(fixture.ROOT, tmp_path)
    assert isolated['LUMEN_FFMPEG'] == '/reference/7/ffmpeg'
    assert isolated['LUMEN_FFPROBE'] == '/reference/7/ffprobe'
    assert performance['LUMEN_FFMPEG'] == before['LUMEN_FFMPEG']
    assert performance['LUMEN_FFPROBE'] == before['LUMEN_FFPROBE']
    assert dict(os.environ) == before


@pytest.mark.parametrize('frozen', [True, False])
def test_child_bootstrap_precedes_every_application_and_pillow_import(frozen, tmp_path):
    # Execute the real generated source but replace runtime initialization with a
    # witness. It fails deliberately at that boundary, before tools/rendering.
    source = oracle.child_source(frozen)
    witness = "assert not any(n == 'lumen' or n.startswith('lumen.') or n == 'PIL' or n.startswith('PIL.') for n in sys.modules); raise RuntimeError('bootstrap-first-witness')"
    source = source.replace('_provider,runtime=initialize_reference_runtime(sys.argv[3],sys.argv[4],json.loads(sys.argv[5]))', witness)
    (tmp_path/'request.json').write_text('{}')
    result = subprocess.run([sys.executable, '-c', source, str(tmp_path), str(fixture.ROOT)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and 'RuntimeError: bootstrap-first-witness' in result.stderr


@pytest.mark.parametrize('early', ['lumen', 'PIL'])
def test_runtime_rejects_preimported_application_or_pillow(early, tmp_path):
    source = inspect.getsource(runtime.initialize_reference_runtime)
    source += f"\nimport sys,types\nsys.modules[{early!r}]=types.ModuleType({early!r})\ninitialize_reference_runtime(None,None,{{}})"
    result = subprocess.run([sys.executable, '-c', source], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode != 0 and 'imported before bootstrap' in result.stderr


@pytest.mark.parametrize('fault', ['hash', 'version'])
def test_reference_runtime_rejects_wrong_tool_identity(monkeypatch, tmp_path, fault):
    # Real file/lease/hash and real initializer, a fake tool version reply only.
    import shutil
    executable = tmp_path/'tool'; executable.write_bytes(b'verified-test-tool')
    bootstrap = tmp_path/'bootstrap'; bootstrap.write_text('raise RuntimeError("must not execute")')
    # initialize_reference_runtime intentionally normalizes only child env; this
    # unit-level fault injection must restore those writes in the parent test.
    monkeypatch.setenv('LUMEN_FFMPEG', 'unit-fault-tool')
    monkeypatch.setenv('LUMEN_FFPROBE', 'unit-fault-probe')
    monkeypatch.setattr(shutil, 'which', lambda value: str(executable))
    monkeypatch.setattr(runtime, 'reference_tool_lease', lambda path: open(path, 'rb'))
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='ffmpeg version 9.0.2\n'))
    # The initializer intentionally rejects any application/PIL already loaded.
    # Run its function with only that guard's view isolated, not mutate sys.modules.
    fake_sys = SimpleNamespace(platform='win32' if fault == 'hash' else 'linux', modules={})
    original_import = __import__
    def imports(name, *args, **kwargs):
        return fake_sys if name == 'sys' else original_import(name, *args, **kwargs)
    namespace = dict(reference_tool_lease=runtime.reference_tool_lease,
                     verify_reference_tool_leases=runtime.verify_reference_tool_leases,
                     __builtins__=dict(vars(__import__('builtins')), __import__=imports))
    exec(inspect.getsource(runtime.initialize_reference_runtime), namespace)
    with pytest.raises(AssertionError, match='pinned binary' if fault == 'hash' else 'FFmpeg 7'):
        namespace['initialize_reference_runtime'](bootstrap, hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
                                                 {'ffmpeg.exe': 'wrong'})


def test_frozen_bytes_and_legacy_only_schema_transport_are_guarded(tmp_path):
    root = fixture.frozen_baseline(fixture.ROOT)
    for name, expected in fixture.BASELINE_VISUAL_DEPENDENCIES.items():
        source = root/name
        if Path(name).stem in oracle.FROZEN_LEAVES:
            source = root/'tests/frozen_final_frame'/Path(name).name
        destination = tmp_path/name; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    (tmp_path/'lumen/render.py').write_bytes((root/'tests/frozen_final_frame/render.py').read_bytes())
    assert fixture.frozen_baseline(tmp_path) == tmp_path
    renderer = tmp_path/'lumen/render.py'; renderer.write_bytes(renderer.read_bytes() + b'\n')
    with pytest.raises(AssertionError, match='Frozen full renderer changed'):
        fixture.frozen_baseline(tmp_path)
    from lumen.model import Project
    project = Project(); before = deepcopy(project.to_dict())
    fixture.write_frozen_project(project, tmp_path/'old.json')
    fixture.write_project(project, tmp_path/'current.json')
    assert json.loads((tmp_path/'old.json').read_text())['version'] == 41
    assert json.loads((tmp_path/'current.json').read_text()) == before
    assert before['version'] == 43 and project.to_dict() == before
    linked = deepcopy(before); linked['av_links'] = [{'id': 'preserve-not-silently-drop'}]
    with pytest.raises(AssertionError):
        fixture.write_frozen_project(SimpleNamespace(to_dict=lambda: linked), tmp_path/'forbidden.json')
    assert linked['av_links'] == [{'id': 'preserve-not-silently-drop'}]
    assert not (tmp_path/'forbidden.json').exists()


@pytest.mark.parametrize('frozen,corruption', [(True, 'origin'), (False, 'origin'), (True, 'candidate')])
def test_application_import_validation_rejects_mixed_sources(frozen, corruption):
    root = fixture.ROOT
    def module(path):
        return SimpleNamespace(__file__=str(path), __spec__=SimpleNamespace(origin=str(path)))
    render = root/('tests/frozen_final_frame/render.py' if frozen else 'lumen/render.py')
    modules = {'lumen': module(root/'lumen/__init__.py'), 'lumen.render': module(render)}
    if not frozen: modules['lumen.final_frame_preview'] = module(root/'lumen/final_frame_preview.py')
    oracle.verify_application_imports(root, root, frozen, modules)
    if corruption == 'origin': modules['lumen.render'].__spec__.origin += '.wrong'
    else: modules['lumen.final_frame_preview'] = module(root/'lumen/final_frame_preview.py')
    with pytest.raises(AssertionError, match='origin mismatch|Candidate backend'):
        oracle.verify_application_imports(root, root, frozen, modules)


def test_bad_font_fixture_fails_closed_before_scene_is_returned(tmp_path, monkeypatch):
    from scripts.provision_test_font import ProvisionError
    # No media command required: reuse a minimal synthetic model object.
    from lumen.model import Media
    monkeypatch.setenv('LUMEN_TEST_CJK_FONT_DIR', str(tmp_path/'missing'))
    media = Media('fake', '/not-opened.mkv', 'fixture', 10, 64, 48, True, False)
    with pytest.raises(ProvisionError, match='Missing test font fixture'):
        fixture.make_scene(tmp_path/'scene', source=media)


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')
def test_exact_assertion_rejects_one_real_candidate_byte(tmp_path, monkeypatch):
    """Exercise the retained acceptance assertion, not a synthetic != check."""
    from dataclasses import replace
    import importlib.util
    spec = importlib.util.spec_from_file_location('_final_frame_acceptance',
        fixture.ROOT/'tests/test_final_frame_preview_oracle.py')
    acceptance = importlib.util.module_from_spec(spec); spec.loader.exec_module(acceptance)
    source = fixture.make_source(tmp_path/'source', Fraction(24))
    original = acceptance.candidate_snapshots
    injected = []
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        full = (Path(kwargs['reference_directory'])/'full.yuv').read_bytes()
        size = len(result[0].yuv420p)
        assert result[0].yuv420p == full[5*size:6*size]  # Real positive control.
        raw = bytearray(result[0].yuv420p); raw[0] ^= 1
        injected.append(True)
        return [replace(result[0], yuv420p=bytes(raw))]
    monkeypatch.setattr(acceptance, 'candidate_snapshots', corrupt)
    with pytest.raises(AssertionError):
        acceptance.test_default_effect_precision_paths((tmp_path, Fraction(24), source), 'legacy_blur')
    assert injected == [True]


def test_worker_timeout_terminates_active_descendants(tmp_path):
    import time
    # Grandchild deliberately survives a polite POSIX TERM; the group cleanup
    # must still kill it even when the Python leader has already exited.
    grandchild = ("import os,time,signal,pathlib; "
                  "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "pathlib.Path('grandchild.pid').write_text(str(os.getpid())); "
                  "time.sleep(120)")
    worker = ("import os,pathlib,subprocess,sys,time; "
              "pathlib.Path('worker.pid').write_text(str(os.getpid())); "
              f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); time.sleep(120)")
    began = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        oracle.run_isolated_process([sys.executable, '-c', worker], tmp_path, dict(os.environ), timeout=10)
    assert time.monotonic() - began < 35
    for name in ('worker', 'grandchild'):
        pid = int((tmp_path/(name + '.pid')).read_text())
        deadline = time.monotonic() + 3
        while process_alive(pid) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not process_alive(pid), f'{name} survived the worker timeout'


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            assert ctypes.get_last_error() == 87  # Process no longer exists.
            return False
        try: return kernel.WaitForSingleObject(handle, 0) == 258
        finally: kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    status = Path(f'/proc/{pid}/stat')
    if status.exists():
        return status.read_text().split(') ', 1)[1].split()[0] != 'Z'
    return True  # macOS has no /proc: a successful kill(pid, 0) means alive.


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group supervisor; Windows uses taskkill /T')
def test_supervisor_cleans_worker_tree_when_outer_pytest_is_killed(tmp_path):
    import signal
    import time
    isolated = tmp_path/'isolated'; isolated.mkdir()
    grandchild = ("import os,time,signal,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "pathlib.Path('grandchild.pid').write_text(str(os.getpid())); time.sleep(120)")
    worker = ("import os,pathlib,subprocess,sys,time; "
              "pathlib.Path('worker.pid').write_text(str(os.getpid())); "
              "pathlib.Path('supervisor.pid').write_text(str(os.getppid())); "
              f"subprocess.Popen([sys.executable,'-c',{grandchild!r}]); time.sleep(120)")
    outer = ("from scripts.final_frame_code_oracle import run_isolated_process; "
             "from pathlib import Path; import os,sys; "
             f"run_isolated_process([sys.executable,'-c',{worker!r}],Path({str(isolated)!r}),dict(os.environ),timeout=60)")
    process = subprocess.Popen([sys.executable, '-c', outer], cwd=fixture.ROOT,
                               env=dict(os.environ, PYTHONPATH=str(fixture.ROOT)),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not (isolated/'grandchild.pid').exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(.05)
        assert (isolated/'grandchild.pid').exists()
        process.kill(); process.wait(timeout=5)
        for name in ('supervisor', 'worker', 'grandchild'):
            pid = int((isolated/(name+'.pid')).read_text())
            deadline = time.monotonic() + 5
            while process_alive(pid) and time.monotonic() < deadline:
                time.sleep(.05)
            assert not process_alive(pid), f'{name} survived its outer parent'
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=5)
        if (isolated/'supervisor.pid').exists():
            try: os.killpg(int((isolated/'supervisor.pid').read_text()), signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize('changed', ['font', 'license'])
def test_child_font_receipt_rejects_fixture_changes(tmp_path, monkeypatch, changed):
    from lumen import caption_render
    font = tmp_path/'font.otf'; font.write_bytes(b'verified-font')
    license = tmp_path/'LICENSE'; license.write_bytes(b'verified-license')
    verified = dict(font_file=str(font), font_sha256=hashlib.sha256(font.read_bytes()).hexdigest(),
                    license_file=str(license), license_sha256=hashlib.sha256(license.read_bytes()).hexdigest())
    project = SimpleNamespace(captions=[True], caption_style=SimpleNamespace(font_family='test'))
    monkeypatch.setattr(caption_render, 'font_identity', lambda style: (str(font), font.stat().st_size, 0, 0, 0))
    assert oracle.font_receipt(project, verified)['sha256'] == verified['font_sha256']
    (font if changed == 'font' else license).write_bytes(b'changed-after-parent-verification')
    with pytest.raises(AssertionError, match='Verified fixture .* changed'):
        oracle.font_receipt(project, verified)


@pytest.mark.parametrize('fail', [False, True])
def test_code_worker_preserves_only_receipts_and_logs_outside_media_tmp(tmp_path, monkeypatch, fail):
    from lumen.model import Project
    evidence = tmp_path/'evidence'
    monkeypatch.setenv('RANGE_ORACLE_EVIDENCE', str(evidence))
    monkeypatch.setenv('LUMEN_REFERENCE_FFMPEG', 'reference-tool')
    monkeypatch.setenv('LUMEN_REFERENCE_FFPROBE', 'reference-probe')
    def worker(command, directory, environment):
        (directory/'candidate.json').write_text('{"runtime":"unit-test"}')
        (directory/'stdout.log').write_text('stdout')
        (directory/'stderr.log').write_text('failure detail' if fail else '')
        (directory/'frames.yuv').write_bytes(b'not-for-artifact')
        if fail: raise RuntimeError('injected worker failure')
    monkeypatch.setattr(oracle, 'run_isolated_process', worker)
    if fail:
        with pytest.raises(RuntimeError, match='injected worker failure'):
            oracle.run_child(Project(), tmp_path/'worker', frozen=False)
    else:
        oracle.run_child(Project(), tmp_path/'worker', frozen=False)
    records = list((evidence/'final-frame-code').iterdir())
    assert len(records) == 1
    assert {p.name for p in records[0].iterdir()} == {'candidate.json', 'stdout.log', 'stderr.log'}
    assert json.loads((records[0]/'candidate.json').read_text()) == {'runtime': 'unit-test'}
    assert (records[0]/'stderr.log').read_text() == ('failure detail' if fail else '')


def test_production_worker_preserves_runtime_not_raw_pixels(tmp_path, monkeypatch):
    from lumen import final_frame_preview
    from scripts import production_final_frame_oracle as production
    evidence = tmp_path/'evidence'
    monkeypatch.setenv('RANGE_ORACLE_EVIDENCE', str(evidence))
    def full(project, directory):
        directory.mkdir(parents=True)
        (directory/'runtime.json').write_text('{"runtime":"unit-test"}')
        (directory/'full.yuv').write_bytes(b'not-for-artifact')
        return b'123456', b''
    monkeypatch.setattr(production, 'full_av', full)
    monkeypatch.setattr(final_frame_preview, 'capture_final_frame_snapshot',
                        lambda *a, **kw: SimpleNamespace(width=2, height=2, yuv420p=b'123456', rgb_bytes=b'x'*12))
    project = SimpleNamespace(width=2, height=2, duration_frames=1, to_dict=lambda: {})
    production.compare_frames(project, tmp_path/'run')
    records = list((evidence/'production-final-frame').iterdir())
    assert len(records) == 1 and records[0].suffix == '.json'
    assert json.loads(records[0].read_text()) == {'runtime': 'unit-test'}
