"""Actual save/reopen/recovery retains lock flags and complete source provenance."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
import wave
from pathlib import Path
from uuid import uuid4

from lumen.model import Project
from lumen.project_recovery import RecoveryStore
from test_clip_groups_persistence import populated_editor, add_groups


def test_locks_save_reopen_recovery_history_and_detached_payloads(tmp_path):
    editor = populated_editor(tmp_path)
    add_groups(editor)
    destination = tmp_path / '锁定分组与来源.lumen'
    # Use real synthetic PCM with truthful sound provenance. Put the source in
    # its portable destination up front so save/reopen equality is exact too.
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x00' * 48000)
    sound_bytes = buffer.getvalue()
    sound = editor.project.media[2]
    sound.sound_provenance = replace(sound.sound_provenance, output_sha256=hashlib.sha256(sound_bytes).hexdigest())
    sound.path = str(destination.parent / (destination.stem + '.assets') / 'sounds' /
                     (sound.sound_provenance.output_sha256 + '.wav'))
    baseline = deepcopy(editor.project)
    paths = [Path(m.path) for m in baseline.media]
    paths.extend([Path(baseline.clips[0].grade.lut_path), Path(baseline.overlays[0].asset_path),
                  Path(baseline.caption_style.font_directory) / 'sample.ttf'])
    originals = {}
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        originals[path] = (sound_bytes if path == Path(sound.path) else
                           f'User-owned source or auxiliary asset {index}'.encode())
        path.write_bytes(originals[path])
    editor.set_track_edit_lock(0, True)
    editor.set_track_edit_lock(2, True)
    locked = deepcopy(editor.project)
    expected = deepcopy(baseline); expected.track_edit_locks = [True, False, True, False]
    assert locked == expected
    locked.save(destination)
    serialized = json.loads(destination.read_text(encoding='utf-8'))
    assert serialized['version'] == 43
    assert serialized['track_edit_locks'] == [True, False, True, False]
    reopened = Project.load(destination)
    assert reopened == locked
    saved_bytes = destination.read_bytes()
    with RecoveryStore(tmp_path / 'recovery') as store:
        identity = str(uuid4())
        entry = store.write(reopened, identity, destination)
        store.release(identity)
        entries, warnings = store.list_entries()
        assert entries == [entry] and not warnings
        restored = store.load(entries[0])
        assert restored == locked
        envelope = json.loads(entry.path.read_text())['project']
        assert envelope['track_edit_locks'] == serialized['track_edit_locks']
        assert envelope['clip_groups'] == serialized['clip_groups']
    for result in (reopened, restored):
        assert result.media == baseline.media
        assert result.media[1].speech_provenance == baseline.media[1].speech_provenance
        assert result.media[2].sound_provenance == baseline.media[2].sound_provenance
        assert result.clips == baseline.clips
        assert result.clip_groups == baseline.clip_groups
        assert result.media_bins == baseline.media_bins
        assert result.media_bin_ids == baseline.media_bin_ids
    assert editor.undo() and editor.undo() and editor.project == baseline
    assert editor.redo() and editor.redo() and editor.project == locked
    restored.track_edit_locks[0] = False
    restored.clip_groups[0].clip_ids.reverse()
    reopened.track_edit_locks[2] = False
    assert editor.project == locked and Project.load(destination) == locked
    assert destination.read_bytes() == saved_bytes
    assert all(path.read_bytes() == data for path, data in originals.items())
