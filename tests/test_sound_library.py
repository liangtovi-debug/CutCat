"""Offline library provenance, adversarial input, and publication regressions."""
from dataclasses import FrozenInstanceError, replace
import errno
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import wave

import numpy as np
import pytest

from lumen import sound_library as sounds


def wav_bytes(value=8000, count=4800, rate=48000, channels=2, width=2):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes((struct.pack('<h', value) if width == 2 else bytes(width)) * count * channels)
    return buf.getvalue()


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / 'original.wav'
    path.write_bytes(wav_bytes())
    return path


@pytest.fixture
def ffmpeg():
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('FFmpeg is not installed.')


def entry(path):
    data = path.read_bytes()
    rate, count, peak = sounds._wav_info(data)
    digest = hashlib.sha256(data).hexdigest()
    return sounds.SoundEntry('sound', 'A sound', '一个音效', 'ui', ('click', '点击'), path,
                             digest, rate, count, 'CutCat original / CC0-1.0', 'procedural', digest, peak)


def test_builtin_original24_verified_bilingual_and_immutable():
    entries = [item for item in sounds.builtin_sounds() if item.origin == 'procedural']
    assert len(entries) == 24
    assert len({item.id for item in entries}) == 24
    assert {item.category for item in entries} == set(sounds.CATEGORIES) - {'foley'}
    for item in entries:
        assert item.path.is_absolute()
        assert item.source_sha256 == item.sha256
        assert item.name_en and item.name_zh and item.name_en != item.name_zh
        assert item.duration == item.sample_count / item.sample_rate
        assert item.license == 'CutCat original / CC0-1.0'
        assert item.origin == 'procedural'
        assert sounds.verify_sound(item) is None
    with pytest.raises(FrozenInstanceError):
        entries[0].name_en = 'changed'


def test_bilingual_search_filters_preserve_order():
    entries = sounds.builtin_sounds()
    assert sounds.search_sounds(entries, '点击')[0].id == 'ui_soft_click'
    assert sounds.search_sounds(entries, 'SOFT CLICK')[0].id == 'ui_soft_click'
    assert sounds.search_sounds(entries, '点击', 'impacts') == []
    assert sounds.search_sounds(entries, category='ui') == [e for e in entries if e.category == 'ui']
    assert sounds.search_sounds(entries) == entries
    assert sounds.search_sounds(entries, 'unmatchable-sound') == []


@pytest.mark.parametrize('digest', ['', 'z' * 64, '0' * 63, None, 42])
def test_bad_digest_is_rejected(audio, digest):
    with pytest.raises(sounds.SoundLibraryError, match='SHA-256'):
        sounds.verify_sound(replace(entry(audio), sha256=digest))


def test_changed_wav_and_metadata_rejected(audio):
    original = entry(audio)
    with pytest.raises(sounds.SoundLibraryError, match='metadata'):
        sounds.verify_sound(replace(original, sample_count=original.sample_count + 1))
    with pytest.raises(sounds.SoundLibraryError, match='peak metadata'):
        sounds.verify_sound(replace(original, peak_dbfs=float('nan')))
    audio.write_bytes(wav_bytes(value=5000))
    with pytest.raises(sounds.SoundLibraryError, match='changed since import'):
        sounds.verify_sound(original)


@pytest.mark.parametrize('data', [wav_bytes(value=32767), wav_bytes(rate=44100),
                                 wav_bytes(channels=3), wav_bytes(width=1),
                                 wav_bytes()[:-20], b'not a wav'],
                         ids=['too-loud', 'wrong-rate', 'surround', 'pcm8', 'truncated', 'not-wav'])
def test_invalid_library_wav_rejected(data):
    with pytest.raises(sounds.SoundLibraryError):
        sounds._wav_info(data)


@pytest.mark.parametrize('path', ['https://example.com/a.wav', 'file:///tmp/a.wav',
                                 '//server/a.wav', r'\\server\a.wav', 'a/../b.wav',
                                 'name.wav:secret', 'invalid\x00.wav'])
def test_network_traversal_and_invalid_paths_rejected(path, tmp_path):
    with pytest.raises(sounds.SoundLibraryError):
        sounds.import_sound(path, tmp_path / 'library')


