"""Real-media acceptance for the offline, selected-object proposal boundary.

The oracle is decoded picture plus pre-AAC PCM, not only project fields or a
generated filter string. Sources are tiny deterministic local WAVs; no model,
network, user assets, audio device, or native build is used.
"""
from copy import deepcopy
from fractions import Fraction
import json
import wave

import numpy as np
import pytest

from lumen.controlled_assistant import ProposalGate
from lumen.media import ffmpeg_available, probe
from lumen.model import Caption, CaptionStyle, Clip, Editor, Project
from test_caption_render import latin_font  # noqa: F401: local-font fixture
from test_retime_video import engine, ff  # noqa: F401: bounded-render fixture


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
WIDTH, HEIGHT, SAMPLE_RATE = 320, 180, 48000


def sample_boundary(frame, fps):
    """Independent rational nearest-sample oracle, including NTSC frame gates."""
    exact = Fraction(frame * SAMPLE_RATE) / fps
    return (2 * exact.numerator + exact.denominator) // (2 * exact.denominator)


def picture(path):
    raw = ff('-protocol_whitelist', 'file,pipe', '-i', path, '-map', '0:V:0',
             '-an', '-threads', '1', '-fps_mode', 'passthrough', '-pix_fmt',
             'rgb24', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(raw, np.uint8).reshape(-1, HEIGHT, WIDTH, 3)


def pcm(path):
    raw = ff('-protocol_whitelist', 'file,pipe', '-i', path, '-vn', '-ar',
             SAMPLE_RATE, '-ac', '1', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    return np.frombuffer(raw, '<f4')


def lossless_output(renderer, project, path):
    # Consume both real production outputs, as normal delivery does. A
    # nullsink/audio-only shortcut can terminate before the last silence block.
    inputs, graph, _ = renderer._compile(project)
    ff(*inputs, '-filter_complex_threads', '1', '-filter_complex', graph,
       '-map', '[vout]', '-map', '[aout]', '-threads', '1', '-c:v', 'ffv1',
       '-c:a', 'pcm_f32le', path)
    return picture(path), pcm(path)


def synthetic_project(tmp_path, fps, family):
    path = tmp_path / 'synthetic-tones.wav'
    time = np.arange(SAMPLE_RATE * 3) / SAMPLE_RATE
    samples = .12 * np.sin(2 * np.pi * 440 * time) + .04 * np.sin(2 * np.pi * 997 * time)
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        output.writeframes(np.rint(samples * 32767).astype('<i2').tobytes())
    media = probe(path)
    project = Project(
        name='Controlled proposal media invariants', width=WIDTH, height=HEIGHT,
        fps_num=fps.numerator, fps_den=fps.denominator, media=[media],
        clips=[
            Clip('selected-audio', media.id, 2, 3, 4, 15, gain_db=-3,
                 fade_in=2, fade_out=3),
            Clip('unselected-audio', media.id, 2, 21, 42, 15, gain_db=-5,
                 fade_in=1, fade_out=2),
            Clip('selected-muted', media.id, 3, 1, 2, 40, gain_db=5,
                 muted=True, fade_in=4, fade_out=5),
        ],
        captions=[Caption('selected-caption', 5, 6, 'OLD', 'en-US'),
                  Caption('unselected-caption', 22, 5, 'KEEP', 'en-GB')],
        caption_style=CaptionStyle(font_family=family, font_size=28,
                                   color='#EFEFFF', outline_color='#191919',
                                   outline_width=1, margin_bottom=17,
                                   safe_margin_percent=8, max_lines=3),
    )
    project.validate()
    return project


def assert_caption_gates(frames, project):
    assert len(frames) == project.duration_frames == 41
    expected = set()
    for caption in project.captions:
        expected.update(range(caption.start, caption.end))
    for index, frame in enumerate(frames):
        if index in expected:
            assert np.count_nonzero(frame > 160) > 100, index
        else:
            assert frame.max() < 8, index


@pytest.mark.parametrize('fps', [Fraction(30), Fraction(30000, 1001)], ids=['30', '29.97'])
@pytest.mark.parametrize('delta_db', [-6, 6], ids=['quieter', 'louder'])
def test_controlled_caption_and_gain_real_output_undo_redo(
        tmp_path, engine, latin_font, fps, delta_db, record_property):
    editor = Editor(synthetic_project(tmp_path, fps, latin_font))
    before = deepcopy(editor.project)
    original_dict = before.to_dict()
    original_picture, original_pcm = lossless_output(engine, before, tmp_path / 'before.mkv')
    original_preview = engine.preview(before)
    original_preview_picture = picture(original_preview)
    assert_caption_gates(original_preview_picture, before)

    gate = ProposalGate(editor)
    caption_ids = ('selected-caption',)
    clip_ids = ('selected-audio', 'selected-muted')
    request = gate.begin(caption_ids=caption_ids, clip_ids=clip_ids,
                         adapter='agents-sdk-local-fake')
    new_gain = -3 + delta_db
    proposal = gate.prepare(request, json.dumps({'schema_version': 1, 'commands': [
        {'op': 'caption.replace_text', 'target_id': caption_ids[0], 'text': 'NEW TEXT'},
        {'op': 'clip.set_gain_db', 'target_id': clip_ids[0], 'gain_db': new_gain},
        {'op': 'clip.set_gain_db', 'target_id': clip_ids[1], 'gain_db': -18},
    ]}))
    # Merely reviewing a proposal cannot mutate the project or its preview.
    assert editor.project.to_dict() == original_dict
    assert engine.preview(editor.project) == original_preview
    assert len(proposal.diff) == 3
    receipt = gate.apply(proposal, gate.approve(proposal),
                         caption_ids=caption_ids, clip_ids=clip_ids)
    assert receipt.changed and receipt.status == 'applied'

    expected = deepcopy(before)
    expected.captions[0].text = 'NEW TEXT'
    expected.clips[0].gain_db = new_gain
    expected.clips[2].gain_db = -18
    # Full-project equality proves IDs, exact timing, language, all style,
    # fades, mute, source selection, and every unselected object are unchanged.
    assert editor.project.to_dict() == expected.to_dict()
    changed_picture, changed_pcm = lossless_output(engine, editor.project, tmp_path / 'after.mkv')
    assert len(changed_pcm) == len(original_pcm) == sample_boundary(41, fps)
    assert_caption_gates(changed_picture, expected)
    caption_start, caption_end = 5, 11
    np.testing.assert_array_equal(changed_picture[:caption_start], original_picture[:caption_start])
    np.testing.assert_array_equal(changed_picture[caption_end:], original_picture[caption_end:])
    assert np.count_nonzero(changed_picture[caption_start:caption_end] !=
                            original_picture[caption_start:caption_end]) > 1000
    # Each visible frame uses the replacement text, with no frame-gate drift.
    for frame in changed_picture[caption_start + 1:caption_end]:
        np.testing.assert_array_equal(frame, changed_picture[caption_start])

    audio_start, audio_end = sample_boundary(3, fps), sample_boundary(18, fps)
    factor = 10 ** (delta_db / 20)
    np.testing.assert_allclose(changed_pcm[audio_start:audio_end],
                               original_pcm[audio_start:audio_end] * factor,
                               rtol=3e-6, atol=2e-7)
    # Exact zero/change boundaries and untouched samples, including the
    # unselected clip with its own distinct gain/fades and the muted-only tail.
    np.testing.assert_array_equal(changed_pcm[:audio_start], original_pcm[:audio_start])
    np.testing.assert_array_equal(changed_pcm[audio_end:], original_pcm[audio_end:])
    for start, end in ((0, 3), (18, 21), (36, 41)):
        assert np.count_nonzero(changed_pcm[sample_boundary(start, fps):sample_boundary(end, fps)]) == 0
    middle = slice(sample_boundary(7, fps), sample_boundary(12, fps))
    original_rms = np.sqrt(np.mean(original_pcm[middle].astype(np.float64) ** 2))
    changed_rms = np.sqrt(np.mean(changed_pcm[middle].astype(np.float64) ** 2))
    assert original_rms > .03
    assert changed_rms / original_rms == pytest.approx(factor, rel=3e-6)
    record_property('actual_pcm_gain_ratio', float(changed_rms / original_rms))
    record_property('expected_pcm_gain_ratio', factor)
    record_property('exact_samples', len(changed_pcm))

    changed_preview = engine.preview(editor.project)
    assert changed_preview != original_preview
    for output in (changed_preview, engine.render(editor.project, tmp_path / 'export.mp4')):
        assert_caption_gates(picture(output), expected)
        delivered_pcm = pcm(output)
        assert len(changed_pcm) <= len(delivered_pcm) < len(changed_pcm) + 1024
        # AAC delivery is lossy; measure the stable interior rather than claim
        # bit equality or a sample-exact boundary through codec padding.
        delivered_rms = np.sqrt(np.mean(delivered_pcm[middle].astype(np.float64) ** 2))
        assert delivered_rms == pytest.approx(changed_rms, rel=.035)

    saved = tmp_path / 'edited.lumen'
    editor.project.save(saved)
    assert Project.load(saved).to_dict() == expected.to_dict()
    assert engine.preview(Project.load(saved)) == changed_preview
    # One atomic history entry restores the full combined proposal. Verify
    # freshly compiled outputs too, not only cache identity or model equality.
    assert editor.undo() and editor.project.to_dict() == original_dict
    assert engine.preview(editor.project) == original_preview
    undo_picture, undo_pcm = lossless_output(engine, editor.project, tmp_path / 'undo.mkv')
    np.testing.assert_array_equal(undo_picture, original_picture)
    np.testing.assert_array_equal(undo_pcm, original_pcm)
    np.testing.assert_array_equal(picture(engine.preview(editor.project)), original_preview_picture)
    assert editor.redo() and editor.project.to_dict() == expected.to_dict()
    assert engine.preview(editor.project) == changed_preview
    redo_picture, redo_pcm = lossless_output(engine, editor.project, tmp_path / 'redo.mkv')
    np.testing.assert_array_equal(redo_picture, changed_picture)
    np.testing.assert_array_equal(redo_pcm, changed_pcm)
