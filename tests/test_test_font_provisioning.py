"""Offline provisioning guards; the caption integration test uses the real OTF."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

from scripts import provision_test_font as setup


def test_fixture_sources_are_official_commit_pins():
    assert len(setup.COMMIT) == 40
    assert setup.FONT_URL == (f"https://raw.githubusercontent.com/notofonts/noto-cjk/{setup.COMMIT}"
                              "/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf")
    assert setup.LICENSE_URL == f"https://raw.githubusercontent.com/notofonts/noto-cjk/{setup.COMMIT}/LICENSE"
    assert len(setup.FONT_SHA256) == len(setup.LICENSE_SHA256) == 64
    assert setup.FONT_SHA256 != setup.LICENSE_SHA256


def test_download_checks_integrity_and_verifies_cache(tmp_path, monkeypatch):
    # This payload tests the download primitive only; it is never a font or a
    # caption-render fixture, and verify_fixture would reject it.
    payload = b"download integrity unit test"

    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/file"

    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: Response(payload))
    target = tmp_path / "file"
    with pytest.raises(setup.ProvisionError, match="SHA256 mismatch"):
        setup.checked_download("https://example.test/file", target, "0" * 64)
    assert not target.exists()
    assert not target.with_suffix(".download").exists()
    expected = hashlib.sha256(payload).hexdigest()
    setup.checked_download("https://example.test/file", target, expected)
    assert target.read_bytes() == payload
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: pytest.fail("verified cache must be offline"))
    assert setup.checked_download("https://example.test/file", target, expected) == target


def test_download_refuses_non_https_and_redirect_downgrade(tmp_path, monkeypatch):
    with pytest.raises(setup.ProvisionError, match="HTTPS"):
        setup.checked_download("http://example.test/file", tmp_path / "file", "0" * 64)

    class Response(io.BytesIO):
        def geturl(self):
            return "http://example.test/file"

    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: Response(b"untrusted"))
    with pytest.raises(setup.ProvisionError, match="HTTPS"):
        setup.checked_download("https://example.test/file", tmp_path / "file", "0" * 64)
    assert not list(tmp_path.iterdir())


def test_download_enforces_size_limit_and_preserves_existing_file(tmp_path, monkeypatch):
    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/file"

    target = tmp_path / "file"
    target.write_bytes(b"previous")
    monkeypatch.setattr(setup, "MAX_DOWNLOAD_BYTES", 8)
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: Response(b"too long payload"))
    with pytest.raises(setup.ProvisionError, match="limit"):
        setup.checked_download("https://example.test/file", target, "0" * 64)
    assert target.read_bytes() == b"previous"
    assert not target.with_suffix(".download").exists()


def test_fixture_requires_license_and_rejects_modified_bytes(tmp_path):
    with pytest.raises(setup.ProvisionError, match="Missing test font fixture"):
        setup.verify_fixture(tmp_path)
    (tmp_path / "LICENSE").write_text("not the upstream license", encoding="utf-8")
    with pytest.raises(setup.ProvisionError, match="SHA256 mismatch"):
        setup.verify_fixture(tmp_path)


def test_missing_fixture_is_failure_without_implicit_download_or_export(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: pytest.fail("download requires explicit flag"))
    env_file = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    report = tmp_path / "report.json"
    assert setup.main(["--cache-dir", str(tmp_path / "missing"), "--report", str(report)]) == 1
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["license"] == "OFL-1.1"
    assert result["application_bundling"] is False
    assert result["system_installation"] is False
    assert "Missing test font fixture" in result["error"]
    assert not env_file.exists()


def test_failed_download_does_not_verify_or_export(tmp_path, monkeypatch):
    def reject(*args):
        raise setup.ProvisionError("SHA256 mismatch")

    monkeypatch.setattr(setup, "checked_download", reject)
    monkeypatch.setattr(setup, "verify_fixture", lambda *a: pytest.fail("download failed"))
    monkeypatch.setattr(setup, "export_github_environment", lambda *a: pytest.fail("download failed"))
    report = tmp_path / "report.json"
    assert setup.main(["--download", "--cache-dir", str(tmp_path / "cache"), "--report", str(report)]) == 1
    assert json.loads(report.read_text(encoding="utf-8"))["error"] == "SHA256 mismatch"


def test_github_export_uses_only_explicit_test_font_variable(tmp_path, monkeypatch):
    env_file = tmp_path / "github-env"
    path_file = tmp_path / "github-path"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    monkeypatch.setenv("GITHUB_PATH", str(path_file))
    setup.export_github_environment(tmp_path / "fonts")
    assert env_file.read_text(encoding="utf-8") == f"LUMEN_TEST_CJK_FONT_DIR={(tmp_path / 'fonts').resolve()}\n"
    assert not path_file.exists()
    with pytest.raises(setup.ProvisionError, match="newlines"):
        setup.export_github_environment(tmp_path / "bad\nname")


@pytest.mark.parametrize("directory", ["", " ", "missing", "corrupt"])
def test_explicit_cjk_fixture_errors_fail_instead_of_skip(tmp_path, monkeypatch, directory):
    # Invoke the real integration-test selection path, stopping before render.
    spec = importlib.util.spec_from_file_location("caption_test_guards", Path(__file__).with_name("test_caption_render.py"))
    caption_tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(caption_tests)
    if directory in ("missing", "corrupt"):
        target = tmp_path / directory
        if directory == "corrupt":
            target.mkdir()
            (target / "LICENSE").write_text("modified", encoding="utf-8")
        directory = str(target)
    monkeypatch.setenv("LUMEN_TEST_CJK_FONT_DIR", directory)
    monkeypatch.setattr(caption_tests, "font_identity", lambda *a: pytest.fail("must not use system font fallback"))
    with pytest.raises(pytest.fail.Exception, match="configured but empty|Configured CJK test fixture is invalid"):
        caption_tests.test_real_chinese_font_decoded_pixels(tmp_path, engine=None)
