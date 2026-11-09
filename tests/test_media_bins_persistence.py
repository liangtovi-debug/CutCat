"""Bin identity survives recovery, generated provenance and source relinking."""
from copy import deepcopy
from uuid import uuid4
from PySide6.QtWidgets import QFileDialog
from lumen.model import Editor, Media
from lumen.project_recovery import RecoveryStore
from test_project_recovery import full_project
from test_ui import app, window


def test_recovery_preserves_bins_generated_provenance_and_source_references(tmp_path):
    project = full_project(tmp_path)
    before = deepcopy(project)
    editor = Editor(project)
    parent = editor.create_media_bin('拍摄')
    audio = editor.create_media_bin('音频', parent.id)
    editor.move_media_to_bin(['speech', 'sound'], audio.id)
    editor.move_media_to_bin(['v'], parent.id)
    assert editor.project.media == before.media
    assert editor.project.clips == before.clips
    assert editor.project.overlays == before.overlays
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(editor.project, str(uuid4()), tmp_path / 'source.lumen')
        restored = store.load(entry)
        assert restored == editor.project
        assert restored.media[1].speech_provenance == before.media[1].speech_provenance
        assert restored.media[2].sound_provenance == before.media[2].sound_provenance
    finally:
        store.close()


def test_relink_preserves_stable_media_id_and_nested_bin_membership(window, tmp_path, monkeypatch):
    old = Media('stable', str(tmp_path / 'missing.mov'), 'Original', 3, 640, 360, True, True)
    window.editor.add_media(old)
    clip = window.editor.add_clip(old.id, 0)
    parent = window.editor.create_media_bin('Parent')
    child = window.editor.create_media_bin('Child', parent.id)
    window.editor.move_media_to_bin([old.id], child.id)
    window.refresh()
    replacement_path = tmp_path / 'replacement.mov'
    replacement_path.write_bytes(b'original synthetic source not altered')
    replacement = Media('new-probe-id', str(replacement_path), 'Replacement', 3, 640, 360, True, True)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(replacement_path), ''))
    monkeypatch.setattr('lumen.ui.probe', lambda *args, **kwargs: replacement)
    window.relink_dialog()
    assert not window.test_warnings
    assert window.project.media[0].id == old.id
    assert window.project.media[0].path == str(replacement_path)
    assert window.project.media_bin_ids == {old.id: child.id}
    assert window.project.media_bins[1].parent_id == parent.id
    assert window.project.clips[0].id == clip.id and window.project.clips[0].media_id == old.id
    assert replacement_path.read_bytes() == b'original synthetic source not altered'


def test_transition_picture_overlay_and_muted_audio_references_are_not_removable(tmp_path):
    import pytest
    from lumen.model import Clip, Project, VideoTransition
    from lumen.clip_composite import ClipComposite
    from lumen.clip_transform import ClipTransform
    media = [Media('picture', str(tmp_path/'picture.mov'), 'Picture', 20, 64, 48, True, True),
             Media('music', str(tmp_path/'music.wav'), 'Music', 20, has_video=False, has_audio=True)]
    project = Project(media=media, clips=[
        Clip('left', 'picture', 0, 0, 30, 60),
        Clip('right', 'picture', 0, 60, 120, 60),
        Clip('pip', 'picture', 1, 0, 0, 30, transform=ClipTransform(scale=.5),
             composite=ClipComposite(opacity=.6, transparent_outside_transform=True)),
        Clip('muted-music', 'music', 2, 0, 0, 30, muted=True)],
        video_transitions=[VideoTransition('dissolve', 'left', 'right', 10)])
    editor = Editor(project); original = deepcopy(project)
    folder = editor.create_media_bin('References')
    editor.move_media_to_bin(['picture', 'music'], folder.id)
    assert editor.project.clips == original.clips
    assert editor.project.video_transitions == original.video_transitions
    before = editor.project.to_dict()
    with pytest.raises(ValueError, match='used by timeline'):
        editor.remove_unused_media(['picture', 'music'])
    assert editor.project.to_dict() == before
