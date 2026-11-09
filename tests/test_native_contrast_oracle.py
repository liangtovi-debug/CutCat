"""The exact native v25 oracle must fail closed, never substitute current output."""
import json
import os
from pathlib import Path
import sys
from types import ModuleType

import numpy as np
import pytest
from scripts import compare_contrast_v25_native as oracle


def archive():
    return Path(os.environ.get('LUMEN_V25_ARCHIVE', oracle.ROOT/'build/private-v25.zip'))


def test_missing_archive_is_an_error(tmp_path):
    with pytest.raises(AssertionError, match='archive is required'):
        oracle.native_capture(tmp_path/'missing.zip', tmp_path/'result')


def test_wrong_archive_identity_is_an_error(tmp_path):
    bad = tmp_path/'wrong.zip'
    bad.write_bytes(b'not the historical implementation')
    with pytest.raises(AssertionError, match='ZIP identity mismatch'):
        oracle.native_capture(bad, tmp_path/'result')


def test_wrong_golden_identity_is_an_error(tmp_path, monkeypatch):
    original = oracle.digest
    monkeypatch.setattr(oracle, 'digest', lambda p: '0'*64 if str(p).endswith('contrast_v25_golden.npz') else original(p))
    with pytest.raises(AssertionError, match='golden identity mismatch'):
        oracle.native_capture(archive(), tmp_path/'result')


def test_wrong_historical_module_digest_is_an_error(tmp_path):
    with pytest.raises(AssertionError, match='color.py'):
        oracle.unpack(archive(), tmp_path/'old', {'source_sha256': {'color.py': '0'*64}})


def test_mixed_module_origin_is_an_error(tmp_path, monkeypatch):
    foreign = ModuleType('lumen.foreign')
    foreign.__file__ = str(tmp_path/'foreign.py')
    monkeypatch.setitem(sys.modules, 'lumen.foreign', foreign)
    with pytest.raises(AssertionError, match='mixed implementation import'):
        oracle.module_origins(oracle.ROOT)


@pytest.mark.parametrize('tamper', ['runtime', 'module', 'inputs'])
def test_invalid_capture_receipt_is_an_error(tmp_path, monkeypatch, tamper):
    original = oracle.subprocess.run
    def altered(command, **kwargs):
        result = original(command, **kwargs)
        captured = Path(command[-2])
        runtime = captured.with_suffix('.runtime.json')
        data = json.loads(runtime.read_text())
        if tamper == 'runtime':
            data['numpy_binary_sha256'] = '0'*64
        elif tamper == 'module':
            data['origins']['lumen.color']['sha256'] = '0'*64
        else:
            with np.load(captured, allow_pickle=False) as f:
                values = {key: f[key].copy() for key in f.files}
            values['rgb'].view(np.uint32).flat[0] ^= 1
            np.savez_compressed(captured, **values)
        runtime.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(oracle.subprocess, 'run', altered)
    with pytest.raises(AssertionError, match={'runtime': 'runtime mismatch', 'module': 'import identity mismatch', 'inputs': 'input identity mismatch'}[tamper]):
        oracle.native_capture(archive(), tmp_path/'result')


def test_product_single_bit_change_fails_compatibility(tmp_path, monkeypatch):
    import test_contrast_pivot as compatibility
    original = compatibility.apply_grade
    def changed(*args, **kwargs):
        result = original(*args, **kwargs).copy()
        result.view(np.uint32).flat[0] ^= 1
        return result
    monkeypatch.setattr(compatibility, 'apply_grade', changed)
    with pytest.raises(AssertionError, match='v25 compatibility scenario 0'):
        compatibility.test_default_pivot_is_bit_exact_against_actual_unchanged_v25_production_capture(tmp_path)


@pytest.mark.parametrize('args,expected', [
    (['tests/test_contrast_pivot.py'], True),
    (['tests/test_contrast_pivot.py::test_example', 'tests/test_native_contrast_oracle.py'], True),
    ([], False), (['tests'], False),
    (['tests/test_caption_render.py'], False),
    (['tests/test_contrast_pivot.py', 'tests/test_ui_style.py'], False),
    (['elsewhere/test_contrast_pivot.py'], False),
])
def test_numeric_profile_is_exact_module_scoped(args, expected):
    from types import SimpleNamespace
    from scripts.pytest_numeric_contrast import numeric_contrast_only
    config = SimpleNamespace(args=args, invocation_params=SimpleNamespace(dir=oracle.ROOT))
    assert numeric_contrast_only(config) is expected


@pytest.mark.parametrize('module', ['PIL', 'PySide6.QtCore', 'lumen.i18n'])
def test_numeric_profile_rejects_ui_imports(monkeypatch, module):
    from types import SimpleNamespace
    from scripts.pytest_numeric_contrast import verify_numeric_imports
    config = SimpleNamespace(args=['tests/test_contrast_pivot.py'], invocation_params=SimpleNamespace(dir=oracle.ROOT))
    monkeypatch.setitem(sys.modules, module, ModuleType(module))
    with pytest.raises(RuntimeError, match='UI/caption dependency'):
        verify_numeric_imports(config)


@pytest.mark.parametrize('numeric', [True, False])
def test_sessionstart_retains_caption_guard_outside_numeric_scope(monkeypatch, numeric):
    import importlib.util
    from types import SimpleNamespace
    from lumen import caption_runtime
    spec = importlib.util.spec_from_file_location('isolated_suite_hooks', oracle.ROOT/'tests/conftest.py')
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    args = ['tests/test_contrast_pivot.py'] if numeric else ['tests/test_contrast_pivot.py', 'tests/test_ui_style.py']
    config = SimpleNamespace(args=args, invocation_params=SimpleNamespace(dir=oracle.ROOT))
    def missing_runtime():
        raise caption_runtime.CaptionRuntimeError('sentinel missing verified caption runtime')
    monkeypatch.setattr(caption_runtime, 'initialize', missing_runtime)
    if numeric:
        # This contract is also collected in full GUI suites; remove existing
        # UI modules only for the scoped hook predicate, never production code.
        monkeypatch.setattr('scripts.pytest_numeric_contrast.verify_numeric_imports', lambda config: None)
        hooks.pytest_sessionstart(SimpleNamespace(config=config))
    else:
        with pytest.raises(caption_runtime.CaptionRuntimeError, match='sentinel'):
            hooks.pytest_sessionstart(SimpleNamespace(config=config))
