"""Pure previews and atomic non-ripple edge trims with exact partial tails."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
from threading import Thread

import pytest

from lumen.model import (MAX_CAPTION_FRAME, Caption, Clip, ClipEffect, Editor,
                         Grade, Media, Overlay, OverlayKeyframe, Project)
from lumen.qualifier import MAX_WINDOW_FRAME, QualifierSettings, WindowKeyframe
from lumen.timing import MAX_SOURCE_NUMERATOR, ClipTimeMap, duration_frames
from lumen.window_animation import evaluate_window, shift_window_animation


def make_editor(speed=Fraction(3, 2), fps=Fraction(30), track=0):
    mapping = ClipTimeMap(100, 100 + Fraction(79, 4) * speed, speed)
    grade = Grade(contrast=1.2, qualifier=QualifierSettings(
        enabled=True, window_shape='ellipse', window_keyframes=[
            WindowKeyframe(-5, .1, .8, .3, 1.5),
            WindowKeyframe(7, .7, .2, .9, .5, 'hold'),
            WindowKeyframe(50, .9, .6, 1.2, .4)]))
    clip = Clip('selected', 'm', track, 50, 0, duration_frames(mapping),
                time_map=mapping, grade=grade, fade_in=2, fade_out=3,
                gain_db=-3, muted=True,
                effects=[ClipEffect('blur', 'blur', 1)] if track < 2 else [])
    media = Media('m', '/tmp/source.mp4', 'Source', 120, 64, 48, True, True)
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator,
        media=[media], clips=[clip,
            Clip('before', 'm', track, 0, 0, 10),
            Clip('later', 'm', track, 120, 150, 10),
            Clip('other', 'm', (track + 1) % 4, 49, 0, 200)],
        captions=[Caption('caption', 53, 10, 'Fixed')],
        overlays=[Overlay('sticker', '/tmp/sticker.png', 'a' * 64, 52, 8,
                          keyframes=[OverlayKeyframe(0)])]))


def history(editor):
    return deepcopy((editor.project, editor._undo, editor._redo,
                     editor.revision, editor.session_id))


def assert_rejected(editor, edge, delta, match=None, clip_id='selected'):
    before = history(editor)
    project = editor.project
    selected = editor.project.clips[0]
    for method in (editor.preview_trim_edge, editor.trim_edge):
        with pytest.raises(ValueError, match=match):
            method(clip_id, edge, delta)
        assert history(editor) == before
        assert editor.project is project and editor.project.clips[0] is selected


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('fps', [Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('track', range(4))
@pytest.mark.parametrize('edge,delta', [('left', -3), ('left', 2), ('right', -3), ('right', 2)])
def test_exact_edges_preserve_partial_tail_settings_anchors_and_history(speed, fps, track, edge, delta):
    editor = make_editor(speed, fps, track)
    before = deepcopy(editor.project)
    old = before.clips[0]
    original = editor.project.clips[0]
    preview = editor.preview_trim_edge(old.id, edge, delta)
    assert editor.project == before and editor.project.clips[0] is original
    assert not editor.can_undo and editor.revision == 0
    assert preview is not original and preview.grade is not original.grade
    result = editor.trim_edge(old.id, edge, delta)
    assert result is original and result == preview
    if edge == 'left':
        assert (result.start, result.end) == (old.start + delta, old.end)
        assert result.timing == ClipTimeMap(old.source_in + delta * speed, old.source_out, speed)
        assert result.grade == shift_window_animation(old.grade, delta)
    else:
        assert (result.start, result.end) == (old.start, old.end + delta)
        assert result.timing == ClipTimeMap(old.source_in, old.source_out + delta * speed, speed)
        assert result.grade == old.grade
    assert result.duration == old.duration + (-delta if edge == 'left' else delta)
    assert duration_frames(result.timing) == result.duration
    old_tail = old.duration * speed - (old.source_out - old.source_in)
    new_tail = result.duration * speed - (result.source_out - result.source_in)
    assert old_tail == new_tail == speed / 4
    for name in ('id', 'media_id', 'track', 'speed', 'fade_in', 'fade_out',
                 'gain_db', 'muted', 'effects', 'restoration', 'stabilization', 'transform'):
        assert getattr(result, name) == getattr(old, name)
    assert editor.project.clips[1:] == before.clips[1:]
    assert editor.project.media == before.media
    assert editor.project.captions == before.captions
    assert editor.project.overlays == before.overlays
    after = deepcopy(editor.project)
    assert Project.from_dict(after.to_dict()) == after
    assert len(editor._undo) == 1 and editor.revision == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_preview_is_detached_and_noop_keeps_redo_revision_and_identity(edge):
    editor = make_editor()
    editor.trim_edge('selected', 'right', 1)
    assert editor.undo()
    before = history(editor)
    original = editor._clip('selected')
    preview = editor.preview_trim_edge('selected', edge, 0)
    assert preview == original and preview is not original
    preview.grade.lift[0] = .5
    preview.grade.qualifier.window_keyframes.clear()
    preview.effects.clear()
    assert history(editor) == before
    assert editor.trim_edge('selected', edge, 0) is original
    assert history(editor) == before


@pytest.mark.parametrize('edge', ['', 'Left', 'out', None, True, 0, []])
def test_invalid_edges_reject_atomically(edge):
    assert_rejected(make_editor(), edge, 1, 'edge')


@pytest.mark.parametrize('delta', [True, False, 1.0, Fraction(1), '1', None,
                                  float('nan'), float('inf'), 2**53, -(2**53)])
@pytest.mark.parametrize('edge', ['left', 'right'])
def test_only_bounded_exact_integer_timeline_deltas_are_accepted(edge, delta):
    editor = make_editor()
    editor.trim_edge('selected', 'right', 1)
    editor.undo()
    assert_rejected(editor, edge, delta, 'trim delta')


@pytest.mark.parametrize('edge,delta', [('left', 20), ('left', 21), ('right', -20), ('right', -21)])
def test_empty_or_negative_duration_rejects_atomically(edge, delta):
    assert_rejected(make_editor(), edge, delta, 'one timeline frame')


@pytest.mark.parametrize('edge,delta', [('left', 19), ('right', -19)])
def test_one_frame_partial_tail_is_retained(edge, delta):
    editor = make_editor()
    editor._clip('selected').fade_in = editor._clip('selected').fade_out = 0
    old = deepcopy(editor._clip('selected'))
    result = editor.trim_edge(old.id, edge, delta)
    assert result.duration == 1
    assert result.source_out - result.source_in == old.speed * Fraction(3, 4)


@pytest.mark.parametrize('edge,delta', [('left', 1), ('right', -1)])
@pytest.mark.parametrize('fade_name', ['fade_in', 'fade_out'])
def test_shortening_fades_is_rejected_not_clamped(edge, delta, fade_name):
    editor = make_editor()
    setattr(editor._clip('selected'), fade_name, 20)
    assert_rejected(editor, edge, delta, 'fade')
    # Overlapping fades remain legal when each independently fits the clip.
    editor._clip('selected').fade_in = editor._clip('selected').fade_out = 19
    result = editor.trim_edge('selected', edge, delta)
    assert result.fade_in == result.fade_out == result.duration == 19


@pytest.mark.parametrize('edge,delta', [('left', -11), ('right', 11)])
def test_same_track_collision_rejects_and_exact_adjacency_is_legal(edge, delta):
    editor = make_editor()
    editor._clip('before').start = 30
    editor._clip('later').start = 80
    editor.project.clips.reverse()  # Neighbor checks cannot rely on list order.
    assert_rejected(editor, edge, delta, 'overlap')
    result = editor.trim_edge('selected', edge, -10 if edge == 'left' else 10)
    assert result.start == editor._clip('before').end if edge == 'left' else result.end == editor._clip('later').start


def test_left_extension_cannot_move_before_timeline_zero():
    editor = make_editor()
    editor.project.clips = [editor._clip('selected')]
    assert_rejected(editor, 'left', -51, 'trim start')
    assert editor.trim_edge('selected', 'left', -50).start == 0


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
def test_exact_source_end_is_inclusive_and_next_frame_rejects(fps):
    editor = make_editor(Fraction(4), fps)
    clip = editor._clip('selected')
    bound = editor.project.media_frames(editor.project.media[0])
    mapping = ClipTimeMap(Fraction(bound) - Fraction(173, 2), bound - 8, Fraction(4))
    editor._set_clip_timing(clip, mapping)
    result = editor.trim_edge(clip.id, 'right', 2)
    assert result.source_out == bound
    assert result.duration * result.speed - (result.source_out - result.source_in) == Fraction(3, 2)
    assert_rejected(editor, 'right', 1, 'source media')


def test_source_zero_boundary_does_not_round_or_clamp():
    editor = make_editor(Fraction(4))
    clip = editor._clip('selected')
    editor._set_clip_timing(clip, ClipTimeMap(12, 90, Fraction(4)))
    assert_rejected(editor, 'left', -4, 'rational bounds')
    result = editor.trim_edge(clip.id, 'left', -3)
    assert result.source_in == 0 and result.source_out == 90


@pytest.mark.parametrize('edge', ['left', 'right'])
def test_coprime_denominator_precision_overflow_rejects(edge):
    editor = make_editor()
    speed = Fraction(999978, 999979)
    origin = Fraction(9999831, 999983) if edge == 'left' else Fraction(10)
    endpoint = Fraction(49999151, 999983) if edge == 'right' else Fraction(50)
    editor._set_clip_timing(editor._clip('selected'), ClipTimeMap(origin, endpoint, speed))
    editor.project.validate()
    assert_rejected(editor, edge, 1, 'rational bounds')


def test_source_numerator_overflow_rejects_before_commit():
    endpoint = Fraction(MAX_SOURCE_NUMERATOR, 1000000)
    mapping = ClipTimeMap(endpoint - 76, endpoint, Fraction(4))
    clip = Clip('selected', 'm', 0, 0, 0, 19, time_map=mapping)
    editor = Editor(Project(fps_num=1000, media=[
        Media('m', '/tmp/large.mp4', 'Large', 100000000, 64, 48)], clips=[clip]))
    assert_rejected(editor, 'right', 1, 'rational bounds')


@pytest.mark.parametrize('edge,delta', [('left', -1), ('right', 1)])
def test_timeline_boundary_overflow_rejects(edge, delta):
    editor = make_editor()
    clip = editor._clip('selected')
    editor.project.clips = [clip]
    clip.start = MAX_CAPTION_FRAME - clip.duration
    if edge == 'left':
        # The existing out-of-range tail must not silently survive a preview.
        clip.start += 1
    assert_rejected(editor, edge, delta, 'trim end')


@pytest.mark.parametrize('delta,key_frame', [(1, -MAX_WINDOW_FRAME), (-1, MAX_WINDOW_FRAME)])
def test_left_window_keyframe_overflow_rejects_but_right_keeps_keys(delta, key_frame):
    editor = make_editor()
    clip = editor._clip('selected')
    clip.grade.qualifier.window_keyframes = [WindowKeyframe(key_frame)]
    assert_rejected(editor, 'left', delta, 'frame')
    result = editor.trim_edge(clip.id, 'right', delta)
    assert result.grade.qualifier.window_keyframes == [WindowKeyframe(key_frame)]


@pytest.mark.parametrize('delta', [-3, 4])
def test_left_rebase_preserves_hidden_handles_and_exact_geometry(delta):
    editor = make_editor()
    old = deepcopy(editor._clip('selected').grade.qualifier)
    result = editor.trim_edge('selected', 'left', delta).grade.qualifier
    assert result.window_keyframes == [replace(key, frame=key.frame-delta) for key in old.window_keyframes]
    for frame in [Fraction(i, 3) for i in range(-30, 180)]:
        assert evaluate_window(result, frame) == evaluate_window(old, frame+delta)


@pytest.mark.parametrize('corruption', ['missing_media', 'unknown_media', 'wrong_track_media',
                                         'bad_duration', 'stale_map', 'overlap'])
def test_invalid_existing_state_is_never_repaired_by_trimming(corruption):
    editor = make_editor()
    if corruption == 'missing_media':
        editor.project.media.clear()
    elif corruption == 'unknown_media':
        editor._clip('selected').media_id = 'unknown'
    elif corruption == 'wrong_track_media':
        editor.project.media[0].has_video = False
    elif corruption == 'bad_duration':
        editor.project.media[0].duration = -1
    elif corruption == 'stale_map':
        editor._clip('selected').duration += 1
    else:
        editor._clip('later').start = 60
    assert_rejected(editor, 'right', 1)


def test_unknown_clip_and_owner_thread_reject_without_mutation():
    editor = make_editor()
    assert_rejected(editor, 'right', 1, 'Unknown clip', clip_id='missing')
    before = history(editor)
    errors = []
    def change():
        for delta in (0, 1):
            try:
                editor.trim_edge('selected', 'right', delta)
            except ValueError as error:
                errors.append(str(error))
    thread = Thread(target=change)
    thread.start()
    thread.join()
    assert len(errors) == 2 and all('owning thread' in error for error in errors)
    assert history(editor) == before


def test_generated_audio_trim_keeps_synthesis_provenance(tmp_path):
    from test_tts_exact_timing import speech
    media = speech(tmp_path, 240000)
    editor = make_editor(track=2)
    editor.project.media = [media]
    editor.project.clips = [editor._clip('selected')]
    editor._clip('selected').media_id = media.id
    editor.project.validate()
    before = deepcopy(media.speech_provenance)
    editor.trim_edge('selected', 'left', -2)
    editor.trim_edge('selected', 'right', 3)
    assert editor.project.media[0].speech_provenance == before
    assert Project.from_dict(editor.project.to_dict()) == editor.project
