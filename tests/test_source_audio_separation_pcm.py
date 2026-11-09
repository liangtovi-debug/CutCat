"""Independent, real f32 PCM and lossless-picture audio-separation oracles.

The schema39 JSON and mixer fixture were captured from accepted 0864f6aa before
schema40 work. The old mixer never imports audio_components or AudioMixBuilder.
Old isolated contributions are placed with independent rational boundaries and
summed in their OLD track/start/id order, not regrouped or decoded through AAC.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from lumen.audio_mix import AudioMixBuilder, compile_audio_mix
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.timing import ClipTimeMap
from schema39_audio_mix_fixture import compile_audio_mix as compile_schema39
from test_audio_mix import _pcm
from test_gap_close_render import gap_av_input, lossless_scene
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                              engine, ff, make_source)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')
RATES = [Fraction(24), Fraction(24000, 1001), Fraction(30000, 1001), Fraction(60000, 1001)]
RATE_IDS = ['24', '23.976', '29.97', '59.94']


def boundary(frame, fps):
    value = Fraction(frame * 48000) / fps
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pcm(project, *, legacy=False):
    build = compile_schema39 if legacy else compile_audio_mix
    result = build(project, {m.id: m for m in project.media})
    return _pcm(result.inputs, result.graph)


def samples(raw):
    return np.frombuffer(raw, dtype='<f4').reshape(-1, 2)


def separated(editor, clip_id='v0', track=3):
    from lumen.source_audio_separation import (plan_source_audio_separation,
        verify_source_audio_separation, apply_source_audio_separation)
    plan = plan_source_audio_separation(editor, clip_id, track)
    preview = verify_source_audio_separation(plan)
    verified = verify_source_audio_separation(plan, previous=preview)
    return apply_source_audio_separation(editor, plan, verified)


def independent_old_sum(project):
    """Decode old individual contributions; do placement/summing ourselves.

    No new gate, anchor, ordering function, edited clip, sample-boundary helper
    or current mix compiler enters this oracle. The old local gain/fade/retime
    pipeline is deliberately retained rather than claiming to reimplement R2.
    """
    result = np.zeros((boundary(project.duration_frames, project.fps), 2), np.float32)
    for clip in sorted(project.clips, key=lambda c: (c.track, c.start, c.id)):
        solo = deepcopy(project)
        solo.master_gain_db = 0
        solo.clips = [deepcopy(clip)]
        solo.clip_groups = []; solo.video_transitions = []
        solo.captions = []; solo.overlays = []
        begin = boundary(clip.start, project.fps)
        contribution = samples(pcm(solo, legacy=True))[begin:]
        assert len(contribution) == boundary(clip.end, project.fps) - begin
        result[begin:begin + len(contribution)] += contribution
    if project.master_gain_db:
        # Independent numeric post-sum multiplier, rounded to float32 as in
        # FFmpeg float volume, using the old compiler's decimal boundary.
        db = float(format(float(project.master_gain_db), '.12g'))
        result *= np.float32(10 ** (db / 20))
    return result.astype('<f4').tobytes()


def constant_av(root, name, left, right):
    path = root / f'{name}.mkv'
    ff('-f', 'lavfi', '-i', 'color=red:s=16x16:r=24:d=0.5',
       '-f', 'lavfi', '-i', f'aevalsrc={left}|{right}:s=48000:d=0.5',
       '-map', '0:v', '-map', '1:a', '-c:v', 'ffv1', '-c:a', 'pcm_f32le', path)
    return probe(path)


def test_real_three_contribution_cancellation_requires_historical_mix_slot(tmp_path):
    tiny = 2 ** -25
    media = [constant_av(tmp_path, 'positive', 1, -1),
             constant_av(tmp_path, 'negative', -1, 1),
             constant_av(tmp_path, 'tiny', tiny, -tiny)]
    project = Project(width=16, height=16, fps_num=24, media=media, clips=[
        Clip('v0', media[0].id, 0, 0, 0, 6),
        Clip('v1', media[1].id, 1, 0, 0, 6),
        Clip('a0', media[2].id, 2, 0, 0, 6)])
    hashes = {m.path: digest(m.path) for m in media}
    files = set(tmp_path.iterdir())
    before = deepcopy(project)
    old = pcm(before, legacy=True)
    np.testing.assert_array_equal(samples(old), np.tile([tiny, -tiny], (12000, 1)))
    assert independent_old_sum(before) == old
    editor = Editor(project)
    audio = separated(editor)
    assert audio.media_id == before.clips[0].media_id and len(project.media) == 3
    actual = pcm(editor.project)
    assert actual == old  # Every f32 byte, including both opposite-phase channels.
    naive = deepcopy(editor.project)
    next(c for c in naive.clips if c.id == audio.id).audio_mix_anchor = None
    damaged = samples(pcm(naive))
    assert np.count_nonzero(damaged) == 0
    assert np.count_nonzero(damaged != samples(old)) == 24000
    # The shared list still orders picture/input traversal historically; only
    # final amix labels can be in the captured handoff order.
    builder = AudioMixBuilder(editor.project)
    assert [c.id for c in builder.clips] == ['v0', 'v1', 'a0', audio.id]
    assert '[silence][a3][a1][a2]amix=' in compile_audio_mix(
        editor.project, {m.id: m for m in media}).graph
    assert set(tmp_path.iterdir()) == files, 'Separation must not extract an audio asset'
    assert {m.path: digest(m.path) for m in media} == hashes


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['1x-partial', 'retimed-partial'])
def test_exact_old_contribution_oracle_signed_offsets_fades_partial_tails_and_master(
        tmp_path, fps, offset, speed):
    a = gap_av_input(tmp_path, fps, offset, 9701)
    b = gap_av_input(tmp_path, fps, -.113 if offset > 0 else .137, 9702)
    mapping = ClipTimeMap(Fraction(9, 8), Fraction(9, 8) + Fraction(19, 2) * speed, speed)
    editor = Editor(Project(width=16, height=16, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[a, b], master_gain_db=-3.25, clips=[
            Clip('v0', a.id, 0, 2, 0, 10, time_map=mapping,
                 gain_db=-4, fade_in=1, fade_out=3),
            Clip('v1', b.id, 1, 3, 4, 9, gain_db=-7, fade_in=2, fade_out=1),
            Clip('a0', b.id, 2, 5, 3, 10, gain_db=-5, fade_in=2, fade_out=1),
            Clip('muted-tail', a.id, 3, 18, 10, 3, muted=True)]))
    before = deepcopy(editor.project)
    hashes = {m.path: digest(m.path) for m in before.media}
    files = set(tmp_path.iterdir())
    old = pcm(before, legacy=True)
    expected = independent_old_sum(before)
    assert expected == old, 'Independent old-order contribution placement must match real f32 sum'
    assert np.any(samples(old)[:, 0] != samples(old)[:, 1])
    assert np.count_nonzero(samples(old)) > 0
    original = deepcopy(before.clips[0])
    audio = separated(editor)
    assert audio.timing == original.timing and audio.time_map == original.time_map
    assert (audio.gain_db, audio.muted, audio.fade_in, audio.fade_out) == (
        original.gain_db, original.muted, original.fade_in, original.fade_out)
    assert next(c for c in editor.project.clips if c.id == 'v0') == replace(
        original, source_audio_enabled=False)
    assert editor.project.media == before.media and set(tmp_path.iterdir()) == files
    assert pcm(editor.project) == expected
    assert len(expected) == boundary(21, fps) * 8
    assert not any(expected[boundary(15, fps) * 8:])
    saved = tmp_path / 'separated.lumen'
    editor.project.save(saved)
    assert Project.load(saved) == editor.project
    assert pcm(Project.load(saved)) == expected
    after = deepcopy(editor.project)
    assert len(editor._undo) == 1 and editor.undo() and editor.project == before
    assert pcm(editor.project) == old
    assert editor.redo() and editor.project == after
    assert pcm(editor.project) == expected
    assert {m.path: digest(m.path) for m in before.media} == hashes


def test_actual_schema39_fixture_migrates_without_graph_pcm_or_picture_drift(tmp_path, engine):
    fixture = Path(__file__).parent / 'fixtures/schema39-before-source-audio-separation.json'
    document = json.loads(fixture.read_text())
    assert document['version'] == 39
    assert all('source_audio_enabled' not in c and 'audio_mix_anchor' not in c
               for c in document['clips'])
    untouched = deepcopy(document)
    migrated = Project.from_dict(document)
    assert document == untouched
    assert all(c.source_audio_enabled and c.audio_mix_anchor is None for c in migrated.clips)
    for index, old in enumerate(migrated.media):
        fresh = gap_av_input(tmp_path, migrated.fps, old.audio_offset, 9801 + index)
        migrated.media[index] = replace(fresh, id=old.id, name=old.name)
    before = deepcopy(migrated)
    checked = {m.id: m for m in migrated.media}
    old = compile_schema39(migrated, checked)
    new = compile_audio_mix(migrated, checked)
    assert (new.inputs, new.graph, new.end_sample, new.source_ids) == (
        old.inputs, old.graph, old.end_sample, old.source_ids)
    expected = _pcm(old.inputs, old.graph)
    assert _pcm(new.inputs, new.graph) == expected == independent_old_sum(migrated)
    # A normal save/load must not invent a handoff or alter output.
    restored = Project.from_dict(migrated.to_dict())
    assert pcm(restored) == expected
    inputs, graph, _ = engine._compile(migrated)
    assert _pcm(inputs, graph, video_output=os.devnull) == expected
    def actual_scene(project):
        inputs, graph, _ = engine._compile(project)
        raw = ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
            '-map', '[vout]', '-an', '-r', str(project.fps), '-fps_mode', 'cfr',
            '-c:v', 'rawvideo', '-pix_fmt', 'rgb24',
            '-threads', '1', '-f', 'rawvideo', 'pipe:1',
            '-map', '[aout]', '-vn', '-f', 'null', os.devnull)
        return np.frombuffer(raw, np.uint8).reshape(-1, project.height, project.width, 3)
    old_pixels = actual_scene(migrated)
    new_pixels = actual_scene(restored)
    np.testing.assert_array_equal(new_pixels, old_pixels)
    assert len(new_pixels) == migrated.duration_frames
    assert migrated == before


@pytest.mark.parametrize('state', ['muted', 'outside-audio', 'zero-source'])
def test_muted_and_structurally_silent_components_keep_exact_silence(tmp_path, state):
    media = constant_av(tmp_path, 'silent-case', 0 if state == 'zero-source' else .3,
                        0 if state == 'zero-source' else -.2)
    if state == 'outside-audio':
        media.audio_offset = 2
    editor = Editor(Project(width=16, height=16, fps_num=24, media=[media],
        clips=[Clip('v0', media.id, 0, 1, 0, 6, muted=state == 'muted')]))
    old = pcm(editor.project, legacy=True)
    assert old == bytes(boundary(7, Fraction(24)) * 8)
    audio = separated(editor)
    assert pcm(editor.project) == old
    if state == 'muted':
        editor.update_clip('v0', muted=False)
        assert pcm(editor.project) == old, 'User unmute cannot reopen suppressed V audio'
        editor.update_clip(audio.id, muted=False)
        audible = pcm(editor.project)
        assert np.count_nonzero(samples(audible)) > 0
        before = deepcopy(editor.project)
        # The separate A is the only emitter; removing V cannot change PCM.
        editor.delete_clip('v0')
        assert pcm(editor.project) == audible
        assert editor.undo() and editor.project == before
        editor.delete_clip(audio.id)
        assert pcm(editor.project) == old, 'Deleting A must never restore V audio'


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
def test_numbered_lossless_picture_and_full_export_audio_unchanged(tmp_path, engine, fps):
    source = make_source(tmp_path / 'numbered.mkv', fps)
    av_path = tmp_path / 'numbered-with-audio.mkv'
    ff('-i', source.media.path, '-f', 'lavfi', '-i',
       'aevalsrc=0.21*sin(2*PI*431*t)|0.13*sin(2*PI*991*t):s=48000:d=4',
       '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_f32le', av_path)
    media = probe(av_path)
    assert media.has_video and media.has_audio
    np.testing.assert_array_equal(decode(av_path), source.original)
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], master_gain_db=-2, clips=[
            Clip('v0', media.id, 0, 2, 0, 6,
                 time_map=ClipTimeMap(Fraction(9, 8), Fraction(73, 8), Fraction(3, 2)),
                 gain_db=-4, fade_in=1),
            Clip('v1', media.id, 1, 4, 30, 3, gain_db=-8, fade_out=1)]))
    before = deepcopy(editor.project)
    source_hash = digest(av_path)
    old_pixels = lossless_scene(engine, before, tmp_path / 'before.mkv')
    expected = np.zeros((8, HEIGHT, WIDTH, 3), np.uint16)
    expected[2:8] = source.original[[math.floor(Fraction(9, 8) + k * Fraction(3, 2))
                                   for k in range(6)]]
    expected[4:7] = source.original[[30, 31, 32]]
    assert_delivery_frames(old_pixels, expected)
    old_pcm = pcm(before, legacy=True)
    separated(editor)
    new_pixels = lossless_scene(engine, editor.project, tmp_path / 'after.mkv')
    np.testing.assert_array_equal(new_pixels, old_pixels)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == old_pcm == pcm(editor.project)
    assert digest(av_path) == source_hash


def test_analysis_preview_export_use_same_separated_float_mix(tmp_path, engine, monkeypatch):
    from lumen import audio_analysis as aa
    media = gap_av_input(tmp_path, Fraction(30000, 1001), .137, 9901)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], master_gain_db=-3.25, clips=[
            Clip('v0', media.id, 0, 1, 0, 20, gain_db=-4, fade_in=2, fade_out=3)]))
    before = deepcopy(editor.project)
    old = pcm(before, legacy=True)
    result_before = aa.analyze_audio(before)
    separated(editor)
    result_after = aa.analyze_audio(editor.project)
    assert result_after.sample_count == result_before.sample_count == len(old) // 8
    assert result_after.sample_peaks == result_before.sample_peaks
    assert result_after.estimated_true_peaks == result_before.estimated_true_peaks
    assert result_after.integrated_lufs == result_before.integrated_lufs
    assert result_after.fingerprint != result_before.fingerprint
    graphs = []
    original = engine._compile
    def capture(*args, **kwargs):
        value = original(*args, **kwargs)
        graphs.append(value)
        return value
    monkeypatch.setattr(engine, '_compile', capture)
    engine.preview(editor.project)
    engine.render(editor.project, tmp_path / 'delivery.mp4')
    assert len(graphs) == 2
    for inputs, graph, _ in graphs:
        assert _pcm(inputs, graph, video_output=os.devnull) == old
