"""Offline unit tests and a real gate for the external CI media dependency."""
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "provision_ffmpeg", Path(__file__).resolve().parents[1] / "scripts/provision_ffmpeg.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def formula(name="ffmpeg@7", tags=("arm64_sequoia",)):
    return {"name": name, "tap": "homebrew/core", "versions": {"stable": setup.BREW_VERSION},
            "revision": setup.BREW_REVISION,
            "bottle": {"stable": {"files": {tag: {"sha256": setup.BREW_BOTTLES.get(tag, "a" * 64)}
                                                for tag in tags}}}}


def test_pins_are_versioned_and_distinct_from_runtime_bundle():
    assert f"/download/{setup.WINDOWS_VERSION}/" in setup.WINDOWS_URL
    assert "latest" not in setup.WINDOWS_URL
    assert len(setup.WINDOWS_SHA256) == 64
    assert setup.BREW_FORMULA == "ffmpeg@7"
    assert all(len(value) == 64 for value in setup.BREW_BOTTLES.values())


def test_check_download_integrity_before_extraction(tmp_path, monkeypatch):
    payload = b"a tiny trusted archive fixture"
    class Response(io.BytesIO):
        def geturl(self):
            return "https://example.test/pinned.zip"
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: Response(payload))
    target = tmp_path / "archive.zip"
    with pytest.raises(setup.ProvisionError, match="SHA256 mismatch"):
        setup.checked_download("https://example.test/pinned.zip", target, "0" * 64)
    assert not target.exists()
    assert not target.with_suffix(".zip.download").exists()
    expected = hashlib.sha256(payload).hexdigest()
    assert setup.checked_download("https://example.test/pinned.zip", target, expected) == target
    assert target.read_bytes() == payload
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *a, **k: pytest.fail("cache should be verified locally"))
    assert setup.checked_download("https://example.test/pinned.zip", target, expected) == target


def test_download_rejects_non_https(tmp_path):
    with pytest.raises(setup.ProvisionError, match="HTTPS"):
        setup.checked_download("http://example.test/file", tmp_path / "file", "0" * 64)


def test_windows_extraction_does_not_trust_archive_paths(tmp_path):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("vendor/bin/ffmpeg.exe", b"fake ffmpeg")
        stream.writestr("vendor/bin/ffprobe.exe", b"fake ffprobe")
        stream.writestr("../../unexpected.txt", b"must not be written")
    ffmpeg, ffprobe = setup.extract_windows_tools(archive, tmp_path / "tools")
    assert ffmpeg.read_bytes() == b"fake ffmpeg"
    assert ffprobe.read_bytes() == b"fake ffprobe"
    assert sorted(p.name for p in ffmpeg.parent.iterdir()) == ["ffmpeg.exe", "ffprobe.exe"]
    assert not (tmp_path / "unexpected.txt").exists()


@pytest.mark.parametrize("duplicate,missing", [(True, False), (False, True)])
def test_windows_extraction_rejects_ambiguous_or_missing_executable(tmp_path, duplicate, missing):
    archive = tmp_path / "ffmpeg.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        if not missing:
            stream.writestr("vendor/bin/ffmpeg.exe", b"fake")
        if duplicate:
            stream.writestr("second/bin/ffmpeg.exe", b"fake")
        stream.writestr("vendor/bin/ffprobe.exe", b"fake")
    with pytest.raises(setup.ProvisionError, match="exactly one"):
        setup.extract_windows_tools(archive, tmp_path / "tools")


