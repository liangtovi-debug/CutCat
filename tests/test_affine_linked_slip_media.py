"""Independent actual-media oracles for affine J/L source slip.

Expected maps come from OLD records and the selected role's requested origin.
The existing raw_unit_pcm oracle slices original procedural stereo arrays and
applies scalar envelopes, fades, historical summation and master gain. The
renderer supplies actual output only. Both production streams are consumed in
each complete FFV1/float32-PCM mux; no audio-only/nullsink conservation shortcut.
The four main cases are 1x, not an independent Rubber Band/DSP accuracy claim.

Keep existing tolerances: complete gain-bearing PCM <= 2 float32 ULP, exact
RGB16 selected segments, existing full-scene frame-ID/color assertions. The
separate gain-free cancellation test additionally requires exact PCM bytes.
All synthetic inputs, muxes and diagnostic JSON remain under tmp_path; the
runner must place TMPDIR and pytest --basetemp in an owned workspace directory.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import (AV_LINK_AFFINE_ALIGNMENT, AVLink, AudioMixAnchor,
                         Clip, Editor, Project)
from lumen.timing import ClipTimeMap
from test_linked_audio_roll_media import (boundary, make_editor,
                                          requested_audio_project,
                                          short_sources, verified_scene)
from test_linked_av_pcm import (assert_sources_untouched,
                                source_fingerprints)
from test_linked_av_roll_media import expected_frames, raw_unit_pcm
from test_retime_video import (HEIGHT, WIDTH, assert_delivery_frames, engine,
                              decode, ff, segment_pixels)
from test_source_audio_separation_pcm import samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(),
                               reason='Local FFmpeg/ffprobe required')


def requested_slip(old, selected_id, requested):
    """Old/request algebra only; never call the candidate or a map evaluator.

    ClipTimeMap is the representation under test, not the arithmetic oracle.
    Crucially this is NOT test_linked_av_pcm.expected_slip: that older helper
    intentionally assigns the same origin to both strict-v1 roles.
    """
    expected = deepcopy(old)
    previous = {clip.id: clip for clip in old.clips}
    link = next(link for link in old.av_links if selected_id in link.clip_ids)
    delta = Fraction(requested) - Fraction(previous[selected_id].source_in)
    for clip in expected.clips:
        if clip.id not in link.clip_ids:
            continue
        before = previous[clip.id]
        origin = Fraction(before.source_in) + delta
        endpoint = before.source_out + delta
        clip.source_in = origin
        clip.time_map = ClipTimeMap(origin, endpoint, before.speed)
        if (before.speed == 1 and origin.denominator == 1
                and endpoint - origin == before.duration):
            clip.source_in, clip.time_map = int(origin), None
    expected.validate()
    return expected


def assert_source_only_change(old, new, selected_id, requested):
    """Each role keeps its OWN timeline, tail, hidden automation and anchor."""
    link = next(link for link in old.av_links if selected_id in link.clip_ids)
    previous = {clip.id: clip for clip in old.clips}
    delta = Fraction(requested) - Fraction(previous[selected_id].source_in)
    assert new.av_links == old.av_links
    assert new.media == old.media
    assert new.duration_frames == old.duration_frames
    assert [clip.id for clip in new.clips] == [clip.id for clip in old.clips]
    for clip in new.clips:
        before = previous[clip.id]
        if clip.id not in link.clip_ids:
            assert clip == before
            continue
        assert clip.source_in == before.source_in + delta
        assert clip.source_out == before.source_out + delta
        assert clip.speed == before.speed
        assert (clip.duration * clip.speed - (clip.source_out - clip.source_in)
                == before.duration * before.speed
                - (before.source_out - before.source_in))
        restored = deepcopy(clip)
        restored.source_in, restored.time_map = before.source_in, before.time_map
        assert restored == before  # Every field other than the two map fields.


def affine_editor(sources, fps, cut):
    """Seed a valid J/L project independently, without invoking audio roll.

    Right V/A have distinct starts and source origins. V retains a half-frame
    source tail; A independently retains a quarter-frame tail. Historical A
    anchors and hidden timeline-local automation come from accepted fixtures.
    """
    old = make_editor(sources, fps).project
    seeded = requested_audio_project(old, cut)
    audio = next(clip for clip in seeded.clips if clip.id == 'right-audio')
    audio.time_map = ClipTimeMap(audio.source_in,
                                audio.source_out + Fraction(1, 4), Fraction(1))
    seeded.validate()
    return Editor(seeded)


# Four cases, not a 4 x 2 x 2 x 2 cross product: each active role, both source
# directions, J and L geometry, and integer/NTSC sample phases are represented.
CASES = [
    pytest.param(Fraction(24), 0, 12, 'right-video', Fraction(5, 4),
                 id='24-J-video-forward'),
    pytest.param(Fraction(24000, 1001), -.113, 14, 'right-audio', Fraction(-3, 4),
                 id='23.976-L-audio-backward'),
    pytest.param(Fraction(30000, 1001), .137, 12, 'right-audio', Fraction(5, 4),
                 id='29.97-J-audio-forward-leading'),
    pytest.param(Fraction(60000, 1001), .137, 14, 'right-video', Fraction(-3, 4),
                 id='59.94-L-video-backward-leading'),
]


@pytest.mark.parametrize('fps,offset,cut,selected_id,delta', CASES)
def test_affine_slip_complete_scene_and_independent_scalar_pcm(
        tmp_path, engine, fps, offset, cut, selected_id, delta):
    sources, paths = short_sources(tmp_path / 'sources', fps, offset)
    fingerprints = source_fingerprints(paths)
    editor = affine_editor(sources, fps, cut)
    before = deepcopy(editor.project)
    raw = {source.media.id: source.audio for source in sources}
    pictures = {source.media.id: source.pictures for source in sources}
    selected_old = next(clip for clip in before.clips if clip.id == selected_id)
    requested = selected_old.source_in + delta
    expected = requested_slip(before, selected_id, requested)
    old_pcm, wanted_pcm = raw_unit_pcm(before, raw), raw_unit_pcm(expected, raw)
    old_segments, old_frames = expected_frames(before, pictures)
    wanted_segments, wanted_frames = expected_frames(expected, pictures)
    assert old_pcm != wanted_pcm, 'Slip selects different marked source audio'
    assert not np.array_equal(old_frames, wanted_frames)

    # Guard the oracle itself against the strict-v1 shortcut: anchoring the
    # other role at the requested origin must produce different audio/picture.
    wrong = deepcopy(expected)
    partner_id = 'right-audio' if selected_id == 'right-video' else 'right-video'
    partner = next(clip for clip in wrong.clips if clip.id == partner_id)
    span = partner.source_out - partner.source_in
    partner.source_in, partner.time_map = requested, ClipTimeMap(requested, requested + span)
    assert (raw_unit_pcm(wrong, raw) != wanted_pcm
            or not np.array_equal(expected_frames(wrong, pictures)[1], wanted_frames))

    # A source slip must NOT rebase timeline-local sample keys. This fixture
    # must expose such an accidental rebase numerically, not just structurally.
    wrong_keys = deepcopy(expected)
    audio = next(clip for clip in wrong_keys.clips if clip.id == 'right-audio')
    audio.audio_gain_keyframes = [replace(key, sample=key.sample-boundary(delta, fps))
                                  for key in audio.audio_gain_keyframes]
    assert raw_unit_pcm(wrong_keys, raw) != wanted_pcm

    old_scene, old_audio = verified_scene(engine, before, before, raw,
                                          tmp_path / 'before.mkv')
    assert_delivery_frames(old_scene, old_frames)
    history, revision = len(editor._undo), editor.revision
    result = editor.slip_linked_av(selected_id, requested)
    assert result is editor._clip(selected_id)
    assert editor.project == expected
    assert len(editor._undo) == history + 1 and editor.revision == revision + 1
    assert_source_only_change(before, editor.project, selected_id, requested)
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT
               for link in editor.project.av_links)
    v, a = (editor._clip(key) for key in ('right-video', 'right-audio'))
    assert v.start == 13 and a.start == cut
    assert v.source_in - v.start == a.source_in - a.start
    assert v.duration - (v.source_out-v.source_in) == Fraction(1, 2)
    assert a.duration - (a.source_out-a.source_in) == Fraction(1, 4)
    assert not v.source_audio_enabled and a.source_audio_enabled

    scene, actual = verified_scene(engine, editor.project, expected, raw,
                                   tmp_path / 'after.mkv')
    assert_delivery_frames(scene, wanted_frames)
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, v),
                                  wanted_segments['right-video'])
    assert not np.array_equal(wanted_segments['right-video'], old_segments['right-video'])
    assert actual != old_audio and not np.array_equal(scene, old_scene)
    # Only genuinely unchanged timeline regions are compared with old output.
    np.testing.assert_array_equal(scene[:13], old_scene[:13])
    np.testing.assert_array_equal(scene[25:], old_scene[25:])
    assert editor.project.duration_frames == before.duration_frames == 29
    assert len(actual) == len(old_audio) == boundary(29, fps) * 8
    # Left A's exact half-tail remains zero before the J/L cut; it is neither
    # repeated nor moved when the OTHER pair slips beneath fixed timeline keys.
    end = boundary(cut, fps)
    assert not np.count_nonzero(samples(actual)[end-max(1, boundary(1, fps)//4):end])
    if offset > 0:
        assert not np.count_nonzero(samples(actual)[boundary(3, fps):boundary(4, fps)])
    # Right source's actual PCM stream has only 13 source frames, even though
    # its video has 40. At this late unchanged region only fixed-audio remains.
    alone = deepcopy(expected)
    for clip in alone.clips:
        if clip.id != 'fixed-audio':
            clip.muted = True
    lo, hi = boundary(24, fps), boundary(25, fps)
    np.testing.assert_array_max_ulp(samples(actual)[lo:hi],
        samples(raw_unit_pcm(alone, raw))[lo:hi], maxulp=2)
    assert not np.count_nonzero(samples(actual)[boundary(27, fps):])

    # One fractional-rate case closes real-media history/reopen paths without
    # multiplying all expensive renders. Model coverage owns the full matrix.
    if fps == Fraction(30000, 1001):
        saved = tmp_path / 'affine-slip.lumen'
        editor.project.save(saved)
        reopened = Project.load(saved)
        assert reopened == expected
        re_scene, re_audio = verified_scene(engine, reopened, expected, raw,
                                            tmp_path / 'reopened.mkv')
        assert re_audio == actual
        np.testing.assert_array_equal(re_scene, scene)
        assert editor.undo() and editor.project == before
        undo_scene, undo_audio = verified_scene(engine, editor.project, before, raw,
                                                 tmp_path / 'undo.mkv')
        assert undo_audio == old_audio
        np.testing.assert_array_equal(undo_scene, old_scene)
        assert editor.redo() and editor.project == expected
        redo_scene, redo_audio = verified_scene(engine, editor.project, expected, raw,
                                                 tmp_path / 'redo.mkv')
        assert redo_audio == actual
        np.testing.assert_array_equal(redo_scene, scene)
    assert_sources_untouched(paths, fingerprints)


def test_affine_slip_preserves_historical_cancellation_order_exactly(tmp_path, engine):
    """A wrong V-slot anchor erases every tiny stereo marker, not just 1 ULP.

    Positive + negative + marked-tiny must sum in that historical order. Tiny
    audio is nonconstant, so this remains a genuine content-changing slip.
    This complements the scalar fade/key/master cases; it uses no gain DSP.
    """
    fps = Fraction(24)
    sources, paths = short_sources(tmp_path / 'sources', fps, 0)
    numbered = sources[0]
    tiny_raw = (numbered.audio * np.float32(2 ** -25)).astype('<f4')
    tiny_path = tmp_path / 'marked-tiny.mkv'
    ff('-i', numbered.media.path, '-f', 'f32le', '-ar', '48000', '-ac', '2',
       '-i', 'pipe:0', '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
       '-c:a', 'pcm_f32le', tiny_path, data=tiny_raw.tobytes())
    tiny = probe(tiny_path)
    assert tiny.audio_offset == 0
    decoded = ff('-i', tiny_path, '-map', '0:a:0', '-vn', '-ar', '48000', '-ac', '2',
                 '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    np.testing.assert_array_equal(samples(decoded), tiny_raw)
    # Keep the source-picture oracle in the same explicit RGB16 domain as
    # the numbered array. The shared audio-only constant_av helper instead
    # emits untagged 16x16 YUV red, not full-size pure RGB red.
    np.testing.assert_array_equal(decode(tiny_path), numbered.pictures)
    positive_raw = np.tile(np.array([1, -1], '<f4'), (24000, 1))
    negative_raw = np.tile(np.array([-1, 1], '<f4'), (24000, 1))

    def constant_numbered_source(name, original_pcm):
        path = tmp_path / f'{name}.mkv'
        ff('-i', numbered.media.path, '-f', 'f32le', '-ar', '48000', '-ac', '2',
           '-i', 'pipe:0', '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
           '-c:a', 'pcm_f32le', path, data=original_pcm.tobytes())
        np.testing.assert_array_equal(decode(path), numbered.pictures)
        decoded_pcm = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', '48000',
                         '-ac', '2', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
        np.testing.assert_array_equal(samples(decoded_pcm), original_pcm)
        media = probe(path)
        assert media.audio_offset == 0 and (media.width, media.height) == (WIDTH, HEIGHT)
        return media

    positive = constant_numbered_source('positive', positive_raw)
    negative = constant_numbered_source('negative', negative_raw)
    paths = (*paths, tiny_path, tmp_path / 'positive.mkv', tmp_path / 'negative.mkv')
    fingerprints = source_fingerprints(paths)
    editor = Editor(Project(width=WIDTH, height=HEIGHT, fps_num=24,
        media=[tiny, positive, negative], clips=[
            Clip('positive', positive.id, 0, 0, 0, 9),
            Clip('video', tiny.id, 1, 2, 3, 6, source_audio_enabled=False,
                 time_map=ClipTimeMap(3, Fraction(17, 2))),
            Clip('negative', negative.id, 2, 0, 0, 9),
            Clip('audio', tiny.id, 3, 1, 2, 8,
                 time_map=ClipTimeMap(2, Fraction(39, 4)),
                 audio_mix_anchor=AudioMixAnchor(3, 0, 'historical-audio'))],
        av_links=[AVLink('link', 'video', 'audio', tiny.id, AV_LINK_AFFINE_ALIGNMENT)]))
    before = deepcopy(editor.project)
    requested = Fraction(13, 4)
    expected = requested_slip(before, 'audio', requested)
    raw = {tiny.id: tiny_raw,
           positive.id: positive_raw, negative.id: negative_raw}
    wanted = raw_unit_pcm(expected, raw)
    assert wanted != raw_unit_pcm(before, raw) and np.count_nonzero(samples(wanted))
    wrong = deepcopy(expected)
    next(clip for clip in wrong.clips if clip.id == 'audio').audio_mix_anchor = (
        AudioMixAnchor(1, 2, 'video'))
    assert not np.count_nonzero(samples(raw_unit_pcm(wrong, raw)))
    old_scene, old_audio = verified_scene(engine, before, before, raw, tmp_path / 'before-order.mkv')
    editor.slip_linked_av('audio', requested)
    assert editor.project == expected
    assert_source_only_change(before, editor.project, 'audio', requested)
    scene, actual = verified_scene(engine, editor.project, expected, raw, tmp_path / 'after-order.mkv')
    assert actual == wanted and actual != old_audio
    # The three-contribution null test still consumes and verifies all video:
    # lower and upper pictures use independently indexed original numbered
    # arrays; no rendered output supplies any expected pixel or frame index.
    pictures = {positive.id: numbered.pictures, tiny.id: numbered.pictures}
    assert_delivery_frames(old_scene, expected_frames(before, pictures)[1])
    wanted_segments, wanted_scene = expected_frames(expected, pictures)
    assert_delivery_frames(scene, wanted_scene)
    np.testing.assert_array_equal(segment_pixels(engine, editor.project, editor._clip('video')),
                                  wanted_segments['video'])
    assert not np.array_equal(scene, old_scene)
    assert len(actual) == boundary(9, fps) * 8
    assert_sources_untouched(paths, fingerprints)
