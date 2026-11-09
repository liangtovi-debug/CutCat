"""Persistent clip membership survives real save, recovery, and source relink.

Groups must never become media bins, rewrite source references/provenance, or
turn timeline deletion into deletion of any source or auxiliary asset bytes.
"""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from PySide6.QtWidgets import QFileDialog

from lumen.clip_motion import ClipMotionKeyframe
from lumen.media import ffmpeg_available
from lumen.model import Clip, Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import WindowKeyframe
from test_project_recovery import full_project
from test_tts_relink_exact_timing import install_exact_take
from test_ui import app, window


def populated_editor(tmp_path):
    project = full_project(tmp_path)
    project.clips.extend([
        Clip('second-voice', 'speech', 2, 100, 0, 30),
        Clip('loose-sound', 'sound', 3, 110, 0, 20)])
    project.clips[0].motion_keyframes = [
        ClipMotionKeyframe(-8, x=-12, y=7, scale=.5, opacity=.3),
        ClipMotionKeyframe(12, x=18, y=-3, scale=.8, opacity=.7, interpolation='hold'),
        ClipMotionKeyframe(70, x=25, y=12, scale=1.1, opacity=.5)]
    project.clips[0].grade.qualifier.window_keyframes = [
        WindowKeyframe(-4, .2, .3, .4, .5), WindowKeyframe(70, .8, .7, .6, .4)]
    editor = Editor(project)
    parent = editor.create_media_bin('素材')
    child = editor.create_media_bin('Generated audio', parent.id)
    editor.move_media_to_bin(('speech', 'sound'), child.id)
    editor.move_media_to_bin(('v',), parent.id)
    return editor


def add_groups(editor):
    first = editor.group_clips(('voice-clip', 'video'))
    second = editor.group_clips(('sound-clip', 'second-voice'))
    assert first.id != second.id
    return deepcopy(editor.project.clip_groups)


def test_save_reopen_recovery_and_undo_keep_membership_bins_and_full_payload(tmp_path):
    editor = populated_editor(tmp_path)
    baseline = deepcopy(editor.project)
    groups = add_groups(editor)
    grouped = deepcopy(editor.project)
    assert grouped.clips == baseline.clips and grouped.media == baseline.media
    assert grouped.media_bins == baseline.media_bins and grouped.media_bin_ids == baseline.media_bin_ids
    destination = tmp_path / '组和素材箱.lumen'
    editor.project.save(destination)
    data = json.loads(destination.read_text(encoding='utf-8'))
    assert data['version'] == 43
    assert data['clip_groups'] == [{'id': group.id, 'clip_ids': group.clip_ids} for group in groups]
    reopened = Project.load(destination)
    assert reopened == grouped
    assert reopened.clips[0].timing == baseline.clips[0].timing
    assert reopened.clips[0].motion_keyframes == baseline.clips[0].motion_keyframes
    assert reopened.clips[0].grade.qualifier.window_keyframes == baseline.clips[0].grade.qualifier.window_keyframes

    # The full recovery envelope, not only the standalone schema round trip.
    original_bytes = destination.read_bytes()
    with RecoveryStore(tmp_path / 'recovery') as recovery:
        identity = str(uuid4())
        entry = recovery.write(reopened, identity, destination)
        recovery.release(identity)
        entries, warnings = recovery.list_entries()
        assert entries == [entry] and not warnings
        restored = recovery.load(entries[0])
        assert restored == grouped
        assert restored.media[1].speech_provenance == baseline.media[1].speech_provenance
        assert restored.media[2].sound_provenance == baseline.media[2].sound_provenance
        assert restored.media_bins == baseline.media_bins and restored.media_bin_ids == baseline.media_bin_ids
        assert json.loads(entry.path.read_text())['project']['clip_groups'] == data['clip_groups']
    assert destination.read_bytes() == original_bytes

    assert editor.undo() and editor.undo() and editor.project == baseline
    assert editor.redo() and editor.redo() and editor.project == grouped
    # Recovery/history are detached: mutating a loaded membership list cannot
    # silently change the live editor, a prior snapshot, or a later file load.
    restored.clip_groups[0].clip_ids.reverse()
    assert editor.project == grouped and Project.load(destination) == grouped


def test_deleting_whole_group_preserves_other_group_bins_provenance_and_all_files(tmp_path):
    editor = populated_editor(tmp_path)
    groups = add_groups(editor)
    before = deepcopy(editor.project)
    paths = [Path(media.path) for media in before.media]
    paths.extend([Path(before.clips[0].grade.lut_path), Path(before.overlays[0].asset_path),
                  Path(before.caption_style.font_directory) / 'sample.ttf'])
    original = {}
    for index, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        original[path] = f'Protected synthetic source or asset {index}'.encode()
        path.write_bytes(original[path])

    editor.delete_clips(('voice-clip', 'video'))
    deleted = deepcopy(editor.project)
    assert deleted.clip_groups == [groups[1]]
    assert {clip.id for clip in deleted.clips} == {'sound-clip', 'second-voice', 'loose-sound'}
    assert deleted.media == before.media
    assert deleted.media_bins == before.media_bins and deleted.media_bin_ids == before.media_bin_ids
    assert deleted.captions == before.captions and deleted.overlays == before.overlays
    assert all(path.read_bytes() == data for path, data in original.items())
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == deleted
    assert all(path.read_bytes() == data for path, data in original.items())


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_relink_exact_generated_source_preserves_group_bins_and_provenance(
        window, app, tmp_path, monkeypatch):
    moved, original_bytes, provenance = install_exact_take(window, tmp_path)
    media = window.project.media[0]
    sibling = window.editor.add_clip(media.id, 3, start=7)
    parent = window.editor.create_media_bin('Generated')
    child = window.editor.create_media_bin('Narration', parent.id)
    window.editor.move_media_to_bin((media.id,), child.id)
    group = window.editor.group_clips(('speech-clip', sibling.id))
    window.changed()
    before = deepcopy(window.project)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(moved), ''))
    window.relink_dialog()
    assert not window.test_warnings and not window.project.missing_media()
    assert window.project.clips == before.clips and window.project.clip_groups == [group]
    assert window.project.media_bins == before.media_bins and window.project.media_bin_ids == before.media_bin_ids
    assert window.project.media[0].id == media.id and window.project.media[0].path == str(moved)
    assert window.project.media[0].speech_provenance == provenance
    assert window.project.media[0].duration == 56000 / 24000
    assert moved.read_bytes() == original_bytes and not window.editor.can_undo
    assert window.save_project()
    reopened = Project.load(window.project_path)
    assert reopened == window.project
    with RecoveryStore(tmp_path / 'relinked-recovery') as recovery:
        assert recovery.load(recovery.write(reopened, str(uuid4()), window.project_path)) == reopened
    assert moved.read_bytes() == original_bytes
