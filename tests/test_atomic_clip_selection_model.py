"""Atomic ID-based clip-group edits preserve exact timing and independent anchors."""
from copy import deepcopy
from dataclasses import fields
from fractions import Fraction
from threading import Thread
from unittest.mock import Mock

import pytest

from lumen.clip_transform import ClipTransform
from lumen.model import (Caption, Clip, ClipEffect, Editor, Grade, Media, Overlay,
                         OverlayKeyframe, Project, SoundProvenance, SpeechProvenance)
from lumen.qualifier import QualifierSettings, WindowKeyframe
from lumen.restoration_settings import RestorationSettings
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap, duration_frames


SELECTED = ('video-a', 'video-b', 'picture', 'audio-a', 'audio-b')


def make_editor(speed=Fraction(3, 2), fps=Fraction(30)):
    clips = []
    for index, (clip_id, track, start) in enumerate([
            ('video-a', 0, 40), ('video-b', 0, 100), ('picture', 1, 50),
            ('audio-a', 2, 60), ('audio-b', 3, 70)]):
        origin = Fraction(101, 2) + index
        mapping = ClipTimeMap(origin, origin + Fraction(79, 4) * speed, speed)
        grade = Grade(contrast=1.2, gain=[1.1, 1.2, 1.3], lut_path='/tmp/look.cube',
            qualifier=QualifierSettings(enabled=True, window_shape='ellipse',
                window_keyframes=[WindowKeyframe(-5, .1, .8, .3, 1.5),
                                  WindowKeyframe(7, .7, .2, .9, .5, 'hold'),
                                  WindowKeyframe(50, .9, .6, 1.2, .4)]))
        clips.append(Clip(clip_id, 'shared', track, start, 0, duration_frames(mapping),
            time_map=mapping, grade=grade, gain_db=-3, muted=True, fade_in=2, fade_out=3,
            effects=[ClipEffect('vignette', 'vignette', .4, radius=.8, feather=1.2),
                     ClipEffect('blur', 'blur', 1.2)] if track < 2 else [],
            stabilization=StabilizationSettings(enabled=track < 2, strength=.7),
            restoration=RestorationSettings(enabled=track < 2, denoise=.3),
            transform=ClipTransform(x=7, rotation=12) if track < 2 else ClipTransform()))
    # Selection is deliberately noncontiguous both in time and in project order.
    gap = Clip('gap', 'shared', 0, 75, 10, 5)
    sentinels = [Clip(f'fixed-{track}', 'shared', track, 250, 0, 10) for track in range(4)]
    clips = [clips[4], gap, clips[1], sentinels[2], clips[0], clips[2], clips[3],
             sentinels[0], sentinels[3], sentinels[1]]
    return Editor(Project(name='Atomic groups', fps_num=fps.numerator, fps_den=fps.denominator,
        media=[Media('shared', '/tmp/source.mp4', 'Shared source', 120, 64, 48, True, True),
               Media('unused', '/tmp/unused.wav', 'Unused asset', 10, has_video=False,
                     has_audio=True)], clips=clips,
        captions=[Caption('caption', 53, 10, 'Independent caption')],
        overlays=[Overlay('sticker', '/tmp/sticker.png', 'a' * 64, 52, 8,
                          keyframes=[OverlayKeyframe(0), OverlayKeyframe(7, 12, 15)])]))


def history(editor):
    return deepcopy((editor.project, editor._undo, editor._redo,
                     editor.revision, editor.session_id))


def live_objects(editor):
    return (editor.project, editor.project.clips, *editor.project.clips,
            editor.project.media, *editor.project.media, editor.project.captions,
            *editor.project.captions, editor.project.overlays, *editor.project.overlays)


def assert_same_objects(editor, original):
    assert all(a is b for a, b in zip(live_objects(editor), original, strict=True))


def editor_with_redo():
    editor = make_editor()
    editor.move_clips(SELECTED, 1)
    editor.undo()
    return editor


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1),
                                  Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(30000, 1001)])
