"""Retrieve the authorized private archive through Git Blobs JSON/base64 only."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

PARTS = [('287743892bb1f77f587f44fff0693adb9174f90d', 6967581),
         ('0451eb02cf7332c6ad5a7ae2e6b2e13e76f9ced3', 6967582)]
ZIP_SHA = '9f05082da94a9387966b9175f496d0672ad71d00a244039ce257bde4fe0da9d2'

def fetch(destination):
    parts = []
    receipt = []
    for sha, size in PARTS:
        raw = subprocess.check_output(['gh', 'api', 'repos/liangtovi-debug/LumenEdit/git/blobs/' + sha], timeout=120)
        data = json.loads(raw)
        assert data['encoding'] == 'base64' and data['sha'] == sha and data['size'] == size
        content = base64.b64decode(''.join(data['content'].split()), validate=True)
        assert len(content) == size
        assert hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + content).hexdigest() == sha
        parts.append(content)
        receipt.append({'blob': sha, 'size': size, 'sha256': hashlib.sha256(content).hexdigest()})
    archive = b''.join(parts)
    assert hashlib.sha256(archive).hexdigest() == ZIP_SHA
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as stream:
        stream.write(archive)
    destination.with_suffix('.receipt.json').write_text(json.dumps({'parts': receipt, 'zip_sha256': ZIP_SHA}, indent=2) + '\n')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    fetch(parser.parse_args().output)
