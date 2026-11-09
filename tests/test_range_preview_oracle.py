"""Real pre-encoder A/V bytes against isolated, hash-pinned accepted source.

No expected pixels/samples come from the candidate range path. The complete
accepted final graph runs first; its finished YUV/PCM outputs are then sliced.
"""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import pytest

from lumen.media import ffmpeg_available, ffmpeg_binary, probe
from lumen.model import Clip, ClipEffect, Media
from lumen.range_preview import capture_range_preview, verify_range_preview
from lumen.render import RenderEngine
from lumen.timing import sample_boundary
from scripts.benchmark_range_preview import (ARCHIVE_SHA256, candidate_range_av,
    expected_range, frozen_archive, frozen_full_av, make_av_scene, make_av_source)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


@pytest.mark.parametrize('mode', ['different-rate', 'vfr'])
def test_legacy_nonzero_source_trim_retains_original_fps_phase(tmp_path, mode):
    source = make_av_source(tmp_path / 'original', Fraction(30), count=48, sample_rate=44100)
    if mode == 'vfr':
        path = tmp_path / 'vfr.mkv'
        # Distinct source pictures with periodically lengthened presentation
        # intervals. No range candidate operation participates in construction.
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-i', source.path,
            '-map', '0:V:0', '-map', '0:a:0', '-vf', 'settb=1/30,setpts=N+floor(N/3)',
            '-fps_mode', 'vfr', '-c:v', 'ffv1', '-threads', '1', '-c:a', 'copy', str(path)],
            capture_output=True, check=True, timeout=30)
        source = probe(path)
    project = make_av_scene(tmp_path / 'scene', Fraction(25), source=source, variant='legacy')
    # Nonzero source_in takes the renderer's seconds-before-fps legacy branch.
    # Evidence cwd must not be able to impersonate either application's imports.
    shadow_reference = tmp_path / 'full/lumen'
    shadow_reference.mkdir(parents=True)
    (shadow_reference / '__init__.py').write_text("raise RuntimeError('shadow reference imported')")
    (shadow_reference.parent / 'json.py').write_text("raise RuntimeError('shadow stdlib imported')")
    full = frozen_full_av(project, tmp_path / 'full')
    for start, end in ((2, 7), (6, 11)):
        shadow_helper = tmp_path / f'range-{start}/scripts'
        shadow_helper.mkdir(parents=True)
        (shadow_helper / '__init__.py').write_text("raise RuntimeError('shadow helper imported')")
        (shadow_helper.parent / 'json.py').write_text("raise RuntimeError('shadow stdlib imported')")
        assert candidate_range_av(project, start, end, tmp_path / f'range-{start}') == expected_range(project, full, start, end)
        reference = json.loads((tmp_path / 'full/reference.json').read_text())
        candidate = json.loads((tmp_path / f'range-{start}/candidate.json').read_text())
        assert candidate['runtime']['tools'] == reference['runtime']['tools']
        assert candidate['runtime']['caption_bootstrap_sha256'] == reference['runtime']['caption_bootstrap_sha256']
        assert all('accepted-lumen-python.zip' in origin for origin in reference['imports'].values())
        assert all('accepted-lumen-python.zip' not in origin for origin in candidate['imports'].values())



def test_outside_range_clip_preserves_full_graph_global_scalar_flag(tmp_path):
    from lumen.clip_composite import ClipComposite
    source = make_av_source(tmp_path / 'source')
    project = make_av_scene(tmp_path / 'scene', source=source, variant='legacy')
    project.clips.append(Clip('outside-scalar-trigger', source.id, 1, 18, 0, 3,
        composite=ClipComposite(True, .7), effects=[ClipEffect('outside-blur', 'blur', .8)],
        source_audio_enabled=False))
    project.validate()
    engine = RenderEngine(timeout=120)
    try:
        inputs, _, _ = engine._compile(project, frame_window=(3, 8), filter_complex_threads=1)
        assert inputs[:2] == ['-cpuflags', '0'], 'A full-project scalar-blur choice must not reset at a range boundary'
    finally:
        engine.close()
    full = frozen_full_av(project, tmp_path / 'full')
    assert candidate_range_av(project, 3, 8, tmp_path / 'range') == expected_range(project, full, 3, 8)


