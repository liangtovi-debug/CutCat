"""Mock-synthesis Qt regression tests; no model, network or listening claims.

The deterministic PCM files exercise ownership, queued results, timing and
project persistence. They are test signals, not speech from a TTS runtime.
"""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import threading
import wave

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox

from test_ui import app, window, pump_until
from lumen.model import Media, Project, VERSION
from lumen import tts


@pytest.fixture(autouse=True)
def isolated_voice_settings(tmp_path, monkeypatch):
    import lumen.ui_captions as captions
    settings = QSettings(str(tmp_path / 'voices.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(captions, 'QSettings', lambda *args: settings)


def configure_mock_synthesis(w, tmp_path, monkeypatch, *, duration=.75):
    """Use actual PCM containers but mock the complete model/runtime boundary."""
    pack = tmp_path / 'mock-pack'
    pack.mkdir(exist_ok=True)
    cue = w.editor.add_caption(10, 30, 'Stored subtitle 原始字幕')
    w.changed()
    w.select_caption(cue.id)
    w.tts_model.setText(str(pack))
    w._tts_verified_pack = w._tts_pack_key()
    w.tts_track.setCurrentIndex(w.tts_track.findData(2))
    w.update_tts_controls()
    target = tmp_path / 'saved-take.wav'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(target), ''))
    calls = []

    def synthesize(text, config, output, **kwargs):
        calls.append((text, config, Path(output)))
        samples = round(duration * 24000)
        with wave.open(str(output), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x01\x00' * samples)
        provenance = tts.TTSProvenance(
            text=text, engine='sherpa-onnx/Kokoro-' + config.pack_version,
            engine_version='mock-test-boundary', model_sha256='a' * 64,
            voice_sha256='b' * 64, assets_sha256='c' * 64,
            voice_id=str(config.voice_id), speed=config.speed, sample_rate=24000,
            output_sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            created_utc='2026-10-17T00:00:00Z')
        return tts.SynthesisResult(str(output), samples / 24000, 24000, provenance)

    monkeypatch.setattr(tts, 'synthesize', synthesize)
    return cue, target, calls, synthesize


def project_state(w):
    """Include history and UI state so an audition cannot silently create edits."""
    return (deepcopy(w.project), w.revision, w.dirty, deepcopy(w.editor._undo),
            deepcopy(w.editor._redo), w.selection, w.active_selection_type,
            w.timeline.playhead, w.timeline.caption_selection)


def test_audition_preserves_populated_project_history_and_durable_take(
        window, app, tmp_path, monkeypatch):
    w = window
    cue, durable, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch)
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)
    durable_bytes = durable.read_bytes()
    w.select_caption(cue.id)
    # Also protect a redo branch, which an accidental editor commit would clear.
    w.editor.update_caption(cue.id, text='Temporary edit')
    w.editor.undo()
    w.refresh()
    w.select_caption(cue.id)
    w.seek_frame(17)
    w.dirty = False
    w.caption_text.setPlainText('Unapplied draft 当前草稿')
    w.copy_caption_to_audition()
    assert w.tts_preview_text.toPlainText() == 'Unapplied draft 当前草稿'
    before = project_state(w)
    played = []
    monkeypatch.setattr(w, 'play_tts_preview', lambda: played.append(w.tts_preview_path))

    w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    first = w.tts_preview_path
    w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    second = w.tts_preview_path

    assert first != second and not first.exists() and second.is_file()
    assert played == [first, second]
    assert all(call[0] == 'Unapplied draft 当前草稿' for call in calls[1:])
    assert project_state(w) == before
    assert w.project.captions[0].text == 'Stored subtitle 原始字幕'
    assert w.pending_speech is None and durable.read_bytes() == durable_bytes
    w.close_tts_preview()
    assert not second.exists() and durable.read_bytes() == durable_bytes
    assert project_state(w) == before


