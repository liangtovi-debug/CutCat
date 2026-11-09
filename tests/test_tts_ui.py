"""Offline GUI boundary tests: synthesis is mocked, no model/network needed."""
from test_ui import app, window, pump_until
from pathlib import Path
from types import SimpleNamespace
import threading
import hashlib
from dataclasses import asdict
import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from lumen.model import Media


def setup_voice(w, tmp_path, monkeypatch, duration=.75):
    import lumen.tts as tts
    caption = w.editor.add_caption(10, 30, 'Original caption')
    w.changed(); w.select_caption(caption.id)
    w.tts_model.setText(str(tmp_path)); w.tts_track.setCurrentIndex(1)
    w._tts_verified_pack = w._tts_pack_key(); w.update_tts_controls()
    target = tmp_path / 'voice.wav'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a: (str(target), ''))
    calls = []
    def synthesize(text, config, output_path, *, progress, cancel):
        calls.append((text, config, output_path))
        Path(output_path).write_bytes(b'generated WAV fixture')
        progress(1)
        return SimpleNamespace(path=output_path, duration=duration, sample_rate=24000,
            provenance=dict(text=text, engine='sherpa-onnx', engine_version='1.12.0',
                model_sha256='1'*64, voice_sha256='2'*64, assets_sha256='3'*64,
                voice_id=str(config.voice_id), speed=config.speed, sample_rate=24000,
                created_utc='2026-10-14T21:00:00Z', output_sha256=hashlib.sha256(Path(output_path).read_bytes()).hexdigest()))
    monkeypatch.setattr(tts, 'synthesize', synthesize)
    return caption, target, calls, synthesize


def test_voice_current_text_full_duration_provenance_and_one_undo(window, app, tmp_path, monkeypatch):
    w = window; cue, target, calls, _ = setup_voice(w, tmp_path, monkeypatch)
    w.caption_text.setPlainText('Reviewed subtitle text'); w.tts_speed.setValue(1.25); w.tts_voice.setValue(3)
    revision = w.revision; w.speak_selected(); pump_until(app, lambda: not w.jobs)
    assert calls[0][0] == 'Reviewed subtitle text'
    assert (calls[0][1].speed, calls[0][1].voice_id) == (1.25, 3)
    clip = w.selected_clip(); assert (clip.start, clip.duration, clip.track) == (10, 23, 2)
    assert w.revision == revision + 1 and w.tts_job is None
    provenance = w.project.media[0].speech_provenance
    assert provenance.text == 'Reviewed subtitle text' and provenance.source_caption_id == cue.id
    assert provenance.source_duration == 30 and w.project.captions[0].text == 'Original caption'
    messages = []; monkeypatch.setattr(QMessageBox, 'information', lambda *a: messages.append(a[2]))
    w.inspect_speech(); assert 'Reviewed subtitle text' in messages[-1] and 'output_sha256' in messages[-1]
    w.undo(); assert not w.project.media and not w.project.clips and target.exists()
    w.redo(); assert w.project.media[0].speech_provenance == provenance
    w.editor.update_caption(cue.id, text='Later changed caption'); w.changed()
    assert w.project.media[0].speech_provenance == provenance


def test_voice_requires_selection_lane_pack_and_new_destination(window, tmp_path, monkeypatch):
    w=window; w.speak_selected(); assert 'Select a subtitle' in w.test_warnings[-1]
    cue,target,calls,_=setup_voice(w,tmp_path,monkeypatch)
    w.tts_track.setCurrentIndex(0); w.speak_selected(); assert 'destination audio lane' in w.test_warnings[-1]
    w.tts_track.setCurrentIndex(1);w.tts_model.setText('');w.speak_selected(); assert 'existing local' in w.test_warnings[-1]
    w.tts_model.setText(str(tmp_path));w._tts_verified_pack=w._tts_pack_key();target.write_bytes(b'preserve')
    w.speak_selected();assert 'new file' in w.test_warnings[-1] and not calls and target.read_bytes()==b'preserve'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(w.cache/'voice.wav'),''))
    w.speak_selected(); assert 'temporary preview cache' in w.test_warnings[-1] and not calls