def test_windows_checksum_failure_never_extracts_or_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.platform, "machine", lambda: "AMD64")
    def reject(*args):
        raise setup.ProvisionError("SHA256 mismatch")
    monkeypatch.setattr(setup, "checked_download", reject)
    monkeypatch.setattr(setup, "extract_windows_tools", lambda *a: pytest.fail("must not extract"))
    monkeypatch.setattr(setup, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(setup.ProvisionError, match="SHA256"):
        setup.install_windows(tmp_path)


def test_macos_never_selects_newer_os_or_wrong_architecture_bottle():
    value = formula(tags=("arm64_sonoma", "arm64_sequoia", "arm64_tahoe", "sonoma"))
    assert setup.compatible_bottle(value, 14, "arm64")[0] == "arm64_sonoma"
    assert setup.compatible_bottle(value, 15, "arm64")[0] == "arm64_sequoia"
    assert setup.compatible_bottle(value, 15, "x86_64")[0] == "sonoma"
    with pytest.raises(setup.ProvisionError, match="source-build fallback is disabled"):
        setup.compatible_bottle(formula("harfbuzz"), 14, "arm64")
    with pytest.raises(setup.ProvisionError, match="source-build fallback is disabled"):
        setup.compatible_bottle(formula(), 15, "x86_64")
    assert setup.compatible_bottle(formula(tags=("all",)), 15, "arm64")[0] == "all"


@pytest.mark.parametrize("field,value", [("revision", 99), ("tap", "other/tap"), ("name", "ffmpeg")])
def test_macos_root_metadata_pin_rejects_drift(field, value):
    item = formula()
    tag, bottle = setup.compatible_bottle(item, 15, "arm64")
    setup.check_brew_pin(item, tag, bottle)
    item[field] = value
    with pytest.raises(setup.ProvisionError, match="version/revision"):
        setup.check_brew_pin(item, tag, bottle)


def test_macos_root_version_and_hash_are_pinned():
    item = formula()
    tag, bottle = setup.compatible_bottle(item, 15, "arm64")
    item["versions"]["stable"] = "7.1.999"
    with pytest.raises(setup.ProvisionError, match="version/revision"):
        setup.check_brew_pin(item, tag, bottle)
    with pytest.raises(setup.ProvisionError, match="SHA256"):
        setup.check_brew_pin(formula(), tag, {"sha256": "0" * 64})


def test_macos_checks_transitive_bottles_before_install(monkeypatch):
    root = formula(tags=("arm64_sonoma",))
    dep = formula("freetype", tags=("arm64_sequoia",))
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(setup.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(setup.platform, "mac_ver", lambda: ("14.8", (), ""))
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1] == "deps":
            assert command[2:] == ["--formula", "--union", setup.BREW_FORMULA]
            return "freetype\n"
        if command[1] == "info":
            return json.dumps({"formulae": [root, dep]})
        if command[1] == "update":
            return ""
        pytest.fail(f"No downloads or installations before preflight succeeds: {command}")
    monkeypatch.setattr(setup, "run", fake_run)
    with pytest.raises(setup.ProvisionError, match="freetype"):
        setup.install_macos()
    assert all("install" not in command and "fetch" not in command for command in commands)


def listings():
    filters = "\n".join(f" TSC {name} V->V test" for name in setup.REQUIRED_FILTERS)
    encoders = "\n".join(f" V....D {name} test" for name in setup.REQUIRED_ENCODERS)
    return filters, encoders


def test_capability_gate_requires_exact_entries_not_mentions():
    filters, encoders = listings()
    setup.require_capabilities(filters, encoders)
    with pytest.raises(setup.ProvisionError, match="zscale"):
        setup.require_capabilities(filters.replace("zscale", "zscale_other") + "\nmentions zscale", encoders)
    with pytest.raises(setup.ProvisionError, match="libx264"):
        setup.require_capabilities(filters, encoders.replace("libx264", "libx264rgb"))


@pytest.mark.parametrize("flags", ["T.", "TS", ".S", "..", "T.C", "TSC", "TS.", "..."])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_capability_gate_accepts_ffmpeg_7_and_9_filter_rows(flags, newline):
    # n7.1.5 prints timeline/threading/command flags; n9.0.2 prints only the
    # first two. Encoder rows retain their six-character flag field.
    filters, encoders = listings()
    filters = filters.replace("TSC", flags).replace("\n", newline)
    setup.require_capabilities(filters, encoders)
    with pytest.raises(setup.ProvisionError, match="zscale"):
        setup.require_capabilities(filters.replace("zscale", "zscale_other"), encoders)


@pytest.mark.parametrize("flags", ["T", "TSC.", "TSC..", "TSC..."])
def test_capability_gate_rejects_unknown_filter_flag_widths(flags):
    filters, encoders = listings()
    with pytest.raises(setup.ProvisionError, match="drawtext"):
        setup.require_capabilities(filters.replace("TSC", flags), encoders)


@pytest.mark.parametrize("flags", ["TS", "TSC"])
def test_capability_gate_does_not_accept_filter_flags_for_encoders(flags):
    filters, encoders = listings()
    with pytest.raises(setup.ProvisionError, match="libx264"):
        setup.require_capabilities(filters, encoders.replace("V....D", flags))


@pytest.mark.parametrize("flags", ["TS", "TSC"])
def test_capability_gate_does_not_join_filter_flags_and_mentions_across_lines(flags):
    filters, encoders = listings()
    with pytest.raises(setup.ProvisionError, match="drawtext"):
        setup.require_capabilities(filters.replace("TSC ", flags + "\n"), encoders)


def test_ffmpeg_9_capability_success_still_requires_real_smoke(tmp_path, monkeypatch):
    ffmpeg, ffprobe = tmp_path / "ffmpeg", tmp_path / "ffprobe"
    ffmpeg.write_bytes(b"test")
    ffprobe.write_bytes(b"test")
    filters, encoders = listings()
    def fake_run(command, **kwargs):
        if command[-1] == "-version":
            return f"{Path(command[0]).name} version 9.0.2\n"
        return filters.replace("TSC", "TS") if command[-1] == "-filters" else encoders
    def reject_smoke(*args):
        raise setup.ProvisionError("real smoke failed")
    monkeypatch.setattr(setup, "run", fake_run)
    monkeypatch.setattr(setup, "exercise_tools", reject_smoke)
    monkeypatch.setattr(setup, "export_github_paths", lambda *a: pytest.fail("smoke failed; must not export"))
    report = tmp_path / "result.json"
    assert setup.main(["--ffmpeg", str(ffmpeg), "--ffprobe", str(ffprobe),
                       "--report", str(report)]) == 1
    assert json.loads(report.read_text())["error"] == "real smoke failed"


def test_version_check_precedes_capability_and_runtime_checks(tmp_path, monkeypatch):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"test")
    monkeypatch.setattr(setup, "run", lambda command, **kwargs: "ffmpeg version 7.1.50\n")
    monkeypatch.setattr(setup, "exercise_tools", lambda *a: pytest.fail("wrong version must not proceed"))
    with pytest.raises(setup.ProvisionError, match="Expected ffmpeg 7.1.5"):
        setup.verify_tools(binary, binary, "7.1.5")


