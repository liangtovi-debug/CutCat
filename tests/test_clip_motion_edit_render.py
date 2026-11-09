"""Rendered edit continuity, with numbered media and an independent time oracle.

Common absolute frames must not change when an animated picture is split,
left-trimmed/extended, or its incoming cut is rolled. Inspect full-chroma
source-over and its straight-alpha layer before chroma reduction/codec loss,
then separately check the actual compiled timeline output. No expected motion
value is obtained from the production keyframe evaluator.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

import numpy as np
import pytest

from lumen.clip_composite import clip_composite_filters
from lumen.clip_motion import ClipMotionKeyframe as K
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available
from lumen.model import Clip, Editor
from lumen.timing import ClipTimeMap
from test_clip_composite_pixels import pack_planes, underlay
from test_clip_motion_render import H, W, compiled, engine, project, sources
from test_color_highbit_reference import ff


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg required")


def editable_project(sources):
    """Six full source intervals and one genuinely fractional final interval."""
    p = project(sources, Fraction(3, 2))
    p.clips[0].duration = 14
    top = p.clips[1]
    top.start = 4
    top.time_map = ClipTimeMap(Fraction(9, 2), Fraction(55, 4), Fraction(3, 2))
    top.source_in, top.duration = top.time_map.source_in, 7
    top.transform = ClipTransform(x=99, y=-99, scale=2, rotation=19, mirror_x=True)
    top.motion_keyframes = [
        K(-4, x=-8, y=4, scale=.4, opacity=.2),
        K(2, x=2, y=1, scale=.55, opacity=.5, interpolation='hold'),
        K(5, x=10, y=-3, scale=.8, opacity=.9),
        K(12, x=-20, y=6, scale=.45, opacity=.3),
    ]
    # Rendering picture continuity must not accidentally depend on audio edits.
    for clip in p.clips:
        clip.muted = True
    p.validate()
    return p


def full_chroma_picture(engine, p, clip, high, tmp_path, name):
    """Actual retime cache -> geometric layer -> raw full-chroma source-over.

    Preserve the layer's own alpha as a second output; comparison of flattened
    RGB alone could miss a coverage error against a coincident source color.
    The reference underlay is identical at every absolute frame.
    """
    source = next(m for m in p.media if m.id == clip.media_id)
    segment = engine._graded_segment(source, clip, p, None)
    raw_source = ff('-i', segment, '-map', '0:V:0', '-pix_fmt', 'gbrp16le',
                    '-fps_mode', 'passthrough', '-f', 'rawvideo', 'pipe:1')
    prepared = np.frombuffer(raw_source, '<u2').reshape(clip.duration, 3, H, W)
    prepared = prepared.transpose(0, 2, 3, 1)[..., [2, 0, 1]]

    main, layer, over = clip_composite_filters(
        clip.composite, clip.transform, W, H,
        high_bit=high, motion_keys=clip.motion_keyframes)
    full_format = 'yuv444p10le' if high else 'yuv444p'
    alpha_format = 'yuva444p10le' if high else 'yuva444p'
    picture_format = 'yuv422p10le' if high else 'yuv420p'
    rate = f'{p.fps_num}/{p.fps_den}'
    # Stop at native full-chroma source-over, before the normal area reduction.
    source_over = over.partition(',')[0]
    graph = (f'[0:v]{main}[main];[1:v]setpts=PTS-STARTPTS,'
             f'fps=fps={rate}:start_time=0,{layer},split=2[layer][alpha];'
             f'[main][layer]{source_over}[out]')
    alpha_path = tmp_path / f'{name}.raw'
    raw = ff('-noauto_conversion_filters', '-filter_complex_threads', '1',
             '-f', 'rawvideo', '-pix_fmt', picture_format, '-s', f'{W}x{H}',
             '-r', rate, '-i', 'pipe:0', '-i', segment,
             '-filter_complex', graph, '-map', '[out]', '-frames:v', clip.duration,
             '-fps_mode', 'passthrough', '-c:v', 'rawvideo', '-threads', '1',
             '-pix_fmt', full_format, '-f', 'rawvideo', 'pipe:1',
             '-map', '[alpha]', '-frames:v', clip.duration,
             '-fps_mode', 'passthrough', '-c:v', 'rawvideo', '-threads', '1',
             '-pix_fmt', alpha_format, '-f', 'rawvideo', alpha_path,
             data=pack_planes(underlay(high), high) * clip.duration)
    dtype = '<u2' if high else 'u1'
    composite = np.frombuffer(raw, dtype).reshape(clip.duration, 3, H, W)
    straight_alpha = np.frombuffer(alpha_path.read_bytes(), dtype).reshape(clip.duration, 4, H, W)
    return prepared, composite, straight_alpha


def assert_numbered_source(prepared, sources, origin, speed):
    # Independent floor source selection from original media, not model timing
    # helpers, motion evaluator, or a second invocation of the renderer.
    indices = [(origin + frame * speed).__floor__() for frame in range(len(prepared))]
    assert len(set(indices)) == len(indices), 'This speed must select distinct numbered source frames'
    for received, index in zip(prepared, indices):
        np.testing.assert_allclose(received, sources[2][index], atol=3, rtol=0)


def assert_hidden_handles(original, edited, delta):
    assert edited.motion_keyframes == [replace(key, frame=key.frame - delta)
                                       for key in original.motion_keyframes]
    assert edited.motion_keyframes[0].frame < 0
    assert edited.motion_keyframes[-1].frame > edited.duration
    assert edited.transform == original.transform
    assert edited.composite == original.composite


@pytest.mark.parametrize('edit', ['split', 'left_trim', 'left_extend', 'roll_right'])
@pytest.mark.parametrize('high', [False, True])
def test_actual_animated_picture_keeps_common_absolute_frames_after_edit(
        sources, engine, tmp_path, edit, high):
    original = editable_project(sources)
    target = original.clips[1]
    if edit == 'roll_right':
        # An independent outgoing picture allows the incoming animated clip's
        # source/animation origin to move without changing either outer edge.
        outgoing = Clip('outgoing', original.media[1].id, 1, 1, 0, 3, muted=True,
                        time_map=ClipTimeMap(Fraction(1, 2), Fraction(17, 4), Fraction(3, 2)),
                        composite=deepcopy(target.composite),
                        motion_keyframes=[K(0, x=-14, scale=.3, opacity=.6)])
        original.clips.append(outgoing)
    original.validate()
    before_output, _ = compiled(engine, original, high)
    before = full_chroma_picture(engine, original, target, high, tmp_path, 'before')
    assert_numbered_source(before[0], sources, Fraction(9, 2), Fraction(3, 2))
    # Make a degenerate/frozen animation or unreadable alpha test fail rather
    # than produce a misleading continuity pass.
    assert not np.array_equal(before[1][0], before[1][4])
    assert not np.array_equal(before[2][0, 3], before[2][4, 3])

    editor = Editor(deepcopy(original))
    if edit == 'split':
        left, right = editor.split_clip(target.id, target.start + 3)
        changed = [(left, 0), (right, 3)]
        assert left.motion_keyframes == target.motion_keyframes
        assert left.motion_keyframes is not right.motion_keyframes
        assert left.source_out == Fraction(9)
        assert right.source_in == Fraction(9)
        common = range(original.duration_frames)
    elif edit == 'roll_right':
        left, right = editor.roll_cut('outgoing', target.start + 2)
        changed = [(right, 2)]
        assert left.start == 1 and left.end == 6
        assert left.source_out == Fraction(29, 4)
        assert left.motion_keyframes == outgoing.motion_keyframes
        common = [frame for frame in range(original.duration_frames) if frame not in (4, 5)]
    else:
        delta = 2 if edit == 'left_trim' else -2
        right = editor.trim_edge(target.id, 'left', delta)
        changed = [(right, delta)]
        common = [frame for frame in range(original.duration_frames)
                  if frame not in ((4, 5) if delta > 0 else (2, 3))]

    assert editor.project.duration_frames == original.duration_frames
    for index, (clip, delta) in enumerate(changed):
        assert_hidden_handles(target, clip, delta)
        assert clip.source_in == Fraction(9, 2) + delta * Fraction(3, 2)
        if edit != 'split' or delta:
            assert clip.source_out == Fraction(55, 4), 'Never round away the fractional source tail'
        after = full_chroma_picture(engine, editor.project, clip, high, tmp_path, f'after-{index}')
        assert_numbered_source(after[0], sources, Fraction(9, 2) + delta * Fraction(3, 2), Fraction(3, 2))
        for absolute in range(max(clip.start, target.start), min(clip.end, target.end)):
            old_local, new_local = absolute - target.start, absolute - clip.start
            for old_frames, new_frames in zip(before, after):
                # Rebased dynamic expressions have identical arithmetic at a
                # common absolute frame: source, geometry, opacity and samples.
                np.testing.assert_array_equal(new_frames[new_local], old_frames[old_local],
                                              err_msg=f"{edit}: absolute frame {absolute}")

    after_output, _ = compiled(engine, editor.project, high)
    for absolute in common:
        for actual, expected in zip(after_output[absolute], before_output[absolute]):
            np.testing.assert_array_equal(actual, expected,
                                          err_msg=f"{edit}: compiled absolute frame {absolute}")
