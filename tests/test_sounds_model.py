"""Sound inserts are atomic, exact-duration and portable, not catalog placeholders."""
from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import wave
import numpy as np
import pytest
from lumen.model import VERSION, Editor, Media, Project, SoundProvenance


def sound(tmp_path, count=48624, rate=48000):
    path = tmp_path / 'original.wav'
    samples = (.3 * np.sin(np.arange(count) * 2 * np.pi * 440 / rate) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wav:
        wav.setparams((1,2,rate,0,'NONE','not compressed')); wav.writeframes(samples.tobytes())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    provenance = SoundProvenance('test-sound','procedural','CC0-1.0',digest,digest,rate,count)
    return Media('sound',str(path),'Test sound',count/rate,has_video=False,has_audio=True,sound_provenance=provenance)


@pytest.mark.parametrize('rate', [(24,1),(25,1),(30,1),(30000,1001),(60,1)])
@pytest.mark.parametrize('samples', [1, 4800, 48624, 56000, 96000])
def test_full_tail_and_atomic_undo_redo(tmp_path, rate, samples):
    media=sound(tmp_path,samples); editor=Editor(Project(fps_num=rate[0],fps_den=rate[1]))
    before=deepcopy(editor.project); clip=editor.add_sound_clip(media,2,17)
    exact=Fraction(samples,48000)*Fraction(*rate)
    assert clip.duration == -(-exact.numerator//exact.denominator)
    assert clip.start==17 and clip.track==2
    after=deepcopy(editor.project); editor.undo(); assert editor.project==before
    editor.redo(); assert editor.project==after


@pytest.mark.parametrize('track,start',[(0,0),(1,0),(4,0),(True,0),(2,-1),(2,1.5),(2,True)])
def test_invalid_inserts_never_leave_media_or_undo(tmp_path,track,start):
    editor=Editor(); before=deepcopy(editor.project)
    with pytest.raises(ValueError): editor.add_sound_clip(sound(tmp_path),track,start)
    assert editor.project==before and not editor.can_undo


def test_overlap_rollback_then_exact_adjacency(tmp_path):
    editor=Editor(); media=sound(tmp_path); a=editor.add_sound_clip(media,2,10)
    before=deepcopy(editor.project); undo=len(editor._undo)
    with pytest.raises(ValueError,match='overlap'): editor.add_sound_clip(replace(media,id='second'),2,a.end-1)
    assert editor.project==before and len(editor._undo)==undo
    editor.add_sound_clip(replace(media,id='second'),2,a.end)
    editor.add_sound_clip(replace(media,id='third'),3,10)
    editor.project.validate()


def test_portable_save_reopen_move_and_missing(tmp_path):
    original=tmp_path/'inputs'; original.mkdir(); media=sound(original)
    editor=Editor(); editor.add_sound_clip(media,2,9)
    target=tmp_path/'project'; target.mkdir(); path=target/'film.lumen'
    editor.project.save(path)
    data=json.loads(path.read_text()); stored=data['media'][0]['path']
    assert stored.startswith('film.assets/sounds/') and '..' not in stored
    assert editor.project.media[0].path==str(original/'original.wav')
    original.rename(tmp_path/'hidden-original')
    moved=tmp_path/'moved'; shutil.move(target,moved)
    project=Project.load(moved/'film.lumen'); assert not project.missing_media()
    assert project.media[0].sound_provenance==media.sound_provenance
    assert project.clips==editor.project.clips
    Path(project.media[0].path).unlink(); assert project.missing_media()
    project.save(moved/'film.lumen'); assert Project.load(moved/'film.lumen').missing_media()


def test_save_fails_without_overwriting_source_or_existing_project(tmp_path):
    media=sound(tmp_path); editor=Editor(); editor.add_sound_clip(media,2,0)
    source=Path(media.path).read_bytes()
    with pytest.raises(ValueError): editor.project.save(media.path)
    assert Path(media.path).read_bytes()==source
    path=tmp_path/'film.lumen'; editor.project.save(path); previous=path.read_bytes()
    Path(media.path).write_bytes(source[:-1]+b'x')
    with pytest.raises(ValueError): editor.project.save(path)
    assert path.read_bytes()==previous


def test_schema_provenance_and_bad_metadata(tmp_path):
    media=sound(tmp_path); project=Project(media=[media]); data=project.to_dict()
    assert data['version']== VERSION and Project.from_dict(data)==project
    with pytest.raises(FrozenInstanceError): media.sound_provenance.sample_count=1
    data['version']=12; strip_contrast_pivot_field(data)
    with pytest.raises(ValueError,match='Legacy'): Project.from_dict(data)
    for bad in (replace(media, duration=2),replace(media,has_video=True),replace(media,audio_offset=.1)):
        with pytest.raises(ValueError): Project(media=[bad]).validate()


def test_live_resave_uses_valid_portable_copy_if_library_original_vanishes(tmp_path):
    media=sound(tmp_path); editor=Editor(); editor.add_sound_clip(media,2,0)
    path=tmp_path/'film.lumen'; editor.project.save(path)
    original_json=path.read_bytes(); Path(media.path).unlink()
    editor.project.save(path)
    assert not Project.load(path).missing_media() and path.read_bytes()==original_json
