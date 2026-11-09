from contrast_legacy_fixture import strip_contrast_pivot_field
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import tempfile
import pytest

from lumen.model import Caption, Editor, Media, Project, SpeechProvenance, VERSION


def provenance(**changes):
    data = dict(text='Hello 世界 👋', engine='kokoro-onnx', engine_version='1.0',
                model_sha256='a' * 64, voice_sha256='b' * 64, assets_sha256='c' * 64,
                voice_id='af_heart', speed=1.0, sample_rate=24000,
                created_utc='2026-10-14T21:00:00Z', source_caption_id='caption',
                source_start=15, source_duration=30, output_sha256='d' * 64)
    return SpeechProvenance(**(data | changes))


def speech():
    path = (Path(tempfile.gettempdir()) / 'speech.wav').resolve()
    return Media('speech', str(path), 'Speech', 1.01,
                 has_video=False, has_audio=True, speech_provenance=provenance())


def editor():
    return Editor(Project(captions=[Caption('caption', 15, 30, 'Hello 世界 👋')]))


def test_immutable_and_roundtrip(tmp_path):
    p = provenance()
    with pytest.raises(FrozenInstanceError):
        p.text = 'Changed'
    ed = editor()
    ed.add_speech_clip(speech(), 'caption', 2)
    path = tmp_path / 'project.lumen'
    ed.project.save(path)
    loaded = Project.load(path)
    assert loaded == ed.project
    assert isinstance(loaded.media[0].speech_provenance, SpeechProvenance)
    assert loaded.to_dict()['version'] == VERSION


@pytest.mark.parametrize('version', [1, 2, 3])
def test_migrate_legacy(version):
    data = Project(media=[Media('old', '/tmp/old.wav', 'Old', 1, has_video=False, has_audio=True)]).to_dict()
    data['version'] = version; strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    data['media'][0].pop('speech_provenance')
    loaded = Project.from_dict(data)
    assert loaded.media[0].speech_provenance is None
    assert loaded.to_dict()['version'] == VERSION


def test_atomic_add_undo_and_redo():
    ed = editor()
    original = deepcopy(ed.project)
    clip = ed.add_speech_clip(speech(), 'caption', 2)
    assert (clip.start, clip.source_in, clip.duration) == (15, 0, 31)
    assert clip.duration / 30 >= 1.01
    assert ed.project.media_frames(Media('old', 'old.wav', 'Old', 1.01)) == 30
    added = deepcopy(ed.project)
    ed.undo()
    assert ed.project == original
    assert not ed.can_undo
    ed.redo()
    assert ed.project == added


def test_conflict_has_no_partial_import_or_history():
    ed = editor()
    ed.add_speech_clip(speech(), 'caption', 2)
    before = deepcopy(ed.project)
    other = speech()
    other.id = 'other'
    with pytest.raises(ValueError, match='overlap'):
        ed.add_speech_clip(other, 'caption', 2)
    assert ed.project == before
    ed.undo()
    assert not ed.project.media and not ed.project.clips


@pytest.mark.parametrize('track', [0, 1, 4, True])
def test_audio_tracks_only(track):
    ed = editor()
    with pytest.raises(ValueError):
        ed.add_speech_clip(speech(), 'caption', track)
    assert not ed.project.media and not ed.can_undo


def test_caption_edits_and_trim_preserve_snapshot():
    ed = editor()
    clip = ed.add_speech_clip(speech(), 'caption', 3, start=99)
    snapshot = ed.project.media[0].speech_provenance
    ed.update_caption('caption', text='New words', start=300, duration=50)
    ed.trim_clip(clip.id, 1, 20)
    ed.delete_caption('caption')
    assert ed.project.media[0].speech_provenance == snapshot
    assert snapshot.source_start == 15 and snapshot.source_duration == 30
    assert snapshot.text == 'Hello 世界 👋'
    ed.project.validate()


