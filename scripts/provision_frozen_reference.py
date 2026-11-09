"""Supply a pinned, external FFmpeg 7 runtime ONLY to the accepted oracle.

Does not replace candidate PATH/LUMEN_FFMPEG or install anything system-wide.
The package pin is independently recorded in Microsoft's winget repository.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.provision_ffmpeg import checked_download, extract_windows_tools

LICENSE_MEMBER = 'ffmpeg-7.1.1-full_build/LICENSE'
LICENSE_SHA256 = '8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903'
VERSION = '7.1.1'
URL = 'https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-full_build.zip'
SHA256 = 'd760e1b3574402ed18b4865851f87d87e73965a982e6453212df8621fed1c508'
MANIFEST_COMMIT = '414d8a8b90fc50b33319b665dabc81d7edfd0284'
MANIFEST_SHA256 = '529ea436d5cd1ef3f76a32db157d04e198287aa0eb22f328800f78d4ca8f2e5c'
TOOL_HASHES = {
    'ffmpeg.exe': 'b1383f5d07470d503edecdaee4bddc5891e986e916a698299b357f79cfe445fd',
    'ffprobe.exe': '012bddded3cbc5204055210d7ff4f0b3f7521bca441a694939856d01909f5756',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_manifest():
    if not __debug__:
        raise RuntimeError('Reference runtime integrity checks require non-optimized Python.')
    path = ROOT / 'tests/frozen_range_runtime/winget-7.1.1-installer.yaml'
    assert digest(path) == MANIFEST_SHA256, 'Independent reference runtime manifest changed'
    text = path.read_text()
    assert 'InstallerUrl: ' + URL in text
    assert 'InstallerSha256: ' + SHA256.upper() in text


def install(directory):
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('The pinned reference package requires Windows x64.')
    verify_manifest()
    directory = Path(directory).resolve()
    archive = checked_download(URL, directory / 'ffmpeg-7.1.1-full_build.zip', SHA256)
    with zipfile.ZipFile(archive) as package:
        license_bytes = package.read(LICENSE_MEMBER)
    if hashlib.sha256(license_bytes).hexdigest() != LICENSE_SHA256:
        raise RuntimeError('Reference package license digest mismatch')
    license_path = directory / 'LICENSE'
    license_path.write_bytes(license_bytes)
    ffmpeg, ffprobe = extract_windows_tools(archive, directory / 'bin')
    for path in (ffmpeg, ffprobe):
        if digest(path) != TOOL_HASHES[path.name]:
            raise RuntimeError('Reference executable digest mismatch')
    return {'provider': 'Gyan/codexffmpeg; independent Microsoft winget pin',
            'version': VERSION, 'url': URL, 'archive_sha256': SHA256,
            'license': {'identifier': 'GPL-3.0', 'archive_member': LICENSE_MEMBER,
                        'sha256': LICENSE_SHA256, 'path': str(license_path)},
            'winget_commit': MANIFEST_COMMIT, 'winget_manifest_sha256': MANIFEST_SHA256,
            'tools': {path.name: {'path': str(path), 'sha256': digest(path)}
                      for path in (ffmpeg, ffprobe)}, 'external_reference_only': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=ROOT / 'build/reference-ffmpeg')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    receipt = install(args.directory)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(receipt, indent=2) + '\n')
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as output:
            for key, name in [('LUMEN_REFERENCE_FFMPEG', 'ffmpeg.exe'),
                              ('LUMEN_REFERENCE_FFPROBE', 'ffprobe.exe')]:
                output.write(key + '=' + receipt['tools'][name]['path'] + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
