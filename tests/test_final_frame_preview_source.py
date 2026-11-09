"""Bounded work/dependency/rejection contracts, independent of codec pixels."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import threading

import numpy as np
import pytest

from lumen import final_frame_preview as module
from lumen.clip_composite import ClipComposite
from lumen.clip_motion import ClipMotionKeyframe
from lumen.model import Clip, ClipEffect, Media, Project


@pytest.fixture
def scene(tmp_path, monkeypatch):
    path = tmp_path / 'current.mkv'
    path.write_bytes(b'current-source')
    project = Project(width=64, height=48, fps_num=25,
        media=[Media('m', str(path), 'current', 30, 64, 48, fps_num=25),
               Media('tail', str(tmp_path / 'unreadable-tail.mkv'), 'tail', 30, 64, 48, fps_num=25),
               Media('audio', str(tmp_path / 'unreadable-audio.wav'), 'audio', 30,
                     has_video=False, has_audio=True)],
        clips=[Clip('current', 'm', 0, 0, 0, 10), Clip('tail', 'tail', 0, 200, 0, 100),
               Clip('audio', 'audio', 2, 0, 0, 300)])
    calls, commands, graphs = [], [], []

    def grade(project, clip_id, frame, cancel, timeout):
        calls.append((clip_id, frame))
        return SimpleNamespace(frame=np.full((48, 64, 3), .4, np.float32), source_frame=frame)

    def run(args, budget, cancel, deadline):
        cancel.is_set()
        commands.append(args)
        if '-/filter_complex' in args:
            graphs.append(Path(args[args.index('-/filter_complex') + 1]).read_text())
        return bytes(budget)

    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', grade)
    monkeypatch.setattr(module, '_self_contained_source', lambda *a: ('matroska',))
    monkeypatch.setattr(module, '_run', run)
    from lumen import clip_composite
    for name in ('verify_clip_composite_support', 'verify_clip_motion_support', 'ensure_composite_rgb8_blur_support'):
        monkeypatch.setattr(clip_composite, name, lambda *a, **k: None)
    return project, calls, commands, graphs


def test_only_active_target_and_no_audio_or_full_render(scene, monkeypatch):
    project, calls, commands, graphs = scene
    from lumen.render import RenderEngine
    for name in ('render', 'preview', 'frame', '_compile', '_graded_segment', '_overlay_segment'):
        monkeypatch.setattr(RenderEngine, name, lambda *a, **k: pytest.fail('Full renderer entered'))
    before = deepcopy(project)
    snapshot = module.capture_final_frame_preview(project, 4)
    assert calls == [('current', 4)]
    assert project == before
    assert snapshot.work.active_clip_ids == ('current',)
    assert snapshot.work.graded_frames == snapshot.work.composite_frames == 1
    assert snapshot.work.sticker_frames == 0
    assert snapshot.work.source_decode_may_traverse_earlier_frames
    assert len(snapshot.yuv420p) == 64 * 48 * 3 // 2
    assert len(snapshot.rgb_bytes) == 64 * 48 * 3
    assert all('-an' in c and '-frames:v' in c for c in commands)
    assert all('unreadable' not in str(c) for c in commands)
    assert 'anullsrc' not in graphs[0] and 'amix' not in graphs[0]
    assert 'trim=end_frame=1' in graphs[0]
    assert module.verify_final_frame_preview(snapshot, project, 4)
    assert not module.verify_final_frame_preview(snapshot, project, 5)
    with pytest.raises(FrozenInstanceError):
        snapshot.stage = 'grade_preview'


def test_unrelated_tail_duration_does_not_change_picture_work(scene):
    project, calls, commands, _ = scene
    first = module.capture_final_frame_preview(project, 4)
    project.clips[1].start = 400
    second = module.capture_final_frame_preview(project, 4)
    assert first.yuv420p == second.yuv420p
    assert first.work == second.work
    assert calls == [('current', 4), ('current', 4)]
    assert first.identity.project_signature != second.identity.project_signature


def test_gap_is_black_composition_without_media_work(scene):
    project, calls, commands, graphs = scene
    snapshot = module.capture_final_frame_preview(project, 100)
    assert not calls
    assert snapshot.work.graded_frames == 0
    assert snapshot.work.active_clip_ids == ()
    assert snapshot.work.composite_frames == 1
    assert 'color=c=black' in graphs[0]
    assert module.verify_final_frame_preview(snapshot, project, 100)


def test_motion_uses_original_local_N_and_exact_effect_order(scene):
    project, calls, commands, graphs = scene
    clip = project.clips[0]
    clip.start = 3
    clip.composite = ClipComposite(True, .6)
    clip.motion_keyframes = [ClipMotionKeyframe(-2, x=-3, opacity=.2),
                             ClipMotionKeyframe(8, x=11, opacity=.8)]
    clip.effects = [ClipEffect('blur-id', 'blur', 2), ClipEffect('vignette-id', 'vignette', .3)]
    module.capture_final_frame_preview(project, 9)
    graph = graphs[0]
    assert 'N' not in graph
    assert '((6-(-2))/(8--2))' in graph
    assert graph.index('gblur=') < graph.index('vignette=') < graph.index('geq=')
    assert graph.index('geq=') < graph.index('alpha=straight')
    assert '-cpuflags' in commands[0]


@pytest.mark.parametrize('width,height', [(1922, 1080), (1920, 1082), (63, 48), (64, 47)])
def test_unsupported_canvas_rejects_before_source_work(scene, width, height):
    project, calls, commands, _ = scene
    project.width, project.height = width, height
    with pytest.raises(module.FinalFramePreviewError, match='Build preview'):
        module.capture_final_frame_preview(project, 4)
    assert not calls and not commands


@pytest.mark.parametrize('frame', [-1, 300, 1.5, True])
def test_invalid_frame_never_clamps_or_renders(scene, frame):
    project, calls, commands, _ = scene
    with pytest.raises(module.FinalFramePreviewError, match='integer timeline frame'):
        module.capture_final_frame_preview(project, frame)
    assert not calls and not commands


def test_active_stabilization_and_apple_log_reject_explicitly(scene):
    project, calls, commands, _ = scene
    project.clips[0].stabilization.enabled = True
    project.clips[0].stabilization.strength = 1
    with pytest.raises(module.FinalFramePreviewError, match='active stabilization.*Build preview'):
        module.capture_final_frame_preview(project, 4)
    project.clips[0].stabilization.enabled = False
    project.clips[0].grade.input_encoding = 'apple_log_bt2020'
    with pytest.raises(module.FinalFramePreviewError, match='Apple Log.*Build preview'):
        module.capture_final_frame_preview(project, 4)
    assert not calls and not commands


def test_cancel_before_work_and_missing_partial_output(scene, monkeypatch):
    project, calls, commands, _ = scene
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(module.FinalFramePreviewCancelled):
        module.capture_final_frame_preview(project, 4, cancel)
    assert not calls and not commands
    monkeypatch.setattr(module, '_run', lambda *a: b'incomplete')
    with pytest.raises(module.FinalFramePreviewError, match='incomplete frame'):
        module.capture_final_frame_preview(project, 4)


def test_source_mutation_cannot_publish_or_verify(scene, monkeypatch):
    project, calls, commands, _ = scene
    snapshot = module.capture_final_frame_preview(project, 4)
    source = Path(project.media[0].path)
    source.write_bytes(b'changed-source')
    assert not module.verify_final_frame_preview(snapshot, project, 4)
    original = module.capture_grade_preview_snapshot
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_bytes(b'changed-source-again')
        return result
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot', change)
    with pytest.raises(module.FinalFramePreviewError, match='dependency changed'):
        module.capture_final_frame_preview(project, 4)


def test_lut_content_checked_even_with_stable_stat(scene, tmp_path, monkeypatch):
    project, calls, commands, _ = scene
    lut = tmp_path / 'look.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + '0 0 0\n' * 8)
    project.clips[0].grade.lut_path = str(lut)
    from lumen import color_picker_source
    original = color_picker_source._source_identity
    fixed = original(lut)
    monkeypatch.setattr(color_picker_source, '_source_identity',
        lambda p: fixed if Path(p) == lut else original(p))
    snapshot = module.capture_final_frame_preview(project, 4)
    lut.write_text('LUT_3D_SIZE 2\n' + '1 0 0\n' * 8)
    assert not module.verify_final_frame_preview(snapshot, project, 4)


def test_pure_signature_never_reads_paths(scene, monkeypatch):
    project, *_ = scene
    monkeypatch.setattr(Path, 'stat', lambda *a, **k: pytest.fail('GUI filesystem access'))
    assert module.project_signature(project) == module.project_signature(deepcopy(project))


def test_active_transition_rejects_but_inactive_interval_is_not_rendered(scene):
    from lumen.video_transition import VideoTransition
    project, calls, commands, _ = scene
    project.clips = [Clip('left', 'm', 0, 0, 0, 10), Clip('right', 'm', 0, 10, 20, 10)]
    project.video_transitions = [VideoTransition('transition', 'left', 'right', 4)]
    with pytest.raises(module.FinalFramePreviewError, match='active video transition.*Build preview'):
        module.capture_final_frame_preview(project, 9)
    assert not calls and not commands
    snapshot = module.capture_final_frame_preview(project, 4)
    assert snapshot.work.active_clip_ids == ('left',)
    assert calls == [('left', 4)]


def test_inactive_composite_blur_preserves_global_scalar_flag_without_tail_decode(scene):
    project, calls, commands, _ = scene
    tail = project.clips[1]
    tail.composite = ClipComposite(True, .5)
    tail.effects = [ClipEffect('tail-blur', 'blur', 1)]
    module.capture_final_frame_preview(project, 4)
    assert calls == [('current', 4)]
    assert '-cpuflags' in commands[0]
    assert commands[0][commands[0].index('-cpuflags') + 1] == '0'


def test_copy_race_cannot_tag_new_pixels_with_old_metadata(scene, monkeypatch):
    project, calls, commands, _ = scene
    original = module.deepcopy
    def changed(value):
        copied = original(value)
        copied.clips[0].grade.exposure += 1
        return copied
    monkeypatch.setattr(module, 'deepcopy', changed)
    with pytest.raises(module.FinalFramePreviewError, match='changed while detaching'):
        module.capture_final_frame_preview(project, 4)
    assert not calls and not commands


@pytest.mark.parametrize('timeout', [True, 0, -1, float('nan'), float('inf'), '30'])
def test_invalid_total_deadline_rejects_without_work(scene, timeout):
    project, calls, commands, _ = scene
    with pytest.raises(module.FinalFramePreviewError, match='positive and finite'):
        module.capture_final_frame_preview(project, 4, timeout=timeout)
    assert not calls and not commands


def test_actual_transparent_source_is_not_silently_stripped(tmp_path):
    from PIL import Image
    from lumen.media import ffmpeg_available
    if not ffmpeg_available(): pytest.skip('FFmpeg/ffprobe required')
    path = tmp_path / 'transparent.png'
    Image.new('RGBA', (16, 16), (80, 120, 190, 128)).save(path)
    project = Project(width=16, height=16, fps_num=25,
        media=[Media('source', str(path), 'alpha', 1, 16, 16, fps_num=25)],
        clips=[Clip('clip', 'source', 0, 0, 0, 1)])
    with pytest.raises(module.FinalFramePreviewError, match='transparent.*Build preview'):
        module.capture_final_frame_preview(project, 0)