def test_voice_stale_does_not_insert_but_preserves_wav(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w=window;cue,target,calls,synth=setup_voice(w,tmp_path,monkeypatch)
    gate=threading.Event()
    def slow(*a,**kw):
        assert gate.wait(3)
        return synth(*a,**kw)
    monkeypatch.setattr(tts,'synthesize',slow)
    w.speak_selected();w.editor.update_caption(cue.id,text='Changed');w.changed();gate.set()
    pump_until(app,lambda:not w.jobs)
    assert target.exists() and not w.project.media and not w.project.clips
    assert 'project changed' in w.test_warnings[-1] and w.pending_speech is None


def test_voice_longer_caption_requires_explicit_full_keep(window, app, tmp_path, monkeypatch):
    w=window;cue,target,calls,_=setup_voice(w,tmp_path,monkeypatch,duration=1.501)
    questions=[]
    def decline(*a): questions.append(a[2]);return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox,'question',decline)
    w.speak_selected();pump_until(app,lambda:not w.jobs)
    assert not w.project.clips and target.exists() and w.pending_speech
    assert '1.501000 seconds (46 timeline frames)' in questions[-1] and '16 frames' in questions[-1]
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    revision=w.revision;w.insert_pending_speech()
    assert w.selected_clip().duration==46 and w.project.captions[0].duration==30
    assert w.revision==revision+1 and not w.pending_speech


def test_voice_overlap_retry_other_lane_preserves_full_voice(window, app, tmp_path, monkeypatch):
    w=window;cue,target,calls,_=setup_voice(w,tmp_path,monkeypatch)
    m=Media('existing',str(tmp_path/'existing.wav'),'Existing',2,has_video=False,has_audio=True)
    w.editor.add_media(m);w.editor.add_clip(m.id,2,0);w.changed();w.select_caption(cue.id)
    revision=w.revision;w.speak_selected();pump_until(app,lambda:not w.jobs)
    assert w.revision==revision and len(w.project.clips)==1 and w.pending_speech
    assert 'overlaps lane 2: f0–f60' in w.test_warnings[-1]
    w.tts_track.setCurrentIndex(2);w.insert_pending_speech()
    assert w.selected_clip().track==3 and w.selected_clip().duration==23 and len(calls)==1
    w.undo();assert len(w.project.clips)==1 and len(w.project.media)==1 and target.exists()


def test_voice_cancel_cleans_job_without_insertion(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w=window;setup_voice(w,tmp_path,monkeypatch)
    def slow(*a,cancel=None,**kw):
        cancel.wait(3)
        raise RuntimeError('Canceled')
    monkeypatch.setattr(tts,'synthesize',slow)
    w.speak_selected();w.cancel_jobs();pump_until(app,lambda:not w.jobs)
    assert not w.project.clips and w.tts_job is None and w.tts_button.isEnabled()
    assert not w.test_warnings


def test_voice_failed_generation_can_retry_new_file(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w=window;cue,target,calls,synth=setup_voice(w,tmp_path,monkeypatch)
    monkeypatch.setattr(tts,'synthesize',lambda *a,**kw:(_ for _ in ()).throw(ValueError('Invalid local pack')))
    w.speak_selected();pump_until(app,lambda:not w.jobs)
    assert 'Invalid local pack' in w.test_warnings[-1] and w.tts_button.isEnabled()
    monkeypatch.setattr(tts,'synthesize',synth)
    w.speak_selected();pump_until(app,lambda:not w.jobs)
    assert len(w.project.clips)==1


def test_voice_regeneration_uses_new_file_and_keeps_previous_take(window, app, tmp_path, monkeypatch):
    w=window;cue,target,calls,_=setup_voice(w,tmp_path,monkeypatch)
    w.speak_selected();pump_until(app,lambda:not w.jobs)
    original=w.project.media[0].speech_provenance; original_bytes=target.read_bytes()
    w.select_caption(cue.id);w.tts_track.setCurrentIndex(2);w.tts_speed.setValue(1.5)
    second=tmp_path/'voice-second.wav'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a:(str(second),''))
    w.speak_selected();pump_until(app,lambda:not w.jobs)
    assert len(w.project.clips)==2 and second.exists() and target.read_bytes()==original_bytes
    assert w.project.media[0].speech_provenance==original
    assert w.project.media[1].speech_provenance.speed==1.5


def test_tts_close_waits_for_cancel_before_cache_cleanup(window, app, tmp_path, monkeypatch):
    import lumen.tts as tts
    w=window;setup_voice(w,tmp_path,monkeypatch);started=threading.Event()
    def slow(*a,cancel=None,**kw):
        started.set();cancel.wait(3)
        raise RuntimeError('Canceled')
    monkeypatch.setattr(tts,'synthesize',slow)
    w.speak_selected();assert started.wait(2)
    monkeypatch.setattr(QMessageBox,'question',lambda *a:QMessageBox.StandardButton.Yes)
    cache=w.cache;w.close()
    pump_until(app,lambda:not w.jobs and not cache.exists())
    assert w.closing and not w.project.clips