@pytest.mark.parametrize('extension', ['.m3u', '.m3u8', '.pls', '.mp4', '.mov', '.json', '.txt'])
def test_only_selected_audio_extensions_allowed(extension, tmp_path):
    path = tmp_path / ('sound' + extension)
    path.write_bytes(wav_bytes())
    with pytest.raises(sounds.SoundLibraryError, match='supported audio'):
        sounds.import_sound(path, tmp_path / 'library')


def test_size_and_precancel_do_not_start_decoder(audio, tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Decoder must not run.')
    monkeypatch.setattr(sounds, '_run', unexpected)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(sounds.SoundCancelled):
        sounds.import_sound(audio, tmp_path / 'library', cancelled)
    with audio.open('wb') as handle:
        handle.truncate(sounds.MAX_SOURCE_BYTES + 1)
    with pytest.raises(sounds.SoundLibraryError, match='size limit'):
        sounds.import_sound(audio, tmp_path / 'library')
    assert not (tmp_path / 'library').exists()


def test_import_is_persistent_content_addressed_and_keeps_source(audio, tmp_path, ffmpeg):
    original = audio.read_bytes()
    root = tmp_path / 'library'
    imported = sounds.import_sound(audio, root)
    assert audio.read_bytes() == original
    assert imported.source_sha256 == hashlib.sha256(original).hexdigest()
    assert imported.path.name == imported.sha256 + '.wav'
    assert imported.id == 'local-' + imported.sha256
    assert imported.category == 'local' and imported.origin == 'user-imported'
    assert imported.license == 'User-imported / rights not verified'
    assert sounds.local_sounds(root) == [imported]
    with wave.open(str(imported.path), 'rb') as handle:
        assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (48000, 2, 2)
        assert np.max(np.abs(np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2'))) == 8000
    assert len(list(root.iterdir())) == 2
    copy = tmp_path / 'renamed.wav'
    copy.write_bytes(original)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert sounds.import_sound(copy, root) == imported
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    audio.unlink()
    assert sounds.local_sounds(root) == [imported]


@pytest.mark.parametrize('value, expected', [(32000, 16384), (-32000, 16384), (2000, 2000), (0, 0)])
def test_import_attenuates_without_boosting(audio, tmp_path, ffmpeg, value, expected):
    audio.write_bytes(wav_bytes(value=value))
    imported = sounds.import_sound(audio, tmp_path / 'library')
    with wave.open(str(imported.path), 'rb') as handle:
        pcm = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2').astype(np.int32)
    assert max(abs(pcm)) == expected
    assert sounds.verify_sound(imported) is None


def test_import_resamples_and_rejects_long_audio(audio, tmp_path, ffmpeg):
    audio.write_bytes(wav_bytes(rate=24000, count=2400, channels=1))
    imported = sounds.import_sound(audio, tmp_path / 'library')
    assert imported.sample_count == 4800
    audio.write_bytes(wav_bytes(count=48000 * 61, channels=1))
    with pytest.raises(sounds.SoundLibraryError, match='at most 60 seconds'):
        sounds.import_sound(audio, tmp_path / 'library')
    assert sounds.local_sounds(tmp_path / 'library') == [imported]


@pytest.mark.parametrize('extension', ['mp3', 'flac', 'm4a', 'ogg'])
def test_real_compressed_audio_import(audio, tmp_path, ffmpeg, extension):
    compressed = tmp_path / ('compressed.' + extension)
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(audio), str(compressed)],
                   capture_output=True, check=True, timeout=20)
    before = compressed.read_bytes()
    imported = sounds.import_sound(compressed, tmp_path / 'library')
    assert imported.sample_rate == 48000
    assert 0 < imported.duration < 1
    assert imported.source_sha256 == hashlib.sha256(before).hexdigest()
    assert compressed.read_bytes() == before
    sounds.verify_sound(imported)


def test_renamed_playlist_is_not_decoded(audio, tmp_path, ffmpeg):
    # A local playlist must not cause any referenced files to be opened either.
    audio.write_text('#EXTM3U\n#EXTINF:1,ignored\nnonexistent-secret.mp3\n')
    with pytest.raises(sounds.SoundLibraryError, match='Cannot decode'):
        sounds.import_sound(audio, tmp_path / 'library')
    assert list((tmp_path / 'library').iterdir()) == []


def fake_decoder(monkeypatch, *, streams=None, raw=None, duration='0.1', on_decode=None):
    commands = []
    def run(command, directory, output, limit, cancel=None):
        commands.append(command)
        assert command[command.index('-protocol_whitelist') + 1] == 'file,pipe'
        assert 'concat' not in command[command.index('-format_whitelist') + 1]
        if output.name == 'probe.json':
            output.write_text(json.dumps({'streams': streams if streams is not None else [
                {'codec_type': 'audio', 'duration': duration}], 'format': {'duration': duration}}))
        else:
            output.write_bytes(raw if raw is not None else np.full(9600, 0.1, dtype='<f4').tobytes())
            if on_decode:
                on_decode()
    monkeypatch.setattr(sounds, '_run', run)
    return commands


@pytest.mark.parametrize('streams', [[], [{'codec_type': 'video'}],
    [{'codec_type': 'audio'}, {'codec_type': 'video', 'disposition': {'attached_pic': 0}}]])
def test_no_audio_or_video_input_rejected(audio, tmp_path, monkeypatch, streams):
    fake_decoder(monkeypatch, streams=streams)
    with pytest.raises(sounds.SoundLibraryError):
        sounds.import_sound(audio, tmp_path / 'library')
    assert list((tmp_path / 'library').iterdir()) == []


def test_attached_cover_art_is_allowed(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch, streams=[{'codec_type': 'audio'},
        {'codec_type': 'video', 'disposition': {'attached_pic': 1}}])
    assert sounds.import_sound(audio, tmp_path / 'library').sample_count == 4800


@pytest.mark.parametrize('raw', [b'', b'x', np.array([float('nan')] * 2, dtype='<f4').tobytes(),
    np.array([float('inf')] * 2, dtype='<f4').tobytes(), bytes((sounds.MAX_SAMPLES + 1) * 8)],
    ids=['empty', 'unaligned', 'nan', 'infinity', 'one-frame-too-long'])
def test_bad_decoded_samples_are_rejected(audio, tmp_path, monkeypatch, raw):
    fake_decoder(monkeypatch, raw=raw, duration=None)
    with pytest.raises(sounds.SoundLibraryError):
        sounds.import_sound(audio, tmp_path / 'library')
    assert list((tmp_path / 'library').iterdir()) == []


def test_exact_60_seconds_is_allowed(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch, raw=bytes(sounds.MAX_SAMPLES * 8), duration='60')
    assert sounds.import_sound(audio, tmp_path / 'library').duration == 60


def test_original_mutation_during_decode_is_rejected(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch, on_decode=lambda: audio.write_bytes(wav_bytes(value=1234)))
    with pytest.raises(sounds.SoundLibraryError, match='source changed'):
        sounds.import_sound(audio, tmp_path / 'library')
    assert list((tmp_path / 'library').iterdir()) == []


def test_local_corruption_is_isolated_and_logged(audio, tmp_path, monkeypatch, caplog):
    fake_decoder(monkeypatch)
    root = tmp_path / 'library'
    imported = sounds.import_sound(audio, root)
    (root / 'bad.json').write_text('{broken')
    malicious = json.loads((root / (imported.sha256 + '.json')).read_text())
    malicious['file'] = '../original.wav'
    (root / 'traversal.json').write_text(json.dumps(malicious))
    malicious['file'] = imported.path.name
    malicious['peak_dbfs'] = 10 ** 1000
    (root / 'overflow.json').write_text(json.dumps(malicious))
    assert sounds.local_sounds(root) == [imported]
    assert 'Skipping invalid local sound' in caplog.text
    assert sounds.local_sounds(tmp_path / 'missing') == []


def test_existing_conflict_is_never_overwritten(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch)
    root = tmp_path / 'library'
    imported = sounds.import_sound(audio, root)
    imported.path.write_bytes(b'changed')
    with pytest.raises(sounds.SoundLibraryError, match='changed since import'):
        sounds.import_sound(audio, root)
    assert imported.path.read_bytes() == b'changed'
    (root / (imported.sha256 + '.json')).unlink()
    with pytest.raises(sounds.SoundLibraryError, match='conflicts'):
        sounds.import_sound(audio, root)
    assert imported.path.read_bytes() == b'changed'


@pytest.mark.parametrize('phase', ['wav', 'json'])
def test_cancel_during_publication_rolls_back_only_new_files(audio, tmp_path, monkeypatch, phase):
    fake_decoder(monkeypatch)
    cancelled = threading.Event()
    publish = sounds._publish
    def cancel_after_publish(stage, destination, expected):
        result = publish(stage, destination, expected)
        if destination.suffix == '.' + phase:
            cancelled.set()
        return result
    monkeypatch.setattr(sounds, '_publish', cancel_after_publish)
    with pytest.raises(sounds.SoundCancelled):
        sounds.import_sound(audio, tmp_path / 'library', cancelled)
    assert list((tmp_path / 'library').iterdir()) == []


def test_cancel_leaves_preexisting_orphan_wav_untouched(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch)
    root = tmp_path / 'library'
    original = sounds.import_sound(audio, root)
    sidecar = root / (original.sha256 + '.json')
    sidecar.unlink()
    before = original.path.read_bytes()
    cancelled = threading.Event()
    publish = sounds._publish
    def cancel_after_publish(stage, destination, expected):
        result = publish(stage, destination, expected)
        cancelled.set()
        return result
    monkeypatch.setattr(sounds, '_publish', cancel_after_publish)
    with pytest.raises(sounds.SoundCancelled):
        sounds.import_sound(audio, root, cancelled)
    assert original.path.read_bytes() == before
    assert list(root.iterdir()) == [original.path]


def test_copy_is_verified_portable_and_immutable(audio, tmp_path):
    original = entry(audio)
    target = tmp_path / 'project' / 'assets' / (original.sha256 + '.wav')
    assert sounds.copy_sound_asset(audio, original.sha256, target) == target
    assert sounds.copy_sound_asset(audio, original.sha256.upper(), target) == target
    assert target.read_bytes() == audio.read_bytes()
    target.write_bytes(b'conflict')
    with pytest.raises(sounds.SoundLibraryError, match='conflicts'):
        sounds.copy_sound_asset(audio, original.sha256, target)
    assert target.read_bytes() == b'conflict'
    with pytest.raises(sounds.SoundLibraryError, match='changed since import'):
        sounds.copy_sound_asset(audio, '0' * 64, tmp_path / 'other.wav')
    assert not (tmp_path / 'other.wav').exists()


def test_symlink_inputs_and_destination_escape_rejected(audio, tmp_path):
    selected = tmp_path / 'linked.wav'
    try:
        selected.symlink_to(audio)
    except OSError:
        pytest.skip('Symlinks are unavailable.')
    with pytest.raises(sounds.SoundLibraryError):
        sounds.import_sound(selected, tmp_path / 'library')
    outside = tmp_path / 'outside'
    outside.mkdir()
    link = tmp_path / 'escape'
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(sounds.SoundLibraryError, match='symbolic links'):
        sounds.copy_sound_asset(audio, entry(audio).sha256, link / 'copy.wav')
    assert list(outside.iterdir()) == []
    target = tmp_path / 'target.wav'
    target.symlink_to(audio)
    with pytest.raises(sounds.SoundLibraryError):
        sounds.copy_sound_asset(audio, entry(audio).sha256, target)
    assert audio.read_bytes() == wav_bytes()


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_subprocess_output_is_hard_bounded(tmp_path, stream):
    command = [sys.executable, '-c', f'import sys; sys.{stream}.buffer.write(b"x" * 1000000)']
    output = tmp_path / 'out'
    with pytest.raises(sounds.SoundLibraryError, match='output limit'):
        sounds._run(command, tmp_path, output, 1024)
    assert output.stat().st_size <= 1024
    assert (tmp_path / 'decoder-error.txt').stat().st_size <= 64 * 1024


def test_subprocess_cancel_and_timeout_kill_child(tmp_path, monkeypatch):
    original_popen, children = subprocess.Popen, []
    def popen(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(sounds.subprocess, 'Popen', popen)
    command = [sys.executable, '-c', 'import time; time.sleep(30)']
    cancelled = threading.Event()
    timer = threading.Timer(0.1, cancelled.set)
    timer.start()
    try:
        with pytest.raises(sounds.SoundCancelled):
            sounds._run(command, tmp_path, tmp_path / 'out', 1024, cancelled)
    finally:
        timer.cancel()
    assert all(child.poll() is not None for child in children)
    monkeypatch.setattr(sounds, 'MAX_PROCESS_SECONDS', 0.05)
    with pytest.raises(sounds.SoundLibraryError, match='timed out'):
        sounds._run(command, tmp_path, tmp_path / 'out', 1024)
    assert all(child.poll() is not None for child in children)


def test_missing_decoder_has_clear_error(audio, tmp_path, monkeypatch):
    monkeypatch.setenv('LUMEN_FFPROBE', str(tmp_path / 'missing-ffprobe'))
    with pytest.raises(sounds.SoundLibraryError, match='FFmpeg and ffprobe'):
        sounds.import_sound(audio, tmp_path / 'library')
    assert list((tmp_path / 'library').iterdir()) == []


def test_project_provenance_count_and_rate_are_verified(audio):
    original = entry(audio)
    assert sounds.verify_sound_file(audio, original.sha256, 48000, 4800) is None
    for rate, count in [(48000, 1), (44100, 4800), (48000, True), (48000, 4800.0)]:
        with pytest.raises(sounds.SoundLibraryError, match='metadata'):
            sounds.verify_sound_file(audio, original.sha256, rate, count)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(sounds.SoundCancelled):
        sounds.verify_sound_file(audio, original.sha256, 48000, 4800, cancelled)


def test_builtin_root_allows_trusted_packaging_symlink(tmp_path, monkeypatch):
    original = sounds.builtin_sounds()
    packaged = tmp_path / 'Frameworks' / 'lumen' / 'assets' / 'sounds'
    packaged.parent.mkdir(parents=True)
    try:
        packaged.symlink_to(sounds._BUILTIN_ROOT, target_is_directory=True)
    except OSError:
        pytest.skip('Symlinks are unavailable.')
    monkeypatch.setattr(sounds, '_BUILTIN_ROOT', packaged)
    assert sounds.builtin_sounds() == original


def test_copy_on_filesystem_without_hardlinks_is_exclusive(audio, tmp_path, monkeypatch):
    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'Filesystem has no hard links')
    monkeypatch.setattr(sounds.os, 'link', unsupported)
    original = entry(audio)
    target = tmp_path / 'portable.wav'
    assert sounds.copy_sound_asset(audio, original.sha256, target) == target
    assert target.read_bytes() == audio.read_bytes()
    assert sounds.copy_sound_asset(audio, original.sha256, target) == target
    target.write_bytes(b'conflict')
    with pytest.raises(sounds.SoundLibraryError, match='conflicts'):
        sounds.copy_sound_asset(audio, original.sha256, target)
    assert target.read_bytes() == b'conflict'


def test_hardlink_permission_denial_does_not_use_fallback(audio, tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, 'Denied')
    def unexpected(*args, **kwargs):
        raise AssertionError('Permission denials must not trigger a fallback.')
    monkeypatch.setattr(sounds.os, 'link', denied)
    monkeypatch.setattr(sounds, '_exclusive_copy', unexpected)
    with pytest.raises(sounds.SoundLibraryError, match='Cannot safely save'):
        sounds.copy_sound_asset(audio, entry(audio).sha256, tmp_path / 'portable.wav')


def test_exclusive_copy_failure_removes_own_partial_file(audio, tmp_path, monkeypatch):
    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'No hard links')
    def no_space(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'Disk full')
    monkeypatch.setattr(sounds.os, 'link', unsupported)
    monkeypatch.setattr(sounds.os, 'fsync', no_space)
    target = tmp_path / 'portable.wav'
    with pytest.raises(sounds.SoundLibraryError):
        sounds.copy_sound_asset(audio, entry(audio).sha256, target)
    assert not target.exists()


def test_cancel_after_fallback_publication_cleans_new_files(audio, tmp_path, monkeypatch):
    fake_decoder(monkeypatch)
    cancelled = threading.Event()
    publish = sounds._publish
    def unsupported(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'No hard links')
    def cancel_after_publish(stage, destination, expected):
        result = publish(stage, destination, expected)
        if destination.suffix == '.json':
            cancelled.set()
        return result
    monkeypatch.setattr(sounds.os, 'link', unsupported)
    monkeypatch.setattr(sounds, '_publish', cancel_after_publish)
    with pytest.raises(sounds.SoundCancelled):
        sounds.import_sound(audio, tmp_path / 'library', cancelled)
    assert list((tmp_path / 'library').iterdir()) == []