@pytest.mark.parametrize('change', ['stop', 'voice', 'speed', 'text', 'close'])
def test_late_audition_success_after_cancel_cannot_play_or_leave_owned_wav(
        window, app, tmp_path, monkeypatch, change):
    w = window
    _, _, calls, synthesize = configure_mock_synthesis(w, tmp_path, monkeypatch)
    w.dirty = False
    before = deepcopy(w.project)
    started, release = threading.Event(), threading.Event()
    played = []
    monkeypatch.setattr(w, 'play_tts_preview', lambda: played.append(True))

    def late_success(*args, cancel, **kwargs):
        started.set()
        assert release.wait(3)
        assert cancel.is_set()
        # Deliberately simulate success winning the native cancellation race.
        return synthesize(*args, cancel=cancel, **kwargs)

    monkeypatch.setattr(tts, 'synthesize', late_success)
    w.preview_tts_voice()
    assert started.wait(2)
    try:
        if change == 'stop':
            w.stop_tts_preview()
        elif change == 'voice':
            w.tts_voice.setValue(45)
        elif change == 'speed':
            w.tts_speed.setValue(1.5)
        elif change == 'text':
            w.tts_preview_text.setPlainText('A newer audition')
        else:
            monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
            w.close()
    finally:
        release.set()
    pump_until(app, lambda: not w.jobs and (change != 'close' or not w.cache.exists()))

    assert len(calls) == 1 and not calls[0][2].exists()
    assert not played and w.tts_preview_path is None and w._tts_preview_task is None
    assert not w.pending_speech and w.project == before and not w.test_warnings
    assert not list((w.cache / 'voice-auditions').glob('*.wav'))
    if change != 'close':
        assert w.tts_job is None and w.tts_preview_button.isEnabled()


def test_cancel_discards_already_queued_audition_result(window, app, tmp_path, monkeypatch):
    w = window
    _, _, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch)
    played = []
    monkeypatch.setattr(w, 'play_tts_preview', lambda: played.append(True))
    w.preview_tts_voice()
    job = w.tts_job
    # Do not pump Qt: result and finished signals are queued, not handled yet.
    assert job.wait(3000)
    assert calls[0][2].is_file() and w.tts_preview_path is None
    w.stop_tts_preview()
    pump_until(app, lambda: not w.jobs)
    assert not played and not calls[0][2].exists()
    assert w.tts_preview_path is None and w._tts_preview_task is None
    assert not w.test_warnings


def test_audition_partial_file_failure_cleans_before_retry(window, app, tmp_path, monkeypatch):
    w = window
    _, durable, calls, synthesize = configure_mock_synthesis(w, tmp_path, monkeypatch)
    durable.write_bytes(b'user-owned saved take')
    partials = []
    before = project_state(w)

    def fail_after_write(text, config, output, **kwargs):
        partials.append(Path(output))
        Path(output).write_bytes(b'incomplete mock WAV')
        raise tts.TTSError('Mock inference failed after creating output')

    monkeypatch.setattr(tts, 'synthesize', fail_after_write)
    w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    assert not partials[0].exists() and w.tts_preview_path is None
    assert w._tts_preview_task is None and w.tts_preview_button.isEnabled()
    assert 'failed after creating output' in w.test_warnings[-1]
    monkeypatch.setattr(tts, 'synthesize', synthesize)
    monkeypatch.setattr(w, 'play_tts_preview', lambda: None)
    w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    assert len(calls) == 1 and calls[0][2] != partials[0]
    assert list((w.cache / 'voice-auditions').glob('*.wav')) == [w.tts_preview_path]
    assert durable.read_bytes() == b'user-owned saved take'
    assert project_state(w) == before


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
def test_audition_finishes_before_start_job_returns_without_losing_cleanup(
        window, app, tmp_path, monkeypatch, outcome):
    """Force the old late-finished-connection race, not a timing-based retry."""
    w = window
    _, durable, calls, synthesize = configure_mock_synthesis(w, tmp_path, monkeypatch)
    durable.write_bytes(b'user-owned saved take')
    before = project_state(w); outputs = []; played = []; workers = []
    monkeypatch.setattr(w, 'play_tts_preview', lambda: played.append(True))

    def finish_immediately(text, config, output, **kwargs):
        outputs.append(Path(output))
        if outcome != 'success':
            Path(output).write_bytes(b'incomplete mock WAV')
            raise tts.TTSError('Immediate mock audition failure')
        return synthesize(text, config, output, **kwargs)

    monkeypatch.setattr(tts, 'synthesize', finish_immediately)
    original = w.start_job
    def start_and_wait(*args, **kwargs):
        if outcome == 'cancel':
            install = kwargs.get('before_start')
            def cancel_before_start(job):
                if install is not None: install(job)
                job.cancel.set()
            kwargs['before_start'] = cancel_before_start
        job = original(*args, **kwargs); workers.append(job)
        assert job.wait(3000), 'Mock worker must finish before start_job returns'
        # Also process queued callbacks before returning, so the caller cannot
        # repair the race by assigning a stale task pointer after startup.
        app.processEvents()
        assert w._tts_preview_task is None
        return job
    monkeypatch.setattr(w, 'start_job', start_and_wait)
    w.preview_tts_voice()
    pump_until(app, lambda: not w.jobs)
    assert len(workers) == 1 and w._tts_preview_task is None
    assert w.tts_job is None and w.tts_preview_button.isEnabled()
    assert durable.read_bytes() == b'user-owned saved take'
    assert project_state(w) == before
    if outcome == 'success':
        assert played == [True] and len(calls) == 1
        assert w.tts_preview_path == outputs[0] and outputs[0].is_file()
        assert not w.test_warnings
    else:
        assert not played and w.tts_preview_path is None
        assert outputs and all(not path.exists() for path in outputs)
        assert not list((w.cache / 'voice-auditions').glob('*.wav'))
        assert bool(w.test_warnings) is (outcome == 'failure')


