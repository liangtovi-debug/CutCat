"""Softness edits survive project/recovery/copy and remain transactional."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from lumen.color_source import SourceColor
from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import QualifierRange, QualifierSettings
from lumen.qualifier_picker import soften_qualifier_range
from lumen.render import RenderEngine
from test_grade_transfer import project_pair
from test_qualifier_integration import project as simple_project


def softened():
    original = QualifierSettings(enabled=True, hue_width=.5,
        inclusion_ranges=[QualifierRange(hue_width=.5, saturation_low=.4)],
        exclusion_ranges=[QualifierRange(hue_width=.5, luma_low=.3)],
        window_shape='ellipse', window_width=.7, exposure=.3)
    rgb = np.array([.2, .2, .2], np.float32)
    first, _ = soften_qualifier_range(original, rgb, target=('inclusion_ranges', 0), channel='saturation')
    result, _ = soften_qualifier_range(first, rgb, target=('exclusion_ranges', 0), channel='luma')
    return original, result, rgb


def test_roundtrip_recovery_copy_undo_redo_and_noop(tmp_path):
    candidate, _ = project_pair(tmp_path)
    original, result, rgb = softened()
    candidate.clips[0].grade.qualifier = original
    editor = Editor(candidate)
    before = deepcopy(editor.project)
    grade = asdict(candidate.clips[0].grade)
    grade['qualifier'] = asdict(result)
    editor.update_clip('source', grade=grade)
    after = deepcopy(editor.project)
    assert editor.revision == 1 and len(editor._undo) == 1
    same, _ = soften_qualifier_range(result, rgb, target=('exclusion_ranges', 0), channel='luma')
    assert same == result
    # UI must avoid committing numerically unchanged settings; pure sampling
    # itself never changes a project or adds history.
    assert editor.project == after and editor.revision == 1
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    path = tmp_path / 'softness.lumen'
    editor.project.save(path)
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'recovery') as store:
        recovered = store.load(store.write(editor.project, uuid4()))
        assert recovered == after
        recovered.clips[0].grade.qualifier.inclusion_ranges[0] = QualifierRange()
        assert editor.project == after
    copied = copy_grade(editor.project, 'source')
    assert paste_grade(editor, 'target', copied)
    assert editor.project.clips[1].grade.qualifier == result
    revision = editor.revision
    assert not paste_grade(editor, 'target', copied)
    assert editor.revision == revision
    editor.project.clips[1].grade.qualifier.exclusion_ranges[0] = QualifierRange()
    assert copied.grade_for_input('rec709').qualifier == result
    assert editor.project.clips[0].grade.qualifier == result


def test_invalid_softness_pick_cannot_modify_project_or_history(tmp_path):
    candidate, _ = project_pair(tmp_path)
    original, _, rgb = softened()
    candidate.clips[0].grade.qualifier = original
    editor = Editor(candidate)
    before = deepcopy(editor.project)
    for options in [dict(channel='hue'), dict(channel='luma', valid=False),
                    dict(channel='luma', target=('exclusion_ranges', 4))]:
        with pytest.raises(ValueError):
            soften_qualifier_range(editor.project.clips[0].grade.qualifier, rgb, **options)
        assert editor.project == before
        assert editor.revision == 0 and not editor.can_undo


@pytest.mark.parametrize('target,channel', [(('base', 0), 'luma'),
    (('inclusion_ranges', 0), 'saturation'), (('exclusion_ranges', 0), 'luma')])
def test_softness_pick_invalidates_both_caches_and_undo_reuses_them(tmp_path, monkeypatch, target, channel):
    import lumen.color_media as color_media
    candidate = simple_project()
    original, _, rgb = softened()
    original.luma_low = .3
    candidate.clips[0].grade.qualifier = original
    source = tmp_path / 'cache-source.mkv'
    source.write_bytes(b'local fixture')
    media = candidate.media[0]
    media.path = str(source)
    for item in candidate.media:
        Path(item.path).write_bytes(b'local fixture')
    candidate.clips[0].grade.input_encoding = 'rec709'
    color = SourceColor('gbrp16le', 'rgb', 'bt709', 'bt709', 'pc', True, 'gbr', 'full')
    monkeypatch.setattr(color_media, 'read_source_color', lambda *a, **k: color)
    monkeypatch.setattr(color_media, 'source_rgb_filters', lambda *a, **k: [])
    calls = []
    def fake_grade(source, target, *a, **k):
        calls.append(('stage', target)); Path(target).write_bytes(b'stage')
    def fake_render(project, target, *a, **k):
        calls.append(('preview', target)); Path(target).write_bytes(b'preview'); return target
    monkeypatch.setattr(color_media, 'grade_segment', fake_grade)
    engine = RenderEngine()
    monkeypatch.setattr(engine, 'render', fake_render)
    try:
        clip = candidate.clips[0]
        before = (engine._graded_segment(media, clip, candidate, None), engine.preview(candidate))
        clip.grade.qualifier, _ = soften_qualifier_range(original, rgb, target=target, channel=channel)
        after = (engine._graded_segment(media, clip, candidate, None), engine.preview(candidate))
        assert before[0] != after[0] and before[1] != after[1]
        assert len(calls) == 4
        same, _ = soften_qualifier_range(clip.grade.qualifier, rgb, target=target, channel=channel)
        clip.grade.qualifier = same
        assert (engine._graded_segment(media, clip, candidate, None), engine.preview(candidate)) == after
        clip.grade.qualifier = original
        assert (engine._graded_segment(media, clip, candidate, None), engine.preview(candidate)) == before
        assert len(calls) == 4
    finally:
        engine.close()
