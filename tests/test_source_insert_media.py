"""Independent original-frame and PCM oracles for bounded single-track Insert.

Expected images are original source arrays and expected sound is a from-origin
normalization, never a reconstruction from the production insertion plan. Full
old/new renderer outputs are consumed, including both picture and audio streams.
Only small synthetic local files are used; no input seeking or external media.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import time

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.source_viewer import SourcePreviewBackend, SourceViewerSession, _probe
from test_retime_video import (WIDTH, HEIGHT, assert_delivery_frames, decode,
                               engine, frame_sources)
from test_roll_media import exact_pcm
from test_source_overwrite_media import pcm_source, project_for, put_pcm, sample
from test_exact_audio_cache_backend import (av_source, copied_short_aac,
    origin_oracle, production_pcm, selected_oracle)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def original_picture(source):
    expected = np.zeros((70, HEIGHT, WIDTH, 3), dtype=np.uint16)
    expected[10:30] = source[5:25]
    expected[40:50] = source[25:35]
    expected[65:70] = source[60:65]
    return expected


def inserted_picture(before, source, start, count):
    # Preserve gaps, including an explicitly captured playhead beyond the end.
    if start > len(before):
        before = np.concatenate([before, np.zeros((start-len(before), HEIGHT, WIDTH, 3), np.uint16)])
    return np.concatenate([before[:start], source[10:10+count], before[start:]])


@pytest.mark.parametrize('fps', [Fraction(24), Fraction(30000, 1001)], ids=['24', '29.97'])
@pytest.mark.parametrize('start', [10, 15, 30, 35, 70, 75], ids=['boundary','interior','adjacent-gap','gap','end','beyond-end'])
def test_original_numbered_frames_survive_insert_and_save_undo_redo(
        tmp_path, frame_sources, engine, fps, start):
    source = frame_sources[fps]
    editor = Editor(project_for(source.media, fps))
    before = deepcopy(editor.project)
    source_hash = hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest()
    old_pixels = original_picture(source.original)
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), old_pixels)
    plan = editor.plan_source_insert(source.media.id, 0, start, 10, 20)
    editor.apply_source_insert(plan)
    expected = inserted_picture(old_pixels, source.original, start, 10)
    rendered = engine.preview(editor.project)
    assert_delivery_frames(decode(rendered, rgb16=False), expected)
    # The marker after the existing gaps moves by exactly the insertion length,
    # unless insertion is at/beyond the old timeline end.
    marker = next(c for c in editor.project.clips if c.id == 'fixed')
    assert marker.start == (75 if start <= 65 else 65)
    saved = tmp_path/'inserted.lumen'
    editor.project.save(saved)
    reopened = Project.load(saved)
    assert reopened == editor.project
    assert engine.preview(reopened) == rendered
    assert editor.undo() and editor.project == before
    assert_delivery_frames(decode(engine.preview(editor.project), rgb16=False), old_pixels)
    assert editor.redo() and engine.preview(editor.project) == rendered
    assert hashlib.sha256(Path(source.media.path).read_bytes()).hexdigest() == source_hash


def test_other_picture_track_remains_fixed_not_cross_track_sync(frame_sources, engine):
    fps = Fraction(24)
    source = frame_sources[fps]
    project = project_for(source.media, fps)
    project.clips.append(Clip('fixed-upper', source.media.id, 1, 55, 70, 5))
    editor = Editor(project)
    upper = deepcopy(project.clips[-1])
    editor.apply_source_insert(editor.plan_source_insert(source.media.id, 0, 15, 10, 20))
    expected = inserted_picture(original_picture(source.original), source.original, 15, 10)
    expected[55:60] = source.original[70:75]
    assert_delivery_frames(decode(engine.preview(project), rgb16=False), expected)
    assert next(c for c in project.clips if c.id == 'fixed-upper') == upper


@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)], ids=['23.976','29.97'])
@pytest.mark.parametrize('offset', [0, .013])
@pytest.mark.parametrize('start', [10, 15, 35, 70, 75], ids=['boundary','interior','gap','end','beyond-end'])
def test_every_old_pcm_sample_translates_exactly_and_insert_is_source_exact(
        tmp_path, engine, fps, offset, start):
    media, raw = pcm_source(tmp_path, offset)
    editor = Editor(project_for(media, fps, track=2))
    source_hash = hashlib.sha256(Path(media.path).read_bytes()).hexdigest()
    expected_old = np.zeros(sample(70, fps), np.float32)
    for destination, origin, count in [(10,5,20), (40,25,10), (65,60,5)]:
        put_pcm(expected_old, raw, fps, destination, origin, count, offset)
    old = exact_pcm(engine, editor.project)
    np.testing.assert_allclose(old, expected_old, rtol=3e-7, atol=3e-7)
    editor.apply_source_insert(editor.plan_source_insert(media.id, 2, start, 10, 20))
    lo, hi = sample(start, fps), sample(start+10, fps)
    padded_old = np.pad(old, (0, max(0, lo-len(old))))
    expected = np.concatenate([padded_old[:lo], np.zeros(hi-lo, np.float32), padded_old[lo:]])
    put_pcm(expected, raw, fps, start, 10, 10, offset)
    actual = exact_pcm(engine, editor.project)
    np.testing.assert_allclose(actual, expected, rtol=3e-7, atol=3e-7)
    np.testing.assert_array_equal(actual[:lo], padded_old[:lo])
    np.testing.assert_array_equal(actual[hi:], padded_old[lo:])
    assert hashlib.sha256(Path(media.path).read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize('offset', [.137, -.113])
def test_late_embedded_aac_insert_proof_and_actual_old_new_pcm(tmp_path, engine, offset):
    path = av_source(tmp_path, offset=offset, origin=5)
    media = probe(path)
    project = Project(width=16, height=16, fps_num=25, media=[media], clips=[
        Clip('retained', media.id, 0, 10, 800, 70),
        Clip('downstream', media.id, 0, 90, 1000, 10)])
    editor = Editor(project)
    before = deepcopy(project)
    session = SourceViewerSession(project, media.id)
    plan = editor.plan_source_insert(media.id, 0, 25, 850, 875)
    backend = SourcePreviewBackend()
    try:
        old = production_pcm(engine, project, tmp_path, 'late-before')
        identity = backend.inspect_frame(session).identity
        assert backend.verify_insert_sources(session, identity, project, plan)
        assert project == before
        editor.apply_source_insert(plan)
        new = production_pcm(engine, project, tmp_path, 'late-after')
        lo, hi = 25*1920*8, 50*1920*8
        assert new[:lo] == old[:lo]
        assert new[hi:] == old[lo:]
        assert new[lo:hi] == selected_oracle(origin_oracle(path), session, 850, 875)
        assert editor.undo() and editor.project == before
        assert editor.redo()
    finally:
        backend.close()


@pytest.mark.parametrize('rate,offset,origin', [(44100,0,0), (22050,.137,5), (96000,-.113,5)])
def test_copied_aac_declared_tail_survives_selected_and_split_insert(
        tmp_path, engine, rate, offset, origin):
    path = copied_short_aac(tmp_path, rate, offset, origin)
    media = probe(path)
    project = Project(width=16, height=16, fps_num=25, media=[media],
                      clips=[Clip('old-tail', media.id, 0, 0, 20, 15)])
    editor = Editor(project)
    session = SourceViewerSession(project, media.id)
    # The original spans the audible copied-AAC padding and real decoded EOF;
    # so does the inserted selection. Neither duration tags nor silence guesses
    # are sufficient evidence for the proof or the expected output.
    plan = editor.plan_source_insert(media.id, 0, 5, 24, 29)
    backend = SourcePreviewBackend()
    try:
        full = origin_oracle(path)
        identity = backend.inspect_frame(session).identity
        info = _probe(session, identity, None, time.monotonic()+60)
        declared = math.ceil(info['audio_duration']*48000)
        assert len(full)//8 > declared
        assert np.max(np.abs(np.frombuffer(full[declared*8:], dtype='<f4')), initial=0) > .001
        old = production_pcm(engine, project, tmp_path, 'copied-before')
        assert backend.verify_insert_sources(session, identity, project, plan)
        assert backend._audio_eof
        assert next(iter(backend._audio_eof.values())) == len(full)//8
        editor.apply_source_insert(plan)
        new = production_pcm(engine, project, tmp_path, 'copied-after')
        lo, hi = 5*1920*8, 10*1920*8
        assert new == old[:lo] + selected_oracle(full, session, 24, 29) + old[lo:]
        assert new[hi:] == old[lo:]
    finally:
        backend.close()


def test_fractional_downstream_fades_allow_only_sample_equivalent_translation(tmp_path, engine):
    fps = Fraction(30000, 1001)
    media, _ = pcm_source(tmp_path, 0)
    original = Clip('fade', media.id, 2, 10, 5, 20, fade_in=1, fade_out=3, gain_db=-3)
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=fps.numerator,
                           fps_den=fps.denominator, media=[media], clips=[original]))
    before = deepcopy((editor.project, editor._undo, editor._redo, editor.revision))
    old = exact_pcm(engine, editor.project)
    # One frame changes at least the local fade sample phase. A simple start
    # mutation would preserve metadata but alter actual sound, so reject it.
    with pytest.raises(ValueError, match='sample|audio|phase|equivalen'):
        editor.plan_source_insert(media.id, 2, 0, 0, 1)
    assert (editor.project, editor._undo, editor._redo, editor.revision) == before
    # Five frames are exactly 8008 samples: the unchanged full mix/fade pipeline
    # must produce a byte-identical translated old contribution.
    editor.apply_source_insert(editor.plan_source_insert(media.id, 2, 0, 0, 5))
    new = exact_pcm(engine, editor.project)
    shift = sample(5, fps)
    np.testing.assert_array_equal(new[shift:], old)
    shifted = next(c for c in editor.project.clips if c.id == 'fade')
    assert (shifted.fade_in, shifted.fade_out, shifted.gain_db) == (1, 3, -3)


def test_split_inside_embedded_leading_silence_preserves_exact_onset(tmp_path, engine):
    fps = Fraction(25)
    media, raw = pcm_source(tmp_path, .137)
    project = Project(width=WIDTH, height=HEIGHT, fps_num=25, media=[media],
                      clips=[Clip('delayed', media.id, 2, 10, 0, 20)])
    editor = Editor(project)
    session = SourceViewerSession(project, media.id)
    # Split after two source frames (80 ms), before the actual 137 ms onset.
    # The leading silence on the right piece must move with the original sound.
    plan = editor.plan_source_insert(media.id, 2, 12, 10, 15)
    backend = SourcePreviewBackend()
    try:
        old = exact_pcm(engine, project)
        expected_old = np.zeros(sample(30, fps), np.float32)
        put_pcm(expected_old, raw, fps, 10, 0, 20, .137)
        np.testing.assert_allclose(old, expected_old, rtol=3e-7, atol=3e-7)
        identity = backend.inspect_frame(session).identity
        assert backend.verify_insert_sources(session, identity, project, plan)
        editor.apply_source_insert(plan)
        new = exact_pcm(engine, project)
        lo, hi = sample(12, fps), sample(17, fps)
        expected = np.concatenate([old[:lo], np.zeros(hi-lo, np.float32), old[lo:]])
        put_pcm(expected, raw, fps, 12, 10, 5, .137)
        np.testing.assert_allclose(new, expected, rtol=3e-7, atol=3e-7)
        np.testing.assert_array_equal(new[:lo], old[:lo])
        np.testing.assert_array_equal(new[hi:], old[lo:])
    finally:
        backend.close()
