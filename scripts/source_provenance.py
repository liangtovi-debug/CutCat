"""Read-only source consistency metadata, not a security attestation.

Only an exact repository root with clean tracked/index state and no nonignored
untracked inputs can produce verified provenance. Generated ignored build/test
outputs are deliberately excluded. The digest covers actual tracked file bytes,
relative paths, and Git modes, rather than trusting a clean status alone.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


class SourceProvenanceUnavailable(ValueError):
    """A source ZIP/synthetic root or host without Git cannot identify a commit."""


def _git(root, *args):
    try:
        return subprocess.run(['git', '-C', str(root), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, timeout=30,
                              env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
    except FileNotFoundError as error:
        raise SourceProvenanceUnavailable('Git is not available on this host') from error
    except subprocess.TimeoutExpired as error:
        raise ValueError('Git source-provenance check timed out') from error


def _required(root, *args):
    result = _git(root, *args)
    if result.returncode:
        raise ValueError(f'Git source-provenance check failed: {args[0]}')
    return result.stdout


def _state(root):
    commit = _required(root, 'rev-parse', '--verify', 'HEAD').decode('ascii').strip()
    tree = _required(root, 'rev-parse', '--verify', 'HEAD^{tree}').decode('ascii').strip()
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-f]{40}', tree):
        raise ValueError('Source provenance requires an exact SHA-1 Git commit and tree')
    # HEAD-vs-worktree alone misses a staged edit whose worktree bytes were
    # subsequently restored. Check the index independently as well.
    if (_git(root, 'diff', '--quiet', '--no-ext-diff', '--no-textconv', 'HEAD', '--').returncode
            or _git(root, 'diff', '--cached', '--quiet', '--no-ext-diff', '--no-textconv', 'HEAD', '--').returncode):
        raise ValueError('Tracked source or index differs from HEAD')
    untracked = _required(root, 'ls-files', '--others', '--exclude-standard', '-z')
    if untracked:
        raise ValueError('Nonignored untracked inputs prevent exact source provenance')
    return commit, tree, _required(root, 'ls-files', '--stage', '-z')


def _digest(root, tracked):
    entries = []
    for record in tracked.split(b'\0'):
        if not record:
            continue
        header, separator, name = record.partition(b'\t')
        if not separator:
            raise ValueError('Malformed tracked-source record')
        fields = header.split()
        if len(fields) != 3 or fields[2] != b'0' or fields[0] not in (b'100644', b'100755'):
            raise ValueError('Only resolved regular tracked files are supported; no symlinks or gitlinks')
        relative = os.fsdecode(name)
        path = root / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.is_file():
            raise ValueError('Tracked source is missing, unsafe, or not a regular file')
        # Reject symlinked parent components too; do not hash bytes outside root.
        if path.resolve() != path or not path.resolve().is_relative_to(root):
            raise ValueError('Tracked source traverses a symlink')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        entries.append([relative.replace(os.sep, '/'), fields[0].decode('ascii'), digest])
    if not entries:
        raise ValueError('A source snapshot must contain tracked files')
    entries.sort(key=lambda entry: entry[0])
    if len({entry[0] for entry in entries}) != len(entries):
        raise ValueError('Duplicate tracked source paths')
    canonical = json.dumps(entries, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return hashlib.sha256(canonical).hexdigest(), len(entries)


def capture_source_provenance(root):
    root = Path(root).resolve()
    top = _git(root, 'rev-parse', '--show-toplevel')
    if top.returncode:
        raise SourceProvenanceUnavailable('The source root is not a Git working tree')
    if Path(os.fsdecode(top.stdout).strip()).resolve() != root:
        raise SourceProvenanceUnavailable('The requested source root is not the Git repository top level')
    before = _state(root)
    source_sha256, count = _digest(root, before[2])
    after = _state(root)
    if before != after:
        raise ValueError('Git source identity changed while capturing provenance')
    return {'schema': 1, 'commit': before[0], 'tree': before[1],
            'source_sha256': source_sha256, 'tracked_files': count, 'clean': True}