@pytest.mark.parametrize('changes', [
    {'speed': math.nan}, {'speed': math.inf}, {'speed': True}, {'speed': 0},
    {'sample_rate': 0}, {'sample_rate': 24000.0}, {'source_start': -1},
    {'source_duration': 0}, {'source_duration': None}, {'source_caption_id': ''},
    {'source_caption_id': None}, {'text': '\ud800'}, {'text': '   '},
    {'model_sha256': 'abc'}, {'voice_sha256': 'g' * 64}, {'assets_sha256': None},
    {'output_sha256': 'x' * 64}, {'created_utc': '2026-02-30T00:00:00Z'},
    {'created_utc': '2026-01-01T00:00:00'}, {'created_utc': '2026-01-01T00:00:00+01:00'},
])
def test_strict_provenance(changes):
    with pytest.raises(ValueError):
        provenance(**changes)


def test_generated_source_protected(tmp_path):
    media = speech()
    source = tmp_path / 'speech.wav'
    source.write_bytes(b'original audio')
    media.path = str(source)
    ed = editor()
    ed.add_speech_clip(media, 'caption', 2)
    with pytest.raises(ValueError, match='overwrite'):
        ed.project.save(source)
    assert source.read_bytes() == b'original audio'
    alias = tmp_path / 'alias.lumen'
    alias.symlink_to(source)
    with pytest.raises(ValueError, match='overwrite'):
        ed.project.save(alias)
    assert source.read_bytes() == b'original audio'


def test_malformed_nested_provenance():
    data = Project(media=[speech()]).to_dict()
    for value in [[], 'bad', {'unexpected': 1}]:
        invalid = deepcopy(data)
        invalid['media'][0]['speech_provenance'] = value
        with pytest.raises(ValueError):
            Project.from_dict(invalid)


def test_ceil_does_not_allow_extra_source_frames():
    ed = editor()
    clip = ed.add_speech_clip(speech(), 'caption', 2)
    before = deepcopy(ed.project)
    with pytest.raises(ValueError, match='exceeds source'):
        ed.trim_clip(clip.id, 0, 32)
    assert ed.project == before
    with pytest.raises(ValueError, match='exceeds source'):
        ed.trim_clip(clip.id, 1, 31)
    assert ed.project == before


def test_unknown_caption_and_mismatched_source_are_atomic():
    ed = editor()
    with pytest.raises(ValueError, match='Unknown caption'):
        ed.add_speech_clip(speech(), 'missing', 2)
    item = speech()
    item.speech_provenance = replace(provenance(), source_caption_id='other')
    with pytest.raises(ValueError, match='different caption'):
        ed.add_speech_clip(item, 'caption', 2)
    assert not ed.project.media and not ed.project.clips and not ed.can_undo


@pytest.mark.parametrize('changes', [
    {'has_video': True}, {'has_audio': False}, {'audio_offset': .1}, {'duration': 0},
    {'speech_provenance': {}},
])
def test_generated_media_invariants(changes):
    item = replace(speech(), **changes)
    with pytest.raises(ValueError):
        Project(media=[item]).validate()


@pytest.mark.parametrize('rate', ['1/4', '1/2', '2/3', '1', '3/2', '2', '4'])
def test_playback_speed_keeps_complete_speech_span_and_immutable_provenance(rate):
    ed = editor()
    clip = ed.add_speech_clip(speech(), 'caption', 2)
    snapshot = deepcopy(ed.project.media[0].speech_provenance)
    expected_captions = deepcopy(ed.project.captions)
    clip = ed.set_clip_speed(clip.id, rate)
    assert clip.timing.source_in == 0
    assert clip.source_out == 31  # Existing ceil-to-frame speech extent, never 32.
    assert ed.project.media[0].speech_provenance == snapshot
    assert ed.project.media[0].speech_provenance.speed == 1.0
    assert ed.project.captions == expected_captions
    baseline = ed.project.to_dict()
    with pytest.raises(ValueError, match='source duration'):
        ed.trim_clip_source(clip.id, 0, 32)
    assert ed.project.to_dict() == baseline
    assert Project.from_dict(baseline) == ed.project