def test_failure_report_does_not_export_github_paths(tmp_path, monkeypatch):
    env, path = tmp_path / "env", tmp_path / "path"
    monkeypatch.setenv("GITHUB_ENV", str(env))
    monkeypatch.setenv("GITHUB_PATH", str(path))
    def fail(*args):
        raise setup.ProvisionError("missing zscale")
    monkeypatch.setattr(setup, "verify_tools", fail)
    report = tmp_path / "result.json"
    assert setup.main(["--ffmpeg", "fake", "--ffprobe", "fake", "--report", str(report)]) == 1
    assert json.loads(report.read_text())["status"] == "failed"
    assert not env.exists() and not path.exists()


def test_export_github_paths_handles_spaces_and_blocks_injection(tmp_path, monkeypatch):
    env, path = tmp_path / "env", tmp_path / "path"
    monkeypatch.setenv("GITHUB_ENV", str(env))
    monkeypatch.setenv("GITHUB_PATH", str(path))
    ffmpeg = tmp_path / "with spaces" / "ffmpeg"
    ffprobe = ffmpeg.with_name("ffprobe")
    setup.export_github_paths(ffmpeg, ffprobe)
    assert env.read_text() == f"LUMEN_FFMPEG={ffmpeg}\nLUMEN_FFPROBE={ffprobe}\n"
    assert path.read_text() == str(ffmpeg.parent) + "\n"
    with pytest.raises(setup.ProvisionError, match="newlines"):
        setup.export_github_paths("bad\nINJECTED=1", ffprobe)


def test_existing_external_tools_pass_real_media_gate():
    ffmpeg = os.environ.get("LUMEN_FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("LUMEN_FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("External FFmpeg is not installed (CI runs the non-skippable provisioning gate first).")
    result = setup.verify_tools(ffmpeg, ffprobe)
    assert result["smoke"]["h264_aac_frames"] == result["smoke"]["ffv1_frames"] == 2
    assert len(result["ffmpeg_sha256"]) == len(result["ffprobe_sha256"]) == 64
    assert result["smoke"]["audio_compatibility"]["algorithm"] == "rubberband-r2-short-mixed-together-48k-512pad-v1"
    assert len(result["smoke"]["audio_compatibility"]["measurements"]) == 6
    assert {x["speed"] for x in result["smoke"]["tempo_audio"]} == {.25, 4.0}


def test_capability_gate_requires_rubberband_without_lower_quality_fallback():
    filters, encoders = listings()
    assert 'rubberband' in setup.REQUIRED_FILTERS
    with pytest.raises(setup.ProvisionError, match='rubberband'):
        setup.require_capabilities(filters.replace('rubberband', 'atempo'), encoders)


@pytest.mark.parametrize('failure', ['duration', 'pitch', 'silence'])
def test_real_audio_gate_rejects_wrong_duration_pitch_or_silence(tmp_path, monkeypatch, failure):
    from array import array
    import math
    import wave

    def fake_run(command, **kwargs):
        count = 95999 if failure == 'duration' else 96000
        frequency = 880 if failure == 'pitch' else 440
        values = array('h', (0 if failure == 'silence' else
                            round(4000 * math.sin(2 * math.pi * frequency * index / 48000))
                            for index in range(count)))
        if setup.sys.byteorder != 'little':
            values.byteswap()
        with wave.open(str(command[-1]), 'wb') as wav:
            wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            wav.writeframes(values.tobytes())
    monkeypatch.setattr(setup, 'run', fake_run)
    match = {'duration': 'exact PCM budget', 'pitch': 'changed pitch', 'silence': 'no measurable tone'}[failure]
    with pytest.raises(setup.ProvisionError, match=match):
        setup.exercise_audio_tempo('ffmpeg', tmp_path)


def test_motion_analysis_filters_required_for_native_distribution():
    assert {"vidstabdetect", "vidstabtransform"} <= setup.REQUIRED_FILTERS
