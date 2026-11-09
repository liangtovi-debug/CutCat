"""Atomic, non-rippling, whole-project-frame source overwrite contracts."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import subprocess
from threading import Thread
from uuid import uuid4

import pytest

from lumen.audio_timing import compile_audio_timing
from lumen.clip_composite import ClipComposite
from lumen.clip_motion import ClipMotionKeyframe
from lumen.clip_transform import ClipTransform
from lumen.model import (MAX_CAPTION_FRAME, Caption, Clip, ClipEffect, ClipGroup,
                         Editor, Grade, Media, Overlay, Project, SoundProvenance,
                         VERSION, VideoTransition)
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierSettings, WindowKeyframe
from lumen.restoration_settings import RestorationSettings
from lumen.source_overwrite import complete_source_frames
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap


def sample(track=0, fps=Fraction(30), audio=True):
    media = [Media('m', '/missing/source.mov', 'Source', 30, 64, 48, True, audio,
                   fps.numerator, fps.denominator),
             Media('new', '/missing/marked.mov', 'Marked', 20, 64, 48, True, True,
                   fps.numerator, fps.denominator)]
    grade = Grade(exposure=.5, gain=[1.1, .9, 1], lut_path='/missing/look.cube',
                  lut_strength=.7, qualifier=QualifierSettings(enabled=True,
                    window_shape='ellipse', window_width=.3, exposure=.2))
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator,
        media=media, clips=[Clip('old', 'm', track, 20, 60, 40, grade=grade, gain_db=-3),
            Clip('earlier', 'm', track, 0, 0, 10),
            Clip('later', 'm', track, 90, 200, 20),
            Clip('other', 'm', (track + 1) % 4, 0, 0, 150)],
        captions=[Caption('caption', 22, 10, 'Independent')],
        overlays=[Overlay('sticker', '/missing/sticker.png', 'a' * 64, 24, 8)]))


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo,
                     editor.revision, editor.session_id))


def rejected(editor, operation, match=None):
    before = state(editor)
    live = editor.project
    clips = list(editor.project.clips)
    with pytest.raises(ValueError, match=match):
        operation()
    assert state(editor) == before
    assert editor.project is live
    assert all(a is b for a, b in zip(editor.project.clips, clips))


@pytest.mark.parametrize('track', range(4))
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('start,duration,expected', [
    (20, 40, []),                 # Full replacement.
    (15, 50, []),                 # Cover both boundaries and neighboring gaps.
    (15, 15, [(30, 70, 30)]),    # Sole right keeps original identity.
    (50, 20, [(20, 60, 30)]),    # Sole left keeps original identity.
    (30, 20, [(20, 60, 10), (50, 90, 10)]),
    (20, 1, [(21, 61, 39)]),
    (59, 1, [(20, 60, 39)]),
])
def test_overlap_shapes_preserve_exact_settings_and_one_history_step(track, fps, start, duration, expected):
    editor = sample(track, fps)
    # Source and destination positions align to the 29.97 sample phase here.
    original = deepcopy(editor.project)
    originals = {clip.id: clip for clip in editor.project.clips}
    before = state(editor)
    plan = editor.plan_source_overwrite('new', track, start, 5, 5 + duration)
    assert state(editor) == before
    assert plan.duration == duration and plan.end == start + duration
    assert plan.affected_clip_ids == ('old',)
    assert (plan.session_id, plan.revision, plan.fps_num, plan.fps_den) == (
        editor.session_id, 0, fps.numerator, fps.denominator)
    assert [(item.start, item.source_in, item.duration) for item in plan.remnants] == expected
    result = editor.apply_source_overwrite(plan)
    assert (result.track, result.start, result.source_in, result.source_out,
            result.duration, result.speed) == (track, start, 5, 5 + duration, duration, 1)
    assert result.grade == Grade() and result.gain_db == 0 and not result.muted
    for remnant in plan.remnants:
        clip = editor._clip(remnant.clip_id)
        wanted = deepcopy(original.clips[0])
        wanted.id, wanted.start = remnant.clip_id, remnant.start
        wanted.source_in, wanted.duration = remnant.source_in, remnant.duration
        assert clip == wanted
        assert clip.grade is not originals['old'].grade
    if plan.remnants:
        assert plan.remnants[0].clip_id == 'old'
    if len(plan.remnants) == 2:
        assert plan.remnants[1].clip_id != 'old'
    for clip_id in ('earlier', 'later', 'other'):
        assert editor._clip(clip_id) is originals[clip_id]
        assert editor._clip(clip_id) == next(c for c in original.clips if c.id == clip_id)
    for field in ('media', 'captions', 'overlays', 'clip_groups', 'video_transitions',
                  'track_edit_locks', 'master_gain_db', 'media_bins', 'media_bin_ids'):
        assert getattr(editor.project, field) == getattr(original, field)
    assert len(editor._undo) == 1 and editor.revision == 1
    assert Project.from_dict(editor.project.to_dict()) == editor.project
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('start,duration', [(10, 10), (60, 30), (110, 10), (150, 10), (200, 1)])
def test_gaps_adjacency_and_beyond_timeline_end_never_shift_existing_clips(start, duration):
    editor = sample()
    old = list(editor.project.clips)
    plan = editor.plan_source_overwrite('new', 0, start, 0, duration)
    assert not plan.affected_clip_ids and not plan.remnants
    editor.apply_source_overwrite(plan)
    assert all(editor._clip(c.id) is c for c in old)


def test_multiple_clips_gaps_and_unrelated_group_are_preserved():
    editor = sample()
    editor.project.clips += [Clip('middle', 'm', 0, 65, 100, 10)]
    editor.group_clips(['other', 'earlier'])
    before = deepcopy(editor.project)
    plan = editor.plan_source_overwrite('new', 0, 30, 10, 80)
    assert plan.affected_clip_ids == ('old', 'later', 'middle')
    assert [(r.clip_id, r.start, r.source_in, r.duration) for r in plan.remnants] == [
        ('old', 20, 60, 10), ('later', 100, 210, 10)]
    editor.apply_source_overwrite(plan)
    assert editor.project.clip_groups == before.clip_groups
    assert all(plan.new_clip_id not in g.clip_ids for g in editor.project.clip_groups)
    assert len(editor._undo) == 2


def test_fractional_project_frames_and_apple_log_import_interpretation():
    editor = sample(fps=Fraction(30000, 1001))
    editor.project.media[1].input_encoding = 'apple_log_bt2020'
    plan = editor.plan_source_overwrite('new', 1, 151, 17, 24)
    clip = editor.apply_source_overwrite(plan)
    assert clip.source_in == 17 and clip.source_out == 24 and clip.duration == 7
    assert clip.grade.input_encoding == 'apple_log_bt2020'


@pytest.mark.parametrize('source', ['m', 'new'])
@pytest.mark.parametrize('fps', [Fraction(24), Fraction(60), Fraction(30000, 1001)])
def test_mixed_native_video_rate_rejects_new_or_affected_picture(source, fps):
    editor = sample()
    media = next(m for m in editor.project.media if m.id == source)
    media.fps_num, media.fps_den = fps.numerator, fps.denominator
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 30, 0, 10), 'mixed-rate video')


def test_mixed_video_rate_on_audio_track_uses_only_audio_semantics():
    editor = sample(track=2)
    editor.project.media[0].fps_num = 24
    editor.project.media[1].fps_num = 60
    editor.apply_source_overwrite(editor.plan_source_overwrite('new', 2, 30, 0, 10))


def test_equivalent_rational_source_rates_are_not_rejected():
    editor = sample(fps=Fraction(30000, 1001))
    for media in editor.project.media:
        media.fps_num, media.fps_den = 60000, 2002
    editor.apply_source_overwrite(editor.plan_source_overwrite('new', 0, 30, 0, 10))


@pytest.mark.parametrize('field,bad', [
    ('track', True), ('track', -1), ('track', 4), ('track', '0'),
    ('start', True), ('start', -1), ('start', 1.5), ('start', Fraction(1)),
    ('start', MAX_CAPTION_FRAME),
    ('source_in', True), ('source_in', -1), ('source_in', 1.0), ('source_in', Fraction(1)),
    ('source_out', False), ('source_out', 0), ('source_out', 1.5), ('source_out', '20'),
    ('source_out', 601), ('media_id', 'missing'), ('media_id', None),
])
def test_invalid_parameters_preserve_live_objects_history_and_redo(field, bad):
    editor = sample()
    editor.move_clip('later', 91)
    editor.undo()
    values = dict(media_id='new', track=0, start=30, source_in=10, source_out=20)
    values[field] = bad
    rejected(editor, lambda: editor.plan_source_overwrite(**values))


@pytest.mark.parametrize('source_in,source_out', [(10, 10), (11, 10)])
def test_empty_reversed_marks_reject(source_in, source_out):
    editor = sample()
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 30, source_in, source_out), 'after In')


@pytest.mark.parametrize('track,video,audio', [(0, False, True), (1, False, True),
                                            (2, True, False), (3, True, False)])
def test_media_track_compatibility(track, video, audio):
    editor = sample()
    editor.project.media[1].has_video, editor.project.media[1].has_audio = video, audio
    rejected(editor, lambda: editor.plan_source_overwrite('new', track, 30, 0, 5), 'incompatible')


@pytest.mark.parametrize('change', [
    lambda c: setattr(c, 'fade_in', 1),
    lambda c: setattr(c, 'fade_out', 1),
    lambda c: setattr(c, 'effects', [ClipEffect('fx', 'blur', 1, enabled=False)]),
    lambda c: setattr(c, 'motion_keyframes', [ClipMotionKeyframe()]),
    lambda c: setattr(c.grade.qualifier, 'window_keyframes', [WindowKeyframe()]),
    lambda c: setattr(c, 'stabilization', StabilizationSettings(enabled=True)),
    lambda c: setattr(c, 'stabilization', StabilizationSettings(smoothing=20)),
    lambda c: setattr(c, 'restoration', RestorationSettings(enabled=True)),
    lambda c: setattr(c, 'restoration', RestorationSettings(denoise=.5)),
    lambda c: setattr(c, 'transform', ClipTransform(x=1)),
    lambda c: setattr(c, 'composite', ClipComposite(opacity=.5)),
    lambda c: setattr(c, 'composite', ClipComposite(transparent_outside_transform=True)),
])
@pytest.mark.parametrize('start,duration', [(30, 10), (20, 40)])
def test_unsafe_affected_settings_reject_even_full_deletion(change, start, duration):
    editor = sample()
    change(editor._clip('old'))
    editor.project.validate()
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, start, 0, duration), 'cannot cut')
    # A different nonoverlapping target does not reject this independent clip.
    editor.apply_source_overwrite(editor.plan_source_overwrite('new', 0, 151, 0, 10))


@pytest.mark.parametrize('mapping', [ClipTimeMap(60, 140, 2),
    ClipTimeMap(Fraction(121, 2), Fraction(201, 2)),
    ClipTimeMap(60, Fraction(199, 2))])
def test_retiming_fractional_origins_and_partial_tails_reject(mapping):
    editor = sample()
    editor.project.clips[0] = Clip('old', 'm', 0, 20, mapping.source_in, 40, time_map=mapping)
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 30, 0, 10), 'whole-frame')


def test_lock_group_transition_protection_including_complete_coverage():
    editor = sample()
    editor.set_track_edit_lock(0, True)
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 151, 0, 10), 'edit-locked')
    editor.set_track_edit_lock(0, False)
    editor.group_clips(['old', 'other'])
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 20, 0, 40), 'Ungroup')
    editor.ungroup_clips(['old'])
    editor.project.clips.append(Clip('adjacent', 'm', 0, 60, 110, 20))
    editor.project.video_transitions = [VideoTransition('transition', 'old', 'adjacent', 4)]
    editor.project.validate()
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 20, 0, 40), 'transition')


@pytest.mark.parametrize('mutate', [
    lambda e: e.move_clip('later', 91),
    lambda e: setattr(e._clip('later'), 'start', 91),
    lambda e: setattr(e._clip('old').grade, 'contrast', 1.1),
    lambda e: setattr(e.project.captions[0], 'text', 'Changed'),
    lambda e: setattr(e.project.overlays[0], 'start', 26),
    lambda e: setattr(e.project.media[1], 'path', '/missing/relinked.mov'),
    lambda e: setattr(e.project, 'fps_num', 25),
    lambda e: e.project.track_edit_locks.__setitem__(0, True),
    lambda e: setattr(e, 'project', deepcopy(e.project)),
])
def test_project_changes_including_direct_mutations_make_plan_stale(mutate):
    editor = sample()
    plan = editor.plan_source_overwrite('new', 0, 30, 0, 10)
    mutate(editor)
    rejected(editor, lambda: editor.apply_source_overwrite(plan), 'stale')


def test_plan_is_deeply_immutable_replay_session_and_tamper_protected():
    editor = sample()
    plan = editor.plan_source_overwrite('new', 0, 30, 0, 10)
    with pytest.raises(FrozenInstanceError):
        plan.start = 31
    with pytest.raises(FrozenInstanceError):
        plan.remnants[0].duration = 5
    clone = deepcopy(editor)
    rejected(clone, lambda: clone.apply_source_overwrite(plan), 'stale')
    for changed in (replace(plan, source_in=1, source_out=11),
                    replace(plan, new_clip_id='other'), replace(plan, remnants=()),
                    replace(plan, affected_clip_ids=()), replace(plan, candidate_signature='x')):
        rejected(editor, lambda: editor.apply_source_overwrite(changed))
    editor.apply_source_overwrite(plan)
    rejected(editor, lambda: editor.apply_source_overwrite(plan), 'stale')
    editor.undo()
    rejected(editor, lambda: editor.apply_source_overwrite(plan), 'stale')


def test_invalid_candidate_and_owner_thread_never_mutate():
    editor = sample()
    plan = editor.plan_source_overwrite('new', 0, 30, 0, 10)
    # A malformed detached ID cannot replace a live clip through a forged plan.
    changed = replace(plan, remnants=(plan.remnants[0], replace(plan.remnants[1], clip_id='later')))
    rejected(editor, lambda: editor.apply_source_overwrite(changed))
    errors = []
    def worker():
        for call in (lambda: editor.plan_source_overwrite('new', 0, 30, 0, 10),
                     lambda: editor.apply_source_overwrite(plan)):
            try:
                call()
            except ValueError as exc:
                errors.append(str(exc))
    before = state(editor)
    thread = Thread(target=worker)
    thread.start()
    thread.join()
    assert len(errors) == 2 and all('owning thread' in error for error in errors)
    assert state(editor) == before


def test_save_reopen_and_recovery_keep_new_ids_settings_and_schema(tmp_path):
    editor = sample(); bind_missing_project_paths(editor.project, tmp_path)
    editor.apply_source_overwrite(editor.plan_source_overwrite('new', 0, 30, 10, 30))
    path = tmp_path / 'overwrite.lumen'
    editor.project.save(path)
    assert Project.load(path).to_dict() == editor.project.to_dict()
    assert editor.project.to_dict()['version'] == VERSION == 43
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(editor.project, str(uuid4()), path)
        assert store.load(entry).to_dict() == editor.project.to_dict()
    finally:
        store.close()


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)])
@pytest.mark.parametrize('count', [1, 1599, 1600, 1601, 56000, 96000])
def test_complete_source_frames_uses_exact_pcm_floor_not_padded_project_tail(fps, count):
    provenance = SoundProvenance('test', 'procedural', 'CC0-1.0', 'a' * 64,
                                'b' * 64, 48000, count)
    media = Media('sound', '/missing/sound.wav', 'Sound', count / 48000,
                  has_video=False, has_audio=True, sound_provenance=provenance)
    frames = complete_source_frames(media, fps)
    assert frames == int(Fraction(count, 48000) * fps)
    editor = Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media]))
    rejected(editor, lambda: editor.plan_source_overwrite('sound', 2, 0, 0, frames + 1), 'complete source')
    if frames:
        editor.apply_source_overwrite(editor.plan_source_overwrite('sound', 2, 0, 0, frames))


def test_decoded_video_endpoint_never_rounds_up_or_uses_longer_embedded_audio():
    media = Media('m', '/missing/source.mov', 'Source', 10, 64, 48, True, True,
                  decoded_video_end_num=13, decoded_video_end_den=20)
    assert complete_source_frames(media, Fraction(30)) == 19
    editor = Editor(Project(media=[media]))
    rejected(editor, lambda: editor.plan_source_overwrite('m', 0, 0, 0, 20), 'complete source')
    editor.project.clips = [Clip('old', 'm', 0, 0, 0, 20)]
    rejected(editor, lambda: editor.plan_source_overwrite('m', 0, 0, 0, 10), 'partial source tail')


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
@pytest.mark.parametrize('has_audio', [False, True])
def test_exact_video_endpoint_preserves_last_complete_frame_despite_float_roundoff(fps, has_audio):
    endpoint = 100 / fps
    media = Media('m', '/missing/source.mov', 'Source', float(endpoint), 16, 16, True,
                  has_audio, fps.numerator, fps.denominator,
                  decoded_video_end_num=endpoint.numerator, decoded_video_end_den=endpoint.denominator)
    assert complete_source_frames(media, fps) == 100
    editor = Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media]))
    plan = editor.plan_source_overwrite('m', 0, 0, 99, 100)
    assert (plan.source_in, plan.source_out, plan.duration) == (99, 100, 1)
    editor.apply_source_overwrite(plan)
    rejected(editor, lambda: editor.plan_source_overwrite('m', 0, 1, 100, 101), 'complete source')


@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)])
def test_exact_partial_tail_is_never_rounded_to_a_complete_frame(fps):
    complete_endpoint = 100 / fps
    # Even an actual one-microsecond shortfall must not be repaired by an
    # epsilon. Float metadata is deliberately longer than the exact evidence.
    endpoint = complete_endpoint - Fraction(1, 1000000)
    media = Media('m', '/missing/source.mov', 'Source', float(complete_endpoint),
                  16, 16, True, False, fps.numerator, fps.denominator,
                  decoded_video_end_num=endpoint.numerator, decoded_video_end_den=endpoint.denominator)
    assert complete_source_frames(media, fps) == 99
    editor = Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media]))
    rejected(editor, lambda: editor.plan_source_overwrite('m', 0, 0, 99, 100), 'complete source')
    # Without decoded evidence, lossy metadata remains conservative. The fix
    # does not introduce heuristic rounding for ordinary duration-only files.
    media.decoded_video_end_num = media.decoded_video_end_den = None
    assert complete_source_frames(media, fps) == 99


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)])
def test_real_imported_100_frame_mov_supports_its_final_frame(tmp_path, fps):
    from lumen.media import ffmpeg_available, ffmpeg_binary, probe

    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe unavailable')
    path = tmp_path / 'complete.mov'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                    f'testsrc=size=16x16:rate={fps.numerator}/{fps.denominator}',
                    '-frames:v', '100', '-c:v', 'png', '-pix_fmt', 'rgb24',
                    '-threads', '1', str(path)], check=True, capture_output=True, timeout=15)
    media = probe(path)
    endpoint = Fraction(media.decoded_video_end_num, media.decoded_video_end_den)
    assert endpoint * fps == 100
    assert complete_source_frames(media, fps) == 100
    editor = Editor(Project(width=16, height=16, fps_num=fps.numerator,
                            fps_den=fps.denominator, media=[media]))
    before = deepcopy(editor.project)
    clip = editor.apply_source_overwrite(editor.plan_source_overwrite(media.id, 0, 0, 99, 100))
    assert clip.source_in == 99 and clip.source_out == 100 and clip.duration == 1
    assert editor.undo() and editor.project == before
    whole = editor.apply_source_overwrite(editor.plan_source_overwrite(media.id, 0, 0, 0, 100))
    assert whole.duration == 100


def pcm_at(plan, sample):
    if plan.silent:
        return None
    local = sample - plan.start_sample - plan.leading_samples
    if 0 <= local < plan.source_end_sample - plan.source_start_sample:
        return plan.source_start_sample + local
    return None


@pytest.mark.parametrize('offset', [0, .001, -.001, 2.1, 2.5])
@pytest.mark.parametrize('source_in', [60, 61, 62, 63, 64])
@pytest.mark.parametrize('cut', [30, 31, 32, 33, 34])
def test_fractional_rate_retained_pcm_matches_original_or_fails_closed(offset, source_in, cut):
    editor = sample(fps=Fraction(30000, 1001))
    editor.project.media[0].audio_offset = offset
    original = editor._clip('old')
    original.source_in = source_in
    old_audio = compile_audio_timing(original, editor.project.fps, offset)
    before = state(editor)
    try:
        plan = editor.plan_source_overwrite('new', 0, cut, 0, 10)
    except ValueError as exc:
        assert 'sample-grid boundary' in str(exc)
        assert state(editor) == before
        return
    editor.apply_source_overwrite(plan)
    for remnant in plan.remnants:
        audio = compile_audio_timing(editor._clip(remnant.clip_id), editor.project.fps, offset)
        # Exhaustive 48 kHz indices, comparing against the original continuous
        # clip, not an oracle reconstructed from the altered remnant timing.
        assert all(pcm_at(audio, s) == pcm_at(old_audio, s)
                   for s in range(audio.start_sample, audio.start_sample + audio.sample_count))


def test_known_one_sample_phase_mismatch_rejects_but_muted_clip_can_cut():
    editor = sample(fps=Fraction(30000, 1001))
    editor._clip('old').source_in = 61
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 31, 0, 10), 'sample-grid')
    editor._clip('old').muted = True
    editor.apply_source_overwrite(editor.plan_source_overwrite('new', 0, 31, 0, 10))


def test_later_unsafe_clip_rejects_the_whole_multiclip_operation():
    editor = sample()
    editor._clip('later').fade_out = 2
    rejected(editor, lambda: editor.plan_source_overwrite('new', 0, 30, 0, 70), 'cannot cut')


def test_unrelated_locked_track_is_retained_and_apply_uses_one_central_commit(monkeypatch):
    editor = sample()
    editor.set_track_edit_lock(1, True)
    old = editor._clip('other')
    calls = []
    central_commit = editor._commit
    def observed_commit(change):
        calls.append(change)
        return central_commit(change)
    monkeypatch.setattr(editor, '_commit', observed_commit)
    plan = editor.plan_source_overwrite('new', 0, 30, 0, 10)
    assert not calls
    editor.apply_source_overwrite(plan)
    assert len(calls) == 1 and len(editor._undo) == 2
    assert editor._clip('other') is old and editor.project.track_edit_locks[1]


def test_picture_source_positions_outside_target_are_unchanged():
    editor = sample(audio=False)
    old = deepcopy(editor.project)
    plan = editor.plan_source_overwrite('new', 0, 7, 11, 106)
    editor.apply_source_overwrite(plan)
    def picture(project, track, frame):
        matches = [c for c in project.clips if c.track == track and c.start <= frame < c.end]
        if not matches:
            return None
        clip = matches[0]
        return clip.media_id, clip.source_in + frame - clip.start, clip.grade
    for track in (0, 1):
        for frame in range(160):
            if track == plan.track and plan.start <= frame < plan.end:
                actual = picture(editor.project, track, frame)
                assert actual[:2] == ('new', plan.source_in + frame - plan.start)
            else:
                assert picture(editor.project, track, frame) == picture(old, track, frame)
