"""Independent source audio: exact narrow edits, persistence and fresh intent."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import hashlib
from pathlib import Path
from threading import Event, Thread

import pytest

from lumen.model import Clip, ClipEffect, Editor, Grade, Media, Project
from lumen.timing import ClipTimeMap
from lumen.source_audio_separation import (plan_source_audio_separation,
    verify_source_audio_separation, apply_source_audio_separation,
    SourceAudioSeparationCancelled)
from test_track_edit_locks_model import snapshot


def sample(tmp_path):
    path = tmp_path / 'av.mov'; path.write_bytes(b'local source bytes' * 100)
    media = Media('m', str(path), 'Picture and sound', 100, 64, 48, True, True, 24, 1)
    return Editor(Project(media=[media], clips=[
        Clip('v', 'm', 0, 10, 20, 12, gain_db=-4, fade_in=2, fade_out=3),
        Clip('v2', 'm', 1, 10, 30, 12)]))


def separate(editor, clip_id='v', track=2):
    plan = plan_source_audio_separation(editor, clip_id, track)
    preview = verify_source_audio_separation(plan)
    verification = verify_source_audio_separation(plan, previous=preview)
    return apply_source_audio_separation(editor, plan, verification)


def test_independent_exact_atomic_no_new_media_no_file_write_undo_redo_save(tmp_path):
    editor = sample(tmp_path); before = snapshot(editor)
    source = editor._clip('v'); source_bytes = Path(editor.project.media[0].path).read_bytes()
    audio = separate(editor)
    assert editor.revision == before[3] + 1 and len(editor._undo) == len(before[1]) + 1
    expected_source = deepcopy(before[0].clips[0]); expected_source.source_audio_enabled = False
    assert source == expected_source and source is editor._clip('v')
    assert audio.id not in {'v', 'v2'} and audio.media_id == source.media_id
    assert (audio.start, audio.duration, audio.timing, audio.gain_db, audio.muted, audio.fade_in, audio.fade_out) == (
        source.start, source.duration, source.timing, source.gain_db, source.muted, source.fade_in, source.fade_out)
    assert audio.track == 2 and audio.source_audio_enabled
    assert (audio.audio_mix_anchor.track, audio.audio_mix_anchor.start, audio.audio_mix_anchor.clip_id) == (0, 10, 'v')
    assert len(editor.project.media) == 1 and not editor.project.clip_groups
    assert Path(editor.project.media[0].path).read_bytes() == source_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == ['av.mov']
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before[0]
    assert editor.redo() and editor.project == after
    path = tmp_path / 'project.lumen'; editor.project.save(path)
    assert Project.load(path) == after


def test_full_time_map_retained_and_visual_settings_not_copied(tmp_path):
    editor = sample(tmp_path); source = editor._clip('v')
    source.time_map = ClipTimeMap(Fraction(5, 4), Fraction(79, 4), Fraction(3, 2))
    source.source_in = Fraction(5, 4); source.duration = 13
    source.grade.lift = [.1, .2, .3]
    source.effects = [ClipEffect('effect', 'blur', 1)]
    source.transform.scale = 1.2
    editor.project.validate()
    original = deepcopy(source)
    audio = separate(editor)
    expected = Clip(audio.id, source.media_id, 2, source.start, source.source_in, source.duration,
        gain_db=source.gain_db, muted=source.muted, fade_in=source.fade_in,
        fade_out=source.fade_out, time_map=source.time_map,
        source_audio_enabled=True, audio_mix_anchor=audio.audio_mix_anchor)
    assert audio == expected and audio.time_map == original.time_map
    original.source_audio_enabled = False
    assert source == original


@pytest.mark.parametrize('muted', [False, True])
def test_mute_is_independent_and_unmute_suppressed_v_never_reenables(tmp_path, muted):
    from lumen.audio_components import clip_emits_source_audio
    editor = sample(tmp_path); editor._clip('v').muted = muted
    audio = separate(editor); media = editor.project.media[0]
    assert audio.muted is muted
    editor.update_clip('v', muted=True); editor.update_clip('v', muted=False)
    assert not clip_emits_source_audio(editor._clip('v'), media)
    assert clip_emits_source_audio(audio, media) is (not muted)
    editor.update_clip(audio.id, muted=False)
    assert clip_emits_source_audio(audio, media)


@pytest.mark.parametrize('deleted', ['audio', 'video'])
def test_deletion_never_cascades_or_restores_other_component(tmp_path, deleted):
    editor = sample(tmp_path); audio = separate(editor)
    other_id = 'v' if deleted == 'audio' else audio.id
    other = deepcopy(editor._clip(other_id))
    editor.delete_clip(audio.id if deleted == 'audio' else 'v')
    assert editor._clip(other_id) == other
    assert editor.undo() and len(editor.project.clips) == 3


@pytest.mark.parametrize('edit', ['move', 'trim', 'speed', 'mute', 'gain'])
def test_audio_edits_independent_and_anchor_is_historical(tmp_path, edit):
    editor = sample(tmp_path); audio = separate(editor)
    before_v = deepcopy(editor._clip('v')); anchor = audio.audio_mix_anchor
    if edit == 'move': editor.update_clip(audio.id, start=50)
    elif edit == 'trim': editor.update_clip(audio.id, duration=10)
    elif edit == 'speed': editor.set_clip_speed(audio.id, Fraction(2))
    elif edit == 'mute': editor.update_clip(audio.id, muted=True)
    else: editor.update_clip(audio.id, gain_db=-12)
    assert editor._clip('v') == before_v and editor._clip(audio.id).audio_mix_anchor == anchor
    assert not editor.project.clip_groups


@pytest.mark.parametrize('track', [2, 3])
def test_explicit_destination_is_not_autoplaced(tmp_path, track):
    editor = sample(tmp_path); audio = separate(editor, track=track)
    assert audio.track == track and audio.start == 10


@pytest.mark.parametrize('failure', ['collision', 'source_lock', 'target_lock', 'group', 'audio_only', 'video_only', 'already', 'missing', 'bad_track'])
def test_rejected_plan_preserves_all_state(tmp_path, failure):
    editor = sample(tmp_path); track = 2; clip_id = 'v'
    if failure == 'collision': editor.project.clips.append(Clip('occupied', 'm', 2, 15, 0, 10))
    elif failure == 'source_lock': editor.set_track_edit_lock(0, True)
    elif failure == 'target_lock': editor.set_track_edit_lock(2, True)
    elif failure == 'group': editor.group_clips(['v', 'v2'])
    elif failure == 'audio_only': clip_id = 'a'; editor.project.clips.append(Clip('a', 'm', 2, 30, 0, 10))
    elif failure == 'video_only': editor.project.media[0].has_audio = False
    elif failure == 'already': separate(editor)
    elif failure == 'missing': clip_id = 'missing'
    else: track = 1
    before = snapshot(editor)
    with pytest.raises(ValueError): plan_source_audio_separation(editor, clip_id, track)
    assert snapshot(editor) == before


@pytest.mark.parametrize('race', ['edit', 'undo', 'replace', 'unversioned', 'lock', 'plan', 'preview_only', 'other_plan'])
def test_stale_commit_atomic(tmp_path, race):
    editor = sample(tmp_path); plan = plan_source_audio_separation(editor, 'v', 2)
    preview = verify_source_audio_separation(plan)
    verified = verify_source_audio_separation(plan, preview)
    if race == 'edit': editor.update_clip('v', gain_db=-3)
    elif race == 'undo': editor.update_clip('v', gain_db=-3); editor.undo()
    elif race == 'replace': editor.project = deepcopy(editor.project)
    elif race == 'unversioned': editor._clip('v').gain_db = -3
    elif race == 'lock': editor.set_track_edit_lock(2, True)
    elif race == 'plan': plan = replace(plan, track=3)
    elif race == 'preview_only': verified = preview
    else: verified = verify_source_audio_separation(plan_source_audio_separation(editor, 'v', 3))
    before = snapshot(editor)
    with pytest.raises(ValueError): apply_source_audio_separation(editor, plan, verified)
    assert snapshot(editor) == before


def test_repeated_apply_once_and_plan_frozen(tmp_path):
    editor = sample(tmp_path); plan = plan_source_audio_separation(editor, 'v', 2)
    with pytest.raises(FrozenInstanceError): plan.track = 3
    preview = verify_source_audio_separation(plan); verified = verify_source_audio_separation(plan, preview)
    apply_source_audio_separation(editor, plan, verified); before = snapshot(editor)
    with pytest.raises(ValueError): apply_source_audio_separation(editor, plan, verified)
    assert snapshot(editor) == before


@pytest.mark.parametrize('problem', ['missing', 'changed', 'cancel', 'deadline'])
def test_worker_failure_never_mutates_project(tmp_path, monkeypatch, problem):
    import lumen.source_audio_separation as module
    editor = sample(tmp_path); plan = plan_source_audio_separation(editor, 'v', 2)
    preview = verify_source_audio_separation(plan); cancel = Event()
    if problem == 'missing': Path(editor.project.media[0].path).unlink()
    elif problem == 'changed': Path(editor.project.media[0].path).write_bytes(b'different')
    elif problem == 'cancel': cancel.set()
    else: monkeypatch.setattr(module, 'MAX_VERIFY_SECONDS', -1)
    before = snapshot(editor)
    with pytest.raises((ValueError, OSError)): verify_source_audio_separation(plan, preview, cancel)
    assert snapshot(editor) == before


def test_no_gui_file_io_and_worker_owns_no_editor(tmp_path, monkeypatch):
    editor = sample(tmp_path); before = snapshot(editor)
    original = Path.open
    monkeypatch.setattr(Path, 'open', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('GUI file I/O')))
    plan = plan_source_audio_separation(editor, 'v', 2)
    monkeypatch.setattr(Path, 'open', original)
    result = []; error = []
    def work():
        try:
            preview = verify_source_audio_separation(plan)
            result.append(verify_source_audio_separation(plan, preview))
        except Exception as exc: error.append(exc)
    t = Thread(target=work); t.start(); t.join(3)
    assert not t.is_alive() and not error and snapshot(editor) == before
    monkeypatch.setattr(Path, 'open', lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('GUI file I/O')))
    apply_source_audio_separation(editor, plan, result[0])


def test_long_source_not_limited_by_source_cut_decode_window(tmp_path):
    editor = sample(tmp_path); editor.project.media[0].duration = 3600
    editor._clip('v').duration = 2400
    assert separate(editor).duration == 2400


@pytest.mark.parametrize('field,value', [('source_audio_enabled', True), ('audio_mix_anchor', None)])
def test_update_and_generic_commit_cannot_restore_structural_state(tmp_path, field, value):
    editor = sample(tmp_path); audio = separate(editor)
    clip = editor._clip('v' if field == 'source_audio_enabled' else audio.id)
    before = snapshot(editor)
    with pytest.raises(ValueError): editor.update_clip(clip.id, **{field: value})
    assert snapshot(editor) == before
    with pytest.raises(ValueError): editor._commit(lambda: setattr(editor._clip(clip.id), field, value))
    assert snapshot(editor) == before


def test_generic_addition_cannot_mint_handoff(tmp_path):
    editor = sample(tmp_path); audio = separate(editor)
    forged = deepcopy(audio); forged.id = 'forged'; forged.start = 60
    before = snapshot(editor)
    with pytest.raises(ValueError): editor._commit(lambda: editor.project.clips.append(forged))
    assert snapshot(editor) == before


def test_copy_disabled_picture_remains_disabled_audio_copy_gets_new_slot(tmp_path):
    from lumen.clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
        plan_clip_paste, verify_clip_paste, apply_clip_paste)
    editor = sample(tmp_path); audio = separate(editor)
    request = prepare_clip_copy(editor, ('v', audio.id))
    copied = accept_clip_copy(editor, request, verify_clip_copy(request))
    plan = plan_clip_paste(editor, copied, 50)
    clones = apply_clip_paste(editor, copied, plan, verify_clip_paste(plan, copied))
    assert not clones[0].source_audio_enabled and clones[0].audio_mix_anchor is None
    assert clones[1].source_audio_enabled and clones[1].audio_mix_anchor is None
    assert not editor.project.clip_groups
