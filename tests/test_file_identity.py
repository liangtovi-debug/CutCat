"""Native and simulated-Windows coverage for all bounded asset readers."""
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from lumen import file_identity, local_ai, semantic_search, sticker_assets, subtitle_ai, tts


def snapshot(**changes):
    fields = dict(st_dev=7, st_ino=123, st_mode=stat.S_IFREG | 0o644,
                  st_size=16, st_mtime_ns=900, st_ctime_ns=800,
                  st_birthtime_ns=100, st_file_attributes=0)
    return SimpleNamespace(**(fields | changes))


def changed(info, **changes):
    return SimpleNamespace(**({key: getattr(info, key) for key in dir(info)
                              if key.startswith('st_')} | changes))


def test_windows_cross_api_ctime_is_not_same_source_ctime(monkeypatch):
    monkeypatch.setattr(file_identity, '_WINDOWS', True)
    by_path = snapshot(st_ctime_ns=100)  # creation time
    by_fd = snapshot(st_ctime_ns=800)  # metadata change time
    assert file_identity.path_matches_descriptor(by_path, by_fd)
    assert not file_identity.same_file_state(by_path, by_fd)
    assert not file_identity.same_file_state(by_fd, changed(by_fd, st_ctime_ns=801))


def test_posix_cross_api_ctime_is_still_checked(monkeypatch):
    monkeypatch.setattr(file_identity, '_WINDOWS', False)
    assert not file_identity.path_matches_descriptor(snapshot(), snapshot(st_ctime_ns=801))


@pytest.mark.parametrize('windows', [False, True])
@pytest.mark.parametrize('field,value', [
    ('st_dev', 8), ('st_ino', 124), ('st_mode', stat.S_IFLNK | 0o644),
    ('st_size', 15), ('st_mtime_ns', 901), ('st_birthtime_ns', 101),
])
def test_identity_size_time_and_type_mismatches_fail_closed(monkeypatch, windows, field, value):
    monkeypatch.setattr(file_identity, '_WINDOWS', windows)
    before, after = snapshot(), snapshot(**{field: value})
    assert not file_identity.same_file_state(before, after)
    assert not file_identity.path_matches_descriptor(before, after)


@pytest.mark.parametrize('info', [snapshot(st_mode=stat.S_IFLNK), snapshot(st_mode=stat.S_IFIFO),
                                  snapshot(st_file_attributes=0x400)])
def test_symlinks_special_files_and_windows_reparse_points_are_not_plain(info):
    assert not file_identity.is_plain_file(info)


READERS = ('stickers', 'models', 'speech', 'search', 'subtitle-assistant')
ERRORS = (sticker_assets.StickerAssetError, local_ai.LocalAIError, tts.TTSError,
          semantic_search.SearchError, subtitle_ai.AssistantError)
PAYLOAD = b'asset\r\nwith binary\x00\x1a\xff bytes\n'


def read_asset(reader, path):
    if reader == 'stickers':
        return hashlib.sha256(sticker_assets._read_bounded(path, 1024)).hexdigest()
    if reader == 'models':
        result = local_ai.inspect_model_files('semantic-search', {'model': path})
        assert result.files[0].size_bytes == len(PAYLOAD)
        return result.files[0].sha256
    if reader == 'speech':
        return tts._hash(path, None)
    if reader == 'subtitle-assistant':
        digest, signature = subtitle_ai._hash(path, None)
        assert signature == subtitle_ai._signature(path)
        return digest
    return semantic_search._hash(path)


@pytest.fixture
def asset(tmp_path):
    # Canonicalize macOS's /var alias, as the production callers do.
    path = tmp_path.resolve() / 'asset.bin'
    path.write_bytes(PAYLOAD)
    return path


@pytest.mark.parametrize('reader', READERS)
def test_native_reader_preserves_binary_content(reader, asset):
    assert read_asset(reader, asset) == hashlib.sha256(PAYLOAD).hexdigest()


@pytest.mark.parametrize('reader', READERS)
def test_windows_stat_fstat_ctime_difference_does_not_reject_unchanged_file(monkeypatch, reader, asset):
    monkeypatch.setattr(file_identity, '_WINDOWS', True)
    original = os.fstat
    path_ctime = asset.lstat().st_ctime_ns

    def fstat(fd):
        return changed(original(fd), st_ctime_ns=path_ctime + 1_000_000)

    monkeypatch.setattr(os, 'fstat', fstat)
    assert read_asset(reader, asset) == hashlib.sha256(PAYLOAD).hexdigest()


@pytest.mark.parametrize('reader', READERS)
@pytest.mark.parametrize('field', ['st_ctime_ns', 'st_mtime_ns', 'st_size', 'st_ino'])
def test_descriptor_changes_during_read_remain_rejected(monkeypatch, reader, asset, field):
    monkeypatch.setattr(file_identity, '_WINDOWS', True)
    original = os.fstat
    path_ctime = asset.lstat().st_ctime_ns
    calls = 0

    def fstat(fd):
        nonlocal calls
        calls += 1
        info = changed(original(fd), st_ctime_ns=path_ctime + 1_000_000)
        return changed(info, **{field: getattr(info, field) + 1}) if calls == 2 else info

    monkeypatch.setattr(os, 'fstat', fstat)
    with pytest.raises(ERRORS, match='changed while'):
        read_asset(reader, asset)
    assert calls == 2


@pytest.mark.parametrize('reader', READERS)
@pytest.mark.parametrize('field', ['st_ctime_ns', 'st_mtime_ns', 'st_size', 'st_ino', 'st_file_attributes'])
def test_path_changes_after_read_remain_rejected(monkeypatch, reader, asset, field):
    monkeypatch.setattr(file_identity, '_WINDOWS', True)
    original_lstat, original_fstat = Path.lstat, os.fstat
    finished_reading = False
    calls = 0

    def fstat(fd):
        nonlocal calls, finished_reading
        calls += 1
        finished_reading = calls == 2
        return original_fstat(fd)

    def lstat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == asset and finished_reading:
            value = 0x400 if field == 'st_file_attributes' else getattr(info, field) + 1
            return changed(info, **{field: value})
        return info

    monkeypatch.setattr(os, 'fstat', fstat)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(ERRORS):
        read_asset(reader, asset)
    assert finished_reading


@pytest.mark.parametrize('reader', READERS)
def test_same_size_same_mtime_replacement_before_open_is_rejected(monkeypatch, reader, asset):
    replacement = asset.with_name('replacement.bin')
    replacement.write_bytes(PAYLOAD)
    before = asset.stat()
    os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
    original = os.open
    swapped = False

    def open_swapped(path, flags, *args, **kwargs):
        nonlocal swapped
        if Path(path) == asset:
            os.replace(replacement, asset)
            swapped = True
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', open_swapped)
    with pytest.raises(ERRORS, match='changed while being opened'):
        read_asset(reader, asset)
    assert swapped


@pytest.mark.parametrize('reader', READERS)
def test_short_read_is_not_accepted_even_if_all_metadata_is_unchanged(monkeypatch, reader, asset):
    original = os.fdopen

    class ShortRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            return self.stream.read(size)[:-1]

    monkeypatch.setattr(os, 'fdopen', lambda *args, **kwargs: ShortRead(original(*args, **kwargs)))
    with pytest.raises(ERRORS, match='changed while'):
        read_asset(reader, asset)
