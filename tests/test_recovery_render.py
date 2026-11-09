"""Real local-media recovery fidelity, not native-platform package validation."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import numpy as np
from PIL import Image
import pytest

from lumen.caption_render import CaptionRenderError, font_identity
from lumen.clip_transform import ClipTransform
from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Grade, Overlay, OverlayKeyframe, Project
from lumen.project_recovery import RecoveryStore
from lumen.render import RenderEngine
from lumen.sticker_assets import load_sticker
from lumen.timing import ClipTimeMap

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def ff(*args):
    return subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', *map(str, args)],
                          check=True, capture_output=True, timeout=30)


def video_frames(path, width, height):
    result = ff('-i', path, '-an', '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1')
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, height, width, 3)


def audio_samples(path):
    result = ff('-i', path, '-vn', '-ac', '2', '-ar', '48000', '-f', 'f32le', 'pipe:1')
    return np.frombuffer(result.stdout, dtype='<f4')


def hashes(paths):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@pytest.fixture
def saved_edit(tmp_path):
    folder = tmp_path / 'project'; folder.mkdir()
    assets = folder / 'assets'; assets.mkdir()
    source = assets / 'pattern.mkv'
    ff('-f', 'lavfi', '-i', 'testsrc2=size=160x96:rate=30:duration=1',
       '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000:duration=1',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', source)
    media = probe(source)
    lut = assets / 'look.cube'
    lut.write_text('LUT_3D_SIZE 2\n' + '\n'.join(
        f'{r * .8} {g} {b * .9}' for b in (0, 1) for g in (0, 1) for r in (0, 1)) + '\n', encoding='utf-8')
    png = assets / 'badge.png'
    Image.new('RGBA', (12, 12), (255, 80, 20, 210)).save(png)
    sticker = load_sticker(png)
    fonts = assets / 'fonts'; fonts.mkdir()
    for family in ('DejaVu Sans', 'Arial', 'Helvetica'):
        try:
            font_source = Path(font_identity(CaptionStyle(font_family=family))[0])
            break
        except CaptionRenderError:
            continue
    else:
        pytest.skip('No local Latin font for actual caption render')
    font_copy = fonts / font_source.name
    shutil.copyfile(font_source, font_copy)
    first = Clip('trimmed-retimed', media.id, 0, 0, 3, 6,
                 time_map=ClipTimeMap(Fraction(3), Fraction(12), Fraction(3, 2)),
                 grade=Grade(exposure=-.3, lut_path=str(lut), lut_strength=.6),
                 gain_db=-4, fade_in=2, fade_out=2,
                 effects=[ClipEffect('blur', 'blur', .8), ClipEffect('vignette', 'vignette', .25)],
                 transform=ClipTransform(x=8, y=-3, scale=.85, rotation=12, mirror_x=True))
    second = Clip('second-cut', media.id, 0, 6, 15, 6,
                  gain_db=-9, fade_in=1, fade_out=2,
                  transform=ClipTransform(mirror_y=True))
    project = Project(name='Edited recovery fixture', width=160, height=96, media=[media],
                      clips=[first, second],
                      captions=[Caption('caption', 2, 7, 'Saved edit', 'en')],
                      caption_style=CaptionStyle(font_family=family, font_directory=str(fonts),
                                                 font_size=16, margin_bottom=6, outline_width=1),
                      overlays=[Overlay('badge', str(png), sticker.digest, 4, 5,
                                        keyframes=[OverlayKeyframe(0, 8, 5, 1, .8),
                                                   OverlayKeyframe(4, 24, 8, 1.3, .5)])])
    path = folder / 'edit.lumen'
    project.save(path)
    inputs = [source, lut, png, font_copy, path]
    return path, inputs


@pytest.mark.parametrize('mode', ['export', 'preview'])
def test_edited_media_recovery_preserves_decoded_frames_audio_and_relative_inputs(saved_edit, tmp_path, monkeypatch, mode):
    path, inputs = saved_edit
    saved_bytes = hashes(inputs)
    serialized = json.loads(path.read_text(encoding='utf-8'))
    assert not Path(serialized['media'][0]['path']).is_absolute()
    assert not Path(serialized['clips'][0]['grade']['lut_path']).is_absolute()
    assert not Path(serialized['overlays'][0]['asset_path']).is_absolute()
    assert not Path(serialized['caption_style']['font_directory']).is_absolute()
    expected = Project.load(path)
    # Even an in-memory project still holding relative references must bind to
    # its source project's directory, not recovery storage or a later cwd.
    relative = Project.from_dict(serialized)
    before_relative = deepcopy(relative.to_dict())
    root = tmp_path / 'separate-recovery'
    writer = RecoveryStore(root)
    try:
        entry = writer.write(relative, str(uuid.uuid4()), path)
        assert relative.to_dict() == before_relative  # Autosave is not an edit.
    finally:
        writer.close()
    elsewhere = tmp_path / 'unrelated-working-directory'; elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    reader = RecoveryStore(root)
    original_engine = RenderEngine(timeout=45)
    restored_engine = RenderEngine(timeout=45)
    try:
        restored = reader.load(entry)
        assert restored.to_dict() == expected.to_dict()
        assert restored.duration_frames == 12 and not restored.missing_media()
        assert restored.clips[0].speed == Fraction(3, 2)
        assert all(Path(media.path).is_absolute() for media in restored.media)
        assert Path(restored.clips[0].grade.lut_path) == inputs[1]
        assert Path(restored.overlays[0].asset_path) == inputs[2]
        assert Path(restored.caption_style.font_directory) == inputs[3].parent
        if mode == 'export':
            before = original_engine.render(expected, tmp_path / 'before.mp4')
            after = restored_engine.render(restored, tmp_path / 'after.mp4')
        else:
            before = original_engine.preview(expected)
            after = restored_engine.preview(restored)
        original_frames = video_frames(before, 160, 96)
        restored_frames = video_frames(after, 160, 96)
        assert original_frames.shape == (12, 96, 160, 3)
        assert np.count_nonzero(original_frames) > 10_000
        np.testing.assert_array_equal(original_frames, restored_frames)
        original_audio = audio_samples(before)
        assert original_audio.size > 1000 and np.max(np.abs(original_audio)) > .01
        np.testing.assert_array_equal(original_audio, audio_samples(after))
        # Separate engines ensure equality is not a shared cached file hit.
        assert before != after
        assert hashes(inputs) == saved_bytes
    finally:
        reader.close()
        original_engine.close()
        restored_engine.close()


def test_recovery_render_missing_media_remains_relinkable_without_changing_source(saved_edit, tmp_path):
    path, inputs = saved_edit
    original = Project.load(path)
    store = RecoveryStore(tmp_path / 'recovery')
    try:
        entry = store.write(original, str(uuid.uuid4()), path)
        store.release(entry.identity)
        missing = inputs[0]
        relocated = missing.with_name('relocated.mkv')
        missing.rename(relocated)
        restored = store.load(entry)
        assert restored.to_dict() == original.to_dict()
        assert [m.path for m in restored.missing_media()] == [str(missing)]
        assert relocated.is_file() and entry.path.is_file()
        # An unavailable input is not an invitation to rewrite paths or delete
        # edits. The user can explicitly relink it through the existing flow.
        assert restored.clips[0].effects == original.clips[0].effects
        assert restored.clips[0].transform == original.clips[0].transform
        assert restored.captions == original.captions
    finally:
        store.close()
