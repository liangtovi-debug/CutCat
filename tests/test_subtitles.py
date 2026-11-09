from copy import deepcopy
from media_bins_legacy_fixture import strip_media_bins_fields
from fractions import Fraction
import json
from pathlib import Path

import pytest

from lumen.model import Caption, CaptionStyle, Editor, Project, VERSION
from lumen.subtitles import format_srt, parse_srt, read_srt, write_srt, seconds_to_frame


def cue(start=0, duration=30, text='Hello\nWorld', id='a'):
    return Caption(id, start, duration, text)


def test_bom_multiline_and_exact_quantization():
    items = parse_srt('\ufeff1\r\n00:00:00,001 --> 00:00:01,001\r\nHello\r\n世界\r\n', 30)
    assert (items[0].start, items[0].duration, items[0].text) == (0, 30, 'Hello\n世界')
    assert items[0].language == 'und'


def test_shared_boundary_and_real_overlap():
    text = '1\n00:00:00,000 --> 00:00:00,500\nA\n\n2\n00:00:00,500 --> 00:00:01,000\nB'
    items = parse_srt(text, 25)
    assert items[0].end == items[1].start == 13
    with pytest.raises(ValueError, match='overlap in source timings'):
        parse_srt(text.replace('00:00:00,500 -->', '00:00:00,499 -->'), 25)
    with pytest.raises(ValueError, match='zero frames'):
        parse_srt('1\n00:00:00,001 --> 00:00:00,002\nA', 25)
    assert seconds_to_frame(Fraction(1, 2), 25) == 13
    assert seconds_to_frame(0.5, 25) == 13


@pytest.mark.parametrize('time', ['00:60:00,000 --> 00:61:00,000', '00:00:00.000 --> 00:00:01.000',
    '0:00:00,000 --> 00:00:01,000', '-1:00:00,000 --> 00:00:01,000',
    '00:00:01,000 --> 00:00:00,000', '00:00:00,000 --> 00:00:00,000',
    '00:00:00,000 --> 00:00:01,000 trailing'])
def test_malformed_times(time):
    with pytest.raises(ValueError):
        parse_srt('1\n' + time + '\nHello', 30)


@pytest.mark.parametrize('text', ['1', '1\n00:00:00,000 --> 00:00:01,000', 'x\n00:00:00,000 --> 00:00:01,000\nText',
    '1\n00:00:00,000 --> 00:00:01,000\n\x00', '1\n00:00:00,000 --> 00:00:01,000\n' + 'x' * 10001], ids=['number-only', 'missing-text', 'invalid-number', 'nul-text', 'over-text-limit'])
def test_malformed_cues(text):
    with pytest.raises(ValueError):
        parse_srt(text, 30)


@pytest.mark.parametrize('fps', [0, -1, True, None, float('nan'), float('inf'), 1001])
def test_invalid_fps(fps):
    with pytest.raises(ValueError):
        parse_srt('', fps)
    with pytest.raises(ValueError):
        format_srt([], fps)


@pytest.mark.parametrize('fps', [24, 25, 30, 60, Fraction(24000, 1001), Fraction(30000, 1001)])
def test_frame_exact_roundtrip(fps):
    original = [cue(start=101, duration=1), cue(start=102, duration=73, id='b')]
    encoded = format_srt(original, fps)
    decoded = parse_srt(encoded, fps)
    assert [(c.start, c.duration, c.text) for c in decoded] == [(c.start, c.duration, c.text) for c in original]
    assert encoded == format_srt(list(reversed(original)), fps)


def test_read_write_protect_existing_files(tmp_path):
    target = tmp_path / 'captions.srt'
    write_srt(target, [cue()], 30)
    assert read_srt(target, 30)[0].text == 'Hello\nWorld'
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        write_srt(target, [cue(text='replace')], 30)
    assert target.read_bytes() == before
    alias = tmp_path / 'alias.srt'
    alias.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_srt(alias, [cue()], 30)
    assert target.read_bytes() == before
    target.write_bytes(b'\xff')
    with pytest.raises(ValueError, match='UTF-8'):
        read_srt(target, 30)


def test_empty_srt():
    assert parse_srt('\ufeff\n', 30) == []
    assert format_srt([], 30) == ''


