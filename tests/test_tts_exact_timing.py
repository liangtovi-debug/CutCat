"""Exact PCM timing regressions; all speech here is synthetic, not model audio."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from fractions import Fraction
import hashlib
import math
from pathlib import Path
import sys
import wave

import pytest

from lumen import model, tts
from lumen.model import VERSION, Caption, Editor, Media, Project, SpeechProvenance
from test_tts import pack
from test_tts_pause_model import speech_provenance_data, tts_provenance_data
from test_ui import app, window, pump_until


def speech(tmp_path, samples, *, legacy=False):
    provenance = SpeechProvenance(**speech_provenance_data(),
                                  sample_count=None if legacy else samples)
    return Media('speech', str(tmp_path / 'speech.wav'), 'Speech', samples / 24000,
                 has_video=False, has_audio=True, speech_provenance=provenance)


@pytest.mark.parametrize('samples', [5599, 5600, 5601, 11200, 18400, 40000,
                                   55999, 56000, 56001, 80000, 24024, 24025])
@pytest.mark.parametrize('fps', [Fraction(30), Fraction(24), Fraction(30000, 1001)])
def test_new_speech_uses_exact_pcm_duration(tmp_path, samples, fps):
    project = Project(fps_num=fps.numerator, fps_den=fps.denominator)
    media = speech(tmp_path, samples)
    expected = math.ceil(Fraction(samples, 24000) * fps)
    assert project.media_frames(media) == expected
    # Display/probe metadata can be rounded without changing verified PCM timing.
    assert project.media_frames(replace(media, duration=round(media.duration, 6))) == expected


def test_exact_boundary_and_one_sample_over_remain_distinct(tmp_path):
    project = Project()
    assert project.media_frames(speech(tmp_path, 56000)) == 70
    assert project.media_frames(speech(tmp_path, 56001)) == 71
    assert project.media_frames(speech(tmp_path, 56000, legacy=True)) == 71
    # Unrelated media keeps the existing floor behavior.
    assert project.media_frames(Media('old', '/tmp/old.wav', 'Old', 1.01)) == 30


@pytest.mark.parametrize('samples,duration', [
    (1, 10.0), (240000, 1 / 24000), (56000, 2.333333),
    (56000, 56001 / 24000), (56001, 56000 / 24000),
])
def test_recorded_count_rejects_inconsistent_duration_in_both_directions(
        tmp_path, samples, duration):
    media = replace(speech(tmp_path, samples), duration=duration)
    with pytest.raises(ValueError, match='duration does not match'):
        Project(media=[media]).validate()
    # A legacy take has no asserted PCM count; do not infer or rewrite one.
    legacy = replace(media, speech_provenance=replace(media.speech_provenance, sample_count=None))
    Project(media=[legacy]).validate()


@pytest.mark.parametrize('direction', [float('-inf'), float('inf')])
def test_duration_consistency_allows_one_float_step_but_not_two(tmp_path, direction):
    media = speech(tmp_path, 56000)
    adjacent = math.nextafter(media.duration, direction)
    project = Project(media=[replace(media, duration=adjacent)])
    project.validate()
    assert Project.from_dict(project.to_dict()) == project
    farther = math.nextafter(adjacent, direction)
    with pytest.raises(ValueError, match='duration does not match'):
        Project(media=[replace(media, duration=farther)]).validate()


@pytest.mark.parametrize('duration', [10.0, 1 / 24000])
def test_serialized_count_duration_mismatch_is_rejected(tmp_path, duration):
    payload = Project(media=[speech(tmp_path, 56000)]).to_dict()
    payload['media'][0]['duration'] = duration
    with pytest.raises(ValueError, match='duration does not match'):
        Project.from_dict(payload)


@pytest.mark.parametrize('version', range(4, 12))
def test_legacy_missing_count_is_not_inferred_or_rewritten(tmp_path, version):
    original = Project(media=[speech(tmp_path, 56000, legacy=True)]).to_dict()
    original['version'] = version; strip_contrast_pivot_field(original)
    if original['version'] < 17:
        for old_clip in original.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if original['version'] < 16:
        for old_clip in original.get('clips', []): old_clip.pop('transform', None)
    del original['media'][0]['speech_provenance']['sample_count']
    if version < 11:
        del original['media'][0]['speech_provenance']['silence_scale']
    snapshot = deepcopy(original)
    loaded = Project.from_dict(original)
    assert original == snapshot
    assert loaded.media[0].speech_provenance.sample_count is None
    assert loaded.media_frames(loaded.media[0]) == 71
    path = tmp_path / 'migrated.lumen'
    loaded.save(path)
    assert Project.load(path) == loaded
    assert loaded.to_dict()['version'] == VERSION


def test_schema_eleven_existing_clip_keeps_its_full_saved_timing(tmp_path):
    editor = Editor(Project(captions=[Caption('caption', 0, 70, 'Original caption')]))
    editor.add_speech_clip(speech(tmp_path, 56000, legacy=True), 'caption', 2)
    data = editor.project.to_dict()
    data['version'] = 11; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    del data['media'][0]['speech_provenance']['sample_count']
    loaded = Project.from_dict(data)
    assert loaded.clips[0].duration == 71
    assert loaded.clips[0].timing.source_out == 71
    assert Project.from_dict(loaded.to_dict()) == loaded


def test_new_count_is_immutable_roundtrips_and_old_reader_rejects(tmp_path, monkeypatch):
    original = Project(media=[speech(tmp_path, 56000)])
    path = tmp_path / 'exact.lumen'
    original.save(path)
    assert Project.load(path) == original
    with pytest.raises(FrozenInstanceError):
        original.media[0].speech_provenance.sample_count = 56001
    data = original.to_dict()
    assert data['version'] == VERSION
    assert data['media'][0]['speech_provenance']['sample_count'] == 56000
    monkeypatch.setattr(model, 'VERSION', 11)
    with pytest.raises(ValueError, match='^Unsupported project format or version$'):
        Project.from_dict(data)


@pytest.mark.parametrize('value', [True, False, 0, -1, 1.5, '24000', [], {},
                                  float('nan'), float('inf'), 2**53])
def test_invalid_sample_counts_rejected_at_both_provenance_boundaries(value):
    with pytest.raises(ValueError, match='sample count'):
        tts.TTSProvenance(**tts_provenance_data(), sample_count=value)
    with pytest.raises(ValueError, match='sample count'):
        SpeechProvenance(**speech_provenance_data(), sample_count=value)


def test_completed_wav_sample_count_reaches_persisted_provenance(pack, tmp_path, monkeypatch):
    script = tmp_path / 'fake_worker.py'
    script.write_text('''import json, sys, wave
from pathlib import Path
r = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
with wave.open(r['output'], 'wb') as w:
 w.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
 w.writeframes(b'\\x01\\x00' * 56000)
Path(sys.argv[2]).write_text(json.dumps({'engine_version': 'mock-worker'}))
''', encoding='utf-8')
    monkeypatch.setattr(tts, '_worker_command',
                        lambda a, b: [sys.executable, str(script), str(a), str(b)])
    text = '2026年10月19日，3.14元。Keep U.S.A. and v1.2.3 unchanged!\n下一句。'
    result = tts.synthesize(text, tts.TTSConfig(str(pack)), tmp_path / 'exact.wav')
    assert result.provenance.sample_count == 56000
    assert result.duration == 56000 / 24000
    assert result.provenance.text == text
    persisted = SpeechProvenance(**asdict(result.provenance), source_caption_id='caption',
                                 source_start=0, source_duration=70)
    media = Media('speech', result.path, 'Speech', result.duration,
                  has_video=False, has_audio=True, speech_provenance=persisted)
    assert Project().media_frames(media) == 70


@pytest.mark.parametrize('samples', [56000, 56001])
@pytest.mark.parametrize('preview', [False, True])
def test_exact_pcm_endpoint_survives_preview_and_aac_export(tmp_path, samples, preview):
    import numpy as np
    from lumen.media import ffmpeg_available, probe
    from lumen.model import Clip
    from lumen.render import RenderEngine
    from test_speech_render import decoded_audio

    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe required')
    media = speech(tmp_path, samples)
    pcm = np.zeros(samples, dtype='<i2')
    pcm[-240:] = (16000 * np.sin(2 * np.pi * 1000 * np.arange(240) / 24000)).astype('<i2')
    source = Path(media.path)
    with wave.open(str(source), 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(pcm.tobytes())
    original = source.read_bytes()
    media.speech_provenance = replace(media.speech_provenance,
        output_sha256=hashlib.sha256(original).hexdigest())
    project = Project(width=64, height=48, media=[media])
    frames = project.media_frames(media)
    project.clips = [Clip('speech-clip', media.id, 2, 0, 0, frames)]
    assert frames == (70 if samples == 56000 else 71)
    engine = RenderEngine(timeout=30)
    try:
        output = engine.preview(project) if preview else engine.render(project, tmp_path / 'export.mp4')
        decoded = decoded_audio(output)
        ending = decoded[(samples - 200) * 2:(samples - 40) * 2]
        assert float(np.sqrt(np.mean(ending ** 2))) > 0.1
        assert probe(output).duration == pytest.approx(frames / 30, abs=.002)
        assert source.read_bytes() == original
    finally:
        engine.close()


@pytest.mark.parametrize('samples', [56000, 56001])
@pytest.mark.parametrize('neighbor', [False, True])
def test_ui_exact_boundary_has_no_false_overrun_or_lane_conflict(
        window, app, tmp_path, monkeypatch, samples, neighbor):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    w = window
    cue = w.editor.add_caption(0, 70, 'Original subtitle')
    if neighbor:
        other = Media('neighbor', str(tmp_path / 'neighbor.wav'), 'Next', 1,
                      has_video=False, has_audio=True)
        w.editor.add_media(other)
        w.editor.add_clip(other.id, 2, 70)
    w.changed()
    w.select_caption(cue.id)
    w.caption_text.setPlainText('Reviewed 2026年，3.14元。\nKeep this draft!')
    w.tts_model.setText(str(tmp_path))
    w._tts_verified_pack = w._tts_pack_key()
    w.tts_track.setCurrentIndex(w.tts_track.findData(2))
    w.update_tts_controls()
    target = tmp_path / 'voice.wav'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(target), ''))
    requests, questions = [], []

    def synthesize(text, config, output, **kwargs):
        requests.append(text)
        with wave.open(str(output), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x01\x00' * samples)
        values = tts_provenance_data() | dict(
            text=text, sample_count=samples,
            output_sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest())
        return tts.SynthesisResult(str(output), samples / 24000, 24000,
                                   tts.TTSProvenance(**values))

    monkeypatch.setattr(tts, 'synthesize', synthesize)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: (
        questions.append(args[2]) or QMessageBox.StandardButton.Yes))
    original_captions = deepcopy(w.project.captions)
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)
    assert requests == ['Reviewed 2026年，3.14元。\nKeep this draft!']
    assert w.project.captions == original_captions
    if neighbor and samples == 56001:
        assert w.pending_speech is not None and len(w.project.clips) == 1
        assert 'overlaps lane 2' in w.test_warnings[-1] and not questions
    else:
        clip = w.selected_clip()
        assert clip.duration == (70 if samples == 56000 else 71)
        assert not w.test_warnings and len(questions) == (samples == 56001)
        generated = next(media for media in w.project.media if media.id == clip.media_id)
        assert generated.speech_provenance.sample_count == samples
        assert generated.speech_provenance.text == requests[0]
        path = tmp_path / 'saved.lumen'
        w.project.save(path)
        assert Project.load(path) == w.project

