"""Independent scalar/frozen-renderer oracles for sample-clock gain envelopes.

The reference first captures the accepted schema40 local audio BEFORE the new
envelope, without fades/master.  A plain Python sample loop then evaluates dB
segments; float32 rounding, fades, global placement, mix order and master are
performed independently.  The new compiler/evaluator never produce expected
values.  Rubber Band itself is deliberately not reimplemented: independently
retimed splits are not claimed to be lossless or phase-continuous.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import math
import os
import shutil

import numpy as np
import pytest

from lumen.audio_gain import AudioGainKeyframe as Key, MAX_AUDIO_GAIN_SAMPLE
from lumen.audio_mix import compile_audio_mix
from lumen.media import ffmpeg_available, probe
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap
from schema40_audio_mix_fixture import compile_audio_mix as frozen_mix
from test_audio_mix import _pcm
from test_gap_close_render import gap_av_input, lossless_scene
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, engine,
                              ff, make_source)
from test_source_audio_reattach_pcm import reattach
from test_source_audio_separation_pcm import (boundary, constant_av, digest,
                                             pcm, samples, separated)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


@pytest.fixture
def media_root(tmp_path):
    # Own and remove only this test's tiny synthetic media.  This module is
    # intentionally safe to run in the space-constrained acceptance workspace.
    root = tmp_path / 'gain-pcm-media'
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def raw_source(root, count=24000, *, name='distinct-stereo'):
    t = np.arange(count, dtype=np.float64)
    source = np.column_stack((.7 * np.sin(t * .137) + .12,
                              .3 * np.cos(t * .073) - .08)).astype('<f4')
    source[::1024, 0] = .75
    source[1023::1024, 1] = -.625
    path = root / (name + '.wav')
    ff('-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0',
       '-c:a', 'pcm_f32le', path, data=source.tobytes())
    return probe(path)


def scalar_offsets(keys, count):
    """Intentionally linear search and direct scalar math, not production code."""
    values = []
    for n in range(count):
        db = float(keys[0].offset_db) if keys else 0.0
        for index, key in enumerate(keys):
            if n < key.sample:
                break
            db = float(key.offset_db)
            if index + 1 < len(keys):
                following = keys[index + 1]
                if n < following.sample and key.interpolation == 'linear':
                    db += (following.offset_db - key.offset_db) * (
                        (n - key.sample) / (following.sample - key.sample))
        values.append(math.pow(10.0, db / 20.0))
    return np.asarray(values, dtype=np.float64)


def independent_pcm(project):
    """Frozen old signal, then independent envelope/fade/sum/master math."""
    result = np.zeros((boundary(project.duration_frames, project.fps), 2), np.float32)
    # This is the documented historical slot order, not an imported helper.
    def order(clip):
        anchor = clip.audio_mix_anchor
        return ((anchor.track, anchor.start, anchor.clip_id) if anchor else
                (clip.track, clip.start, clip.id)), clip.start, clip.id
    media_by_id = {item.id: item for item in project.media}
    for clip in sorted(project.clips, key=order):
        if clip.muted or not clip.source_audio_enabled or not media_by_id[clip.media_id].has_audio:
            continue
        begin = boundary(clip.start, project.fps)
        count = boundary(clip.end, project.fps) - begin
        solo = deepcopy(project)
        solo.clips = [replace(deepcopy(clip), fade_in=0, fade_out=0,
                              audio_gain_keyframes=[])]
        solo.captions = []; solo.overlays = []; solo.video_transitions = []
        solo.clip_groups = []; solo.master_gain_db = 0
        old = frozen_mix(solo, media_by_id)
        # Capture the complete old local post-basegain signal, not a newly
        # compiled envelope, or an expected signal derived from the edit.
        local = samples(_pcm(old.inputs, old.graph))[begin:].copy()
        assert local.shape == (count, 2)
        if clip.audio_gain_keyframes:
            gains = scalar_offsets(clip.audio_gain_keyframes, count)
            local = (local.astype(np.float64) * gains[:, None]).astype(np.float32)
        if clip.fade_in:
            length = boundary(clip.start + clip.fade_in, project.fps) - begin
            gain = np.minimum(np.arange(count, dtype=np.float64) / length, 1.0)
            local = (local.astype(np.float64) * gain[:, None]).astype(np.float32)
        if clip.fade_out:
            start = boundary(clip.end - clip.fade_out, project.fps) - begin
            length = count - start
            gain = np.clip((count - np.arange(count, dtype=np.float64)) / length, 0, 1)
            local = (local.astype(np.float64) * gain[:, None]).astype(np.float32)
        result[begin:begin + count] += local
    if project.master_gain_db:
        rounded_db = float(format(float(project.master_gain_db), '.12g'))
        result *= np.float32(math.pow(10.0, rounded_db / 20.0))
    return result.astype('<f4').tobytes()


def assert_scalar_reference(project):
    expected = independent_pcm(deepcopy(project))
    actual = pcm(project)
    assert len(actual) == len(expected)
    # Every sample is compared.  The 1-ULP allowance is solely for libm's pow
    # implementation; it is never a temporal/AAC/downmix or block-size tolerance.
    np.testing.assert_array_max_ulp(samples(actual), samples(expected), maxulp=1)
    assert np.any(samples(actual)[:, 0] != samples(actual)[:, 1])
    return actual


@pytest.mark.parametrize('edge', [1, 1023, 1024, 1025, 2048], ids=str)
def test_single_sample_hold_steps_do_not_quantize_to_audio_frames(media_root, edge):
    media = raw_source(media_root)
    keys = [Key(-11, -6, 'hold'), Key(edge, 6, 'hold'),
            Key(edge + 1, -18, 'hold'), Key(edge + 2, 24, 'hold')]
    clip = Clip('audio', media.id, 2, 2, 0, 6, audio_gain_keyframes=keys)
    project = Project(fps_num=24, media=[media], clips=[clip])
    before = deepcopy(project); hashed = digest(media.path)
    actual = samples(assert_scalar_reference(project))[4000:]
    old = deepcopy(project); old.clips[0].audio_gain_keyframes = []
    flat = samples(pcm(old))[4000:]
    # Explicit neighbors make a displaced or frame-quantized step impossible
    # to hide behind a global floating-point error statistic.
    for n, db in [(edge - 1, -6), (edge, 6), (edge + 1, -18), (edge + 2, 24)]:
        expected = (flat[n].astype(np.float64) * math.pow(10, db / 20)).astype(np.float32)
        np.testing.assert_array_max_ulp(actual[n], expected, maxulp=1)
    assert np.max(np.abs(actual)) > 1.0, 'There is no hidden limiter or automatic gain clamp'
    assert project == before and digest(media.path) == hashed


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001), Fraction(60000, 1001)],
                         ids=['24', '29.97', '59.94'])
def test_linear_db_hidden_handles_cross_blocks_and_global_fractional_phase(media_root, fps):
    media = raw_source(media_root)
    keys = [Key(-307, -24), Key(1023, 9), Key(2051, -17), Key(18000, 6)]
    clip = Clip('audio', media.id, 2, 1, 0, 7, gain_db=3.25,
                audio_gain_keyframes=keys)
    project = Project(fps_num=fps.numerator, fps_den=fps.denominator,
                      media=[media], clips=[clip])
    actual = assert_scalar_reference(project)
    assert len(actual) == boundary(8, fps) * 8
    assert actual[:boundary(1, fps) * 8] == bytes(boundary(1, fps) * 8)
    # 29.97/59.94 expose a global endpoint budget which need not equal the
    # duration rounded in isolation.  No frame-to-sample snapping is used.
    assert len(samples(actual)[boundary(1, fps):]) == boundary(8, fps) - boundary(1, fps)


def test_all_128_keys_and_large_signed_hidden_handles_have_scalar_pcm_reference(media_root):
    media = raw_source(media_root)
    keys = [Key(-100 + index * 188, -37.125 + (index % 9) * 6.375,
                'hold' if index % 3 == 0 else 'linear') for index in range(128)]
    clip = Clip('audio', media.id, 2, 1, 0, 12, gain_db=3.125,
                audio_gain_keyframes=keys)
    project = Project(fps_num=24, media=[media], clips=[clip])
    actual = assert_scalar_reference(project)
    assert np.count_nonzero(samples(actual)) > 0
    # Wide signed handles exercise integer subtraction and double conversion;
    # they must not be truncated to 32 bits or dropped as invisible UI points.
    clip.audio_gain_keyframes = [Key(-MAX_AUDIO_GAIN_SAMPLE, -48),
                                Key(MAX_AUDIO_GAIN_SAMPLE, 24)]
    assert_scalar_reference(project)


@pytest.mark.parametrize('block', [17, 511, 2048], ids=str)
def test_envelope_is_invariant_to_audio_input_block_partition(media_root, block):
    media = raw_source(media_root)
    clip = Clip('audio', media.id, 2, 1, 0, 6, gain_db=-2.125,
        audio_gain_keyframes=[Key(-13, -17), Key(1023, 8, 'hold'),
                              Key(1024, -9), Key(6147, 4)])
    project = Project(fps_num=24, media=[media], clips=[clip])
    compiled = compile_audio_mix(project, {media.id: media})
    expected = assert_scalar_reference(project)
    needle = 'asetpts=N/SR/TB,volume='
    assert compiled.graph.count(needle) == 1
    partitioned = compiled.graph.replace(needle,
        f'asetpts=N/SR/TB,asetnsamples=n={block}:p=0,volume=')
    assert _pcm(compiled.inputs, partitioned) == expected


@pytest.mark.parametrize('offset', [.137, -.113], ids=['leading-silence', 'negative-offset'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['normal', 'retimed'])
def test_offsets_retime_basegain_fades_and_master_follow_output_sample_clock(media_root, offset, speed):
    fps = Fraction(30000, 1001)
    media = gap_av_input(media_root, fps, offset, 42031)
    mapping = ClipTimeMap(Fraction(1, 8), Fraction(1, 8) + 9 * speed, speed)
    clip = Clip('video', media.id, 0, 1, 0, 9, time_map=mapping,
                gain_db=4.125, fade_in=1, fade_out=2,
                audio_gain_keyframes=[Key(-99, -23), Key(1024, 10),
                                      Key(4800, -15, 'hold'), Key(6213, 7), Key(24000, -7)])
    project = Project(width=16, height=16, fps_num=fps.numerator, fps_den=fps.denominator,
                      media=[media], clips=[clip], master_gain_db=-5.75)
    hashed = digest(media.path)
    actual = samples(assert_scalar_reference(project))
    assert np.count_nonzero(actual) > 0
    if offset > 0:
        onset = round((float(Fraction(clip.start) / fps) +
                       (offset - float(mapping.source_in / fps)) / float(speed)) * 48000)
        assert np.count_nonzero(actual[:onset]) == 0
        assert np.count_nonzero(actual[onset:onset + 100]) > 0
    assert digest(media.path) == hashed


def test_real_short_audio_eof_padding_uses_clip_clock_and_stays_silent(media_root):
    audio = raw_source(media_root, 7000)
    av = media_root / 'video-outlasts-audio.mkv'
    ff('-f', 'lavfi', '-i', 'color=blue:s=16x16:r=24:d=1', '-i', audio.path,
       '-map', '0:v', '-map', '1:a', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', av)
    media = probe(av)
    clip = Clip('av', media.id, 0, 1, 0, 12,
                audio_gain_keyframes=[Key(0, -9), Key(7000, 24), Key(12000, -96)])
    project = Project(width=16, height=16, fps_num=24, media=[media], clips=[clip])
    actual = samples(assert_scalar_reference(project))
    assert np.count_nonzero(actual[2000:9000]) > 0
    assert np.count_nonzero(actual[9000:]) == 0


@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['normal', 'retimed'])
def test_empty_envelope_exact_legacy_graph_and_pcm_no_extra_format(media_root, speed):
    fps = Fraction(30000, 1001)
    media = gap_av_input(media_root, fps, .137, 42032)
    clip = Clip('av', media.id, 0, 2, 0, 8,
                time_map=ClipTimeMap(Fraction(1, 8), Fraction(1, 8) + 8 * speed, speed),
                gain_db=-3.5, fade_in=2, fade_out=1)
    project = Project(width=16, height=16, fps_num=fps.numerator, fps_den=fps.denominator,
                      media=[media], clips=[clip], master_gain_db=-2.25)
    checked = {media.id: media}
    old = frozen_mix(project, checked); new = compile_audio_mix(project, checked)
    assert (new.inputs, new.graph, new.end_sample, new.source_ids) == (
        old.inputs, old.graph, old.end_sample, old.source_ids)
    assert 'aeval' not in new.graph
    assert _pcm(new.inputs, new.graph) == _pcm(old.inputs, old.graph)


@pytest.mark.parametrize('state', ['muted', 'disabled', 'no-audio', 'late-source'])
def test_keys_never_reenable_or_decode_inactive_sources(state, monkeypatch):
    media = Media('missing', '/no-such-audio.wav', 'Absent', 4,
                  has_video=True, has_audio=state != 'no-audio',
                  audio_offset=20 if state == 'late-source' else 0)
    clip = Clip('c', media.id, 0, 1, 0, 5, muted=state == 'muted',
                source_audio_enabled=state != 'disabled',
                audio_gain_keyframes=[Key(0, 24), Key(1, -96, 'hold')])
    project = Project(width=16, height=16, fps_num=24, media=[media], clips=[clip])
    def forbidden(*args, **kwargs):
        pytest.fail('A silent source reached envelope compilation/decoding')
    monkeypatch.setattr('lumen.audio_mix.compile_audio_gain_filters', forbidden)
    compiled = compile_audio_mix(project, {media.id: media})
    assert compiled.inputs == () and compiled.source_ids == ()
    assert 'aeval' not in compiled.graph and '[0:a:0]' not in compiled.graph
    assert _pcm(compiled.inputs, compiled.graph) == bytes(boundary(6, Fraction(24)) * 8)
    assert clip.source_audio_enabled is (state != 'disabled')


def test_historical_anchor_changes_sum_slot_not_local_envelope_clock(media_root):
    a = constant_av(media_root, 'plus', 1, -1)
    b = constant_av(media_root, 'minus', -1, 1)
    tiny = constant_av(media_root, 'tiny', 2 ** -25, -2 ** -25)
    keys = [Key(-1, -6, 'hold'), Key(1024, 0, 'hold'), Key(2049, 6, 'hold')]
    project = Project(width=16, height=16, fps_num=24, media=[a, b, tiny], clips=[
        Clip('plus', a.id, 0, 1, 0, 6), Clip('minus', b.id, 1, 1, 0, 6),
        Clip('tiny', tiny.id, 2, 1, 0, 6, audio_gain_keyframes=keys,
             audio_mix_anchor=AudioMixAnchor(3, 1000000000, 'historical-deleted'))])
    actual = assert_scalar_reference(project)
    relocated_anchor = deepcopy(project)
    relocated_anchor.clips[2].audio_mix_anchor = AudioMixAnchor(3, 0, 'different-history')
    assert pcm(relocated_anchor) == actual, 'Anchor history must never set sample zero'
    first = boundary(1, Fraction(24))
    assert samples(actual)[first + 1024, 0] == np.float32(2 ** -25)
    assert samples(actual)[first + 1023, 0] != samples(actual)[first + 1024, 0]


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
def test_real_separate_edit_current_audio_envelope_reattach_mix_exact_history(media_root, fps):
    media = gap_av_input(media_root, fps, .137, 42033)
    original_keys = [Key(-13, -7), Key(1024, 6), Key(19000, -5)]
    editor = Editor(Project(width=16, height=16, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], master_gain_db=-2.5, clips=[
            Clip('v0', media.id, 0, 1, 0, 9, gain_db=-3, fade_in=1,
                 audio_gain_keyframes=original_keys),
            Clip('v1', media.id, 1, 2, 2, 8, gain_db=-9)]))
    source_hash = digest(media.path)
    before = deepcopy(editor.project)
    independent_before = assert_scalar_reference(before)
    audio = separated(editor)
    assert audio.audio_gain_keyframes == original_keys
    assert pcm(editor.project) == independent_before
    current_keys = [Key(-999, -19, 'hold'), Key(1023, 8),
                    Key(5301, -11), Key(26000, 3)]
    editor.update_clip(audio.id, audio_gain_keyframes=current_keys, gain_db=-5.125)
    captured = deepcopy(editor.project)
    expected = assert_scalar_reference(captured)
    result = reattach(editor, audio=audio.id)
    assert result.audio_gain_keyframes == current_keys != original_keys
    assert result.gain_db == -5.125 and result.source_audio_enabled
    assert pcm(editor.project) == expected
    restored = Project.from_dict(editor.project.to_dict())
    assert pcm(restored) == expected
    assert editor.undo() and editor.project == captured and pcm(editor.project) == expected
    assert editor.redo() and pcm(editor.project) == expected
    assert digest(media.path) == source_hash


def test_gain_envelope_does_not_change_numbered_picture_or_full_export_audio(media_root, engine):
    fps = Fraction(30000, 1001)
    source = make_source(media_root / 'numbered.mkv', fps)
    av = media_root / 'numbered-av.mkv'
    ff('-i', source.media.path, '-f', 'lavfi', '-i',
       'aevalsrc=0.21*sin(2*PI*431*t)|0.13*sin(2*PI*991*t):s=48000:d=4',
       '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_f32le', av)
    media = probe(av)
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], clips=[
            Clip('v0', media.id, 0, 2, 3, 6, gain_db=-2, fade_out=1)]))
    source_hash = digest(av)
    old_pixels = lossless_scene(engine, editor.project, media_root / 'before.mkv')
    expected_pixels = np.zeros((8, HEIGHT, WIDTH, 3), np.uint16)
    expected_pixels[2:8] = source.original[3:9]
    assert_delivery_frames(old_pixels, expected_pixels)
    editor.update_clip('v0', audio_gain_keyframes=[Key(-301, -10), Key(1025, 9), Key(23000, -8)])
    expected_audio = assert_scalar_reference(editor.project)
    audio = separated(editor)
    editor.update_clip(audio.id, audio_gain_keyframes=[Key(-321, -18), Key(1023, 7), Key(24000, -3)])
    expected_audio = assert_scalar_reference(editor.project)
    reattach(editor, audio=audio.id)
    new_pixels = lossless_scene(engine, editor.project, media_root / 'after.mkv')
    np.testing.assert_array_equal(new_pixels, old_pixels)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == expected_audio == pcm(editor.project)
    assert digest(av) == source_hash
