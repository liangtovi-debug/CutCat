"""Independent local integrity checks; not native Windows/macOS validation."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from lumen.model import Media, Project
from lumen.project_recovery import (RecoveryBusyError, RecoveryCapacityError,
                                    RecoveryError, RecoveryStore)


def test_repeated_post_rename_sync_failures_do_not_grow_generation_count(tmp_path, monkeypatch):
    """A successful rename followed by failed directory fsync is a distinct phase."""
    with RecoveryStore(tmp_path / 'recovery') as store:
        identity = str(uuid4())
        baseline = store.write(Project(name='last durable edit'), identity)
        sync = store._sync_directory

        def unavailable():
            raise OSError('directory fsync unavailable')

        monkeypatch.setattr(store, '_sync_directory', unavailable)
        for index in range(6):
            with pytest.raises(OSError, match='directory fsync unavailable'):
                store.write(Project(name=f'unconfirmed edit {index}'), identity)
        monkeypatch.setattr(store, '_sync_directory', sync)
        assert baseline.path.exists()
        assert store.load(baseline).name == 'last durable edit'
        assert len(list(store.root.glob(f'cutcat-{identity}.*.json'))) <= store.generations
        assert not list(store.root.glob('*.tmp'))


def test_discard_validates_all_generations_before_deleting_any(tmp_path):
    with RecoveryStore(tmp_path / 'recovery') as store:
        identity = str(uuid4())
        safe = store.write(Project(name='safe generation'), identity)
        unsafe = store.write(Project(name='replaced by a link'), identity)
        outside = tmp_path / 'unrelated-data.txt'
        outside.write_bytes(b'never modify this input')
        unsafe.path.unlink()
        try:
            unsafe.path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f'Symlink creation unavailable: {exc}')
        before = safe.path.read_bytes()
        with pytest.raises(RecoveryError):
            store.discard(identity)
        assert safe.path.read_bytes() == before
        assert outside.read_bytes() == b'never modify this input'
        assert unsafe.path.is_symlink()


@pytest.mark.parametrize('lock_kind', ['store', 'session'])
def test_hardlinked_locks_never_touch_outside_file(tmp_path, lock_kind):
    root = tmp_path / 'recovery'
    root.mkdir()
    identity = str(uuid4())
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(b'external contents')
    name = '.cutcat-recovery.lock' if lock_kind == 'store' else f'.cutcat-{identity}.lock'
    try:
        os.link(outside, root / name)
    except OSError as exc:
        pytest.skip(f'Hardlink creation unavailable: {exc}')
    with RecoveryStore(root) as store:
        with pytest.raises(RecoveryError):
            store.write(Project(), identity)
    assert outside.read_bytes() == b'external contents'
    assert not list(root.glob('*.json'))


def test_unknown_project_settings_never_become_recovered_configuration(tmp_path):
    with RecoveryStore(tmp_path / 'recovery') as store:
        identity = str(uuid4())
        entry = store.write(Project(), identity)
        payload = json.loads(entry.path.read_text(encoding='utf-8'))
        payload['project']['api_settings'] = {'api_key': 'synthetic-never-use-key'}
        entry.path.write_text(json.dumps(payload), encoding='utf-8')
        store.release(identity)
        entries, warnings = store.list_entries()
        assert not entries and warnings
        with pytest.raises(RecoveryError):
            store.load(entry)


@pytest.mark.skipif(os.name == 'nt', reason='Checks a foreign Windows reference on POSIX')
def test_foreign_windows_reference_is_not_rebased_into_recovery_directory(tmp_path):
    path = r'C:\CutCat Media\missing clip.mp4'
    project = Project(media=[Media('source', path, 'missing clip', 1)])
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(project, uuid4())
        restored = store.load(entry)
        assert restored.media[0].path == path
        assert project.media[0].path == path
        assert str(store.root) not in restored.media[0].path


def test_forged_original_path_metadata_is_not_trusted(tmp_path):
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(Project(), uuid4(), tmp_path / 'original.lumen')
        forged = replace(entry, original_path=str(tmp_path / 'other.lumen'))
        with pytest.raises(RecoveryError, match='changed'):
            store.load(forged)
        assert store.load(entry).name == Project().name


def test_first_write_process_crash_is_visible_bounded_and_discardable(tmp_path):
    root = tmp_path / 'recovery'
    identity = str(uuid4())
    code = '''
import os, sys
from lumen.model import Project
from lumen.project_recovery import RecoveryStore
def stop_before_publish(*args, **kwargs):
    os._exit(17)
os.replace = stop_before_publish
RecoveryStore(sys.argv[1], max_sessions=1).write(Project(), sys.argv[2])
'''
    crash = subprocess.run([sys.executable, '-c', code, str(root), identity], timeout=15)
    assert crash.returncode == 17
    with RecoveryStore(root, max_sessions=1) as store:
        entries, warnings = store.list_entries()
        assert not entries and warnings
        assert store.unrecoverable_identities == (identity,)
        assert len(list(root.glob('*.tmp'))) == 1
        with pytest.raises(RecoveryCapacityError):
            store.write(Project(name='must not exceed session cap'), uuid4())
        store.discard(identity)
        assert not list(root.glob('*.tmp'))
        store.write(Project(name='space explicitly reclaimed'), uuid4())


def test_process_lock_cleanup_never_allows_two_owners(tmp_path):
    """Contention exercises lock deletion, recreation and observer cleanup."""
    root = tmp_path / 'recovery'
    identity = str(uuid4())
    sentinel = tmp_path / 'exclusive-owner'
    code = '''
import os, sys, time
from pathlib import Path
from lumen.project_recovery import RecoveryBusyError, RecoveryStore
store = RecoveryStore(sys.argv[1])
identity, marker = sys.argv[2], Path(sys.argv[3])
deadline = time.monotonic() + 20
completed = 0
try:
    while completed < 40:
        if time.monotonic() > deadline:
            raise AssertionError('session claim did not make progress')
        try:
            owned = store.acquire(identity)
        except RecoveryBusyError:
            owned = False
        if not owned:
            time.sleep(.001)
            continue
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            time.sleep(.002)
            marker.unlink()
            completed += 1
        finally:
            store.release(identity)
finally:
    store.close()
print(completed)
'''
    children = [subprocess.Popen([sys.executable, '-c', code, str(root), identity, str(sentinel)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    try:
        deadline = time.monotonic() + 25
        with RecoveryStore(root) as observer:
            while any(child.poll() is None for child in children):
                assert time.monotonic() < deadline, 'contending processes stalled'
                try:
                    assert observer.list_entries() == ([], [])
                except RecoveryBusyError:
                    pass  # Global transaction contention is a retryable outcome.
                time.sleep(.001)
            for child in children:
                output, errors = child.communicate(timeout=5)
                assert child.returncode == 0, errors
                assert output.strip() == '40'
            assert observer.list_entries() == ([], [])
        assert not sentinel.exists()
        assert len(list(root.glob('*.lock'))) <= 1  # The global lock is permanent.
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)
