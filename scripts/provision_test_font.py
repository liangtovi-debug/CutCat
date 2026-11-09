"""Explicitly provision a genuine, licensed CJK font for caption tests only.

No system installation or application bundling. Downloads require --download;
otherwise this command only verifies the cache. The default cache is outside
the source tree, under RUNNER_TEMP (or the operating system temp directory).

CI: python scripts/provision_test_font.py --download
The verified directory is exported as LUMEN_TEST_CJK_FONT_DIR via GITHUB_ENV.
For local pytest, set that variable to the printed font_directory yourself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.error
import urllib.request


REPOSITORY = "https://github.com/notofonts/noto-cjk"
RELEASE = "Sans2.004"
COMMIT = "523d033d6cb47f4a80c58a35753646f5c3608a78"
BASE_URL = f"https://raw.githubusercontent.com/notofonts/noto-cjk/{COMMIT}"
FONT_NAME = "NotoSansCJKsc-Regular.otf"
FONT_FAMILY = "Noto Sans CJK SC"
FONT_URL = f"{BASE_URL}/Sans/OTF/SimplifiedChinese/{FONT_NAME}"
FONT_SHA256 = "2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b"
LICENSE_URL = f"{BASE_URL}/LICENSE"
LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
# Hashes measured from the actual, unmodified upstream files on 2026-10-15.
# The upstream license and the font's name-table license both specify OFL-1.1.
REQUIRED_TEXT = "中文测试再见"
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
ENVIRONMENT_VARIABLE = "LUMEN_TEST_CJK_FONT_DIR"


class ProvisionError(RuntimeError):
    pass


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_download(url, destination, expected_sha256):
    """Publish only hash-verified HTTPS bytes; never open unverified font data."""
    if not url.startswith("https://"):
        raise ProvisionError("Test font downloads require HTTPS.")
    destination = Path(destination)
    if destination.is_file() and sha256(destination) == expected_sha256:
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".download")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LumenEdit-CI/1"})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as out:
            if not response.geturl().startswith("https://"):
                raise ProvisionError("Test font download redirected away from HTTPS.")
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise ProvisionError("Test font download exceeds the 32 MiB limit.")
                out.write(chunk)
        actual = sha256(partial)
        if actual != expected_sha256:
            raise ProvisionError(f"SHA256 mismatch for {url}: expected {expected_sha256}, got {actual}")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def verify_fixture(directory):
    """Check immutable bytes, actual family/license metadata, and Chinese cmap."""
    directory = Path(directory).expanduser().resolve()
    font_path, license_path = directory / FONT_NAME, directory / "LICENSE"
    for path, expected in ((license_path, LICENSE_SHA256), (font_path, FONT_SHA256)):
        if not path.is_file():
            raise ProvisionError(f"Missing test font fixture: {path}. Run provision_test_font.py --download.")
        if sha256(path) != expected:
            raise ProvisionError(f"SHA256 mismatch for cached fixture: {path}")
    license_text = license_path.read_text(encoding="utf-8")
    if "SIL OPEN FONT LICENSE Version 1.1" not in license_text:
        raise ProvisionError("The test font's upstream OFL-1.1 license is missing.")

    from fontTools.ttLib import TTFont

    with TTFont(font_path, lazy=True) as font:
        def names(name_id):
            return sorted({record.toUnicode() for record in font["name"].names
                           if record.nameID == name_id})

        families, versions, licenses = names(1), names(5), names(13)
        if FONT_FAMILY not in families or not any(value.startswith("Version 2.004;") for value in versions):
            raise ProvisionError("Unexpected test font family or version.")
        if not any("SIL Open Font License, Version 1.1" in value for value in licenses):
            raise ProvisionError("The test font's embedded OFL-1.1 license is missing.")
        cmap = font.getBestCmap() or {}
        if any(ord(character) not in cmap for character in REQUIRED_TEXT):
            raise ProvisionError("The test font does not cover every Chinese caption test glyph.")
        metadata = {"families": families, "versions": versions, "copyright": names(0),
                    "embedded_license": licenses, "covered_test_text": REQUIRED_TEXT,
                    "cmap_codepoints": len(cmap)}

    return {"font_directory": str(directory), "font_file": str(font_path),
            "font_sha256": sha256(font_path), "font_bytes": font_path.stat().st_size,
            "license_file": str(license_path), "license_sha256": sha256(license_path),
            "metadata": metadata}


def export_github_environment(directory):
    value = str(Path(directory).resolve())
    if "\n" in value or "\r" in value:
        raise ProvisionError("Test font directory must not contain newlines.")
    if os.environ.get("GITHUB_ENV"):
        with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as out:
            out.write(f"{ENVIRONMENT_VARIABLE}={value}\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Explicitly download the pinned font and license.")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
                        / "lumen-test-fonts" / RELEASE)
    parser.add_argument("--report", type=Path, default=Path("build/test-font-provenance.json"))
    args = parser.parse_args(argv)
    report = {"status": "failed", "purpose": "CJK caption test fixture only",
              "repository": REPOSITORY, "release": RELEASE, "commit": COMMIT,
              "font_url": FONT_URL, "expected_font_sha256": FONT_SHA256,
              "license": "OFL-1.1", "license_url": LICENSE_URL,
              "expected_license_sha256": LICENSE_SHA256,
              "system_installation": False, "application_bundling": False}
    try:
        if args.download:
            # Retain the upstream license alongside every downloaded font.
            checked_download(LICENSE_URL, args.cache_dir / "LICENSE", LICENSE_SHA256)
            checked_download(FONT_URL, args.cache_dir / FONT_NAME, FONT_SHA256)
        report["verification"] = verify_fixture(args.cache_dir)
        export_github_environment(report["verification"]["font_directory"])
        report["status"] = "passed"
        print(f"{ENVIRONMENT_VARIABLE}={report['verification']['font_directory']}")
        print("Verified Noto Sans CJK SC 2.004 and its OFL-1.1 license for tests only.")
    except (ProvisionError, OSError, ValueError, KeyError, ImportError, urllib.error.URLError) as error:
        report["error"] = str(error)
        print(f"Test font provisioning failed: {error}", file=sys.stderr)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
