"""Archive a native draft without losing macOS executable bits or symlinks.

Run after the packaged runtime smoke test. No network or publishing operations.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tarfile
import zipfile


def archive_native(dist: Path, platform: str) -> Path:
    dist = dist.resolve()
    if platform == "darwin":
        source = dist / "LumenEdit-Draft.app"
        executable = source / "Contents" / "MacOS" / "LumenEdit-Draft"
        if not executable.is_file() or not executable.stat().st_mode & 0o111:
            raise ValueError("macOS application executable is missing or not executable")
        target = dist / "LumenEdit-Draft-macOS.tar.gz"
        with tarfile.open(target, "w:gz", dereference=False) as archive:
            archive.add(source, arcname=source.name)
        with tarfile.open(target) as archive:
            entry = archive.getmember("LumenEdit-Draft.app/Contents/MacOS/LumenEdit-Draft")
            if not entry.mode & 0o111:
                raise ValueError("Archive lost application executable permissions")
    elif platform == "win32":
        source = dist / "LumenEdit-Draft"
        if not (source / "LumenEdit-Draft.exe").is_file():
            raise ValueError("Windows application executable is missing")
        target = Path(shutil.make_archive(str(dist / "LumenEdit-Draft-Windows"), "zip", dist, source.name))
        with zipfile.ZipFile(target) as archive:
            if archive.testzip() is not None:
                raise ValueError("Windows archive integrity check failed")
    else:
        raise ValueError("Native archives require Windows or macOS")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--extract-to", type=Path, help="Fresh folder for testing the archived package")
    args = parser.parse_args()
    target = archive_native(args.dist, sys.platform)
    if args.extract_to is not None:
        if args.extract_to.exists():
            parser.error("Extraction target must not exist")
        args.extract_to.mkdir(parents=True)
        if sys.platform == "darwin":
            with tarfile.open(target) as archive:
                archive.extractall(args.extract_to, filter="data")
        else:
            with zipfile.ZipFile(target) as archive:
                archive.extractall(args.extract_to)
    print(target)