@pytest.mark.parametrize('delta', [-3, 4])
def test_group_move_preserves_all_payload_tails_order_assets_and_one_history_step(speed, fps, delta):
    editor = make_editor(speed, fps)
    original_objects = live_objects(editor)
    before = deepcopy(editor.project)
    commit = Mock(wraps=editor._commit)
    editor._commit = commit
    preview = editor.preview_move_clips(reversed(SELECTED), delta)
    assert editor.project == before and editor.revision == 0 and not editor.can_undo
    assert_same_objects(editor, original_objects)
    assert not commit.called
    expected_order = [clip.id for clip in before.clips if clip.id in SELECTED]
    assert [clip.id for clip in preview] == expected_order
    result = editor.move_clips(set(SELECTED), delta)
    assert result == preview
    assert [clip.id for clip in result] == expected_order
    assert all(clip is editor._clip(clip.id) for clip in result)
    assert_same_objects(editor, original_objects)
    for original, current in zip(before.clips, editor.project.clips, strict=True):
        assert current.start == original.start + (delta if original.id in SELECTED else 0)
        for field in fields(Clip):
            if field.name != 'start':
                assert getattr(current, field.name) == getattr(original, field.name)
        if original.id in SELECTED:
            tail = current.duration * current.speed - (current.source_out - current.source_in)
            assert tail == speed / 4
    assert editor.project.media == before.media
    assert editor.project.captions == before.captions
    assert editor.project.overlays == before.overlays
    assert editor.project.caption_style == before.caption_style
    assert editor.project.duration_frames == before.duration_frames
    assert commit.call_count == 1 and len(editor._undo) == 1 and editor.revision == 1
    after = deepcopy(editor.project)
    assert Project.from_dict(after.to_dict()) == after
    assert editor.undo() and editor.project == before and editor.revision == 2
    assert editor.redo() and editor.project == after and editor.revision == 3


@pytest.mark.parametrize('delta', [0, 4])
def test_preview_is_recursively_detached_and_keeps_redo(delta):
    editor = editor_with_redo()
    before, objects = history(editor), live_objects(editor)
    preview = editor.preview_move_clips(SELECTED, delta)
    for clip in preview:
        assert clip is not editor._clip(clip.id)
        assert clip.grade is not editor._clip(clip.id).grade
        clip.start += 1000
        clip.grade.lift[0] = .5
        clip.grade.qualifier.window_keyframes.clear()
        clip.effects.clear()
        clip.stabilization.strength = .1
        clip.restoration.denoise = .1
        clip.transform.x = 100
    assert history(editor) == before
    assert_same_objects(editor, objects)


def test_zero_delta_keeps_redo_revision_session_identity_and_never_commits():
    editor = editor_with_redo()
    before, objects = history(editor), live_objects(editor)
    editor._commit = Mock(wraps=editor._commit)
    result = editor.move_clips(iter(SELECTED), 0)
    assert all(clip is editor._clip(clip.id) for clip in result)
    assert history(editor) == before and not editor._commit.called
    assert_same_objects(editor, objects)
    assert editor.redo()


@pytest.mark.parametrize('delta', [-10, 10])
def test_final_candidate_only_allows_selected_old_position_overlap(delta):
    media = Media('m', '/tmp/source.mp4', 'Source', 10)
    editor = Editor(Project(media=[media], clips=[Clip('b', 'm', 0, 30, 10, 10),
        Clip('after', 'm', 0, 60, 0, 10), Clip('a', 'm', 0, 20, 0, 10),
        Clip('before', 'm', 0, 0, 0, 10), Clip('c', 'm', 0, 40, 20, 10)]))
    before = deepcopy(editor.project)
    preview = editor.preview_move_clips(['c', 'a', 'b'], delta)
    assert editor.project == before
    result = editor.move_clips(['c', 'a', 'b'], delta)
    assert result == preview and len(editor._undo) == 1
    assert [clip.id for clip in result] == ['b', 'a', 'c']
    assert editor._clip('a').end == editor._clip('b').start
    assert editor._clip('b').end == editor._clip('c').start
    assert editor._clip('before') == before.clips[3]
    assert editor._clip('after') == before.clips[1]


