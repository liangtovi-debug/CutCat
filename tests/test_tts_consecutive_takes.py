"""Consecutive-cue workflows with real PCM containers and mocked synthesis.

These are deterministic timing/ownership checks, not model or listening tests.
Each saved take has distinct bytes, a real hash and its original cue snapshot.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import wave

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QMessageBox

from test_ui import app, window, pump_until
from lumen import tts
from lumen.model import Project, VERSION


@pytest.fixture(autouse=True)
def isolated_voice_settings(tmp_path, monkeypatch):
    import lumen.ui_captions as captions
    settings = QSettings(str(tmp_path / 'voices.ini'), QSettings.Format.IniFormat)
    monkeypatch.setattr(captions, 'QSettings', lambda *args: settings)


class ConsecutiveTakes:
    def __init__(self, w, app, tmp_path, monkeypatch, *, durations, cue_frames=30):
        self.w, self.app, self.root = w, app, tmp_path
        self.calls, self.questions = [], []
        self.durations = durations
        self.cues = [w.editor.add_caption(index * cue_frames, cue_frames,
                                        f'Caption {index + 1} 原始字幕')
                     for index in range(len(durations))]
        w.changed()
        self.captions = deepcopy(w.project.captions)
        self.baseline = deepcopy(w.project)
        pack = tmp_path / 'mock-pack'
        pack.mkdir()
        w.tts_model.setText(str(pack))
        w._tts_verified_pack = w._tts_pack_key()
        w.update_tts_controls()
        monkeypatch.setattr(tts, 'synthesize', self.synthesize)
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (
            str(self.root / f'take-{len(self.calls) + 1}.wav'), ''))
        monkeypatch.setattr(QMessageBox, 'question', self.keep_full)

    def keep_full(self, *args):
        self.questions.append(args[2])
        return QMessageBox.StandardButton.Yes

    def synthesize(self, text, config, output, **kwargs):
        index = next(index for index, cue in enumerate(self.cues) if cue.text == text)
        count = round(self.durations[index] * 24000)
        with wave.open(str(output), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes((index + 1).to_bytes(2, 'little', signed=True) * count)
        provenance = tts.TTSProvenance(
            text=text, engine='mock-synthesis/Kokoro-' + config.pack_version,
            engine_version='mock-consecutive-boundary', model_sha256='a' * 64,
            voice_sha256='b' * 64, assets_sha256='c' * 64,
            voice_id=str(config.voice_id), speed=config.speed, sample_rate=24000,
            output_sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest(),
            created_utc='2026-10-18T00:00:00Z')
        result = tts.SynthesisResult(str(output), count / 24000, 24000, provenance)
        self.calls.append(result)
        return result

    def generate(self, index, *, track=2):
        self.w.select_caption(self.cues[index].id)
        self.w.tts_track.setCurrentIndex(self.w.tts_track.findData(track))
        self.w.speak_selected()
        pump_until(self.app, lambda: not self.w.jobs)
        return self.w.selected_clip()

    def verify_files(self):
        for result in self.calls:
            path = Path(result.path)
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == result.provenance.output_sha256
            with wave.open(str(path), 'rb') as stream:
                assert stream.getnframes() / stream.getframerate() == result.duration


def test_three_consecutive_takes_collision_retry_trim_undo_save_and_reopen(
        window, app, tmp_path, monkeypatch):
    """A long middle cue must not silently cut or ripple either neighbor."""
    w = window
    takes = ConsecutiveTakes(w, app, tmp_path, monkeypatch, durations=(.75, 1.501, .625))
    first = deepcopy(takes.generate(0))
    second = deepcopy(takes.generate(1))
    assert (first.start, first.end, second.start, second.end) == (0, 23, 30, 76)
    assert len(takes.questions) == 1
    assert '16 frames beyond this caption' in takes.questions[0]
    before_third = deepcopy(w.project)
    history = len(w.editor._undo)
    takes.generate(2)
    assert w.pending_speech is not None and w.project == before_third
    assert len(w.editor._undo) == history
    assert 'Full voice f60–f79 overlaps lane 2: f30–f76' in w.test_warnings[-1]
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    third = deepcopy(w.selected_clip())
    assert (third.start, third.duration, third.track) == (60, 19, 3)
    assert len(takes.calls) == 3 and w.pending_speech is None
    assert w.project.clips[:2] == [first, second]
    assert w.project.captions == takes.captions
    inserted = deepcopy(w.project)

    w.undo()
    assert w.project == before_third
    w.redo()
    assert w.project == inserted
    takes.verify_files()

    # The inspector is the explicit, reversible route for shortening a take.
    w.select_clip(second.id)
    w.duration_spin.setValue(30)
    w.apply_timing()
    trimmed = deepcopy(w.project)
    assert w.selected_clip().end == takes.cues[2].start
    assert w.project.media == inserted.media
    assert w.project.captions == takes.captions
    w.undo()
    assert w.project == inserted
    w.redo()
    assert w.project == trimmed

    w.project_path = tmp_path / 'consecutive.lumen'
    assert w.save_project()
    saved = json.loads(w.project_path.read_text(encoding='utf-8'))
    assert saved['version'] == VERSION
    assert [entry['path'] for entry in saved['media']] == ['take-1.wav', 'take-2.wav', 'take-3.wav']
    for entry, media in zip(saved['media'], trimmed.media):
        assert entry['speech_provenance'] == asdict(media.speech_provenance)
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(w.project_path), ''))
    w.open_project()
    assert w.project == trimmed and not w.editor.can_undo
    assert w.project == Project.load(w.project_path)
    assert w.project.clips[1].duration == 30
    assert w.project.media[1].duration == 1.501
    assert [media.speech_provenance.source_caption_id for media in w.project.media] == [cue.id for cue in takes.cues]
    takes.verify_files()


def test_both_lanes_occupied_repeated_retry_never_imports_or_shortens_third_take(
        window, app, tmp_path, monkeypatch):
    w = window
    takes = ConsecutiveTakes(w, app, tmp_path, monkeypatch, durations=(2.5, 2.5, .75))
    takes.generate(0, track=2)
    takes.generate(1, track=3)
    before = deepcopy(w.project)
    history = deepcopy(w.editor._undo)
    revision = w.revision
    takes.generate(2, track=2)
    pending = w.pending_speech
    for track in (2, 3, 2, 3):
        w.tts_track.setCurrentIndex(w.tts_track.findData(track))
        w.insert_pending_speech()
        assert w.pending_speech is pending
        assert w.project == before and w.editor._undo == history
        assert w.revision == revision and w.tts_insert_button.isEnabled()
    assert len(takes.calls) == 3 and len(takes.questions) == 2
    assert 'overlaps lane 3: f30–f105' in w.test_warnings[-1]
    assert w.project.captions == takes.captions
    takes.verify_files()


def test_pending_consecutive_take_stays_with_original_cue_after_selection_change(
        window, app, tmp_path, monkeypatch):
    w = window
    takes = ConsecutiveTakes(w, app, tmp_path, monkeypatch, durations=(1.5, .75, .5))
    takes.generate(0)
    takes.generate(1)
    pending = w.pending_speech
    assert pending[1].id == takes.cues[1].id
    w.select_caption(takes.cues[2].id)
    w.caption_text.setPlainText('Unapplied later-cue draft')
    w.tts_voice.setValue(45)
    w.tts_speed.setValue(1.5)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    clip = w.selected_clip()
    media = next(media for media in w.project.media if media.id == clip.media_id)
    assert (clip.start, clip.duration, clip.track, clip.speed) == (30, 23, 3, 1)
    assert media.speech_provenance == pending[0].speech_provenance
    assert media.speech_provenance.source_caption_id == takes.cues[1].id
    assert media.speech_provenance.text == takes.cues[1].text
    assert w.project.captions == takes.captions and len(takes.calls) == 2
    takes.verify_files()


@pytest.mark.parametrize('fps', [(30, 1), (30000, 1001)])
def test_consecutive_exact_frame_takes_share_lane_without_gaps_or_ripple(
        window, app, tmp_path, monkeypatch, fps):
    w = window
    w.project.fps_num, w.project.fps_den = fps
    duration = 30 / float(w.project.fps)
    takes = ConsecutiveTakes(w, app, tmp_path, monkeypatch,
                             durations=(duration, duration, duration))
    for index in range(3):
        clip = takes.generate(index)
        assert (clip.start, clip.duration, clip.track) == (index * 30, 30, 2)
    assert not w.pending_speech and not w.test_warnings and not takes.questions
    assert [(clip.start, clip.end) for clip in w.project.clips] == [(0, 30), (30, 60), (60, 90)]
    assert w.project.duration_frames == 90 and w.project.captions == takes.captions
    for _ in range(3):
        w.undo()
    assert w.project == takes.baseline
    for _ in range(3):
        w.redo()
    assert len(w.project.media) == len(w.project.clips) == 3
    takes.verify_files()


def pending_after_collision(w, app, tmp_path, monkeypatch, *, second_duration=.75):
    takes = ConsecutiveTakes(w, app, tmp_path, monkeypatch,
                             durations=(1.5, second_duration))
    first = deepcopy(takes.generate(0))
    takes.generate(1)
    assert w.pending_speech is not None and len(w.project.clips) == 1
    return takes, first


@pytest.mark.parametrize('repair', ['trim', 'move', 'delete'])
def test_repair_blocking_previous_take_reuses_saved_next_take_without_regeneration(
        window, app, tmp_path, monkeypatch, repair):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch)
    pending = w.pending_speech
    if repair == 'trim':
        w.select_clip(first.id)
        w.duration_spin.setValue(30)
        w.apply_timing()
    elif repair == 'move':
        w.move_clip(first.id, 0, 3)
    else:
        w.select_clip(first.id)
        w.delete_selected()
    before = deepcopy(w.project)
    revision, history = w.revision, len(w.editor._undo)
    w.insert_pending_speech()
    clip = w.selected_clip()
    assert (clip.start, clip.source_in, clip.duration, clip.track, clip.speed) == (30, 0, 23, 2, 1)
    assert w.pending_speech is None and len(takes.calls) == 2
    assert w.project.media[-1] == pending[0]
    assert w.project.captions == takes.captions
    assert w.revision == revision + 1 and len(w.editor._undo) == history + 1
    assert 'Inserted rechecked full voice after a timeline edit' in w.tts_status.text()
    after = deepcopy(w.project)
    w.undo()
    assert w.project == before
    w.redo()
    assert w.project == after
    takes.verify_files()


def test_unrelated_edit_undo_replaces_project_but_same_session_can_reuse_pending(
        window, app, tmp_path, monkeypatch):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch)
    original_project, original_editor = w.project, w.editor
    before = deepcopy(w.project)
    # Restore all values, while advancing the revision and replacing Project.
    w.edit(lambda: w.editor.update_clip(first.id, gain_db=-6))
    w.undo()
    assert w.project is not original_project and w.editor is original_editor
    assert w.project == before
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    assert w.selected_clip().start == 30 and w.pending_speech is None
    assert len(takes.calls) == 2
    takes.verify_files()


def test_rechecked_take_uses_current_collision_range_and_keeps_retry_available(
        window, app, tmp_path, monkeypatch):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch)
    pending = w.pending_speech
    w.move_clip(first.id, 40, 2)
    before, history = deepcopy(w.project), deepcopy(w.editor._undo)
    w.insert_pending_speech()
    assert w.pending_speech is pending and w.project == before and w.editor._undo == history
    assert 'Full voice f30–f53 overlaps lane 2: f40–f85' in w.test_warnings[-1]
    assert 'Saved voice rechecked after a timeline edit' in w.tts_status.text()
    assert len(takes.calls) == 2
    takes.verify_files()


def test_reused_long_take_still_requires_full_duration_consent_and_can_retry(
        window, app, tmp_path, monkeypatch):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch, second_duration=1.5)
    w.select_clip(first.id)
    w.duration_spin.setValue(30)
    w.apply_timing()
    pending = w.pending_speech
    before, history = deepcopy(w.project), deepcopy(w.editor._undo)
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    w.insert_pending_speech()
    assert w.pending_speech is pending and w.project == before and w.editor._undo == history
    monkeypatch.setattr(QMessageBox, 'question', takes.keep_full)
    w.insert_pending_speech()
    assert (w.selected_clip().start, w.selected_clip().duration) == (30, 45)
    assert '15 frames beyond this caption' in takes.questions[-1]
    assert w.project.captions == takes.captions and len(takes.calls) == 2
    takes.verify_files()


@pytest.mark.parametrize('change', ['text', 'start', 'duration', 'language', 'delete', 'replace_id'])
def test_changed_original_caption_rejects_pending_even_if_lane_is_free(
        window, app, tmp_path, monkeypatch, change):
    w = window
    takes, _ = pending_after_collision(w, app, tmp_path, monkeypatch)
    cue = takes.cues[1]
    if change in ('delete', 'replace_id'):
        w.editor.delete_caption(cue.id)
        if change == 'replace_id':
            w.editor.add_caption(cue.start, cue.duration, cue.text, cue.language)
    else:
        value = dict(text='Changed source text', start=31, duration=29, language='en')[change]
        w.editor.update_caption(cue.id, **{change: value})
    w.changed()
    before, history = deepcopy(w.project), deepcopy(w.editor._undo)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    assert w.pending_speech is None and not w.tts_insert_button.isEnabled()
    assert w.project == before and w.editor._undo == history
    assert 'project session, frame rate or original caption changed' in w.test_warnings[-1]
    assert len(takes.calls) == 2
    takes.verify_files()


@pytest.mark.parametrize('change', ['reopen_same_project', 'new_project', 'fps'])
def test_new_session_or_frame_rate_rejects_pending_saved_take(
        window, app, tmp_path, monkeypatch, change):
    from PySide6.QtWidgets import QInputDialog
    w = window
    takes, _ = pending_after_collision(w, app, tmp_path, monkeypatch)
    original_editor = w.editor
    if change == 'reopen_same_project':
        w.project_path = tmp_path / 'pending.lumen'
        assert w.save_project()
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(w.project_path), ''))
        w.open_project()
        assert w.editor is not original_editor
    elif change == 'new_project':
        monkeypatch.setattr(w, 'confirm_discard', lambda: True)
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args: ('1280 × 720 · 30 fps', True))
        w.new_project()
        assert w.editor is not original_editor
    else:
        w.project.fps_num, w.project.fps_den = 60, 1
        w.changed()
    before = deepcopy(w.project)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    assert w.project == before and w.pending_speech is None
    assert 'project session, frame rate or original caption changed' in w.test_warnings[-1]
    takes.verify_files()


@pytest.mark.parametrize('change', ['missing', 'changed_bytes', 'symlink'])
@pytest.mark.parametrize('edit_timeline', [False, True])
def test_missing_or_replaced_pending_wav_rejects_then_original_can_be_restored(
        window, app, tmp_path, monkeypatch, change, edit_timeline):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch)
    pending = w.pending_speech
    path = Path(pending[0].path)
    original = path.read_bytes()
    if edit_timeline:
        w.move_clip(first.id, 0, 3)
    else:
        w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    if change == 'missing':
        path.unlink()
    elif change == 'changed_bytes':
        modified = bytearray(original)
        modified[-1] ^= 1
        path.write_bytes(modified)
    else:
        moved = path.with_name('unchanged-original.wav')
        path.rename(moved)
        try:
            path.symlink_to(moved)
        except OSError:
            moved.rename(path)
            pytest.skip('Creating symlinks is unavailable on this platform')
    before, history = deepcopy(w.project), deepcopy(w.editor._undo)
    w.insert_pending_speech()
    assert w.project == before and w.editor._undo == history
    assert w.pending_speech is pending and w.tts_insert_button.isEnabled()
    assert 'saved WAV is missing, changed or cannot be verified' in w.test_warnings[-1]
    path.unlink(missing_ok=True)
    path.write_bytes(original)
    w.insert_pending_speech()
    assert w.pending_speech is None and len(w.project.clips) == 2
    assert len(takes.calls) == 2
    takes.verify_files()


@pytest.mark.parametrize('change', ['edit_undo', 'replace_wav', 'replace_pending'])
def test_reused_long_take_rechecks_revision_bytes_and_pending_identity_after_consent(
        window, app, tmp_path, monkeypatch, change):
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch, second_duration=1.5)
    w.select_clip(first.id)
    w.duration_spin.setValue(30)
    w.apply_timing()
    pending = w.pending_speech
    path = Path(pending[0].path)
    original = path.read_bytes()
    before = deepcopy(w.project)
    newer = []

    def change_while_confirming(*args):
        if change == 'edit_undo':
            w.edit(lambda: w.editor.update_clip(first.id, gain_db=-6))
            w.undo()
            assert w.project == before
        elif change == 'replace_wav':
            path.write_bytes(original[:-2] + b'\x7f\x00')
        else:
            # Simulate a queued newer take replacing the pending selection.
            item = (deepcopy(pending[0]), deepcopy(pending[1]), w.revision)
            newer.append(item)
            w.pending_speech = item
            w._pending_speech_context = replace(w._pending_speech_context, pending=item)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', change_while_confirming)
    w.insert_pending_speech()
    assert w.project == before and len(takes.calls) == 2
    if change == 'replace_wav':
        assert w.pending_speech is pending
        assert 'saved WAV is missing, changed or cannot be verified' in w.test_warnings[-1]
        assert path.read_bytes() != original
    else:
        assert 'changed while confirming insertion' in w.test_warnings[-1]
        assert w.pending_speech is (newer[0] if newer else None)
        takes.verify_files()


def test_stale_warning_cannot_clear_a_newer_pending_take(window, app, tmp_path, monkeypatch):
    w = window
    takes, _ = pending_after_collision(w, app, tmp_path, monkeypatch)
    pending = w.pending_speech
    context = w._pending_speech_context
    w.editor.update_caption(takes.cues[1].id, text='Changed source')
    w.changed()
    newer = (deepcopy(pending[0]), deepcopy(pending[1]), w.revision)

    def replace_during_warning(*args):
        w.pending_speech = newer
        w._pending_speech_context = replace(context, pending=newer)

    monkeypatch.setattr(QMessageBox, 'warning', replace_during_warning)
    w.insert_pending_speech()
    assert w.pending_speech is newer and w._pending_speech_context.pending is newer
    assert len(w.project.clips) == 1
    takes.verify_files()


@pytest.mark.parametrize('mutation', ['caption_snapshot', 'media_path', 'media_duration'])
def test_mutating_pending_snapshot_does_not_redefine_original_take_identity(
        window, app, tmp_path, monkeypatch, mutation):
    w = window
    takes, _ = pending_after_collision(w, app, tmp_path, monkeypatch)
    pending = w.pending_speech
    if mutation == 'caption_snapshot':
        pending[1].text = 'Tampered saved caption'
    elif mutation == 'media_path':
        pending[0].path = str(tmp_path / 'different.wav')
    else:
        pending[0].duration = .5
    before = deepcopy(w.project)
    w.tts_track.setCurrentIndex(w.tts_track.findData(3))
    w.insert_pending_speech()
    assert w.project == before and w.pending_speech is None
    assert len(takes.calls) == 2
    takes.verify_files()


def test_rechecked_take_status_live_translates_without_regeneration(
        window, app, tmp_path, monkeypatch):
    from lumen.i18n import set_locale
    w = window
    takes, first = pending_after_collision(w, app, tmp_path, monkeypatch)
    w.move_clip(first.id, 0, 3)
    w.insert_pending_speech()
    path = takes.calls[-1].path
    assert 'Inserted rechecked full voice after a timeline edit' in w.tts_status.text()
    set_locale('zh-CN')
    assert w.tts_status.text() == f'时间轴编辑后已重新核验并插入完整语音（0.750000 秒）：{path}'
    set_locale('en')
    assert 'Inserted rechecked full voice after a timeline edit' in w.tts_status.text()
    assert len(takes.calls) == 2
