"""Real WAV/probe relinking for exact-count speech; synthetic PCM, no model."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import wave

import numpy as np
import pytest
from PySide6.QtWidgets import QFileDialog

from lumen.media import ffmpeg_available, probe
from lumen.model import Clip, Editor, Project
from lumen.render import RenderEngine
from test_tts_exact_timing import speech
from test_speech_render import decoded_audio
from test_ui import app, window


pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def install_exact_take(w, tmp_path):
    media = speech(tmp_path, 56000)
    samples = np.zeros(56000, dtype='<i2')
    samples[-240:] = (16000 * np.sin(2 * np.pi * 1000 * np.arange(240) / 24000)).astype('<i2')
    source = Path(media.path)
    with wave.open(str(source), 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(samples.tobytes())
    original = source.read_bytes()
    media.speech_provenance = replace(media.speech_provenance,
        output_sha256=hashlib.sha256(original).hexdigest())
    project = Project(width=64, height=48, media=[media],
                      clips=[Clip('speech-clip', media.id, 2, 0, 0, 70)])
    project.validate()
    w.editor = Editor(project)
    w.changed()
    w.project_path = tmp_path / 'project.lumen'
    assert w.save_project()
    moved = tmp_path / 'moved-original.wav'
    source.rename(moved)
    assert w.project.missing_media() == [media]
    # Reproduce actual ffprobe rounding, rather than a mocked probe boundary.
    assert probe(moved).duration == 2.333333
    return moved, original, deepcopy(media.speech_provenance)


def test_relink_identical_exact_take_preserves_timing_reopens_and_previews(
        window, app, tmp_path, monkeypatch):
    w = window
    moved, original, provenance = install_exact_take(w, tmp_path)
    original_clips = deepcopy(w.project.clips)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(moved), ''))
    w.relink_dialog()
    media = w.project.media[0]
    assert not w.test_warnings and not w.project.missing_media()
    assert media.path == str(moved)
    assert media.duration == 56000 / 24000 and media.speech_provenance == provenance
    assert w.project.clips == original_clips and w.project.media_frames(media) == 70
    assert not w.editor.can_undo and w.save_project()
    saved = deepcopy(w.project)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(w.project_path), ''))
    w.open_project()
    assert w.project == saved
    assert not w.test_warnings and moved.read_bytes() == original
    engine = RenderEngine(timeout=30)
    try:
        rendered = engine.preview(w.project)
        decoded = decoded_audio(rendered)
        ending = decoded[(56000 - 200) * 2:(56000 - 40) * 2]
        assert float(np.sqrt(np.mean(ending ** 2))) > 0.1
        assert probe(rendered).duration == pytest.approx(70 / 30, abs=.002)
    finally:
        engine.close()


@pytest.mark.parametrize('changed_length', [False, True])
def test_relink_changed_take_rejected_without_project_or_history_change(
        window, app, tmp_path, monkeypatch, changed_length):
    w = window
    moved, original, provenance = install_exact_take(w, tmp_path)
    if changed_length:
        with wave.open(str(moved), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x01\x00' * 56001)
    else:
        changed = bytearray(original)
        changed[-20] ^= 32  # Valid WAV, identical duration and size, different PCM.
        moved.write_bytes(changed)
    replacement = moved.read_bytes()
    before, editor = deepcopy(w.project), w.editor
    revision, dirty = w.revision, w.dirty
    undo, redo = deepcopy(w.editor._undo), deepcopy(w.editor._redo)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(moved), ''))
    w.relink_dialog()
    assert 'Generated speech source has changed' in w.test_warnings[-1]
    assert w.project == before and w.editor is editor
    assert (w.revision, w.dirty) == (revision, dirty)
    assert w.editor._undo == undo and w.editor._redo == redo
    assert w.project.media[0].speech_provenance == provenance
    assert w.project.missing_media() and moved.read_bytes() == replacement

