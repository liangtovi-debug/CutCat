"""Actual preview/export transport for envelopes exceeding command-line limits."""
from pathlib import Path
import subprocess
import tempfile

import pytest

from lumen.audio_gain import AudioGainKeyframe
from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Project
from lumen.render import RenderEngine
from test_retime_video import ff


@pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')
def test_large_audio_envelope_uses_same_literal_graph_file_in_preview_and_export(tmp_path, monkeypatch):
    source = tmp_path / 'source.wav'
    ff('-f', 'lavfi', '-i', 'aevalsrc=0.2*sin(2*PI*431*t)|0.13*sin(2*PI*991*t):s=48000:d=0.25',
       '-c:a', 'pcm_f32le', source)
    media = probe(source)
    keys = [AudioGainKeyframe(i*100, -95.12345678901234 + i*.9) for i in range(128)]
    project = Project(width=16, height=16, media=[media], clips=[
        Clip(f'c{i}', media.id, 2, i*6, 0, 6, audio_gain_keyframes=keys) for i in range(4)])
    special = tmp_path / "graph's [a],; spaced"
    special.mkdir()
    engine = RenderEngine(timeout=45)
    engine._cache_dir.cleanup()
    engine._cache_dir = tempfile.TemporaryDirectory(dir=special)
    original = subprocess.Popen
    graphs = []
    def inspect(command, *args, **kwargs):
        if '-/filter_complex' in command:
            assert '-filter_complex' not in command and '-filter_complex_script' not in command
            graph_path = Path(command[command.index('-/filter_complex')+1])
            assert graph_path.parent == Path(engine._cache_dir.name).resolve()
            graph = graph_path.read_text(encoding='utf-8')
            assert len(graph) > 32767 and graph.count('aeval=') == 4
            graphs.append(graph)
            assert not kwargs.get('shell', False)
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', inspect)
    try:
        export = engine.render(project, tmp_path / 'output.mp4')
        preview = engine.preview(project)
        assert export.is_file() and preview.is_file()
        assert len(graphs) == 2 and graphs[0] == graphs[1]
    finally:
        engine.close()
