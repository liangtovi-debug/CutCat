"""Production FFmpeg range/full edge coverage; no frozen-runtime substitution."""
from fractions import Fraction
from pathlib import Path
import subprocess
import pytest
from lumen.media import ffmpeg_binary, probe
from lumen.model import Clip, ClipEffect
from scripts.benchmark_range_preview import make_av_scene, make_av_source
from scripts.production_range_oracle import compare_ranges


@pytest.mark.parametrize('case', ['different-rate', 'vfr', 'outside-scalar', 'motion', 'empty'])
def test_production_full_slice_matches_range_at_edges(tmp_path, case):
    if case in ('different-rate', 'vfr'):
        source = make_av_source(tmp_path / 'source', Fraction(30), count=48, sample_rate=44100)
        if case == 'vfr':
            path = tmp_path / 'vfr.mkv'
            subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-i', source.path,
                            '-map', '0:V:0', '-map', '0:a:0', '-vf', 'settb=1/30,setpts=N+floor(N/3)',
                            '-fps_mode', 'vfr', '-c:v', 'ffv1', '-threads', '1', '-c:a', 'copy', str(path)],
                           capture_output=True, check=True, timeout=30)
            source = probe(path)
        project = make_av_scene(tmp_path / 'scene', Fraction(25), source=source, variant='legacy')
        windows = ((2, 7), (6, 11))
    elif case == 'outside-scalar':
        from lumen.clip_composite import ClipComposite
        source = make_av_source(tmp_path / 'source')
        project = make_av_scene(tmp_path / 'scene', source=source, variant='legacy')
        project.clips.append(Clip('outside-scalar-trigger', source.id, 1, 18, 0, 3,
            composite=ClipComposite(True, .7), effects=[ClipEffect('outside-blur', 'blur', .8)],
            source_audio_enabled=False))
        windows = ((3, 8),)
    else:
        project = make_av_scene(tmp_path / 'scene')
        if case == 'motion':
            project.captions = []; project.overlays = []
            for clip in project.clips:
                clip.effects = []; clip.grade.qualifier.window_keyframes = []
            assert any(clip.motion_keyframes for clip in project.clips)
            windows = ((5, 11),)
        else:
            project.clips = []; project.av_links = []
            windows = ((3, 9), (12, 13))
    project.validate()
    compare_ranges(project, tmp_path / 'comparison', windows)


@pytest.mark.parametrize('channel', [0, 1], ids=['picture', 'sound'])
def test_real_production_comparison_detects_one_changed_byte(tmp_path, monkeypatch, channel):
    from scripts import benchmark_range_preview as helper
    project = make_av_scene(tmp_path / 'scene', variant='legacy')
    # Establish this exact fixture passes before attributing rejection to injection.
    compare_ranges(project, tmp_path / 'unmodified', ((1, 2),))
    original = helper._candidate_range_av_in_process
    def changed(*args, **kwargs):
        result = list(original(*args, **kwargs))
        data = bytearray(result[channel]); data[0] ^= 1
        result[channel] = bytes(data)
        return tuple(result)
    monkeypatch.setattr(helper, '_candidate_range_av_in_process', changed)
    with pytest.raises(AssertionError, match='production ' + ('YUV' if channel == 0 else 'PCM') + ' mismatch'):
        compare_ranges(project, tmp_path / 'comparison', ((1, 2),))