INVALID_IDS = [[], (), set(), None, True, 1, 'video-a', b'video-a', {'video-a': True},
               ['video-a', 'video-a'], ['video-a', 'missing'], ['video-a', ''],
               ['video-a', ' '], ['video-a', 'bad\x00id'], ['video-a', None],
               ['video-a', True], ['video-a', 2], ['video-a', []]]


@pytest.mark.parametrize('clip_ids', INVALID_IDS)
@pytest.mark.parametrize('operation', ['preview', 'move', 'noop', 'delete'])
def test_invalid_id_collection_rejects_without_mutating_any_object_or_history(clip_ids, operation):
    editor = editor_with_redo()
    before, objects = history(editor), live_objects(editor)
    editor._commit = Mock(wraps=editor._commit)
    with pytest.raises(ValueError):
        if operation == 'delete':
            editor.delete_clips(clip_ids)
        else:
            getattr(editor, 'preview_move_clips' if operation == 'preview' else 'move_clips')(
                clip_ids, 0 if operation == 'noop' else 1)
    assert history(editor) == before and not editor._commit.called
    assert_same_objects(editor, objects)


@pytest.mark.parametrize('container', [list, tuple, set, frozenset, iter])
def test_single_id_and_supported_iterable_collections(container):
    editor = make_editor()
    selected = editor._clip('video-a')
    original = deepcopy(selected)
    assert editor.move_clips(container(['video-a']), 1) == [selected]
    assert selected.start == original.start + 1
    editor.delete_clips(container(['video-a']))
    assert not any(clip.id == selected.id for clip in editor.project.clips)
    assert editor.undo() and editor.undo()
    assert editor._clip('video-a') == original


@pytest.mark.parametrize('delta', [True, False, 1.0, Fraction(1), '1', None,
                                  float('nan'), float('inf'), [], {}])
def test_non_integer_deltas_reject_before_mutation(delta):
    editor = editor_with_redo()
    before, objects = history(editor), live_objects(editor)
    editor._commit = Mock(wraps=editor._commit)
    for method in (editor.preview_move_clips, editor.move_clips):
        with pytest.raises(ValueError, match='delta'):
            method(SELECTED, delta)
        assert history(editor) == before and not editor._commit.called
        assert_same_objects(editor, objects)


@pytest.mark.parametrize('delta,match', [(-41, 'start'), (16, 'overlap')])
def test_one_invalid_member_rejects_entire_move_and_preserves_redo(delta, match):
    editor = editor_with_redo()
    before, objects = history(editor), live_objects(editor)
    editor._commit = Mock(wraps=editor._commit)
    for method in (editor.preview_move_clips, editor.move_clips):
        with pytest.raises(ValueError, match=match):
            method(SELECTED, delta)
        assert history(editor) == before and not editor._commit.called
        assert_same_objects(editor, objects)


def test_exact_zero_and_unselected_neighbor_adjacency_are_legal():
    editor = make_editor()
    editor.move_clips([clip_id for clip_id in SELECTED if clip_id != 'video-b'], -40)
    assert editor._clip('video-a').start == 0
    editor.undo()
    editor.move_clips(SELECTED, 15)
    assert editor._clip('video-a').end == editor._clip('gap').start


@pytest.mark.parametrize('delete_all', [False, True])
def test_delete_group_is_one_non_ripple_edit_and_keeps_assets_anchors_order_and_survivors(delete_all):
    editor = make_editor()
    before = deepcopy(editor.project)
    project, clips = editor.project, editor.project.clips
    media, captions, overlays = editor.project.media, editor.project.captions, editor.project.overlays
    originals = {clip.id: clip for clip in clips}
    selected = set(originals) if delete_all else set(SELECTED)
    editor._commit = Mock(wraps=editor._commit)
    assert editor.delete_clips(selected) is None
    assert editor.project is project and editor.project.clips is clips
    assert editor.project.clips == [clip for clip in before.clips if clip.id not in selected]
    assert all(clip is originals[clip.id] for clip in editor.project.clips)
    assert editor.project.media is media and media == before.media
    assert editor.project.captions is captions and captions == before.captions
    assert editor.project.overlays is overlays and overlays == before.overlays
    assert editor._commit.call_count == 1 and len(editor._undo) == 1 and editor.revision == 1
    after = deepcopy(editor.project)
    assert Project.from_dict(after.to_dict()) == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('operation', ['move', 'delete'])
