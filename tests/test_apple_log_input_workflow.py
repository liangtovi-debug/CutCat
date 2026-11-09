"""Explicit import persistence and strict legacy schema compatibility."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from lumen.model import Media, Project, Editor, VERSION
from lumen.media import probe, MediaError


def apple_media(tmp_path):
    return Media('apple', str(tmp_path / 'apple.mkv'), 'Apple fixture', 1,
                 width=64, height=64, color_space='gbr', color_range='pc',
                 color_primaries='bt2020', input_encoding='apple_log_bt2020')


def test_explicit_import_roundtrip_add_split_and_undo(tmp_path):
    editor = Editor(Project(media=[apple_media(tmp_path)]))
    clip = editor.add_clip('apple', 0)
    assert clip.grade.input_encoding == 'apple_log_bt2020'
    before = deepcopy(editor.project)
    left, right = editor.split_clip(clip.id, 10)
    assert left.grade.input_encoding == right.grade.input_encoding == 'apple_log_bt2020'
    assert editor.undo() and editor.project == before
    data = editor.project.to_dict()
    assert data['version'] == VERSION
    assert Project.from_dict(data) == editor.project


def test_legacy_default_migrates_but_forward_apple_assignment_rejected(tmp_path):
    editor = Editor(Project(media=[Media('m', str(tmp_path / 'sdr.mov'), 'SDR', 1)]))
    editor.add_clip('m', 0)
    data = editor.project.to_dict(); data['version'] = 28
    from window_animation_legacy_fixture import strip_window_animation_field
    strip_window_animation_field(data)
    data['media'][0].pop('input_encoding')
    assert Project.from_dict(data).media[0].input_encoding == 'rec709'
    for field in ('media', 'grade'):
        forged = deepcopy(data)
        target = forged['media'][0] if field == 'media' else forged['clips'][0]['grade']
        target['input_encoding'] = 'apple_log_bt2020'
        with pytest.raises(ValueError, match='Legacy projects cannot contain Apple Log'):
            Project.from_dict(forged)


def test_default_import_does_not_autodetect_srgb_or_log(tmp_path):
    media = Media('m', str(tmp_path / 'sdr.mov'), 'SDR', 1, color_transfer='iec61966-2-1')
    editor = Editor(Project(media=[media]))
    assert editor.add_clip('m', 0).grade.input_encoding == 'rec709'


def test_probe_explicit_profile_is_opt_in(tmp_path, monkeypatch):
    import lumen.media as module
    path = tmp_path / 'fixture.mkv'; path.write_bytes(b'fixture')
    stream = dict(codec_type='video', pix_fmt='gbrp16le', color_space='gbr',
                  color_primaries='bt2020', color_range='pc', color_transfer='unknown',
                  width=64, height=64, duration='1')
    monkeypatch.setattr(module, 'ffmpeg_available', lambda: True)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps({'streams': [stream], 'format': {'duration': '1'}})))
    with pytest.raises(MediaError):
        probe(path)
    media = probe(path, input_encoding='apple_log_bt2020')
    assert media.input_encoding == 'apple_log_bt2020'
    assert media.color_primaries == 'bt2020'
    stream['color_transfer'] = 'smpte2084'
    with pytest.raises(MediaError):
        probe(path, input_encoding='apple_log_bt2020')
