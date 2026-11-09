"""Timeline annotations must be invisible to real delivery and recovery.

Small lossless numbered-picture/stereo AAC sources establish independent frame
and audio oracles, including offset audio and an exact fractional project rate.
Then active caption/sticker/effect layers exercise the actual compositing path.
Each marker state is rendered by a fresh engine: equality cannot come from a
shared preview result or a compiler fingerprint assertion. Both actual delivery
encodes and a dual-stream, pre-encode stereo-f32 render are fully decoded.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest
from PySide6.QtWidgets import QApplication

from lumen.media import ffmpeg_available, ffprobe_binary
from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Editor, Overlay, OverlayKeyframe, Project
from lumen.project_recovery import RecoveryStore
from lumen.render import RenderEngine
from lumen.sticker_assets import load_sticker
from test_exact_audio_cache_backend import origin_oracle, production_pcm
from test_highbit_caption_reference import latin_font
from test_retime_video import WIDTH, HEIGHT, assert_delivery_frames, decode, ff
from test_source_ranges_media import numbered_aac

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
CASES = ((Fraction(25), .137, 5), (Fraction(25), -.113, 5),
         (Fraction(30000, 1001), .137, 5))


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def marker_sources():
    # These generated media are owned by this fixture and removed even on test
    # failure. Persistent receipts contain digests, not unnecessary media copies.
    with tempfile.TemporaryDirectory(prefix='cutcat-marker-sources-') as directory:
        root = Path(directory)
        yield {case: numbered_aac(root / f'{case[0].numerator}-{case[1]}.mov', *case)
               for case in CASES}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def samples_at(frame, fps):
    exact = Fraction(frame * 48000, 1) / fps
    return (2 * exact.numerator + exact.denominator) // (2 * exact.denominator)


def plain_project(media, fps):
    return Project(name='Persistent timeline marker media proof', width=WIDTH, height=HEIGHT,
                   fps_num=fps.numerator, fps_den=fps.denominator,
                   media=[deepcopy(media)], clips=[
                       Clip('main', media.id, 0, 0, 2, 12),
                       Clip('tail', media.id, 0, 15, 26, 3)])


def independently_selected_pcm(media, project, fps):
    full = origin_oracle(media.path)
    offset = Fraction(str(media.audio_offset))

    def rounded(value):
        return (2 * value.numerator + value.denominator) // (2 * value.denominator)

    def selected(source_start, source_end, timeline_start, timeline_end):
        lo = rounded(max(Fraction(0), Fraction(source_start, 1) / fps - offset) * 48000)
        hi = rounded(max(Fraction(0), Fraction(source_end, 1) / fps - offset) * 48000)
        leading = max(0, rounded((offset - Fraction(source_start, 1) / fps) * 48000))
        count = samples_at(timeline_end, fps) - samples_at(timeline_start, fps)
        # Offset-adjusted source endpoints and timeline endpoints are rounded
        # independently. Do not first truncate to a source-viewer selection's
        # frame budget: that can discard a valid last sample at fractional FPS.
        return (bytes(leading * 8) + full[lo * 8:hi * 8] + bytes(count * 8))[:count * 8]

    silence = bytes((samples_at(15, fps) - samples_at(12, fps)) * 8)
    return selected(2, 14, 0, 12) + silence + selected(26, 29, 15, 18)


def delivery_result(path, fps, frame_count):
    picture = decode(path, rgb16=False)
    assert picture.shape == (frame_count, HEIGHT, WIDTH, 3)
    pcm = ff('-i', path, '-map', '0:a:0', '-vn', '-ar', 48000, '-ac', 2,
             '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1')
    audio = np.frombuffer(pcm, dtype='<f4').reshape(-1, 2)
    expected_count = samples_at(frame_count, fps)
    assert expected_count <= len(audio) < expected_count + 1024  # AAC padding.
    assert np.max(np.abs(audio)) > .05
    assert not np.array_equal(audio[:, 0], audio[:, 1])
    timing = json.loads(subprocess.run(
        [ffprobe_binary(), '-v', 'error', '-show_entries',
         'format=duration:stream=codec_type,r_frame_rate,avg_frame_rate,time_base,'
         'start_time,duration,nb_frames,sample_rate,channels', '-of', 'json', str(path)],
        capture_output=True, text=True, check=True, timeout=40).stdout)
    video = next(stream for stream in timing['streams'] if stream['codec_type'] == 'video')
    assert Fraction(video['avg_frame_rate']) == fps
    assert int(video['nb_frames']) == frame_count
    assert abs(Fraction(video['duration']) - Fraction(frame_count, 1) / fps) <= Fraction(1, 1000000)
    return picture, pcm, timing


def actual_outputs(project, root, name, fps):
    """Independent engines guarantee no cross-state render/preview cache hit."""
    engine = RenderEngine(timeout=45)
    destination = root / f'{name}.mp4'
    try:
        preview_path = engine.preview(project)
        preview = delivery_result(preview_path, fps, project.duration_frames)
        exported = delivery_result(engine.render(project, destination), fps, project.duration_frames)
        exact = production_pcm(engine, project, root, name + '-preencode')
        assert len(exact) == samples_at(project.duration_frames, fps) * 8
        assert np.max(np.abs(np.frombuffer(exact, '<f4'))) > .05
        return {'preview': preview, 'export': exported, 'preencode': exact}
    finally:
        destination.unlink(missing_ok=True)
        engine.close()


def assert_outputs_identical(actual, expected):
    for mode in ('preview', 'export'):
        np.testing.assert_array_equal(actual[mode][0], expected[mode][0])
        assert actual[mode][1] == expected[mode][1]
        assert actual[mode][2] == expected[mode][2]
    assert actual['preencode'] == expected['preencode']


def output_receipt(outputs):
    return {mode: {'frames': len(outputs[mode][0]),
                   'frame_sha256': digest(outputs[mode][0].tobytes()),
                   'decoded_stereo_pcm_bytes': len(outputs[mode][1]),
                   'decoded_stereo_pcm_sha256': digest(outputs[mode][1]),
                   'timing': outputs[mode][2]}
            for mode in ('preview', 'export')} | {
                'preencode_stereo_pcm_bytes': len(outputs['preencode']),
                'preencode_stereo_pcm_sha256': digest(outputs['preencode'])}


def update(editor, marker, **changes):
    replacement = replace(marker, **changes)
    fields = asdict(replacement)
    marker_id = fields.pop('id')
    return editor.update_timeline_marker(marker_id, **fields)


def marker_states(editor, root):
    """All yielded records are immutable snapshots, not a later edited alias."""
    point = editor.add_timeline_marker('Opening <b> 开场', 0,
                                       comment='Literal <script>note</script>', color='yellow')
    yield 'point-added', deepcopy(editor.project)
    editor.add_timeline_marker('Same frame 第二个', 0, color='pink')
    range_marker = editor.add_timeline_marker('Review range', 3, 8,
                                              comment='Exclusive end frame', color='cyan')
    # An annotation beyond the edit must not extend any output stream.
    editor.add_timeline_marker('Future out-of-range', 90, 105, color='red')
    yield 'same-frame-range-outside-added', deepcopy(editor.project)
    point = update(editor, point, name='Renamed <i>标题', comment='Line one\n第二行 & plain text')
    yield 'name-comment-updated', deepcopy(editor.project)
    range_marker = update(editor, range_marker, color='purple')
    yield 'color-updated', deepcopy(editor.project)
    range_marker = update(editor, range_marker, start_frame=4, end_frame=11)
    yield 'range-updated', deepcopy(editor.project)
    point = update(editor, point, start_frame=6, end_frame=7)
    yield 'point-to-range-updated', deepcopy(editor.project)
    point = update(editor, point, end_frame=None)
    yield 'range-to-point-updated', deepcopy(editor.project)
    revision = editor.revision
    update(editor, point)
    assert editor.revision == revision
    marked = deepcopy(editor.project)
    saved = root / 'marker-project.lumen'
    marked.save(saved)
    reopened = Project.load(saved)
    assert reopened == marked
    yield 'saved-reopened', reopened
    store = RecoveryStore(root / 'recovery')
    try:
        entry = store.write(reopened, str(uuid4()), saved)
    finally:
        store.close()
    reader = RecoveryStore(root / 'recovery')
    try:
        restored = reader.load(entry)
        assert restored == marked
        yield 'recovery-restored', restored
    finally:
        reader.close()
    for marker in tuple(editor.project.timeline_markers):
        editor.delete_timeline_marker(marker.id)
    assert not editor.project.timeline_markers
    yield 'all-deleted', deepcopy(editor.project)
    for _ in marked.timeline_markers:
        assert editor.undo()
    assert editor.project == marked
    yield 'delete-undone', deepcopy(editor.project)
    for _ in marked.timeline_markers:
        assert editor.redo()
    assert not editor.project.timeline_markers
    yield 'delete-redone', deepcopy(editor.project)


@pytest.mark.parametrize('fps,offset,origin', CASES,
                         ids=['25-positive-offset', '25-negative-offset', '29.97-positive-offset'])
def test_marker_mutations_save_reopen_recovery_are_real_pixel_pcm_and_duration_neutral(
        marker_sources, app, latin_font, record_property, fps, offset, origin):
    media, pixels = marker_sources[(fps, offset, origin)]
    with tempfile.TemporaryDirectory(prefix='cutcat-marker-render-proof-') as directory:
        root = Path(directory)
        plain = plain_project(media, fps)
        original = actual_outputs(plain, root, 'plain', fps)
        expected_picture = np.concatenate([pixels[2:14], np.zeros((3, HEIGHT, WIDTH, 3), np.uint16),
                                           pixels[26:29]])
        for mode in ('preview', 'export'):
            assert_delivery_frames(original[mode][0], expected_picture)
        expected_pcm = independently_selected_pcm(media, plain, fps)
        assert original['preencode'] == expected_pcm
        decorated = deepcopy(plain)
        decorated.clips[0].effects = [ClipEffect('blur', 'blur', 1.1),
                                      ClipEffect('vignette', 'vignette', .2)]
        decorated.captions = [Caption('caption', 3, 6, 'NOTE', 'en')]
        decorated.caption_style = CaptionStyle(font_family=latin_font, font_size=12,
                                                margin_bottom=2, outline_width=1)
        png = root / 'marker-fixture-sticker.png'
        Image.new('RGBA', (7, 7), (255, 50, 15, 230)).save(png)
        sticker = load_sticker(png)
        decorated.overlays = [Overlay('badge', str(png), sticker.digest, 4, 6,
                                      keyframes=[OverlayKeyframe(0, 2, 2, 1, 1),
                                                 OverlayKeyframe(5, 8, 4, 1.2, .8)])]
        decorated.validate()
        baseline = actual_outputs(decorated, root, 'decorated', fps)
        assert baseline['preencode'] == expected_pcm
        for mode in ('preview', 'export'):
            assert np.count_nonzero(baseline[mode][0] != original[mode][0]) > 100
        hashes = {str(path): digest(path.read_bytes()) for path in (Path(media.path), png)}
        editor = Editor(deepcopy(decorated))
        stages = []
        for name, snapshot in marker_states(editor, root):
            content = snapshot.to_dict(); content.pop('timeline_markers')
            reference = decorated.to_dict(); reference.pop('timeline_markers')
            assert content == reference
            assert snapshot.duration_frames == 18
            actual = actual_outputs(snapshot, root, name, fps)
            assert_outputs_identical(actual, baseline)
            assert all(digest(Path(path).read_bytes()) == value for path, value in hashes.items())
            stages.append(name)
        # Ordinary editing may shorten the project below an annotation. It
        # must retain the marker coordinates, not extend/truncate the export.
        late = editor.add_timeline_marker('Late annotation', 15, 18, color='green')
        future = editor.add_timeline_marker('Future annotation', 90, 105, color='red')
        fixed_markers = deepcopy(editor.project.timeline_markers)
        editor.delete_clip('tail')
        assert editor.project.duration_frames == 12
        assert editor.project.timeline_markers == fixed_markers
        assert [(m.start_frame, m.end_frame) for m in fixed_markers] == [(15, 18), (90, 105)]
        shortened = actual_outputs(editor.project, root, 'shortened-markers-retained', fps)
        editor.delete_timeline_marker(late.id)
        editor.delete_timeline_marker(future.id)
        without = actual_outputs(editor.project, root, 'shortened-without-markers', fps)
        assert_outputs_identical(shortened, without)
        assert shortened['preencode'] == expected_pcm[:samples_at(12, fps) * 8]
        assert all(digest(Path(path).read_bytes()) == value for path, value in hashes.items())
        record_property('shortened_outputs', json.dumps(output_receipt(shortened), sort_keys=True))
        record_property('out_of_range_coordinates_retained_after_shortening', True)
        # Record complete independently obtained baseline stream identities;
        # every stage above proved byte-identical decoded signals and timing.
        for key, value in {'fps': str(fps), 'source_origin': origin,
                           'source_audio_offset': media.audio_offset,
                           'source_sha256': hashes[str(Path(media.path))],
                           'sticker_sha256': hashes[str(png)],
                           'fresh_engine_each_state': True,
                           'stages': json.dumps(stages),
                           'outputs': json.dumps(output_receipt(baseline), sort_keys=True)}.items():
            record_property(key, value)
