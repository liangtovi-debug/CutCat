"""Strict explicit two-record recombination and fresh source/session intent."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from pathlib import Path
from threading import Event, Thread

import pytest

from lumen.model import (AudioMixAnchor, Caption, Clip, ClipEffect, ClipGroup,
                         Editor, Media, Project, MAX_CAPTION_FRAME, VERSION)
from lumen.timing import ClipTimeMap
from lumen.timeline_markers import TimelineMarker
from lumen.source_audio_reattach import (plan_source_audio_reattach,
    verify_source_audio_reattach, apply_source_audio_reattach,
    SourceAudioReattachCancelled)
from test_track_edit_locks_model import snapshot


def sample(tmp_path, *, anchored=True):
    path = tmp_path / 'source.mov'; path.write_bytes(b'original source bytes' * 100)
    media = Media('m', str(path), 'Shared AV', 100, 64, 48, True, True, 24, 1)
    anchor = AudioMixAnchor(0, 10, 'v') if anchored else None
    return Editor(Project(media=[media], clips=[
        Clip('v', 'm', 0, 10, 20, 12, gain_db=-3, source_audio_enabled=False),
        Clip('peer', 'm', 1, 4, 5, 9),
        Clip('a', 'm', 2, 10, 20, 12, gain_db=-7, fade_in=2, fade_out=3,
             audio_mix_anchor=anchor),
        Clip('other', 'm', 3, 40, 5, 8)],
        captions=[Caption('caption', 2, 6, 'unchanged')],
        timeline_markers=[TimelineMarker(id='marker', start_frame=9, name='fixed', comment='', color='yellow')]))


def reattach(editor, video='v', audio='a'):
    plan = plan_source_audio_reattach(editor, video, audio)
    preview = verify_source_audio_reattach(plan)
    verified = verify_source_audio_reattach(plan, preview)
    return apply_source_audio_reattach(editor, plan, verified)


@pytest.mark.parametrize('anchored', [False, True])
@pytest.mark.parametrize('muted', [False, True])
def test_exact_two_record_edit_transfers_audio_retains_all_else_history_and_file(tmp_path, anchored, muted):
    e = sample(tmp_path, anchored=anchored); e._clip('a').muted = muted
    v = e._clip('v'); v.grade.lift = [.1, .2, .3]
    v.effects = [ClipEffect('effect', 'blur', 1)]; v.transform.scale = 1.2
    before = snapshot(e); old_media = tuple(e.project.media); old_clips = tuple(e.project.clips)
    source = Path(e.project.media[0].path).read_bytes()
    retained = reattach(e)
    expected = deepcopy(before[0]); expected.clips = [c for c in expected.clips if c.id != 'a']
    out = expected.clips[0]; out.source_audio_enabled = True
    out.gain_db = -7; out.muted = muted; out.fade_in = 2; out.fade_out = 3
    out.audio_mix_anchor = AudioMixAnchor(0, 10, 'v') if anchored else AudioMixAnchor(2, 10, 'a')
    assert e.project == expected and retained is v and retained is e._clip('v')
    assert all(old is new for old, new in zip(old_media, e.project.media))
    assert old_clips[1] is e._clip('peer') and old_clips[3] is e._clip('other')
    assert e.revision == before[3] + 1 and len(e._undo) == len(before[1]) + 1
    assert VERSION == 43 and Project.from_dict(e.project.to_dict()) == expected
    assert e.undo() and e.project == before[0]
    assert e.redo() and e.project == expected
    saved = tmp_path / 'project.lumen'; e.project.save(saved)
    assert Project.load(saved) == expected
    assert Path(e.project.media[0].path).read_bytes() == source
    assert sorted(p.name for p in tmp_path.iterdir()) == ['project.lumen', 'source.mov']


@pytest.mark.parametrize('representation', ['legacy', 'rational', 'partial'])
def test_exact_semantic_timing_equality_not_internal_representation(tmp_path, representation):
    e = sample(tmp_path)
    if representation == 'legacy':
        # Manually retain a valid but non-normalized equivalent map on A.
        e._clip('a').time_map = ClipTimeMap(20, 32)
    else:
        mapping = ClipTimeMap(Fraction(5, 4), Fraction(5, 4) +
            (Fraction(35, 2) if representation == 'partial' else 18), Fraction(3, 2))
        for name in ('v', 'a'):
            clip = e._clip(name); clip.time_map = mapping; clip.source_in = mapping.source_in
    before_v = deepcopy(e._clip('v'))
    reattach(e)
    assert (e._clip('v').time_map, e._clip('v').source_in, e._clip('v').duration) == (
        before_v.time_map, before_v.source_in, before_v.duration)


@pytest.mark.parametrize('failure', ['video_enabled', 'audio_disabled', 'missing_v', 'missing_a',
    'same_id', 'reversed', 'other_media', 'start', 'duration', 'source_in',
    'source_out_same_ceil', 'speed', 'video_only', 'lock_v', 'lock_a', 'group_v', 'group_a'])
def test_invalid_explicit_pair_rejects_without_history_or_mutation(tmp_path, failure):
    e = sample(tmp_path); video, audio = 'v', 'a'
    if failure == 'video_enabled': e._clip('v').source_audio_enabled = True
    elif failure == 'audio_disabled': e._clip('a').source_audio_enabled = False
    elif failure == 'missing_v': video = 'missing'
    elif failure == 'missing_a': audio = 'missing'
    elif failure == 'same_id': audio = video
    elif failure == 'reversed': video, audio = audio, video
    elif failure == 'other_media':
        media = deepcopy(e.project.media[0]); media.id = 'other'; e.project.media.append(media)
        e._clip('a').media_id = 'other'
    elif failure == 'start': e._clip('a').start += 1
    elif failure == 'duration': e._clip('a').duration -= 1
    elif failure == 'source_in': e._clip('a').source_in += 1
    elif failure == 'source_out_same_ceil':
        e._clip('a').time_map = ClipTimeMap(20, Fraction(63, 2))
    elif failure == 'speed':
        e._clip('a').time_map = ClipTimeMap(20, 38, Fraction(3, 2))
    elif failure == 'video_only':
        # A-track validation itself rejects no-audio Media atomically.
        e.project.media[0].has_audio = False
    elif failure.startswith('lock_'): e.set_track_edit_lock(0 if failure == 'lock_v' else 2, True)
    else: e.group_clips(['v' if failure == 'group_v' else 'a', 'peer'])
    before = snapshot(e)
    with pytest.raises(ValueError): plan_source_audio_reattach(e, video, audio)
    assert snapshot(e) == before
    with pytest.raises(ValueError): e._commit_source_audio_reattach(video, audio)
    assert snapshot(e) == before


@pytest.mark.parametrize('name,bad', [('video', None), ('video', ''), ('video', '\x00'),
                                     ('audio', 1), ('audio', ' '), ('audio', False)])
def test_explicit_identifiers_are_strict(tmp_path, name, bad):
    e = sample(tmp_path); before = snapshot(e)
    with pytest.raises(ValueError): plan_source_audio_reattach(e, bad if name == 'video' else 'v', bad if name == 'audio' else 'a')
    assert snapshot(e) == before


@pytest.mark.parametrize('muted', [False, True])
@pytest.mark.parametrize('suppressed', [False, True])
def test_other_equal_anchor_and_current_start_rejects_even_if_currently_silent(tmp_path, muted, suppressed):
    e = sample(tmp_path); peer = e._clip('peer')
    peer.start = 10; peer.audio_mix_anchor = AudioMixAnchor(0, 10, 'v')
    peer.muted = muted; peer.source_audio_enabled = not suppressed
    before = snapshot(e)
    with pytest.raises(ValueError, match='ambiguous'): plan_source_audio_reattach(e, 'v', 'a')
    assert snapshot(e) == before


def test_shared_historical_anchor_distinct_current_starts_preserves_order(tmp_path):
    from lumen.audio_components import audio_mix_order_key
    e = sample(tmp_path); e._clip('peer').audio_mix_anchor = AudioMixAnchor(0, 10, 'v')
    before = [c.id for c in sorted(e.project.clips, key=audio_mix_order_key) if c.source_audio_enabled]
    reattach(e)
    after = [c.id for c in sorted(e.project.clips, key=audio_mix_order_key) if c.source_audio_enabled]
    assert after == ['v' if name == 'a' else name for name in before]


def test_historical_anchor_bounds_reject_unrepresentable_implicit_slot(tmp_path):
    e = sample(tmp_path, anchored=False)
    for name in ('v', 'a'): e._clip(name).start = MAX_CAPTION_FRAME + 1
    e.project.validate(); before = snapshot(e)
    with pytest.raises(ValueError, match='cannot represent'): plan_source_audio_reattach(e, 'v', 'a')
    assert snapshot(e) == before


def test_explicit_audio_choice_among_multiple_matching_clips_only_selected_deleted(tmp_path):
    e = sample(tmp_path, anchored=False); e._clip('other').start = 10
    e._clip('other').source_in = 20; e._clip('other').duration = 12
    e._clip('other').gain_db = -19
    untouched = deepcopy(e._clip('a'))
    out = reattach(e, audio='other')
    assert out.gain_db == -19 and out.audio_mix_anchor == AudioMixAnchor(3, 10, 'other')
    assert e._clip('a') == untouched and set(c.id for c in e.project.clips) == {'v', 'peer', 'a'}


@pytest.mark.parametrize('race', ['edit', 'undo_aba', 'replace', 'unversioned', 'lock',
    'media', 'role_swap', 'candidate', 'preview_only', 'other_plan'])
def test_stale_commit_atomic_and_no_implicit_rebase(tmp_path, race):
    e = sample(tmp_path); plan = plan_source_audio_reattach(e, 'v', 'a')
    preview = verify_source_audio_reattach(plan); verified = verify_source_audio_reattach(plan, preview)
    if race == 'edit': e.update_clip('a', gain_db=-9)
    elif race == 'undo_aba': e.update_clip('a', gain_db=-9); e.undo()
    elif race == 'replace': e.project = deepcopy(e.project)
    elif race == 'unversioned': e._clip('a').gain_db = -9
    elif race == 'lock': e.set_track_edit_lock(2, True)
    elif race == 'media': e.project.media[0].path += '.relinked'
    elif race == 'role_swap': plan = replace(plan, video_clip_id='a', audio_clip_id='v')
    elif race == 'candidate': plan = replace(plan, candidate_signature='wrong')
    elif race == 'preview_only': verified = preview
    else: verified = verify_source_audio_reattach(plan_source_audio_reattach(e, 'v', 'a'))
    before = snapshot(e)
    with pytest.raises(ValueError): apply_source_audio_reattach(e, plan, verified)
    assert snapshot(e) == before


def test_plan_frozen_repeat_apply_once_and_individual_audio_edits_allowed(tmp_path):
    e = sample(tmp_path); e.update_clip('a', gain_db=-11, fade_in=4, fade_out=6, muted=True)
    plan = plan_source_audio_reattach(e, 'v', 'a')
    with pytest.raises(FrozenInstanceError): plan.audio_clip_id = 'other'
    preview = verify_source_audio_reattach(plan); verified = verify_source_audio_reattach(plan, preview)
    result = apply_source_audio_reattach(e, plan, verified)
    assert (result.gain_db, result.fade_in, result.fade_out, result.muted) == (-11, 4, 6, True)
    before = snapshot(e)
    with pytest.raises(ValueError): apply_source_audio_reattach(e, plan, verified)
    assert snapshot(e) == before


@pytest.mark.parametrize('problem', ['missing', 'changed', 'cancel', 'deadline', 'read_budget'])
def test_worker_failure_no_project_changes(tmp_path, monkeypatch, problem):
    import lumen.source_audio_reattach as module
    e = sample(tmp_path); plan = plan_source_audio_reattach(e, 'v', 'a')
    preview = verify_source_audio_reattach(plan); cancel = Event()
    if problem == 'missing': Path(e.project.media[0].path).unlink()
    elif problem == 'changed': Path(e.project.media[0].path).write_bytes(b'different')
    elif problem == 'cancel': cancel.set()
    elif problem == 'deadline': monkeypatch.setattr(module, 'MAX_VERIFY_SECONDS', -1)
    else: monkeypatch.setattr(module, 'MAX_IDENTITY_BYTES', 1)
    before = snapshot(e)
    with pytest.raises((ValueError, OSError)): verify_source_audio_reattach(plan, preview, cancel)
    assert snapshot(e) == before


def test_planning_and_commit_no_file_io_worker_has_no_editor(tmp_path, monkeypatch):
    e = sample(tmp_path); before = snapshot(e); original = Path.open
    def forbidden(*args, **kwargs): raise AssertionError('Owner-thread file IO')
    monkeypatch.setattr(Path, 'open', forbidden)
    plan = plan_source_audio_reattach(e, 'v', 'a')
    monkeypatch.setattr(Path, 'open', original)
    results, errors = [], []
    def worker():
        try:
            preview = verify_source_audio_reattach(plan)
            results.append(verify_source_audio_reattach(plan, preview))
        except Exception as exc: errors.append(exc)
    thread = Thread(target=worker); thread.start(); thread.join(5)
    assert not thread.is_alive() and not errors and snapshot(e) == before
    monkeypatch.setattr(Path, 'open', forbidden)
    apply_source_audio_reattach(e, plan, results[0])


def test_generic_callback_cannot_imitate_reattach_and_detach_stays_available(tmp_path):
    from lumen.source_audio_separation import (plan_source_audio_separation,
        verify_source_audio_separation, apply_source_audio_separation)
    e = sample(tmp_path); before = snapshot(e)
    def fake():
        e._clip('v').source_audio_enabled = True
        e.project.clips = [c for c in e.project.clips if c.id != 'a']
    with pytest.raises(ValueError): e._commit(fake)
    assert snapshot(e) == before
    reattach(e)
    # Reattachment retains a historical slot, so unsupported split/cut policy
    # remains conservative; subsequent explicit separation still works.
    with pytest.raises(ValueError): e.split_clip('v', 15)
    plan = plan_source_audio_separation(e, 'v', 2)
    preview = verify_source_audio_separation(plan)
    result = apply_source_audio_separation(e, plan, verify_source_audio_separation(plan, preview))
    assert result.audio_mix_anchor == AudioMixAnchor(0, 10, 'v')
    assert not e._clip('v').source_audio_enabled


def test_transition_endpoint_rejects_without_implicit_transition_cleanup(tmp_path):
    from lumen.model import VideoTransition
    e = sample(tmp_path)
    e.project.clips.append(Clip('next', 'm', 0, 22, 40, 12))
    e.project.video_transitions = [VideoTransition('transition', 'v', 'next', 2)]
    e.project.validate(); before = snapshot(e)
    with pytest.raises(ValueError, match='Remove transitions'):
        plan_source_audio_reattach(e, 'v', 'a')
    assert snapshot(e) == before
    with pytest.raises(ValueError, match='Remove transitions'):
        e._commit_source_audio_reattach('v', 'a')
    assert snapshot(e) == before


def test_generated_audio_provenance_cannot_be_forged_into_av_source(tmp_path):
    from lumen.model import SoundProvenance
    e = sample(tmp_path)
    e.project.media[0].sound_provenance = SoundProvenance('sound', 'procedural', 'CC0-1.0',
        'a' * 64, 'a' * 64, 48000, 48000)
    before = snapshot(e)
    with pytest.raises(ValueError, match='audio-only'): plan_source_audio_reattach(e, 'v', 'a')
    assert snapshot(e) == before


def test_worker_receipts_are_bound_to_same_plan_and_preview_phase(tmp_path):
    e = sample(tmp_path); plan = plan_source_audio_reattach(e, 'v', 'a')
    preview = verify_source_audio_reattach(plan)
    applied = verify_source_audio_reattach(plan, preview)
    other = plan_source_audio_reattach(e, 'v', 'a')
    for invalid in (applied, replace(preview, plan=other), object()):
        with pytest.raises(ValueError): verify_source_audio_reattach(plan, invalid)


def test_owner_thread_enforced_on_plan_and_apply(tmp_path):
    e = sample(tmp_path); before = snapshot(e); results = []
    def work():
        try: plan_source_audio_reattach(e, 'v', 'a')
        except ValueError as exc: results.append(str(exc))
    thread = Thread(target=work); thread.start(); thread.join(5)
    assert results and 'owning thread' in results[0] and snapshot(e) == before
