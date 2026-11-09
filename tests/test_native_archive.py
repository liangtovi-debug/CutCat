import importlib.util
import os
from pathlib import Path
import tarfile
import zipfile

import pytest

spec = importlib.util.spec_from_file_location("archive_native", Path(__file__).parents[1] / "scripts" / "archive_native.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.skipif(os.name != "posix", reason="macOS archive modes require POSIX executable bits")
def test_mac_archive_keeps_executable_and_symlink(tmp_path):
    app = tmp_path / "LumenEdit-Draft.app"
    exe = app / "Contents" / "MacOS" / "LumenEdit-Draft"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fixture executable")
    exe.chmod(0o755)
    link = app / "Contents" / "runtime-link"
    try:
        link.symlink_to("MacOS/LumenEdit-Draft")
    except OSError:
        pytest.skip("Test host cannot create symlinks")
    target = module.archive_native(tmp_path, "darwin")
    with tarfile.open(target) as archive:
        assert archive.getmember("LumenEdit-Draft.app/Contents/MacOS/LumenEdit-Draft").mode & 0o111
        assert archive.getmember("LumenEdit-Draft.app/Contents/runtime-link").issym()


def test_windows_archive_roundtrip(tmp_path):
    source = tmp_path / "LumenEdit-Draft"
    source.mkdir()
    (source / "LumenEdit-Draft.exe").write_bytes(b"synthetic fixture")
    target = module.archive_native(tmp_path, "win32")
    with zipfile.ZipFile(target) as archive:
        assert archive.read("LumenEdit-Draft/LumenEdit-Draft.exe") == b"synthetic fixture"


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_archive_rejects_missing_or_unsupported(tmp_path, platform):
    with pytest.raises(ValueError):
        module.archive_native(tmp_path, platform)
