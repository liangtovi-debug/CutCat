"""Static windows survive normal persistence/history/recovery and grade transfer."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict
import json
from uuid import uuid4

import numpy as np
import pytest

from lumen.grade_transfer import copy_grade, paste_grade
from lumen.model import VERSION, Editor, Project
from lumen.project_recovery import RecoveryStore
from lumen.qualifier import qualifier_mask
from test_grade_transfer import project_pair
from test_qualifier_integration import project
from test_static_window_render import window_settings


def test_window_transaction_save_recovery_undo_redo_remains_independent(tmp_path):
    editor = Editor(project()); bind_missing_project_paths(editor.project, tmp_path)
    before = deepcopy(editor.project)
    pending = window_settings(window_shape='rectangle', window_center_x=.123456789,
                              window_width=.712345678, window_feather=.212345678,
                              window_invert=True)
    grade = asdict(before.clips[0].grade)
    grade['qualifier'] = asdict(pending)
    editor.update_clip('picture', grade=grade)
    after = deepcopy(editor.project)
    assert after.clips[0].grade.qualifier == pending
    assert editor.revision == 1 and len(editor._undo) == 1
    pending.window_width = .9
    grade['qualifier']['window_width'] = .8
    assert editor.project == after
    path = tmp_path / 'window.lumen'
    editor.project.save(path)
    assert json.loads(path.read_text())['version'] == VERSION
    assert Project.load(path) == after
    with RecoveryStore(tmp_path / 'recovery') as store:
        entry = store.write(editor.project, uuid4())
        recovered = store.load(entry)
        assert recovered == after
        recovered.clips[0].grade.qualifier.window_center_y = .9
        assert store.load(entry) == after and editor.project == after
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_grade_copy_keeps_normalized_window_and_target_geometry_and_input(tmp_path, encoding):
    candidate, _ = project_pair(tmp_path)
    candidate.clips[0].grade.qualifier = window_settings(window_center_x=.25, window_width=.3)
    candidate.clips[1].grade.input_encoding = encoding
    before = deepcopy(candidate)
    copied = copy_grade(candidate, 'source')
    editor = Editor(candidate)
    assert paste_grade(editor, 'target', copied)
    target = editor.project.clips[1]
    assert target.grade.qualifier == before.clips[0].grade.qualifier
    assert target.grade.input_encoding == encoding
    assert target.transform == before.clips[1].transform
    assert target.restoration == before.clips[1].restoration
    assert target.stabilization == before.clips[1].stabilization
    assert target.grade.qualifier is not editor.project.clips[0].grade.qualifier
    # The copied window is canvas-normalized, not source media pixels. The
    # matching location at two canvas sizes lies inside; distant same color is out.
    for width, height in ((80, 40), (160, 120)):
        frame = np.empty((height, width, 3), np.float32)
        frame[:] = [30007 / 65535, 12011 / 65535, 10009 / 65535]
        matte = qualifier_mask(frame, target.grade.qualifier)
        assert matte[height // 2, width // 4] == 1
        assert matte[height // 2, 3 * width // 4] == 0
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after
    assert not paste_grade(editor, 'target', copied)
    assert len(editor._undo) == 1
