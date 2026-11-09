"""Windowed self-test failures remain nonzero and retain bounded diagnostics."""
import builtins
import json
import subprocess

import pytest

from lumen import self_test


def test_early_qt_import_failure_is_reported_and_reraised(tmp_path, monkeypatch):
    destination = tmp_path / 'new-smoke'
    original_import = builtins.__import__
    failure = ModuleNotFoundError("No module named 'PySide6.QtWidgets'")
    def fail_qt(name, *args, **kwargs):
        if name == 'PySide6.QtWidgets':
            raise failure
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', fail_qt)
    monkeypatch.setenv('PRIVATE_SELF_TEST_SENTINEL', 'not-for-diagnostics')
    with pytest.raises(ModuleNotFoundError) as actual:
        self_test.run(destination)
    assert actual.value is failure
    raw = (destination / 'failure.json').read_text()
    report = json.loads(raw)
    assert report['passed'] is False
    assert report['exception_type'] == 'ModuleNotFoundError'
    assert 'PySide6.QtWidgets' in report['error_message']
    assert 'not-for-diagnostics' not in raw and 'PRIVATE_SELF_TEST_SENTINEL' not in raw
    assert not (destination / 'result.json').exists()


def test_existing_evidence_directory_is_never_modified(tmp_path, monkeypatch):
    sentinel = tmp_path / 'failure.json'; sentinel.write_text('original evidence')
    def must_not_run(folder):
        pytest.fail('an existing smoke directory must not be reused')
    monkeypatch.setattr(self_test, '_run', must_not_run)
    with pytest.raises(FileExistsError):
        self_test.run(tmp_path)
    assert sentinel.read_text() == 'original evidence'
    assert list(tmp_path.iterdir()) == [sentinel]


def test_failure_text_is_bounded_and_subprocess_status_retained(tmp_path, monkeypatch):
    error = subprocess.CalledProcessError(17, ['synthetic-tool', 'x' * 10000])
    def fail(folder):
        raise error
    monkeypatch.setattr(self_test, '_run', fail)
    with pytest.raises(subprocess.CalledProcessError) as actual:
        self_test.run(tmp_path / 'failed')
    assert actual.value is error
    result = json.loads((tmp_path / 'failed' / 'failure.json').read_text())
    assert result['subprocess_returncode'] == 17
    assert len(result['error_message']) == 2048
    assert set(result) == {'passed', 'platform', 'frozen', 'exception_type',
                           'error_message', 'subprocess_returncode'}


def test_failure_report_collision_does_not_replace_original_error(tmp_path, monkeypatch):
    error = RuntimeError('original synthetic failure')
    def fail(folder):
        (folder / 'failure.json').write_text('stage-specific evidence')
        raise error
    monkeypatch.setattr(self_test, '_run', fail)
    with pytest.raises(RuntimeError) as actual:
        self_test.run(tmp_path / 'failed')
    assert actual.value is error
    assert (tmp_path / 'failed' / 'failure.json').read_text() == 'stage-specific evidence'


def test_success_returns_original_status_without_failure_file(tmp_path, monkeypatch):
    def succeed(folder):
        assert folder.is_dir()
        (folder / 'result.json').write_text('{"passed": true}')
        return 0
    monkeypatch.setattr(self_test, '_run', succeed)
    assert self_test.run(tmp_path / 'success') == 0
    assert not (tmp_path / 'success' / 'failure.json').exists()


def test_smoke_uses_only_local_font_fallbacks(monkeypatch):
    import lumen.caption_render as caption
    families = []
    def resolve(style):
        families.append(style.font_family)
        if style.font_family != 'DejaVu Sans':
            raise caption.CaptionRenderError('Fixture face is not installed')
    monkeypatch.setattr(caption, 'font_identity', resolve)
    style = self_test._self_test_caption_style('')
    assert style.font_family == 'DejaVu Sans'
    assert families == ['Arial', 'Helvetica', 'DejaVu Sans']
    assert not style.font_directory


def test_explicit_cjk_font_failure_never_silently_falls_back(monkeypatch):
    import lumen.caption_render as caption
    families = []
    def fail(style):
        families.append(style.font_family)
        raise caption.CaptionRenderError('Invalid configured fixture')
    monkeypatch.setattr(caption, 'font_identity', fail)
    with pytest.raises(caption.CaptionRenderError, match='Invalid configured fixture'):
        self_test._self_test_caption_style('/synthetic-fixture')
    assert families == ['Noto Sans CJK SC']


def test_smoke_requires_some_real_local_caption_font(monkeypatch):
    import lumen.caption_render as caption
    def fail(style):
        raise caption.CaptionRenderError('Missing local face')
    monkeypatch.setattr(caption, 'font_identity', fail)
    with pytest.raises(RuntimeError, match='requires a local'):
        self_test._self_test_caption_style('')
