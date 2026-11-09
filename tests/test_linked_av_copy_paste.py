"""Whole-pair clipboard identities, exact parameters and atomic rejection."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from lumen.audio_gain import AudioGainKeyframe
from lumen.clip_copy_paste import (
    accept_clip_copy, apply_clip_paste, plan_clip_paste,
    prepare_clip_copy, verify_clip_copy, verify_clip_paste,
)
from lumen.model import AudioMixAnchor, AVLink, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap


def linked_editor(tmp_path, *, fps=Fraction(24), muted=False, start=10, duration=10):
    path = tmp_path / 'same-source.mov'
    path.write_bytes(b'same-source-verified-content' * 20)
    media = Media('m', str(path), 'Same source', 1000, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    video = Clip('v', 'm', 0, start, 30, duration, source_audio_enabled=False,
                 gain_db=4, fade_in=2, fade_out=3,
                 audio_gain_keyframes=[AudioGainKeyframe(-10, -2), AudioGainKeyframe(100000, 3)])
    audio = Clip('a', 'm', 2, start, 30, duration, muted=muted, gain_db=-3,
                 fade_in=2, fade_out=3, audio_mix_anchor=AudioMixAnchor(0, start, 'v'),
                 audio_gain_keyframes=[AudioGainKeyframe(-30, -2), AudioGainKeyframe(100000, 3)])
    project = Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media],
                      clips=[video, audio, Clip('unrelated', 'm', 1, 2, 0, 5)],
                      av_links=[AVLink('link', 'v', 'a', 'm')])
    return Editor(project)


def copy_pair(editor, selected=('v',)):
    request = prepare_clip_copy(editor, selected)
    return accept_clip_copy(editor, request, verify_clip_copy(request))


def paste_pair(editor, clipboard, destination=100):
    plan = plan_clip_paste(editor, clipboard, destination)
    return apply_clip_paste(editor, clipboard, plan, verify_clip_paste(plan, clipboard))


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision, editor.session_id))


def rejects(editor, call, match=None):
    before = state(editor)
    with pytest.raises(ValueError, match=match):
        call()
    assert state(editor) == before


@pytest.mark.parametrize('selected', [('v',), ('a',), ('a', 'v')])
def test_pair_selection_expansion_and_snapshot_excludes_unrelated_links(tmp_path, selected):
    editor = linked_editor(tmp_path)
    editor.project.clips.extend([
        Clip('v2', 'm', 1, 30, 50, 5, source_audio_enabled=False),
        Clip('a2', 'm', 3, 30, 50, 5)])
    editor.project.av_links.append(AVLink('link2', 'v2', 'a2', 'm'))
    before = state(editor)
    clipboard = copy_pair(editor, selected)
    snapshot = Project.from_dict(json.loads(clipboard.request.payload))
    assert [c.id for c in snapshot.clips] == ['v', 'a']
    assert snapshot.av_links == editor.project.av_links[:1]
    assert clipboard.clip_count == 2 and clipboard.anchor == 10
    assert state(editor) == before


def test_pair_paste_new_identities_exact_records_one_undo_and_save(tmp_path):
    editor = linked_editor(tmp_path, muted=True)
    # Complex exact timing has a retained partial final output frame.
    for clip in editor.project.clips[:2]:
        clip.time_map = ClipTimeMap(Fraction(5, 4), Fraction(79, 4), Fraction(2))
        clip.source_in = Fraction(5, 4)
    editor.project.validate()
    before = deepcopy(editor.project)
    original_objects = list(editor.project.clips)
    clipboard = copy_pair(editor, ('a',))
    revision, undos = editor.revision, len(editor._undo)
    clones = paste_pair(editor, clipboard)
    assert len(clones) == 2
    assert all(a is b for a, b in zip(original_objects, editor.project.clips))
    assert editor.revision == revision + 1 and len(editor._undo) == undos + 1
    assert editor.project.av_links[0] == before.av_links[0]
    pasted = editor.project.av_links[1]
    assert pasted.id != before.av_links[0].id
    assert pasted.clip_ids == (clones[0].id, clones[1].id)
    assert pasted.media_id == 'm' and pasted.alignment == 'same-source-zero-offset-v1'
    for old, clone in zip(before.clips[:2], clones):
        expected = deepcopy(old)
        expected.id = clone.id
        expected.start = 100
        expected.audio_mix_anchor = None
        assert clone == expected
    assert not clones[0].source_audio_enabled and clones[1].source_audio_enabled
    assert clones[1].muted and clones[1].audio_gain_keyframes == before.clips[1].audio_gain_keyframes
    accepted = deepcopy(editor.project)
    assert Project.from_dict(accepted.to_dict()) == accepted
    path = tmp_path / 'linked-paste.lumen'
    accepted.save(path)
    assert Project.load(path) == accepted
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == accepted


def test_repeat_paste_each_pair_has_new_link_and_clip_ids(tmp_path):
    editor = linked_editor(tmp_path)
    clipboard = copy_pair(editor)
    first = paste_pair(editor, clipboard, 100)
    second = paste_pair(editor, clipboard, 120)
    all_clip_ids = [c.id for c in editor.project.clips]
    all_link_ids = [link.id for link in editor.project.av_links]
    assert len(all_clip_ids) == len(set(all_clip_ids)) == 7
    assert len(all_link_ids) == len(set(all_link_ids)) == 3
    assert editor.project.av_links[1].clip_ids == tuple(c.id for c in first)
    assert editor.project.av_links[2].clip_ids == tuple(c.id for c in second)
    assert all(c.track == track for pair in (first, second) for c, track in zip(pair, (0, 2)))
    assert editor.project.av_links[0].clip_ids == ('v', 'a')


def test_copy_locked_pair_is_read_only_but_either_destination_lock_rejects(tmp_path):
    editor = linked_editor(tmp_path)
    editor.set_track_edit_lock(0, True)
    editor.set_track_edit_lock(2, True)
    before = state(editor)
    clipboard = copy_pair(editor, ('a',))
    assert state(editor) == before
    rejects(editor, lambda: plan_clip_paste(editor, clipboard, 100), 'edit-locked')
    editor.set_track_edit_lock(0, False)
    rejects(editor, lambda: plan_clip_paste(editor, clipboard, 100), 'edit-locked')
    editor.set_track_edit_lock(2, False)
    assert len(paste_pair(editor, clipboard)) == 2


def test_failed_pair_collision_preserves_redo_and_repeated_apply_is_stale(tmp_path):
    editor = linked_editor(tmp_path)
    clipboard = copy_pair(editor)
    paste_pair(editor, clipboard, 100)
    paste_pair(editor, clipboard, 120)
    assert editor.undo() and editor.can_redo
    rejects(editor, lambda: plan_clip_paste(editor, clipboard, 100), 'overlap')
    assert editor.can_redo
    plan = plan_clip_paste(editor, clipboard, 140)
    proof = verify_clip_paste(plan, clipboard)
    apply_clip_paste(editor, clipboard, plan, proof)
    assert not editor.can_redo
    rejects(editor, lambda: apply_clip_paste(editor, clipboard, plan, proof), 'stale')


@pytest.mark.parametrize('new_ids', [(), ('a',), ('link',), ('altered',), ('new', 'extra'), ['new']])
def test_link_identity_plan_tamper_rejects_atomically(tmp_path, new_ids):
    editor = linked_editor(tmp_path)
    clipboard = copy_pair(editor)
    plan = plan_clip_paste(editor, clipboard, 100)
    forged = replace(plan, new_link_ids=new_ids)
    # Verification checks dependencies, not an adversarial cryptographic seal;
    # final owner-thread candidate validation must independently reject edits.
    proof = verify_clip_paste(forged, clipboard)
    rejects(editor, lambda: apply_clip_paste(editor, clipboard, forged, proof))


def test_new_link_identity_cannot_reuse_new_clip_identity(tmp_path):
    editor = linked_editor(tmp_path)
    clipboard = copy_pair(editor)
    plan = plan_clip_paste(editor, clipboard, 100)
    forged = replace(plan, new_link_ids=(plan.new_clip_ids[0],))
    proof = verify_clip_paste(forged, clipboard)
    rejects(editor, lambda: apply_clip_paste(editor, clipboard, forged, proof), 'new and distinct')


@pytest.mark.parametrize('kind', ['missing-member', 'swap-roles', 'unknown-member', 'duplicate-link'])
def test_malformed_clipboard_links_reject_before_source_io(tmp_path, monkeypatch, kind):
    editor = linked_editor(tmp_path)
    request = prepare_clip_copy(editor, ('v',))
    payload = json.loads(request.payload)
    if kind == 'missing-member':
        payload['clips'] = payload['clips'][:1]
    elif kind == 'swap-roles':
        payload['av_links'][0]['video_clip_id'] = 'a'
        payload['av_links'][0]['audio_clip_id'] = 'v'
    elif kind == 'unknown-member':
        payload['av_links'][0]['audio_clip_id'] = 'unknown'
    else:
        payload['av_links'].append(deepcopy(payload['av_links'][0]))
    malformed = replace(request, payload=json.dumps(payload), clip_count=len(payload['clips']))
    before = state(editor)
    def forbidden(*args, **kwargs):
        raise AssertionError('Malformed clipboard must be rejected before dependency I/O')
    monkeypatch.setattr(Path, 'lstat', forbidden)
    with pytest.raises(ValueError):
        verify_clip_copy(malformed)
    assert state(editor) == before


def test_narrow_paste_cannot_link_to_old_members_or_hijack_old_link(tmp_path):
    editor = linked_editor(tmp_path)
    video = Clip('new-v', 'm', 0, 100, 30, 10, source_audio_enabled=False)
    audio = Clip('new-a', 'm', 2, 100, 30, 10)
    rejects(editor, lambda: editor._commit_clip_paste([video, audio], [],
        av_links=[AVLink('new-link', 'v', 'new-a', 'm')]))
    rejects(editor, lambda: editor._commit_clip_paste([video, audio], [],
        av_links=[AVLink('link', 'new-v', 'new-a', 'm')]))
    # An incomplete link cannot authorize a hidden source clip append/deletion.
    rejects(editor, lambda: editor._commit_clip_paste([video], [],
        av_links=[AVLink('new-link', 'new-v', 'new-a', 'm')]))


def test_copied_pair_survives_explicit_original_pair_delete(tmp_path):
    editor = linked_editor(tmp_path)
    clipboard = copy_pair(editor)
    editor.delete_clips(['v', 'a'])
    assert editor.project.av_links == []
    clones = paste_pair(editor, clipboard)
    assert len(clones) == 2 and len(editor.project.av_links) == 1
    assert editor.project.av_links[0].clip_ids == tuple(c.id for c in clones)
    assert not clones[0].source_audio_enabled


def test_pair_capture_plan_apply_use_no_gui_io_and_never_write_source(tmp_path, monkeypatch):
    editor = linked_editor(tmp_path)
    path = Path(editor.project.media[0].path)
    original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    clipboard = copy_pair(editor)
    plan = plan_clip_paste(editor, clipboard, 100)
    proof = verify_clip_paste(plan, clipboard)
    with monkeypatch.context() as patch:
        def forbidden(*args, **kwargs):
            raise AssertionError('Linked clipboard GUI path must perform no filesystem I/O')
        for name in ('open', 'stat', 'lstat', 'resolve', 'read_bytes', 'read_text', 'write_bytes', 'write_text'):
            patch.setattr(Path, name, forbidden)
        assert prepare_clip_copy(editor, ('a',)).clip_count == 2
        assert plan_clip_paste(editor, clipboard, 120).new_link_ids
        assert len(apply_clip_paste(editor, clipboard, plan, proof)) == 2
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_hash


def test_linked_muted_audio_is_proved_as_potential_contribution(tmp_path, monkeypatch):
    import lumen.clip_audio_translation as guards
    editor = linked_editor(tmp_path, muted=True)
    clipboard = copy_pair(editor)
    local, common = [], []
    old_local, old_common = guards.guard_local_audio_translation, guards.guard_common_audio_translation
    def local_proof(a, b, project, **kwargs):
        local.append((a.id, a.muted, b.muted, a.source_audio_enabled, b.source_audio_enabled))
        return old_local(a, b, project, **kwargs)
    def common_proof(a, b, project, **kwargs):
        common.append((a.id, a.muted, b.muted))
        return old_common(a, b, project, **kwargs)
    monkeypatch.setattr(guards, 'guard_local_audio_translation', local_proof)
    monkeypatch.setattr(guards, 'guard_common_audio_translation', common_proof)
    clones = paste_pair(editor, clipboard)
    assert ('a', False, False, True, True) in local
    assert ('a', False, False) in common
    assert ('v', False, False, False, False) in local
    assert editor._clip('a').muted and clones[1].muted


def test_muted_linked_fractional_fade_cannot_bypass_sample_phase_guard(tmp_path):
    # At 30000/1001 fps, one frame is 1601.6 output samples. Moving the
    # one-frame fade from start 0 to start 1 changes its boundary by one sample.
    editor = linked_editor(tmp_path, fps=Fraction(30000, 1001), muted=True, start=0)
    editor._clip('a').fade_in = 1
    editor._clip('a').fade_out = 0
    editor.project.clips = editor.project.clips[:2]
    editor.project.validate()
    clipboard = copy_pair(editor)
    # Destination 11 is collision-free but has the phase of frame 1.
    rejects(editor, lambda: plan_clip_paste(editor, clipboard, 11), 'sample-grid phase')
    clones = paste_pair(editor, clipboard, 15)
    assert clones[1].muted
