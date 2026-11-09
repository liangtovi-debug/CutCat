"""Approved icon pixels, native containers and build flags, without a native build.

Qt decodes each embedded PNG so these tests need no Pillow runtime dependency.
Actual Windows/macOS shell appearance remains part of native package validation.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tomllib
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QApplication, QWidget

from scripts import build_app


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'lumen' / 'assets' / 'app'
APPROVED_MASTER_SHA256 = 'db9e62a4c873f66ca67e09d5a5c32f70959c653dd78f2f6971e259781bc91546'
ICO_SIZES = {16, 24, 32, 48, 64, 128, 256}
ICNS_ENTRIES = {b'ic07': 128, b'ic08': 256, b'ic09': 512, b'ic10': 1024,
                b'ic11': 32, b'ic12': 64, b'ic13': 256, b'ic14': 512}


def decoded_png(data, size):
    assert data.startswith(b'\x89PNG\r\n\x1a\n')
    image = QImage.fromData(data, 'PNG')
    assert not image.isNull()
    assert (image.width(), image.height()) == (size, size)
    assert image.hasAlphaChannel()
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    alpha = bytes(rgba.constBits())[3::4]
    assert min(alpha) == 0 and max(alpha) == 255
    # Preserve the source's semitransparency; Lanczos may leave one alpha step
    # in a tiny-size corner. Do not force the approved artwork to opaque.
    assert image.pixelColor(0, 0).alpha() <= 1
    assert image.pixelColor(size // 2, size // 2).alpha() >= 250
    return image


def test_assets_match_approved_source_provenance():
    provenance = json.loads((ASSETS / 'provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_sha256'] == APPROVED_MASTER_SHA256
    assert provenance['source_dimensions'] == [1254, 1254]
    assert provenance['source_mode'] == 'RGBA'
    assert set(provenance['files']) == {'cutcat.png', 'cutcat.ico', 'cutcat.icns'}
    for filename, expected in provenance['files'].items():
        data = (ASSETS / filename).read_bytes()
        assert len(data) == expected['bytes']
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
    decoded_png((ASSETS / 'cutcat.png').read_bytes(), 512)


def test_windows_ico_has_valid_multisize_rgba_resources():
    data = (ASSETS / 'cutcat.ico').read_bytes()
    assert struct.unpack_from('<HHH', data) == (0, 1, len(ICO_SIZES))
    sizes = set()
    expected_offset = 6 + 16 * len(ICO_SIZES)
    for index in range(len(ICO_SIZES)):
        width, height, colors, reserved, planes, depth, length, offset = struct.unpack_from(
            '<BBBBHHII', data, 6 + index * 16)
        width, height = width or 256, height or 256
        assert width == height and width not in sizes
        assert (colors, reserved, depth) == (0, 0, 32)
        assert planes in (0, 1)
        assert offset == expected_offset and offset + length <= len(data)
        decoded_png(data[offset:offset + length], width)
        expected_offset = offset + length
        sizes.add(width)
    assert sizes == ICO_SIZES
    assert expected_offset == len(data)


def test_macos_icns_has_valid_standard_and_retina_resources():
    data = (ASSETS / 'cutcat.icns').read_bytes()
    assert data[:4] == b'icns'
    assert struct.unpack_from('>I', data, 4)[0] == len(data)
    entries = {}
    table_of_contents = None
    offset = 8
    while offset < len(data):
        kind, length = struct.unpack_from('>4sI', data, offset)
        assert length > 8 and offset + length <= len(data)
        payload = data[offset + 8:offset + length]
        if kind == b'TOC ':
            assert table_of_contents is None
            table_of_contents = dict(struct.iter_unpack('>4sI', payload))
        else:
            assert kind not in entries
            assert kind in ICNS_ENTRIES
            decoded_png(payload, ICNS_ENTRIES[kind])
            entries[kind] = length
        offset += length
    assert offset == len(data)
    assert entries.keys() == ICNS_ENTRIES.keys()
    assert table_of_contents == entries


def test_qt_icon_renders_actual_rgba_pixels_and_window_inherits_it():
    app = QApplication.instance() or QApplication([])
    original = QIcon(app.windowIcon())
    icon = QIcon(str(ASSETS / 'cutcat.png'))
    assert not icon.isNull()
    try:
        app.setWindowIcon(icon)
        window = QWidget()
        for size in (16, 32, 64, 256, 512):
            pixmap = window.windowIcon().pixmap(size, size)
            assert not pixmap.isNull()
            image = pixmap.toImage()
            assert image.pixelColor(0, 0).alpha() == 0
            center = image.pixelColor(image.width() // 2, image.height() // 2)
            assert center.alpha() >= 250
            # A sample inside the approved cat's forehead is bright mint/teal.
            mint = image.pixelColor(round(image.width() * .4), round(image.height() * .35))
            assert mint.alpha() >= 250
            assert mint.green() > mint.red() and mint.blue() > mint.red()
        window.close()
    finally:
        app.setWindowIcon(original)


def test_application_main_sets_approved_icon_without_changing_identity(monkeypatch):
    import PySide6.QtWidgets
    from lumen import main, ui

    app = QApplication.instance() or QApplication([])
    original = (app.applicationName(), app.applicationDisplayName(), app.organizationName(), QIcon(app.windowIcon()))
    window = QWidget()
    # Exercise the real entry point without creating a second QApplication or
    # starting a blocking event loop. The icon itself uses actual Qt rendering.
    monkeypatch.setattr(PySide6.QtWidgets, 'QApplication', lambda _: app)
    monkeypatch.setattr(ui, 'MainWindow', lambda: window)
    monkeypatch.setattr(app, 'exec', lambda: 0)
    try:
        assert main.main(['cutcat']) == 0
        assert app.applicationName() == 'Lumen Edit'
        assert app.organizationName() == 'LumenEditPrototype'
        assert app.applicationDisplayName() == 'CutCat'
        expected = QIcon(str(ASSETS / 'cutcat.png')).pixmap(64, 64).toImage()
        actual = window.windowIcon().pixmap(64, 64).toImage()
        assert not actual.isNull() and actual == expected
    finally:
        window.close()
        app.setApplicationName(original[0])
        app.setApplicationDisplayName(original[1])
        app.setOrganizationName(original[2])
        app.setWindowIcon(original[3])


def test_package_metadata_includes_all_application_icon_assets():
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    globs = metadata['tool']['setuptools']['package-data']['lumen']
    for suffix in ('png', 'ico', 'icns', 'json'):
        assert f'assets/app/*.{suffix}' in globs


@pytest.mark.parametrize('platform,suffix,separator', [('win32', '.ico', ';'), ('darwin', '.icns', ':')])
@pytest.mark.parametrize('console', [False, True])
def test_native_build_selects_platform_icon_without_renaming_app(tmp_path, monkeypatch, platform, suffix, separator, console):
    icon = tmp_path / 'lumen' / 'assets' / 'app' / f'cutcat{suffix}'
    icon.parent.mkdir(parents=True)
    icon.write_bytes((ASSETS / icon.name).read_bytes())
    qt_translations = tmp_path / 'qt-translations'
    qt_translations.mkdir()
    (qt_translations / 'qtbase_zh_CN.qm').write_bytes(b'translation fixture')
    versions = {'PyInstaller': '6.12.0', 'PySide6': '6.8.3', 'pyinstaller-hooks-contrib': '2025.1',
                'numpy': '2.2.4', 'fonttools': '4.61.1', 'Pillow': '13.0.0', 'regex': '2026.9.29'}
    monkeypatch.setattr(build_app, 'ROOT', tmp_path)
    monkeypatch.setattr(build_app, 'caption_bundle_args', lambda platform: [])
    monkeypatch.setattr(build_app.importlib.metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(build_app, 'sys', SimpleNamespace(
        platform=platform, version_info=(3, 12), executable=sys.executable))
    monkeypatch.setattr(sys, 'argv', ['build_app.py', *(['--console'] if console else [])])
    import PySide6.QtCore
    monkeypatch.setattr(PySide6.QtCore, 'QLibraryInfo', SimpleNamespace(
        path=lambda _: str(qt_translations), LibraryPath=SimpleNamespace(TranslationsPath='translations')))
    calls = []
    monkeypatch.setattr(build_app, 'capture_source_provenance', lambda root: {
        'schema': 1, 'commit': 'a' * 40, 'tree': 'b' * 40, 'source_sha256': 'c' * 64,
        'tracked_files': 12, 'clean': True})
    monkeypatch.setattr(build_app.subprocess, 'run', lambda command, **kwargs: calls.append((command, kwargs)))
    assert build_app.main() == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[command.index('--icon') + 1] == str(icon)
    assert command[command.index('--name') + 1] == 'LumenEdit-Draft'
    assert str(tmp_path / 'lumen' / 'assets') + separator + 'lumen/assets' in command
    assert ('--windowed' in command) is not console
    assert '--osx-bundle-identifier' not in command
    assert kwargs == {'cwd': tmp_path, 'check': True}


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
def test_native_build_rejects_missing_approved_icon(tmp_path, monkeypatch, platform, capsys):
    versions = {'PyInstaller': '6.12.0', 'PySide6': '6.8.3', 'pyinstaller-hooks-contrib': '2025.1',
                'numpy': '2.2.4', 'fonttools': '4.61.1', 'Pillow': '13.0.0', 'regex': '2026.9.29'}
    monkeypatch.setattr(build_app, 'ROOT', tmp_path)
    monkeypatch.setattr(build_app, 'caption_bundle_args', lambda platform: [])
    monkeypatch.setattr(build_app.importlib.metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(build_app, 'sys', SimpleNamespace(platform=platform, version_info=(3, 12)))
    monkeypatch.setattr(sys, 'argv', ['build_app.py'])
    with pytest.raises(SystemExit) as error:
        build_app.main()
    assert error.value.code == 2
    assert 'Missing approved CutCat application icon' in capsys.readouterr().err
    assert not (tmp_path / 'build').exists()


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
@pytest.mark.parametrize('package,version', [('Pillow', '13.0.0'), ('regex', '2026.9.29')])
@pytest.mark.parametrize('missing', [False, True], ids=['wrong-version', 'missing'])
def test_native_build_rejects_unpinned_caption_dependencies(
        tmp_path, monkeypatch, platform, package, version, missing, capsys):
    versions = {'PyInstaller': '6.12.0', 'PySide6': '6.8.3', 'pyinstaller-hooks-contrib': '2025.1',
                'numpy': '2.2.4', 'fonttools': '4.61.1', 'Pillow': '13.0.0', 'regex': '2026.9.29'}

    def installed_version(name):
        if name == package:
            if missing:
                raise build_app.importlib.metadata.PackageNotFoundError(name)
            return '0.0.0'
        return versions[name]

    monkeypatch.setattr(build_app, 'ROOT', tmp_path)
    monkeypatch.setattr(build_app, 'caption_bundle_args', lambda platform: [])
    monkeypatch.setattr(build_app.importlib.metadata, 'version', installed_version)
    monkeypatch.setattr(build_app, 'sys', SimpleNamespace(platform=platform, version_info=(3, 12)))
    monkeypatch.setattr(sys, 'argv', ['build_app.py'])
    calls = []
    monkeypatch.setattr(build_app.subprocess, 'run', lambda *args, **kwargs: calls.append(args))
    with pytest.raises(SystemExit) as error:
        build_app.main()
    assert error.value.code == 2
    expected = f'Missing {package}' if missing else f'Expected {package} {version}, found 0.0.0'
    assert expected in capsys.readouterr().err
    assert not calls and not (tmp_path / 'build').exists()
