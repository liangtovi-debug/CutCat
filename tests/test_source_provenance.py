"""Source consistency tests use temporary local Git repositories only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import source_provenance as provenance
from scripts import run_bounded_tests as runner


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=dict(os.environ, GIT_AUTHOR_NAME='Fixture',
                          GIT_AUTHOR_EMAIL='fixture@example.invalid', GIT_COMMITTER_NAME='Fixture',
                          GIT_COMMITTER_EMAIL='fixture@example.invalid')).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    # Avoid host CRLF conversion so actual-byte expectations are portable.
    (root / '.gitattributes').write_bytes(b'* -text\n')
    (root / '.gitignore').write_bytes(b'build/\n__pycache__/\n.pytest_cache/\n')
    (root / 'source.py').write_bytes(b'answer = 42\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'synthetic source snapshot')
    return root


def test_exact_clean_snapshot_has_portable_canonical_digest(repo):
    result = provenance.capture_source_provenance(repo)
    records = [[name, '100644', hashlib.sha256((repo / name).read_bytes()).hexdigest()]
               for name in ['.gitattributes', '.gitignore', 'source.py']]
    digest = hashlib.sha256(json.dumps(records, ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest()
    assert result == {'schema': 1, 'commit': git(repo, 'rev-parse', 'HEAD').decode().strip(),
                      'tree': git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip(),
                      'source_sha256': digest, 'tracked_files': 3, 'clean': True}
    assert provenance.capture_source_provenance(repo) == result


def test_ignored_generated_outputs_do_not_change_source_digest(repo):
    before = provenance.capture_source_provenance(repo)
    (repo / 'build').mkdir()
    (repo / 'build' / 'result.json').write_text('synthetic generated report')
    assert provenance.capture_source_provenance(repo) == before


@pytest.mark.parametrize('change', ['modified', 'staged', 'untracked', 'deleted'])
def test_dirty_or_untracked_inputs_fail_closed(repo, change):
    if change == 'untracked':
        (repo / 'extra.py').write_text('untracked source')
    elif change == 'deleted':
        (repo / 'source.py').unlink()
    else:
        (repo / 'source.py').write_text('answer = 99\n')
        if change == 'staged':
            git(repo, 'add', 'source.py')
    with pytest.raises(ValueError):
        provenance.capture_source_provenance(repo)
    with pytest.raises(ValueError):
        runner.source_provenance(repo)


def test_new_commit_changes_identity_and_source_digest(repo):
    before = provenance.capture_source_provenance(repo)
    (repo / 'source.py').write_text('answer = 99\n')
    git(repo, 'add', 'source.py')
    git(repo, 'commit', '-qm', 'new synthetic snapshot')
    after = provenance.capture_source_provenance(repo)
    assert all(after[key] != before[key] for key in ['commit', 'tree', 'source_sha256'])


def test_nested_checkout_folder_cannot_claim_parent_commit(repo):
    nested = repo / 'build'
    nested.mkdir()
    with pytest.raises(provenance.SourceProvenanceUnavailable):
        provenance.capture_source_provenance(nested)
    assert runner.source_provenance(nested)['available'] is False


def test_source_zip_has_explicit_unavailable_provenance(tmp_path):
    with pytest.raises(provenance.SourceProvenanceUnavailable):
        provenance.capture_source_provenance(tmp_path)
    assert runner.source_provenance(tmp_path)['available'] is False


def test_head_change_during_capture_is_rejected(repo, monkeypatch):
    original = provenance._digest
    def changed(root, tracked):
        result = original(root, tracked)
        git(root, 'commit', '--allow-empty', '-qm', 'changed identity during capture')
        return result
    monkeypatch.setattr(provenance, '_digest', changed)
    with pytest.raises(ValueError, match='identity changed'):
        provenance.capture_source_provenance(repo)


def test_mid_capture_source_mutation_is_rejected(repo, monkeypatch):
    original = provenance._digest
    def changed(root, tracked):
        result = original(root, tracked)
        (root / 'source.py').write_text('changed during hash')
        return result
    monkeypatch.setattr(provenance, '_digest', changed)
    with pytest.raises(ValueError, match='differs from HEAD'):
        provenance.capture_source_provenance(repo)


def test_git_timeout_is_not_treated_as_source_zip(tmp_path, monkeypatch):
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 30)
    monkeypatch.setattr(provenance.subprocess, 'run', expired)
    with pytest.raises(ValueError, match='timed out'):
        runner.source_provenance(tmp_path)


def test_missing_git_is_explicitly_unavailable(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('git')
    monkeypatch.setattr(provenance.subprocess, 'run', missing)
    assert runner.source_provenance(tmp_path)['available'] is False


def test_symlink_input_is_not_followed(repo):
    target = repo / 'link.py'
    try:
        target.symlink_to('source.py')
    except OSError:
        pytest.skip('Test host does not permit symbolic link creation')
    git(repo, 'add', 'link.py')
    git(repo, 'commit', '-qm', 'synthetic symlink input')
    with pytest.raises(ValueError, match='regular tracked files'):
        provenance.capture_source_provenance(repo)


def add_test(repo, source):
    (repo / 'tests').mkdir()
    (repo / 'tests' / 'test_fixture.py').write_text(source, encoding='utf-8')
    git(repo, 'add', 'tests')
    git(repo, 'commit', '-qm', 'synthetic test fixture')


def test_runner_records_matching_start_and_end_snapshots(repo, tmp_path):
    add_test(repo, 'def test_fixture(): assert 1 + 1 == 2\n')
    expected = provenance.capture_source_provenance(repo)
    output = tmp_path / 'bounded-results'
    assert runner.execute(root=repo, output=output, paths=['tests'], workers=1,
                          collection_timeout=30, shard_timeout=30, suite_timeout=60) == 0
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['source_provenance_start'] == summary['source_provenance_end'] == expected


def test_runner_refuses_success_after_tracked_source_changes(repo, tmp_path):
    add_test(repo, "from pathlib import Path\ndef test_fixture():\n    Path('source.py').write_text('changed during tests\\n')\n")
    output = tmp_path / 'bounded-results'
    assert runner.execute(root=repo, output=output, paths=['tests'], workers=1,
                          collection_timeout=30, shard_timeout=30, suite_timeout=60) == 1
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['success'] is False
    assert summary['source_provenance_start']['clean'] is True
    assert 'differs from HEAD' in summary['error']
    assert not (output / 'results.xml').exists()


def test_runner_refuses_dirty_git_source_before_collection(repo, tmp_path):
    (repo / 'source.py').write_text('dirty before collection')
    output = tmp_path / 'bounded-results'
    assert runner.execute(root=repo, output=output, paths=['tests']) == 1
    summary = json.loads((output / 'summary.json').read_text())
    assert summary['success'] is False
    assert not (output / 'collection').exists()


def test_staged_edit_with_restored_worktree_is_not_clean(repo):
    source = repo / 'source.py'
    original = source.read_bytes()
    source.write_bytes(b'answer = 99\n')
    git(repo, 'add', 'source.py')
    source.write_bytes(original)
    # Reproduce the blind spot: HEAD-vs-worktree is clean, but index-vs-HEAD
    # still contains a staged edit (porcelain status is MM).
    assert git(repo, 'diff', '--quiet', 'HEAD', '--') == b''
    assert git(repo, 'status', '--porcelain', '--', 'source.py').startswith(b'MM ')
    cached = subprocess.run(['git', '-C', str(repo), 'diff', '--cached', '--quiet', 'HEAD', '--'],
                            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert cached.returncode == 1
    with pytest.raises(ValueError, match='source or index differs'):
        provenance.capture_source_provenance(repo)
    with pytest.raises(ValueError, match='source or index differs'):
        runner.source_provenance(repo)