def test_pending_take_retains_original_voice_pack_and_speed_after_browser_changes(
        window, app, tmp_path, monkeypatch):
    w = window
    cue, durable, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch)
    existing = Media('existing', str(tmp_path / 'existing.wav'), 'Existing', 2,
                     has_video=False, has_audio=True)
    w.editor.add_media(existing)
    w.editor.add_clip(existing.id, 2, 0)
    w.changed()
    w.select_caption(cue.id)
    w.tts_voice.setValue(45)
    w.tts_speed.setValue(1.25)
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)
    pending = w.pending_speech
    original = deepcopy(pending[0].speech_provenance)
    original_bytes = durable.read_bytes()
    before_insert = deepcopy(w.project)

    w.tts_pack.setCurrentIndex(w.tts_pack.findData('v1.1'))
    w.tts_voice.setValue(102)
    w.tts_speed.setValue(.5)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    assert w._tts_verified_pack is None and not w.tts_button.isEnabled()
    assert w.tts_insert_button.isEnabled()
    w.insert_pending_speech()

    assert len(calls) == 1 and w.pending_speech is None
    assert w.selected_clip().track == 3 and w.selected_clip().duration == 23
    assert w.project.media[-1].speech_provenance == original
    assert (original.engine, original.voice_id, original.speed) == (
        'sherpa-onnx/Kokoro-v1.0', '45', 1.25)
    w.undo()
    assert w.project == before_insert and durable.read_bytes() == original_bytes
    w.redo()
    assert w.project.media[-1].speech_provenance == original


@pytest.mark.parametrize('duration,frames', [(1.001, 30), (1.001 + 1 / 24000, 31)])
@pytest.mark.parametrize('boundary', ['touches_before', 'touches_after', 'overlaps_last_frame'])
def test_fractional_fps_full_take_uses_ceil_and_half_open_conflict_boundaries(
        window, app, tmp_path, monkeypatch, duration, frames, boundary):
    w = window
    w.project.fps_num, w.project.fps_den = 30000, 1001
    cue, durable, calls, _ = configure_mock_synthesis(w, tmp_path, monkeypatch, duration=duration)
    other = Media('other', str(tmp_path / 'other.wav'), 'Neighbor', 1.001,
                  has_video=False, has_audio=True)
    w.editor.add_media(other)
    if boundary == 'touches_before':
        neighbor = w.editor.add_clip(other.id, 2, 0)
        w.editor.trim_clip(neighbor.id, 0, 10)
    else:
        w.editor.add_clip(other.id, 2, cue.start + frames - (boundary == 'overlaps_last_frame'))
    w.changed()
    w.select_caption(cue.id)
    before = deepcopy(w.project)
    questions = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: (
        questions.append(args[2]) or QMessageBox.StandardButton.Yes))
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)

    assert len(calls) == 1 and durable.is_file()
    if boundary == 'overlaps_last_frame':
        assert w.project == before and w.pending_speech is not None
        assert not questions and 'overlaps lane 2' in w.test_warnings[-1]
    else:
        clip = w.selected_clip()
        assert (clip.start, clip.duration, clip.end) == (10, frames, 10 + frames)
        assert clip.timing.speed == 1 and w.pending_speech is None
        assert w.project.captions == before.captions
        assert len(questions) == (frames > cue.duration)
        assert not w.test_warnings


