"""Real-media proof for whole-clip private Copy/Paste, not a renderer rewrite.

Numbered lossless inputs provide an independent source-frame oracle. Audio is
48 kHz stereo f32: each pre-copy contribution is decoded independently and
placed at explicitly specified boundaries, then added in the historical
(track, start, id) order. The expected mix is not old-mix + copied-mix, which
would silently change floating-point summation order. Backgrounds are not part
of a clip snapshot: final composite pixels need not equal a translated old scene.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import subprocess
import wave

import numpy as np
import pytest

from lumen.audio_timing import compile_audio_timing
from lumen.clip_audio_translation import guard_local_audio_translation
from lumen.clip_composite import ClipComposite
from lumen.clip_motion import ClipMotionKeyframe
from lumen.clip_copy_paste import (prepare_clip_copy, verify_clip_copy, accept_clip_copy,
    plan_clip_paste, verify_clip_paste, apply_clip_paste)
from lumen.media import ffmpeg_available, probe
from lumen.model import (Caption, CaptionStyle, Clip, ClipEffect, Editor, Grade, Media, Project,
                         SoundProvenance)
from lumen.qualifier import QualifierSettings, WindowKeyframe
from lumen.source_ranges import SourceRange, SourceRangeBinding
from lumen.timeline_markers import TimelineMarker
from lumen.timing import ClipTimeMap
from test_gap_close_render import gap_av_input, lossless_scene
from test_caption_render import latin_font
from test_overlay_render import sticker
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                               engine, ff, make_source)
from test_ripple_preservation_media import boundary, pcm, solo
from test_speech_model import provenance as speech_provenance

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
RATES = [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)]
RATE_IDS = ['24', '23.976', '29.97', '59.94']


def copied(editor, ids):
    request = prepare_clip_copy(editor, ids)
    return accept_clip_copy(editor, request, verify_clip_copy(request))


def pasted(editor, clipboard, destination):
    plan = plan_clip_paste(editor, clipboard, destination)
    receipt = verify_clip_paste(plan, clipboard)
    return apply_clip_paste(editor, clipboard, plan, receipt)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision))


def original_records(before, after):
    """Only append approved clips and groups; every old record remains exact."""
    assert after.clips[:len(before.clips)] == before.clips
    assert after.clip_groups[:len(before.clip_groups)] == before.clip_groups
    for name in vars(before):
        if name not in {'clips', 'clip_groups'}:
            assert getattr(after, name) == getattr(before, name)


@pytest.fixture(scope='module')
def copy_numbered_sources(tmp_path_factory):
    root = tmp_path_factory.mktemp('copy-numbered-source')
    return {fps: make_source(root / f'{fps.numerator}-{fps.denominator}.mkv', fps)
            for fps in RATES}


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('mode', ['preview', 'export'])
def test_numbered_source_oracle_rational_speed_partial_tails_groups_and_hidden_keys(
        tmp_path, copy_numbered_sources, engine, fps, mode):
    source = copy_numbered_sources[fps]
    originals = [
        Clip('picture-a', source.media.id, 0, 2, 0, 6,
             time_map=ClipTimeMap(Fraction(9, 8), Fraction(73, 8), Fraction(3, 2))),
        Clip('picture-b', source.media.id, 1, 12, 0, 5,
             time_map=ClipTimeMap(Fraction(121, 4), Fraction(147, 4), Fraction(3, 2))),
        Clip('loose', source.media.id, 0, 21, 50, 4)]
    originals[0].motion_keyframes = [ClipMotionKeyframe(-3),
        ClipMotionKeyframe(2, interpolation='hold'), ClipMotionKeyframe(11)]
    originals[0].grade.qualifier.window_keyframes = [
        WindowKeyframe(-4, .2, .3, .4, .5), WindowKeyframe(12, .8, .7, .6, .4)]
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[deepcopy(source.media)], clips=originals,
        timeline_markers=[TimelineMarker('mark', 'Fixed annotation', '', 'cyan', 12, 16)]))
    group = deepcopy(editor.group_clips(('picture-a', 'picture-b')))
    before = deepcopy(editor.project)
    originals = deepcopy(before.clips)
    snapshot_state = state(editor)
    digest = sha(source.media.path)
    clipboard = copied(editor, ('picture-b', 'loose'))  # whole first group expands
    assert state(editor) == snapshot_state
    new = pasted(editor, clipboard, 35)
    assert len(new) == 3 and len({c.id for c in new}) == 3
    assert not {c.id for c in originals}.intersection(c.id for c in new)
    original_records(before, editor.project)
    first = next(c for c in new if c.start == 35)
    second = next(c for c in new if c.start == 45)
    loose = next(c for c in new if c.start == 54)
    for old, clone in zip(originals, (first, second, loose)):
        assert clone == replace(old, id=clone.id, start=old.start + 33)
        assert clone.grade is not old.grade and clone.effects is not old.effects
        assert clone.motion_keyframes is not old.motion_keyframes
    new_group = editor.project.clip_groups[-1]
    assert new_group.id != group.id and set(new_group.clip_ids) == {first.id, second.id}
    assert loose.id not in new_group.clip_ids
    expected = np.zeros((58, HEIGHT, WIDTH, 3), np.uint16)
    # Explicit fixture source-time constants, never the edited records/timing helper.
    for offset in (0, 33):
        for start, count, origin, speed in (
                (2 + offset, 6, Fraction(9, 8), Fraction(3, 2)),
                (12 + offset, 5, Fraction(121, 4), Fraction(3, 2)),
                (21 + offset, 4, Fraction(50), Fraction(1))):
            indexes = [math.floor(origin + k * speed) for k in range(count)]
            expected[start:start + count] = source.original[indexes]
    path = (engine.preview(editor.project) if mode == 'preview' else
            engine.render(editor.project, tmp_path / 'copied.mp4'))
    assert_delivery_frames(decode(path, rgb16=False), expected)
    for old, clone in zip(originals, (first, second, loose)):
        # Local graded intermediates are lossless and independent of placement.
        original_segment = engine._graded_segment(source.media, old, before, None)
        clone_segment = engine._graded_segment(source.media, clone, editor.project, None)
        np.testing.assert_array_equal(decode(clone_segment), decode(original_segment))
    after = deepcopy(editor.project)
    assert len(editor._undo) == len(snapshot_state[1]) + 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    saved = tmp_path / 'copy.lumen'
    editor.project.save(saved)
    assert Project.load(saved) == after
    assert sha(source.media.path) == digest


def stereo_project(root, fps, offset, speed):
    first = gap_av_input(root, fps, offset, 9401)
    second = gap_av_input(root, fps, -.113 if offset > 0 else .137, 9402)
    # Fractional origin and incomplete final interval are frozen whole, not
    # rounded into an ordinary source insert. A forty-frame displacement has
    # compatible absolute sample phase at every rate in this suite.
    mapping = ClipTimeMap(Fraction(9, 8), Fraction(9, 8) + Fraction(19, 2) * speed, speed)
    return Project(width=16, height=16, fps_num=fps.numerator, fps_den=fps.denominator,
        media=[first, second], clips=[
            Clip('a', first.id, 0, 2, 0, 10, time_map=mapping,
                 gain_db=-4, fade_in=1, fade_out=3),
            Clip('b', second.id, 2, 5, 3, 10, gain_db=-5, fade_in=2, fade_out=1),
            Clip('c', first.id, 3, 9, 10, 7, gain_db=-3, fade_in=1, fade_out=2),
            Clip('fixed-under-copy', second.id, 1, 39, 20, 25, gain_db=-8)])


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['1x-partial', 'retimed-partial'])
def test_stereo_pcm_per_contribution_oracle_offsets_fades_partial_tail_and_mix_order(
        tmp_path, fps, offset, speed):
    editor = Editor(stereo_project(tmp_path, fps, offset, speed))
    editor.group_clips(('a', 'c'))
    before = deepcopy(editor.project)
    fingerprints = {m.path: sha(m.path) for m in before.media}
    # Decode OLD isolated contributions once, with all old gain/fade/retime
    # details. Expected destination samples use independent absolute rounding.
    locals_by_id = {}
    for old in before.clips:
        old_pcm = pcm(solo(before, old))
        locals_by_id[old.id] = old_pcm[boundary(old.start, before.fps):].copy()
    assert any(np.any(v[:, 0] != v[:, 1]) for v in locals_by_id.values())
    clipboard = copied(editor, ('a', 'b'))
    new = pasted(editor, clipboard, 42)
    original_records(before, editor.project)
    old_by_track = {old.track: old for old in before.clips[:3]}
    descriptors = [(c.track, c.start, c.id, c.id) for c in before.clips]
    for clone in new:
        old = old_by_track[clone.track]
        assert clone == replace(old, id=clone.id, start=old.start + 40)
        descriptors.append((old.track, old.start + 40, clone.id, old.id))
        old_local = locals_by_id[old.id]
        actual_local = pcm(solo(editor.project, clone))[boundary(old.start + 40, before.fps):]
        np.testing.assert_array_equal(actual_local, old_local)
    expected = np.zeros((boundary(64, before.fps), 2), np.float32)
    for track, start, clip_id, origin_id in sorted(descriptors):
        contribution = locals_by_id[origin_id]
        begin = boundary(start, before.fps)
        expected[begin:begin + len(contribution)] += contribution
    actual = pcm(editor.project)
    np.testing.assert_array_equal(actual, expected)
    # The fixed track now lies BETWEEN copied contributions in the real mix.
    # Real old-whole-mix + real copy-only-mix therefore has a different float32
    # addition grouping. Establish the counterexample, not just an explicit
    # ordering in the otherwise possibly accidental expected-value algorithm.
    copy_only = deepcopy(editor.project)
    copy_only.clips = deepcopy(list(new)); copy_only.clip_groups = []
    copy_mix = pcm(copy_only)
    naive = pcm(before)
    naive[:len(copy_mix)] += copy_mix
    assert np.count_nonzero(naive != expected) > 0, 'Fixture must expose whole-mix regrouping errors'
    assert len(actual) == boundary(64, before.fps)
    assert np.max(np.abs(actual[boundary(42, before.fps):boundary(55, before.fps)])) > .01
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    np.testing.assert_array_equal(pcm(editor.project), expected)
    for path, digest in fingerprints.items():
        assert sha(path) == digest


def small_stereo(root, *, count=96000, rate=48000, name='noise.wav'):
    path = root / name
    raw = np.random.default_rng(9417).integers(-13000, 13001, (count, 2), dtype=np.int16)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(raw.astype('<i2').tobytes())
    return probe(path), raw


@pytest.mark.parametrize('duration,fade,offset', [(1, 0, 0), (5, 1, 0), (5, 0, .13701)],
                         ids=['sample-budget', 'fade-phase', 'leading-silence'])
def test_incompatible_local_stereo_pcm_refuses_atomic_copy_placement(tmp_path, duration, fade, offset):
    media, _ = small_stereo(tmp_path)
    media.audio_offset = offset
    original = Clip('original', media.id, 2, 2, 0, duration, fade_in=fade)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
                            media=[media], clips=[original]))
    clipboard = copied(editor, ('original',))
    candidate = replace(original, id='naive-copy', start=11)
    old = pcm(solo(editor.project, original))[boundary(2, editor.project.fps):]
    altered = pcm(solo(editor.project, candidate))[boundary(11, editor.project.fps):]
    assert not np.array_equal(old, altered), 'Refusal must guard actual PCM, not only metadata'
    editor.update_clip('original', gain_db=-1)
    assert editor.undo() and editor.can_redo
    before = state(editor)
    with pytest.raises(ValueError, match='sample|audio|phase|equivalen'):
        plan_clip_paste(editor, clipboard, 11)
    assert state(editor) == before
    assert copied(editor, ('original',)) is not clipboard


def test_equal_local_stereo_pcm_but_unequal_common_shift_must_refuse(tmp_path):
    media, _ = small_stereo(tmp_path)
    originals = [Clip('one', media.id, 2, 1, 0, 5), Clip('two', media.id, 3, 2, 10, 5)]
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
                            media=[media], clips=originals))
    clipboard = copied(editor, ('one', 'two'))
    shifts = []
    for original in originals:
        candidate = replace(original, id='naive-' + original.id, start=original.start + 11)
        guard_local_audio_translation(original, candidate, editor.project, operation='test')
        local = pcm(solo(editor.project, original))[boundary(original.start, editor.project.fps):]
        moved = pcm(solo(editor.project, candidate))[boundary(candidate.start, editor.project.fps):]
        np.testing.assert_array_equal(local, moved)
        shifts.append(boundary(candidate.start, editor.project.fps) - boundary(original.start, editor.project.fps))
    assert shifts == [17617, 17618]
    assert boundary(12, editor.project.fps) - boundary(1, editor.project.fps) == 17617
    before = state(editor)
    with pytest.raises(ValueError, match='sample|audio|phase|shift|synchron'):
        plan_clip_paste(editor, clipboard, 12)
    assert state(editor) == before


@pytest.mark.parametrize('silent', ['muted', 'outside-audio'])
def test_pure_silence_does_not_add_a_spurious_phase_constraint(tmp_path, silent):
    media, _ = small_stereo(tmp_path)
    if silent == 'outside-audio':
        media.audio_offset = 5
    original = Clip('quiet', media.id, 2, 2, 0, 1, muted=silent == 'muted')
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
                            media=[media], clips=[original]))
    new, = pasted(editor, copied(editor, ('quiet',)), 11)
    assert new.start == 11 and new.muted == original.muted
    assert not np.any(pcm(editor.project))


@pytest.mark.parametrize('kind', ['sound', 'speech'])
def test_generated_full_hash_and_partial_sample_tail_keep_same_media_no_caption_copy(tmp_path, kind):
    media, raw = small_stereo(tmp_path, count=9867)
    digest = sha(media.path)
    media.duration = len(raw) / 48000
    if kind == 'sound':
        media.sound_provenance = SoundProvenance('synthetic', 'procedural', 'CC0-1.0',
                                               digest, digest, 48000, len(raw))
    else:
        media.speech_provenance = speech_provenance(sample_rate=48000, sample_count=len(raw),
            output_sha256=digest, source_caption_id='caption', source_start=2, source_duration=5)
    fps = Fraction(24)
    duration = math.ceil(Fraction(len(raw), 48000) * fps)
    original = Clip('generated', media.id, 2, 2, 0, duration)
    project = Project(width=16, height=16, fps_num=24, media=[media], clips=[original],
        captions=[Caption('caption', 2, 5, 'Generated provenance is not a link')],
        timeline_markers=[TimelineMarker('fixed', 'Fixed', '', 'purple', 2)])
    editor = Editor(project)
    before = deepcopy(project)
    clipboard = copied(editor, ('generated',))
    new, = pasted(editor, clipboard, 12)
    assert new.media_id == media.id and len(project.media) == 1
    original_records(before, project)
    expected = np.zeros((17 * 2000, 2), np.float32)
    converted = raw.astype(np.float32) / 32768
    expected[2 * 2000:2 * 2000 + len(raw)] = converted
    expected[12 * 2000:12 * 2000 + len(raw)] = converted
    np.testing.assert_array_equal(pcm(project), expected)
    assert not np.any(expected[12 * 2000 + len(raw):])  # exact partial tail
    assert sha(media.path) == digest
    # This corrupts real generated bytes. Copying/pasting never re-synthesizes
    # or silently adopts them, even though the path remains the same.
    plan = plan_clip_paste(editor, clipboard, 22)
    with Path(media.path).open('r+b') as stream:
        stream.seek(128); stream.write(b'\x91\x29\x19\x57')
    unchanged = state(editor)
    with pytest.raises(ValueError, match='changed|identity|content|source|Source|Generated|generated'):
        verify_clip_paste(plan, clipboard)
    assert state(editor) == unchanged


def test_long_whole_clip_over_thirty_seconds_is_not_a_source_cut(tmp_path, monkeypatch):
    media, _ = small_stereo(tmp_path, count=8000 * 32, rate=8000, name='long-lowrate.wav')
    original = Clip('long', media.id, 2, 0, 0, 31 * 24, gain_db=-4, fade_in=2, fade_out=3)
    editor = Editor(Project(width=16, height=16, fps_num=24, media=[media], clips=[original]))
    digest = sha(media.path)
    old_local = pcm(editor.project)
    def forbidden(*args, **kwargs):
        raise AssertionError('Whole clip copy/placement should only verify identities, not decode a source cut')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'run', forbidden)
        patch.setattr(subprocess, 'Popen', forbidden)
        new, = pasted(editor, copied(editor, ('long',)), 32 * 24)
    assert new.duration == 744
    actual_local = pcm(solo(editor.project, new))[32 * 48000:]
    np.testing.assert_array_equal(actual_local, old_local)
    assert sha(media.path) == digest


def test_active_window_motion_effects_are_frozen_but_background_is_not(
        tmp_path, copy_numbered_sources, engine, latin_font):
    source = copy_numbered_sources[Fraction(24)]
    top = Clip('top', source.media.id, 1, 2, 0, 4,
        time_map=ClipTimeMap(Fraction(9, 8), Fraction(13, 2), Fraction(3, 2)),
        grade=Grade(exposure=.15, qualifier=QualifierSettings(enabled=True, hue_width=.5,
            exposure=.2, window_shape='ellipse', window_feather=.4,
            window_keyframes=[WindowKeyframe(-2, .25, .5, .6, .6),
                              WindowKeyframe(7, .75, .5, .6, .6)])),
        composite=ClipComposite(True),
        motion_keyframes=[ClipMotionKeyframe(-2, x=-5, y=1, scale=.5, opacity=.3),
                          ClipMotionKeyframe(7, x=6, y=-2, scale=.8, opacity=.9)],
        effects=[ClipEffect('blur', 'blur', .6), ClipEffect('vig', 'vignette', .2,
                                                        radius=.8, feather=.9)])
    lower = Clip('different-background', source.media.id, 0, 12, 60, 4)
    overlay, _ = sticker(tmp_path, name='annotation.png', start=7, duration=2)
    saved_range = SourceRange('range', 'Do not copy', source.media.id, 0, 1, 1, 1, 24, 1,
        SourceRangeBinding(1, Path(source.media.path).stat().st_size, 'a' * 64, 'b' * 64))
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=24,
        media=[deepcopy(source.media)], clips=[top, lower], overlays=[overlay],
        captions=[Caption('annotation', 7, 2, 'Fixed')],
        caption_style=CaptionStyle(font_family=latin_font, font_size=10, margin_bottom=2, outline_width=0),
        source_ranges=[saved_range],
        timeline_markers=[TimelineMarker('mark', 'Fixed', '', 'yellow', 2, 6)]))
    before = deepcopy(editor.project)
    clipboard = copied(editor, ('top',))
    original_local = lossless_scene(engine, solo(before, top), tmp_path / 'original-local.mkv')[2:6]
    # Change original after Copy: the accepted snapshot keeps all original
    # creative parameters, including hidden negative/outside keyframe handles.
    editor.update_clip('top', grade=Grade(exposure=-.4))
    changed_original = deepcopy(editor.project.clips[0])
    top = before.clips[0]
    new, = pasted(editor, clipboard, 12)
    expected = replace(top, id=new.id, start=12)
    expected.effects = [replace(effect, id=actual.id) for effect, actual in zip(top.effects, new.effects)]
    assert new == expected
    assert editor.project.clips[0] == changed_original
    assert {effect.id for effect in new.effects}.isdisjoint(effect.id for effect in top.effects)
    cloned_local = lossless_scene(engine, solo(editor.project, new), tmp_path / 'cloned-local.mkv')[12:16]
    np.testing.assert_array_equal(cloned_local, original_local)
    final = lossless_scene(engine, editor.project, tmp_path / 'changed-context.mkv')
    assert not np.array_equal(final[12:16], original_local), 'The new background was never frozen by Copy'
    for name in ('captions', 'overlays', 'source_ranges', 'timeline_markers', 'media'):
        assert getattr(editor.project, name) == getattr(before, name)
    assert new.grade.qualifier.window_keyframes == top.grade.qualifier.window_keyframes
    assert new.motion_keyframes == top.motion_keyframes


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
def test_earlier_paste_uses_signed_absolute_sample_delta_without_moving_originals(tmp_path, fps):
    media, _ = small_stereo(tmp_path)
    old = Clip('later-original', media.id, 2, 22, 3, 10, gain_db=-2, fade_in=2, fade_out=1)
    editor = Editor(Project(width=16, height=16, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], clips=[old]))
    before = deepcopy(editor.project)
    original_pcm = pcm(before)
    local = original_pcm[boundary(22, fps):].copy()
    new, = pasted(editor, copied(editor, (old.id,)), 2)
    expected = original_pcm.copy()
    expected[boundary(2, fps):boundary(2, fps) + len(local)] += local
    np.testing.assert_array_equal(pcm(editor.project), expected)
    original_records(before, editor.project)
    assert new == replace(old, id=new.id, start=2)
    assert boundary(2, fps) - boundary(22, fps) < 0


def test_two_complete_groups_and_one_loose_clip_have_new_distinct_relationships_and_raw_pcm(tmp_path):
    media, raw = small_stereo(tmp_path)
    specifications = [
        ('one-a', 2, 2, 0, 4), ('one-b', 3, 3, 5, 3),
        ('two-a', 2, 12, 10, 4), ('two-b', 3, 13, 15, 3),
        ('loose', 2, 20, 20, 2)]
    originals = [Clip(cid, media.id, track, start, origin, duration)
                 for cid, track, start, origin, duration in specifications]
    editor = Editor(Project(width=16, height=16, fps_num=24, media=[media], clips=originals))
    editor.group_clips(('one-a', 'one-b')); editor.group_clips(('two-a', 'two-b'))
    before = deepcopy(editor.project)
    new = pasted(editor, copied(editor, ('one-b', 'two-a', 'loose')), 32)
    assert len(new) == 5 and len(editor.project.clip_groups) == 4
    original_records(before, editor.project)
    by_position = {(c.track, c.start): c for c in new}
    memberships = [set(group.clip_ids) for group in editor.project.clip_groups[2:]]
    assert memberships == [
        {by_position[(2, 32)].id, by_position[(3, 33)].id},
        {by_position[(2, 42)].id, by_position[(3, 43)].id}]
    assert len({group.id for group in editor.project.clip_groups}) == 4
    assert all(by_position[(2, 50)].id not in ids for ids in memberships)
    converted = raw.astype(np.float32) / 32768
    expected = np.zeros((52 * 2000, 2), np.float32)
    descriptors = [(track, start + shift, cid, origin, duration)
        for cid, track, start, origin, duration in specifications for shift in (0, 30)]
    for track, start, cid, origin, duration in sorted(descriptors):
        expected[start * 2000:(start + duration) * 2000] += converted[origin * 2000:(origin + duration) * 2000]
    np.testing.assert_array_equal(pcm(editor.project), expected)
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
