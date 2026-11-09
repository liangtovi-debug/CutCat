"""Build pinned FriBidi from official source on Windows x64; verify RAQM.

No system DLL installation. --build explicitly permits source download and a
bounded local MSVC build. The source and LGPL license travel with the library.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
import platform
import struct
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lumen.caption_runtime import (SOURCE_SHA256, VERSION, digest,
    require_capability, runtime_directory, verify_windows_payload)

URL = f'https://github.com/fribidi/fribidi/releases/download/v{VERSION}/fribidi-{VERSION}.tar.xz'
TOOLS = {'meson': '1.8.3', 'ninja': '1.11.1.4'}


def build(directory):
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('FriBidi DLL must be built on native Windows x64.')
    for package, version in TOOLS.items():
        if importlib.metadata.version(package) != version:
            raise RuntimeError(f'Expected {package}=={version}')
    directory = Path(directory).resolve()
    directory.parent.mkdir(parents=True, exist_ok=True)
    # Stage atomically; a failed build must not replace a verified payload.
    with tempfile.TemporaryDirectory(prefix='fribidi-build-', dir=directory.parent) as work:
        work = Path(work)
        archive = work / 'fribidi-source.tar.xz'
        with urllib.request.urlopen(URL, timeout=60) as response:
            if not response.geturl().startswith('https://'):
                raise RuntimeError('FriBidi source redirected away from HTTPS')
            payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise RuntimeError('FriBidi source exceeds download budget')
        archive.write_bytes(payload)
        if digest(archive) != SOURCE_SHA256:
            raise RuntimeError('FriBidi official source SHA256 mismatch')
        with tarfile.open(archive) as source:
            members = source.getmembers()
            if sum(member.size for member in members) > 64 * 1024 * 1024:
                raise RuntimeError('FriBidi source exceeds extraction budget')
            if any(member.issym() or member.islnk() for member in members):
                raise RuntimeError('Unexpected source archive links')
            source.extractall(work, filter='data')
        source_dir = work / f'fribidi-{VERSION}'
        build_dir = work / 'compiled'
        setup = [sys.executable, '-m', 'mesonbuild.mesonmain', 'setup', str(build_dir), str(source_dir),
                 '--vsenv', '--backend=ninja', '--buildtype=release', '--default-library=shared',
                 '--wrap-mode=nodownload', '-Db_vscrt=mt', '-Ddocs=false', '-Dbin=false', '-Dtests=false']
        subprocess.run(setup, check=True, timeout=120)
        subprocess.run([sys.executable, '-m', 'mesonbuild.mesonmain', 'compile', '-C', str(build_dir), '-j', '2'],
                       check=True, timeout=180)
        libraries = list((build_dir / 'lib').glob('*fribidi*.dll'))
        if len(libraries) != 1:
            raise RuntimeError('Expected exactly one compiled FriBidi DLL')
        staged = work / 'payload'
        staged.mkdir()
        shutil.copyfile(libraries[0], staged / 'fribidi.dll')
        shutil.copyfile(source_dir / 'COPYING', staged / 'COPYING')
        shutil.copyfile(archive, staged / 'fribidi-source.tar.xz')
        (staged / 'BUILDING.txt').write_text(
            'GNU FriBidi ' + VERSION + '\nLGPL-2.1-or-later, dynamically linked, unmodified source included.\n'
            'Upstream: ' + URL + '\nSource SHA256: ' + SOURCE_SHA256 + '\n'
            'Rebuild on Windows x64 with Visual Studio C tools and Python 3.12.\n'
            'pip install meson==1.8.3 ninja==1.11.1.4\n'
            'Extract fribidi-source.tar.xz, then:\n'
            'meson setup compiled fribidi-' + VERSION + ' --vsenv --backend=ninja --buildtype=release '
            '--default-library=shared --wrap-mode=nodownload -Db_vscrt=mt -Ddocs=false -Dbin=false -Dtests=false\n'
            'meson compile -C compiled -j 2\n'
            'The DLL is loaded as fribidi.dll from this folder, not installed system-wide.\n'
            'For debugging library modifications, replace this shared DLL and update its SHA256 in receipt.json.\n'
            'Application integrity checks are not signatures and do not prohibit LGPL library replacement.\n', encoding='utf-8')
        compilers = json.loads((build_dir / 'meson-info/intro-compilers.json').read_text())
        compiler = compilers['host']['c']
        if compiler['id'] != 'msvc':
            raise RuntimeError('Expected native MSVC compiler')
        receipt = {'version': VERSION, 'architecture': 'x86_64', 'source_url': URL,
                   'source_sha256': SOURCE_SHA256, 'license': 'LGPL-2.1-or-later',
                   'compiler': {'id': compiler['id'], 'version': compiler['version']}, 'tools': TOOLS,
                   'files': {p.name: digest(p) for p in staged.iterdir()}}
        (staged / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        verify_windows_payload(staged)
        if directory.exists():
            raise RuntimeError('Refusing to replace an existing runtime directory; use a new empty destination.')
        staged.rename(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--report', type=Path, default=ROOT / 'build/caption-runtime.json')
    args = parser.parse_args()
    directory = (args.directory or runtime_directory()).resolve()
    report = {'status': 'pending', 'python': platform.python_version(),
              'architecture': platform.machine(), 'receipt': None}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Probe in a separate process: an early failed DLL load must not poison
        # Pillow's module state in the process that verifies the new provider.
        report['pillow'] = importlib.metadata.version('Pillow')
        before = subprocess.run([sys.executable, '-c',
            'from PIL import features; import json; print(json.dumps({"raqm":features.check_feature("raqm"),"fribidi":features.version_feature("fribidi")}))'],
            capture_output=True, text=True, timeout=20, check=True)
        report['before'] = json.loads(before.stdout)
        if sys.platform == 'win32':
            if args.build and not directory.exists():
                build(directory)
            report['receipt'] = verify_windows_payload(directory)
            os.environ['LUMEN_FRIBIDI_DIR'] = str(directory)
        report['after'] = require_capability()
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, error=str(error)[:2048])
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if sys.platform == 'win32' and os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as env:
            env.write('LUMEN_FRIBIDI_DIR=' + str(directory) + '\n')
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