def test_project_edit_inside_long_take_confirmation_invalidates_pending_insertion(
        window, app, tmp_path, monkeypatch):
    w = window
    cue, durable, _, _ = configure_mock_synthesis(w, tmp_path, monkeypatch, duration=1.5)
    before = deepcopy(w.project)

    def edit_during_confirmation(*args):
        w.editor.update_caption(cue.id, text='Changed while dialog was open')
        w.changed()
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', edit_during_confirmation)
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)
    assert durable.is_file() and w.pending_speech is None
    assert not w.project.media and not w.project.clips
    assert 'changed while confirming insertion' in w.test_warnings[-1]
    w.undo()
    assert w.project == before


def test_generated_take_relocation_reopen_and_relink_preserve_schema11_provenance(
        window, app, tmp_path, monkeypatch):
    import lumen.ui as ui
    w = window
    bundle = tmp_path / 'original'
    bundle.mkdir()
    cue, durable, _, _ = configure_mock_synthesis(w, bundle, monkeypatch)
    w.editor.update_caption_style(safe_margin_percent=12, max_lines=4)
    w.changed()
    w.select_caption(cue.id)
    w.speak_selected()
    pump_until(app, lambda: not w.jobs)
    provenance = deepcopy(w.project.media[0].speech_provenance)
    saved_clip = deepcopy(w.project.clips[0])
    saved_bytes = durable.read_bytes()
    w.project_path = bundle / 'project.lumen'
    assert w.save_project()
    data = json.loads(w.project_path.read_text(encoding='utf-8'))
    assert data['version'] == VERSION
    assert data['media'][0]['path'] == 'saved-take.wav'
    assert data['media'][0]['speech_provenance'] == asdict(provenance)

    moved = tmp_path / 'moved'
    bundle.rename(moved)
    reopened_path = moved / 'project.lumen'
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(reopened_path), ''))
    w.open_project()
    assert w.project_path == reopened_path and not w.project.missing_media()
    assert Path(w.project.media[0].path) == moved / 'saved-take.wav'
    assert w.project.media[0].speech_provenance == provenance
    assert w.project.clips == [saved_clip]
    assert (w.project.caption_style.safe_margin_percent, w.project.caption_style.max_lines) == (12, 4)

    relocated_wav = tmp_path / 'relocated-original.wav'
    (moved / 'saved-take.wav').rename(relocated_wav)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(relocated_wav), ''))
    # Media probing is another mocked boundary; the actual PCM bytes stay intact.
    monkeypatch.setattr(ui, 'probe', lambda path: Media('probe-result', str(path), 'Relocated', .75,
                                                    has_video=False, has_audio=True))
    w.relink_dialog()
    assert not w.project.missing_media() and w.project.media[0].path == str(relocated_wav)
    assert w.project.media[0].speech_provenance == provenance
    assert not w.editor.can_undo and w.project.clips == [saved_clip]
    assert w.save_project()
    restored = Project.load(reopened_path)
    assert restored == w.project and restored.media[0].speech_provenance == provenance
    assert relocated_wav.read_bytes() == saved_bytes
    assert hashlib.sha256(saved_bytes).hexdigest() == provenance.output_sha256
    assert not w.test_warnings
