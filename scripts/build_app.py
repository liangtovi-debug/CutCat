"""Build an unsigned native draft. Run separately on Windows and macOS.

No dependency downloads, FFmpeg bundling, release signing, or publishing occur here.
PyInstaller may apply its default ad-hoc macOS signatures, without an identity.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

# Support both direct script execution and package import by tests.
if __package__ in (None, ''):
    sys.path.insert(0, str(ROOT))
from scripts.source_provenance import capture_source_provenance
from lumen.build_provenance import validate_source_provenance


def caption_bundle_args(platform_name):
    from lumen.caption_runtime import require_capability, runtime_directory, verify_windows_payload
    require_capability()
    if platform_name != 'win32':
        return []
    directory = runtime_directory().resolve()
    verify_windows_payload(directory)
    destination = 'lumen/assets/native/fribidi'
    arguments = ['--add-binary', str(directory / 'fribidi.dll') + ';' + destination]
    for name in ('receipt.json', 'COPYING', 'fribidi-source.tar.xz', 'BUILDING.txt'):
        arguments.extend(['--add-data', str(directory / name) + ';' + destination])
    return arguments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", action="store_true", help="Keep a console for diagnostics")
    args = parser.parse_args()
    if sys.platform not in ("win32", "darwin"):
        parser.error("Build on Windows or macOS; PyInstaller is not a cross-compiler.")
    if sys.version_info[:2] != (3, 12):
        parser.error("Use the pinned Python 3.12 development environment.")
    expected = {"PyInstaller": "6.12.0", "PySide6": "6.8.3", "pyinstaller-hooks-contrib": "2025.1",
                "numpy": "2.2.4", "fonttools": "4.61.1", "Pillow": "13.0.0", "regex": "2026.9.29"}
    for package, version in expected.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            parser.error(f"Missing {package}; run python -m pip install -e '.[build]'.")
        if actual != version:
            parser.error(f"Expected {package} {version}, found {actual}.")
    # Pre-converted native resources avoid icon conversion during packaging.
    # Pillow is still a pinned runtime dependency for caption text layout.
    # This only changes presentation; executable names and persisted IDs stay put.
    icon = ROOT / "lumen" / "assets" / "app" / ("cutcat.ico" if sys.platform == "win32" else "cutcat.icns")
    if not icon.is_file():
        parser.error(f"Missing approved CutCat application icon: {icon}")
    source = validate_source_provenance(capture_source_provenance(ROOT))
    generated = ROOT / "build" / "launcher"
    generated.mkdir(parents=True, exist_ok=True)
    provenance_path = generated / 'build-provenance.json'
    provenance_path.write_text(json.dumps(source, indent=2) + '\n', encoding='utf-8')
    launcher = generated / "lumen_launcher.py"
    launcher.write_text("from lumen.main import main\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noupx",
               "--name", "LumenEdit-Draft", "--icon", str(icon), "--paths", str(ROOT),
               "--add-data", str(ROOT / "lumen" / "assets") + (";" if sys.platform == "win32" else ":") + "lumen/assets",
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
               "--specpath", str(generated), "--exclude-module", "PyQt5", "--exclude-module", "PyQt6",
               "--exclude-module", "sherpa_onnx", "--exclude-module", "sherpa_onnx_core",
               "--exclude-module", "onnxruntime", "--exclude-module", "tokenizers"]
    command.extend(caption_bundle_args(sys.platform))
    command.extend(['--add-data', str(provenance_path) +
                    (';' if sys.platform == 'win32' else ':') + 'lumen/assets'])
    # Retain Qt's official Simplified Chinese standard-dialog translation.
    from PySide6.QtCore import QLibraryInfo
    qt_zh = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / 'qtbase_zh_CN.qm'
    if not qt_zh.is_file():
        parser.error('Pinned PySide6 package is missing qtbase_zh_CN.qm')
    command.extend(['--add-data', str(qt_zh) + (';' if sys.platform == 'win32' else ':') + 'lumen/assets/translations'])
    # Catalog modules are dynamically discovered; frozen builds must retain them.
    for catalog in sorted((ROOT / 'lumen').glob('i18n_catalog_*.py')):
        command.extend(['--hidden-import', 'lumen.' + catalog.stem])
    if not args.console:
        command.append("--windowed")
    command.append(str(launcher))
    subprocess.run(command, cwd=ROOT, check=True)
    if validate_source_provenance(capture_source_provenance(ROOT)) != source:
        raise RuntimeError('Source changed while PyInstaller was running; this draft is not verified')
    print("Draft created under dist/. FFmpeg/ffprobe remain external runtime prerequisites.")
    print("Optional TTS/search runtimes are deliberately excluded pending component license and native packaging review.")
    print("No release signing, notarization, license audit, or public release has been performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
