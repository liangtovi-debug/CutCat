"""Render-only subtitle layout settings are bounded, undoable and portable."""
from av_links_legacy_fixture import strip_av_links_field
from contrast_legacy_fixture import strip_contrast_pivot_field
from media_bins_legacy_fixture import strip_media_bins_fields
from clip_groups_legacy_fixture import strip_clip_groups_field
from track_edit_locks_legacy_fixture import strip_track_edit_locks_field
from source_ranges_legacy_fixture import strip_source_ranges_field
from timeline_markers_legacy_fixture import strip_timeline_markers_field
from source_audio_legacy_fixture import strip_source_audio_state_fields
from white_balance_legacy_fixture import strip_white_balance_fields
from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from lumen.model import Caption, CaptionStyle, Editor, Project, VERSION
from lumen.subtitles import format_srt, read_srt, write_srt


def caption_project():
    return Project(captions=[Caption(
        'original', 12, 120,
        '  Original English and 中文 text with spacing  \n'
        'A deliberately long caption line that stays exactly as entered.\tTab  ',
        'zh-CN',
    )])


@pytest.mark.parametrize('version', range(1, VERSION + 1))
def test_missing_layout_fields_use_defaults_without_mutating_old_styles(version):
    original = caption_project()
    original.caption_style = CaptionStyle(
        'Original font family', 37, '#ABCDEF', '#123456', 3, 27, '',
    )
    data = original.to_dict()
    data['version'] = version
    if version < 42:
        strip_av_links_field(data)
    if version < 40:
        strip_source_audio_state_fields(data)
    if version < 39:
        strip_timeline_markers_field(data)
    if version < 38:
        strip_source_ranges_field(data)
    if version < 37:
        strip_track_edit_locks_field(data)
    if version < 36:
        strip_clip_groups_field(data)
    if version < 35:
        strip_media_bins_fields(data)
    if version < 34:
        data.pop('master_gain_db')
    if version < 27: strip_white_balance_fields(data)
    if version < 26: strip_contrast_pivot_field(data)
    if data['version'] < 17:
        for old_clip in data.get('clips', []):
            old_clip.get('grade', {}).pop('hue_hue_curve', None)
            old_clip.get('grade', {}).pop('hue_sat_curve', None)
            old_clip.get('grade', {}).pop('qualifier', None)
    if data['version'] < 16:
        for old_clip in data.get('clips', []): old_clip.pop('transform', None)
    del data['caption_style']['safe_margin_percent']
    del data['caption_style']['max_lines']
    source = deepcopy(data)
    loaded = Project.from_dict(data)
    assert data == source
    assert loaded == original
    assert loaded.caption_style.safe_margin_percent == 5
    assert loaded.caption_style.max_lines == 6
    assert loaded.to_dict()['version'] == VERSION


def test_version_nine_reader_gate_rejects_new_project_before_decoding(monkeypatch):
    # The existing reader gate rejects any version above its supported VERSION.
    # Simulate a v9 reader's ceiling instead of mislabeling new style fields as v9.
    from lumen import model
    payload = caption_project().to_dict()
    assert payload['version'] == VERSION
    monkeypatch.setattr(model, 'VERSION', 9)
    with pytest.raises(ValueError, match='^Unsupported project format or version$'):
        Project.from_dict(payload)


@pytest.mark.parametrize('field,remaining,value', [
    ('safe_margin_percent', 'max_lines', 9),
    ('max_lines', 'safe_margin_percent', 13),
])
def test_each_missing_layout_field_defaults_independently(field, remaining, value):
    data = Project().to_dict()
    del data['caption_style'][field]
    data['caption_style'][remaining] = value
    loaded = Project.from_dict(data)
    assert getattr(loaded.caption_style, field) == getattr(CaptionStyle(), field)
    assert getattr(loaded.caption_style, remaining) == value


@pytest.mark.parametrize('margin,lines', [(0, 1), (5, 6), (20, 20)])
def test_layout_bounds_and_roundtrip(tmp_path, margin, lines):
    project = caption_project()
    project.caption_style.safe_margin_percent = margin
    project.caption_style.max_lines = lines
    project.validate()
    path = tmp_path / 'layout.lumen'
    project.save(path)
    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['caption_style']['safe_margin_percent'] == margin
    assert stored['caption_style']['max_lines'] == lines
    assert Project.load(path) == project


@pytest.mark.parametrize('field,values', [
    ('safe_margin_percent', [-1, 21, True, False, 1.5, 5.0, '5', None, float('inf'), float('nan')]),
    ('max_lines', [0, 21, True, False, 1.5, 6.0, '6', None, float('inf'), float('nan')]),
])
def test_invalid_layout_values_reject_load_and_rollback_atomic_edits(field, values):
    editor = Editor(caption_project())
    editor.update_caption_style(font_size=60)
    editor.undo()
    baseline = deepcopy(editor.project)
    undo_before = deepcopy(editor._undo)
    redo_before = deepcopy(editor._redo)
    for value in values:
        with pytest.raises(ValueError, match='must be an integer'):
            editor.update_caption_style(font_size=24, **{field: value})
        assert editor.project == baseline
        assert editor._undo == undo_before
        assert editor._redo == redo_before
        data = baseline.to_dict()
        data['caption_style'][field] = value
        with pytest.raises(ValueError, match='must be an integer'):
            Project.from_dict(data)


def test_apply_save_undo_redo_preserves_raw_caption_and_srt(tmp_path):
    editor = Editor(caption_project())
    baseline = deepcopy(editor.project)
    raw_captions = [asdict(caption) for caption in baseline.captions]
    raw_srt = format_srt(baseline.captions, baseline.fps)
    before_path = tmp_path / 'before.srt'
    write_srt(before_path, baseline.captions, baseline.fps)
    editor.update_caption_style(
        font_family='User font', font_size=31, margin_bottom=18,
        safe_margin_percent=12, max_lines=4,
    )
    after = deepcopy(editor.project)
    assert len(editor._undo) == 1
    assert [asdict(caption) for caption in after.captions] == raw_captions
    assert format_srt(after.captions, after.fps) == raw_srt
    project_path = tmp_path / 'layout.lumen'
    after.save(project_path)
    loaded = Project.load(project_path)
    after_path = tmp_path / 'after.srt'
    write_srt(after_path, loaded.captions, loaded.fps)
    assert after_path.read_bytes() == before_path.read_bytes()
    assert read_srt(after_path, loaded.fps)[0].text == baseline.captions[0].text
    assert editor.undo() and editor.project == baseline
    assert not editor.can_undo
    assert editor.redo() and editor.project == after
    assert format_srt(editor.project.captions, editor.project.fps) == raw_srt