def test_real_edit_clears_redo_as_exactly_one_new_step(operation):
    editor = editor_with_redo()
    assert editor.can_redo and not editor.can_undo
    if operation == 'move':
        editor.move_clips(SELECTED, 2)
    else:
        editor.delete_clips(SELECTED)
    assert len(editor._undo) == 1 and not editor.can_redo and editor.revision == 3


@pytest.mark.parametrize('corruption', ['missing_media', 'unknown_media', 'stale_map', 'overlap'])
@pytest.mark.parametrize('operation', ['preview', 'move', 'noop', 'delete'])
def test_existing_invalid_project_is_not_repaired_or_partly_changed(corruption, operation):
    editor = make_editor()
    if corruption == 'missing_media':
        editor.project.media.clear()
    elif corruption == 'unknown_media':
        editor._clip('audio-b').media_id = 'missing'
    elif corruption == 'stale_map':
        editor._clip('audio-b').duration += 1
    else:
        editor._clip('gap').start = 50
    before, objects = history(editor), live_objects(editor)
    with pytest.raises(ValueError):
        if operation == 'delete':
            editor.delete_clips(SELECTED)
        elif operation == 'preview':
            editor.preview_move_clips(SELECTED, 1)
        else:
            editor.move_clips(SELECTED, 0 if operation == 'noop' else 1)
    assert history(editor) == before
    assert_same_objects(editor, objects)


def test_mutating_group_operations_require_owner_thread_even_for_noop():
    editor = make_editor()
    before, objects = history(editor), live_objects(editor)
    errors = []
    def change():
        for method, args in [(editor.move_clips, (SELECTED, 0)),
                             (editor.move_clips, (SELECTED, 1)),
                             (editor.delete_clips, (SELECTED,))]:
            try:
                method(*args)
            except ValueError as error:
                errors.append(str(error))
    thread = Thread(target=change)
    thread.start()
    thread.join()
    assert len(errors) == 3 and all('owning thread' in error for error in errors)
    assert history(editor) == before
    assert_same_objects(editor, objects)


def test_shared_generated_sound_and_speech_assets_are_never_rewritten_or_deleted(tmp_path):
    sound = SoundProvenance('sound', 'procedural', 'CC0-1.0', 'a' * 64, 'b' * 64,
                            48000, 480000)
    speech = SpeechProvenance('Original text', 'test', '1', 'a' * 64, 'b' * 64,
        'c' * 64, 'voice', 1.0, 24000, '2026-10-24T00:00:00Z', 'caption', 10, 20,
        'd' * 64, sample_count=240000)
    sound_path, speech_path = tmp_path / 'sound.wav', tmp_path / 'speech.wav'
    sound_path.write_bytes(b'original sound asset')
    speech_path.write_bytes(b'original speech asset')
    media = [Media('sound', str(sound_path), 'Sound', 10, has_video=False, has_audio=True,
                   sound_provenance=sound),
             Media('speech', str(speech_path), 'Speech', 10, has_video=False, has_audio=True,
                   speech_provenance=speech)]
    editor = Editor(Project(media=media, clips=[Clip('a', 'sound', 2, 10, 0, 20),
        Clip('b', 'sound', 2, 40, 20, 20), Clip('c', 'speech', 3, 10, 0, 20),
        Clip('keep', 'speech', 3, 70, 40, 20)]))
    before = deepcopy(editor.project)
    editor.move_clips(['a', 'b', 'c'], 5)
    editor.delete_clips(['a', 'b', 'c'])
    assert editor.project.media == before.media
    assert editor.project.media is media and editor.project.media[0].sound_provenance is sound
    assert editor.project.media[1].speech_provenance is speech
    assert editor.project.clips == [before.clips[-1]]
    assert sound_path.read_bytes() == b'original sound asset'
    assert speech_path.read_bytes() == b'original speech asset'
    assert editor.undo() and editor.undo() and editor.project == before