def test_pure_motion_matches_frozen_production_multithread_selector(tmp_path):
    """Full playback may choose 2/4 filter threads; range deliberately uses 1."""
    import os
    project = make_av_scene(tmp_path / 'scene')
    project.captions = []; project.overlays = []
    for clip in project.clips:
        clip.effects = []
        clip.grade.qualifier.window_keyframes = []
    assert any(c.motion_keyframes for c in project.clips)
    project.validate()
    full = frozen_full_av(project, tmp_path / 'full', production_threads=True)
    receipt = json.loads((tmp_path / 'full/reference.json').read_text())
    available = os.cpu_count() or 1
    assert receipt['filter_complex_threads'] == (4 if available >= 4 else 2 if available >= 2 else 1)
    assert candidate_range_av(project, 5, 11, tmp_path / 'range') == expected_range(project, full, 5, 11)


def test_no_active_clip_retains_global_sticker_caption_and_silence_clocks(tmp_path):
    project = make_av_scene(tmp_path / 'scene')
    project.clips = []; project.av_links = []
    project.validate()
    full = frozen_full_av(project, tmp_path / 'full')
    for start, end in ((3, 9), (12, 13)):
        assert candidate_range_av(project, start, end, tmp_path / f'range-{start}') == expected_range(project, full, start, end)


def test_real_capture_emits_only_window_and_never_opens_unrelated_missing_tail(tmp_path, monkeypatch):
    source = make_av_source(tmp_path / 'source')
    project = make_av_scene(tmp_path / 'scene', source=source)
    absent = Media('unrelated', str(tmp_path / 'MUST-NOT-OPEN.mkv'), 'Unrelated missing tail', 10,
                   width=64, height=48, has_video=True, has_audio=True, fps_num=24, fps_den=1)
    project.media.append(absent)
    project.clips.append(Clip('unrelated-tail', absent.id, 0, 2400, 0, 120))
    project.validate()
    before = deepcopy(project.to_dict())
    calls = []
    original = RenderEngine._graded_segment
    def observed(self, media, clip, owner, cancel, *args, **kwargs):
        calls.append((clip.id, kwargs.get('frame_window')))
        return original(self, media, clip, owner, cancel, *args, **kwargs)
    monkeypatch.setattr(RenderEngine, '_graded_segment', observed)
    snapshot = capture_range_preview(project, 3, 9, timeout=120)
    path = snapshot.path
    try:
        assert calls == [('bottom', (2, 8)), ('top', (0, 6))]
        assert snapshot.work.composite_frames == 6
        assert snapshot.work.graded_frames == 12
        assert snapshot.work.sticker_frames == 6
        assert snapshot.work.mixed_samples == sample_boundary(9, project.fps, 48000) - sample_boundary(3, project.fps, 48000)
        assert 'unrelated-tail' not in snapshot.work.active_clip_ids
        assert snapshot.path.is_file() and snapshot.work.intermediate_bytes > snapshot.path.stat().st_size
        assert verify_range_preview(snapshot, project)
        # A source replacement is recognized even though the project revision
        # and the owned encoded artifact are unchanged.
        Path(source.path).touch()
        assert not verify_range_preview(snapshot, project)
        assert project.to_dict() == before
    finally:
        snapshot.close()
    assert not path.exists()


def test_frozen_bundle_cannot_import_candidate_range_module():
    import hashlib
    import zipfile
    archive = frozen_archive()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA256
    with zipfile.ZipFile(archive) as frozen:
        assert 'lumen/range_preview.py' not in frozen.namelist()
        assert b'frame_window' not in frozen.read('lumen/render.py')
        assert b'output_frame_offset' not in frozen.read('lumen/color_media.py')


def test_fixture_detects_audio_ownership_mute_gain_and_fade_omissions(tmp_path):
    project = make_av_scene(tmp_path / 'scene', variant='legacy')
    full = frozen_full_av(project, tmp_path / 'full')
    expected = expected_range(project, full, 1, 11)
    for omission in ('ownership', 'mute', 'gain_keys', 'fade', 'master'):
        wrong = deepcopy(project)
        audio = next(c for c in wrong.clips if c.id == 'j-l-audio')
        if omission == 'ownership':
            wrong.av_links = []
            wrong.clips[0].source_audio_enabled = True
        elif omission == 'mute': audio.muted = True
        elif omission == 'gain_keys': audio.audio_gain_keyframes = []
        elif omission == 'fade': audio.fade_in = 0; audio.fade_out = 0
        else: wrong.master_gain_db = 0
        # Use accepted full renderer again, never the candidate, to establish
        # that this fixture would detect the omitted operation.
        actual = expected_range(wrong, frozen_full_av(wrong, tmp_path / omission), 1, 11)
        assert actual[1] != expected[1], f'Fixture does not expose omitted {omission}'