def test_caption_editor_transactions_and_independent_snapshots():
    editor = Editor()
    caption = editor.add_caption(10, 20, 'Hi\r\nthere', 'en-US')
    assert editor.project.duration_frames == 30
    assert caption.text == 'Hi\nthere'
    baseline = deepcopy(editor.project)
    with pytest.raises(ValueError, match='overlap'):
        editor.add_caption(20, 20, 'overlap')
    assert editor.project == baseline
    editor.update_caption(caption.id, text='Updated', start=20)
    assert editor.project.duration_frames == 40
    assert editor.undo() and editor.project == baseline
    assert editor.redo() and editor.project.captions[0].text == 'Updated'
    editor.delete_caption(caption.id)
    assert editor.project.duration_frames == 0
    assert editor.undo() and len(editor.project.captions) == 1


def test_import_captions_one_undo_and_defensive_copy():
    editor = Editor()
    editor.add_caption(0, 30, 'Old')
    original = deepcopy(editor.project)
    incoming = [cue(start=40), cue(start=80, id='b')]
    editor.import_captions(incoming, replace=True)
    incoming[0].text = 'external mutation'
    assert editor.project.captions[0].text == 'Hello\nWorld'
    assert editor.undo() and editor.project == original
    assert editor.redo() and len(editor.project.captions) == 2
    baseline = deepcopy(editor.project)
    with pytest.raises(ValueError):
        editor.import_captions([cue(start=40, id='other')])
    assert editor.project == baseline


@pytest.mark.parametrize('field,value', [('start', True), ('start', 1.5), ('start', float('inf')), ('start', -1),
    ('duration', 0), ('duration', float('nan')), ('duration', 2**54), ('text', ''), ('text', ' '),
    ('text', '\x00'), ('text', 'x' * 10001), ('language', ''), ('id', 'change')], ids=['bool-start', 'float-start', 'infinite-start', 'negative-start', 'zero-duration', 'nan-duration', 'huge-duration', 'empty-text', 'blank-text', 'nul-text', 'over-text-limit', 'empty-language', 'immutable-id'])
def test_invalid_caption_update_rolls_back(field, value):
    editor = Editor()
    caption = editor.add_caption(0, 30, 'Text')
    baseline = deepcopy(editor.project)
    with pytest.raises(ValueError):
        editor.update_caption(caption.id, **{field: value})
    assert editor.project == baseline


def test_count_and_duplicate_limits():
    with pytest.raises(ValueError, match='20000'):
        Project(captions=[cue()] * 20001).validate()
    with pytest.raises(ValueError, match='Duplicate'):
        Project(captions=[cue(), cue(start=40)]).validate()


def test_style_validation_and_undo():
    editor = Editor()
    editor.update_caption_style(font_size=64, color='#112233', outline_width=0)
    assert editor.project.caption_style.font_size == 64
    assert editor.undo() and editor.project.caption_style == CaptionStyle()
    for kwargs in [{'font_size': True}, {'color': 'red'}, {'outline_width': -1}, {'margin_bottom': float('inf')}, {'font_family': ''}, {'unknown': 3}]:
        with pytest.raises(ValueError):
            editor.update_caption_style(**kwargs)
        assert editor.project.caption_style == CaptionStyle()


def test_schema_migrations_and_relative_font_path(tmp_path):
    editor = Editor()
    editor.add_caption(0, 30, 'Text')
    fonts = tmp_path / 'fonts'
    fonts.mkdir()
    editor.update_caption_style(font_directory=str(fonts))
    target = tmp_path / 'project.lumen'
    editor.project.save(target)
    data = json.loads(target.read_text())
    assert data['version'] == VERSION
    assert data['caption_style']['font_directory'] == 'fonts'
    assert Project.load(target) == editor.project
    for version in [1, 2]:
        legacy = dict(data, version=version)
        strip_media_bins_fields(legacy)
        legacy.pop('master_gain_db')
        legacy.pop('captions')
        legacy.pop('caption_style')
        migrated = Project.from_dict(legacy)
        assert migrated.captions == [] and migrated.caption_style == CaptionStyle()
        assert migrated.to_dict()['version'] == VERSION
    protected = fonts / 'custom.ttf'
    protected.write_bytes(b'font bytes')
    with pytest.raises(ValueError, match='font directory'):
        editor.project.save(protected)
    assert protected.read_bytes() == b'font bytes'


def test_caption_text_spaces_preserved_and_video_edits_independent():
    from lumen.model import Media
    original = cue(text='  padded text  ')
    assert parse_srt(format_srt([original], 30), 30)[0].text == original.text
    editor = Editor(Project(media=[Media('m', '/nonexistent.mp4', 'test', 10)]))
    editor.import_captions([original])
    clip = editor.add_clip('m', 0)
    editor.move_clip(clip.id, 50)
    editor.trim_clip(clip.id, 20, 50)
    editor.split_clip(clip.id, 70)
    editor.delete_clip(clip.id)
    assert editor.project.captions == [original]
