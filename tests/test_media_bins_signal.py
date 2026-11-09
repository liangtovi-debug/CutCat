"""Organization changes project metadata, never the signal or source files."""
from copy import deepcopy
import hashlib
from pathlib import Path
import numpy as np
import pytest
from lumen.model import Editor, Project
from lumen.render import RenderEngine
from lumen.audio_mix import compile_audio_mix
from lumen import audio_analysis as aa
from test_audio_mix import _pcm
from test_master_gain_signal import public_project, real, _decode_audio, _decode_video


@real
@pytest.mark.parametrize('mode', ['preview', 'h264', 'prores'])
def test_nested_organization_preserves_public_graph_pcm_and_picture(tmp_path, public_project, monkeypatch, mode):
    project = public_project
    project.master_gain_db = -3.25
    editor = Editor(project)
    media_id = project.media[0].id
    source = Path(project.media[0].path)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline = deepcopy(project)
    settings = aa.audio_settings_fingerprint(project)
    mix = compile_audio_mix(project, {m.id:m for m in project.media})
    pcm = _pcm(mix.inputs, mix.graph)
    engine = RenderEngine(timeout=60)
    graphs = []
    compile_original = engine._compile
    def capture(*args, **kwargs):
        result = compile_original(*args, **kwargs)
        graphs.append(result)
        return result
    monkeypatch.setattr(engine, '_compile', capture)
    def render(name):
        if mode == 'preview':
            return engine.preview(editor.project)
        return engine.render(editor.project, tmp_path / (name + ('.mov' if mode == 'prores' else '.mp4')),
            export_profile='prores_422_hq_10bit' if mode == 'prores' else 'h264_8bit')
    try:
        first = render('before')
        audio, video = _decode_audio(first), _decode_video(first)
        parent = editor.create_media_bin('Camera A')
        child = editor.create_media_bin('Day 1', parent.id)
        editor.move_media_to_bin([media_id], child.id)
        editor.rename_media_bin(child.id, '第一天')
        editor.reparent_media_bin(child.id, None)
        editor.delete_media_bin(parent.id)
        organized = deepcopy(editor.project)
        saved = tmp_path / 'organized.lumen'
        editor.project.save(saved)
        assert Project.load(saved) == organized
        assert editor.project.media == baseline.media and editor.project.clips == baseline.clips
        assert aa.audio_settings_fingerprint(editor.project) == settings
        after_mix = compile_audio_mix(editor.project, {m.id:m for m in editor.project.media})
        assert after_mix == mix and _pcm(after_mix.inputs, after_mix.graph) == pcm
        second = render('after')
        assert len(graphs) == 2 and graphs[0] == graphs[1]
        np.testing.assert_array_equal(_decode_audio(second), audio)
        assert _decode_video(second) == video
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        for _ in range(6): assert editor.undo()
        assert editor.project == baseline
        for _ in range(6): assert editor.redo()
        assert editor.project == organized
    finally:
        engine.close()
