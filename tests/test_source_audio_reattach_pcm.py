"""Real f32 whole-mix / numbered-picture strict audio recombination oracles.

Expected audio is captured BEFORE the new edit with the frozen accepted schema40
compiler. It is never rebuilt from retained V settings or a proposed new anchor.
Picture IDs are independently selected from source arrays. No AAC tolerance is
used for the audio contract, and all original files are hash checked.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

from lumen.audio_mix import AudioMixBuilder, compile_audio_mix
from lumen.media import ffmpeg_available, probe
from lumen.model import AudioMixAnchor, Clip, Editor, Project
from lumen.timing import ClipTimeMap
from schema40_audio_mix_fixture import compile_audio_mix as compile_schema40
from test_audio_mix import _pcm
from test_gap_close_render import gap_av_input, lossless_scene
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                              engine, ff, make_source)
from test_source_audio_separation_pcm import (RATES, RATE_IDS, boundary,
    constant_av, digest, pcm, samples, separated)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


def old_pcm(project):
    mix = compile_schema40(project, {m.id: m for m in project.media})
    return _pcm(mix.inputs, mix.graph)


def reattach(editor, video='v0', audio='a1'):
    from lumen.source_audio_reattach import (plan_source_audio_reattach,
        verify_source_audio_reattach, apply_source_audio_reattach)
    plan = plan_source_audio_reattach(editor, video, audio)
    preview = verify_source_audio_reattach(plan)
    verified = verify_source_audio_reattach(plan, previous=preview)
    return apply_source_audio_reattach(editor, plan, verified)


def test_actual_float32_cancellation_preserves_unanchored_audio_slot_not_video_slot(tmp_path):
    tiny = 2 ** -25
    media = [constant_av(tmp_path, 'tiny', tiny, -tiny),
             constant_av(tmp_path, 'positive', 1, -1),
             constant_av(tmp_path, 'negative', -1, 1)]
    editor = Editor(Project(width=16, height=16, fps_num=24, media=media, clips=[
        Clip('v0', media[0].id, 0, 0, 0, 6, source_audio_enabled=False),
        Clip('v1', media[1].id, 1, 0, 0, 6),
        Clip('a0', media[2].id, 2, 0, 0, 6),
        Clip('a1', media[0].id, 3, 0, 0, 6)]))
    before = deepcopy(editor.project)
    hashes = {m.path: digest(m.path) for m in media}; files = set(tmp_path.iterdir())
    expected = old_pcm(before)
    np.testing.assert_array_equal(samples(expected), np.tile([tiny, -tiny], (12000, 1)))
    assert pcm(before) == expected
    result = reattach(editor)
    assert result.id == 'v0' and result.audio_mix_anchor == AudioMixAnchor(3, 0, 'a1')
    assert [c.id for c in editor.project.clips] == ['v0', 'v1', 'a0']
    assert pcm(editor.project) == expected
    assert [c.id for c in AudioMixBuilder(editor.project).clips] == ['v0', 'v1', 'a0']
    assert '[silence][a1][a2][a0]amix=' in compile_audio_mix(editor.project,
        {m.id: m for m in media}).graph
    naive = deepcopy(editor.project); naive.clips[0].audio_mix_anchor = None
    damaged = samples(pcm(naive))
    assert np.count_nonzero(damaged) == 0
    assert np.count_nonzero(damaged != samples(expected)) == 24000
    assert set(tmp_path.iterdir()) == files and {m.path: digest(m.path) for m in media} == hashes


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
@pytest.mark.parametrize('offset', [.137, -.113], ids=['positive-offset', 'negative-offset'])
@pytest.mark.parametrize('speed', [Fraction(1), Fraction(3, 2)], ids=['1x-partial', 'retimed-partial'])
def test_full_old_mix_after_independent_audio_edits_offsets_partial_tails_save_history(
        tmp_path, fps, offset, speed):
    a = gap_av_input(tmp_path, fps, offset, 10001)
    b = gap_av_input(tmp_path, fps, -.113 if offset > 0 else .137, 10002)
    mapping = ClipTimeMap(Fraction(9, 8), Fraction(9, 8) + Fraction(19, 2) * speed, speed)
    editor = Editor(Project(width=16, height=16, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[a, b], master_gain_db=-3.25, clips=[
        Clip('v0', a.id, 0, 2, 0, 10, time_map=mapping,
             gain_db=-4, fade_in=1, fade_out=3),
        Clip('v1', b.id, 1, 3, 4, 9, gain_db=-7, fade_in=2, fade_out=1),
        Clip('a0', b.id, 2, 5, 3, 10, gain_db=-5, fade_in=2, fade_out=1),
        Clip('muted-tail', a.id, 3, 18, 10, 3, muted=True)]))
    audio = separated(editor)
    editor.update_clip(audio.id, gain_db=-9.5, fade_in=2, fade_out=1)
    before = deepcopy(editor.project); old_v = deepcopy(editor._clip('v0'))
    hashes = {m.path: digest(m.path) for m in before.media}; files = set(tmp_path.iterdir())
    expected = old_pcm(before)
    assert pcm(before) == expected and np.count_nonzero(samples(expected)) > 0
    assert np.any(samples(expected)[:, 0] != samples(expected)[:, 1])
    undo_count = len(editor._undo); revision = editor.revision
    retained = reattach(editor, audio=audio.id)
    assert retained == replace(old_v, source_audio_enabled=True,
        audio_mix_anchor=audio.audio_mix_anchor, gain_db=audio.gain_db,
        muted=audio.muted, fade_in=audio.fade_in, fade_out=audio.fade_out)
    assert editor.project.media == before.media and set(tmp_path.iterdir()) == files
    assert len(editor._undo) == undo_count + 1 and editor.revision == revision + 1
    assert pcm(editor.project) == expected
    assert len(expected) == boundary(21, fps) * 8
    assert not any(expected[boundary(15, fps) * 8:])
    saved = tmp_path / 'reattached.lumen'; editor.project.save(saved)
    assert Project.load(saved) == editor.project and pcm(Project.load(saved)) == expected
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before and pcm(editor.project) == expected
    assert editor.redo() and editor.project == after and pcm(editor.project) == expected
    assert {m.path: digest(m.path) for m in before.media} == hashes


@pytest.mark.parametrize('state', ['muted', 'outside-audio', 'zero-source'])
def test_user_muted_or_source_silent_selected_audio_is_reattached_without_unmuting(tmp_path, state):
    media = constant_av(tmp_path, 'silent', 0 if state == 'zero-source' else .3,
                        0 if state == 'zero-source' else -.2)
    if state == 'outside-audio': media.audio_offset = 2
    editor = Editor(Project(width=16, height=16, fps_num=24, media=[media], clips=[
        Clip('v0', media.id, 0, 1, 0, 6, source_audio_enabled=False),
        Clip('a1', media.id, 3, 1, 0, 6, muted=state == 'muted', gain_db=-9,
             fade_in=1, fade_out=2)]))
    before = old_pcm(editor.project)
    assert before == bytes(boundary(7, Fraction(24)) * 8)
    result = reattach(editor)
    assert result.source_audio_enabled and result.muted is (state == 'muted')
    assert pcm(editor.project) == before
    if state == 'muted':
        editor.update_clip(result.id, muted=False)
        assert np.count_nonzero(samples(pcm(editor.project))) > 0


@pytest.mark.parametrize('track', [2, 3], ids=['choose-A0', 'choose-A1'])
def test_two_matching_audio_candidates_only_explicit_one_removed_full_mix_preserved(tmp_path, track):
    media = constant_av(tmp_path, 'same-media', .21, -.13)
    editor = Editor(Project(width=16, height=16, fps_num=24, media=[media], clips=[
        Clip('v0', media.id, 0, 2, 1, 6, source_audio_enabled=False),
        Clip('a0', media.id, 2, 2, 1, 6, gain_db=-9, fade_in=1),
        Clip('a1', media.id, 3, 2, 1, 6, gain_db=-4, fade_out=2)]))
    chosen = 'a0' if track == 2 else 'a1'; other = 'a1' if track == 2 else 'a0'
    before = deepcopy(editor.project); unchanged = deepcopy(editor._clip(other))
    expected = old_pcm(before)
    result = reattach(editor, audio=chosen)
    assert {c.id for c in editor.project.clips} == {'v0', other}
    assert editor._clip(other) == unchanged
    assert result.audio_mix_anchor == AudioMixAnchor(track, 2, chosen)
    assert pcm(editor.project) == expected


@pytest.mark.parametrize('fps', RATES, ids=RATE_IDS)
def test_numbered_lossless_picture_full_export_audio_and_source_bytes_unchanged(tmp_path, engine, fps):
    source = make_source(tmp_path / 'numbered.mkv', fps)
    av_path = tmp_path / 'numbered-with-audio.mkv'
    ff('-i', source.media.path, '-f', 'lavfi', '-i',
       'aevalsrc=0.21*sin(2*PI*431*t)|0.13*sin(2*PI*991*t):s=48000:d=4',
       '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_f32le', av_path)
    media = probe(av_path)
    np.testing.assert_array_equal(decode(av_path), source.original)
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
        fps_den=fps.denominator, media=[media], master_gain_db=-2, clips=[
        Clip('v0', media.id, 0, 2, 0, 6,
             time_map=ClipTimeMap(Fraction(9, 8), Fraction(73, 8), Fraction(3, 2)),
             gain_db=-4, fade_in=1),
        Clip('v1', media.id, 1, 4, 30, 3, gain_db=-8, fade_out=1)]))
    audio = separated(editor)
    editor.update_clip(audio.id, gain_db=-6.25, fade_in=2, fade_out=1)
    before = deepcopy(editor.project); source_hash = digest(av_path)
    old_pixels = lossless_scene(engine, before, tmp_path / 'before.mkv')
    expected = np.zeros((8, HEIGHT, WIDTH, 3), np.uint16)
    expected[2:8] = source.original[[math.floor(Fraction(9, 8) + k * Fraction(3, 2))
                                   for k in range(6)]]
    expected[4:7] = source.original[[30, 31, 32]]
    assert_delivery_frames(old_pixels, expected)
    expected_audio = old_pcm(before)
    reattach(editor, audio=audio.id)
    new_pixels = lossless_scene(engine, editor.project, tmp_path / 'after.mkv')
    np.testing.assert_array_equal(new_pixels, old_pixels)
    inputs, graph, _ = engine._compile(editor.project)
    assert _pcm(inputs, graph, video_output=os.devnull) == expected_audio == pcm(editor.project)
    assert digest(av_path) == source_hash


def test_saved_genuine_schema40_fixture_stays_schema40_without_audio_graph_drift(tmp_path):
    fixture = Path(__file__).parent / 'fixtures/schema40-before-source-audio-reattach.json'
    document = json.loads(fixture.read_text()); original = deepcopy(document)
    assert document['version'] == 40
    project = Project.from_dict(document)
    assert document == original
    for index, old in enumerate(project.media):
        fresh = gap_av_input(tmp_path, project.fps, old.audio_offset, 10101 + index)
        project.media[index] = replace(fresh, id=old.id, name=old.name)
    project.validate()
    checked = {m.id: m for m in project.media}
    old = compile_schema40(project, checked); new = compile_audio_mix(project, checked)
    assert (new.inputs, new.graph, new.end_sample, new.source_ids) == (
        old.inputs, old.graph, old.end_sample, old.source_ids)
    expected = _pcm(old.inputs, old.graph)
    assert pcm(project) == expected
    editor = Editor(project); before = deepcopy(project)
    reattach(editor, video='legacy-v0', audio='legacy-audio')
    assert pcm(editor.project) == expected
    assert Project.from_dict(editor.project.to_dict()) == editor.project
    assert editor.undo() and editor.project == before
    assert pcm(editor.project) == expected


@pytest.mark.parametrize('anchor_start', [0, 1000000000], ids=['historical-zero', 'historical-upper-bound'])
def test_shared_historical_anchor_distinct_current_start_keeps_real_float_order(tmp_path, anchor_start):
    from lumen.model import MAX_CAPTION_FRAME
    # The upper bound is historical metadata, not a live timeline coordinate.
    if anchor_start: anchor_start = MAX_CAPTION_FRAME
    tiny = 2 ** -25
    media = [constant_av(tmp_path, 'positive', 1, -1),
             constant_av(tmp_path, 'negative', -1, 1),
             constant_av(tmp_path, 'tiny', tiny, -tiny)]
    anchor = AudioMixAnchor(3, anchor_start, 'deleted historical clip')
    editor = Editor(Project(width=16, height=16, fps_num=24, media=media, clips=[
        Clip('v0', media[0].id, 0, 0, 0, 6, source_audio_enabled=False),
        Clip('v1', media[1].id, 1, 1, 0, 5, audio_mix_anchor=anchor),
        Clip('a0', media[2].id, 2, 2, 0, 4, audio_mix_anchor=anchor),
        Clip('a1', media[0].id, 3, 0, 0, 6, audio_mix_anchor=anchor)]))
    expected = old_pcm(editor.project)
    np.testing.assert_array_equal(samples(expected)[4000:], np.tile([tiny, -tiny], (8000, 1)))
    result = reattach(editor)
    assert result.audio_mix_anchor == anchor
    assert pcm(editor.project) == expected


def test_analysis_preview_export_reuse_exact_reattached_mix(tmp_path, engine, monkeypatch):
    from lumen import audio_analysis as aa
    media = gap_av_input(tmp_path, Fraction(30000, 1001), .137, 10201)
    editor = Editor(Project(width=16, height=16, fps_num=30000, fps_den=1001,
        media=[media], master_gain_db=-3.25, clips=[
        Clip('v0', media.id, 0, 1, 0, 20, source_audio_enabled=False),
        Clip('a1', media.id, 3, 1, 0, 20, gain_db=-4, fade_in=2, fade_out=3)]))
    before = deepcopy(editor.project); expected = old_pcm(before)
    result_before = aa.analyze_audio(before)
    reattach(editor)
    result_after = aa.analyze_audio(editor.project)
    assert result_after.sample_count == result_before.sample_count == len(expected) // 8
    assert result_after.sample_peaks == result_before.sample_peaks
    assert result_after.estimated_true_peaks == result_before.estimated_true_peaks
    assert result_after.integrated_lufs == result_before.integrated_lufs
    assert result_after.fingerprint != result_before.fingerprint
    graphs = []; original = engine._compile
    def capture(*args, **kwargs):
        value = original(*args, **kwargs); graphs.append(value); return value
    monkeypatch.setattr(engine, '_compile', capture)
    engine.preview(editor.project)
    engine.render(editor.project, tmp_path / 'delivery.mp4')
    assert len(graphs) == 2
    for inputs, graph, _ in graphs:
        assert _pcm(inputs, graph, video_output=os.devnull) == expected
