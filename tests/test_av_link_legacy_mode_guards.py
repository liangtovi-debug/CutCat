"""Joint roll stays strict-v1; slip, paired trim and dedicated split support v2.

Retained node IDs explicitly exercise each newly supported capability while
preserving the independent generic/roll fences and worker/Apply requirements.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import pytest

from lumen.linked_av import (LinkedAVSlipPlan, apply_linked_av_slip,
    linked_av_slip_candidate, plan_linked_av_slip, verify_linked_av_slip)
from lumen.linked_av_roll import linked_av_roll_candidate, resolve_linked_av_roll
from lumen.linked_av_split import (LinkedAVSplitPlan, apply_linked_av_split,
    linked_av_split_candidate, plan_linked_av_split, qualify_linked_av_split,
    verify_linked_av_split)
from lumen.linked_av_trim import linked_av_trim_candidate
from lumen.model import (AVLink, AV_LINK_AFFINE_ALIGNMENT, AV_LINK_ALIGNMENT,
    Clip, Editor, Media, Project)

IDS = ('lv', 'la', 'rv', 'ra')
MODE_ERROR = 'same-source-zero-offset-v1'


def fixture(*, skew=False, affine=('left', 'right')):
    source = Media('m', '/must-not-open.mov', 'Source', 100, 64, 48,
                   True, True, 24, 1)
    project = Project(fps_num=24, media=[source], clips=[
        Clip('lv', 'm', 0, 5, 40, 20, source_audio_enabled=False),
        Clip('la', 'm', 2, 5, 40, 20),
        Clip('rv', 'm', 0, 25, 80, 20, source_audio_enabled=False),
        Clip('ra', 'm', 2, 25, 80, 20),
        Clip('other', 'm', 1, 55, 0, 10)], av_links=[
        AVLink('left', 'lv', 'la', 'm'), AVLink('right', 'rv', 'ra', 'm')])
    project.av_links = [replace(link, alignment=AV_LINK_AFFINE_ALIGNMENT)
        if link.id in affine else link for link in project.av_links]
    if skew:
        # Independently valid affine relation: picture [5,25), audio [7,25),
        # both source-time intercepts are 35; right pair remains aligned.
        project.clips[1].start, project.clips[1].source_in, project.clips[1].duration = 7, 42, 18
    editor = Editor(project)
    editor.update_clip('other', gain_db=-3)
    editor.undo()  # A refusal must not discard redo or create an Undo item.
    return editor


def raw_state(editor):
    # Dataclass equality considers -0.0 == +0.0 and int == float, so additionally
    # compare raw JSON spellings to detect validation's scalar canonicalization.
    return (json.dumps(asdict(editor.project), sort_keys=True, default=str),
            deepcopy((editor._undo, editor._redo, editor.revision, editor.session_id)))


def assert_rejected(editor, action):
    editor.project.master_gain_db = -0.0
    before = raw_state(editor)
    project, clips, links = editor.project, tuple(editor.project.clips), tuple(editor.project.av_links)
    with pytest.raises(ValueError, match=MODE_ERROR):
        action()
    assert raw_state(editor) == before
    assert editor.project is project and editor.can_redo
    assert all(a is b for a, b in zip(editor.project.clips, clips))
    assert all(a is b for a, b in zip(editor.project.av_links, links))


@pytest.mark.parametrize('skew', [False, True], ids=['aligned-v2', 'unequal-edges-v2'])
@pytest.mark.parametrize('role', ['lv', 'la'])
@pytest.mark.parametrize('operation', [
    'slip-candidate', 'slip-plan', 'slip-editor', 'slip-noop',
    'trim-candidate', 'trim-preview', 'trim-editor', 'trim-noop',
    'split-candidate', 'split-qualify', 'split-plan'])
def test_pair_candidate_plan_and_editor_refuse_v2_before_noop(skew, role, operation):
    editor = fixture(skew=skew)
    actions = {
        'slip-candidate': lambda: linked_av_slip_candidate(editor.project, role, 50),
        'slip-plan': lambda: plan_linked_av_slip(editor, role, 50),
        'slip-editor': lambda: editor.slip_linked_av(role, 50),
        'slip-noop': lambda: editor.slip_linked_av(role, editor._clip(role).source_in),
        'trim-candidate': lambda: linked_av_trim_candidate(editor.project, role, 'left', 1),
        'trim-preview': lambda: editor.preview_trim_linked_av(role, 'left', 1),
        'trim-editor': lambda: editor.trim_linked_av(role, 'left', 1),
        'trim-noop': lambda: editor.trim_linked_av(role, 'right', 0),
        'split-candidate': lambda: linked_av_split_candidate(editor.project, role, 15,
                                                             ('new-v', 'new-a', 'new-link')),
        'split-qualify': lambda: qualify_linked_av_split(editor.project, role, 15),
        'split-plan': lambda: plan_linked_av_split(editor, role, 15),
    }
    if operation.startswith('slip'):
        before = raw_state(editor)
        old = deepcopy(editor.project)
        requested = editor._clip(role).source_in if operation == 'slip-noop' else 50
        delta = requested-editor._clip(role).source_in
        result = actions[operation]()
        expected = deepcopy(old)
        for clip in expected.clips:
            if clip.id in ('lv', 'la'):
                clip.source_in += delta
        if operation == 'slip-candidate':
            assert result == expected and raw_state(editor) == before
        elif operation == 'slip-plan':
            assert result.clip_id == role and result.source_in == '50' and raw_state(editor) == before
        else:
            assert editor.project == expected
            assert editor.revision == before[1][2]+bool(delta)
            if not delta:
                assert raw_state(editor) == before and editor.can_redo
        assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT
    elif operation.startswith('trim'):
        before = raw_state(editor)
        expected = deepcopy(editor.project)
        delta = 0 if operation == 'trim-noop' else 1
        for clip in expected.clips:
            if clip.id in ('lv', 'la'):
                clip.start += delta
                clip.source_in += delta
                clip.duration -= delta
        result = actions[operation]()
        if operation == 'trim-candidate':
            assert result == expected and raw_state(editor) == before
        elif operation == 'trim-preview':
            assert tuple(result) == tuple(clip for clip in expected.clips if clip.id in ('lv', 'la'))
            assert raw_state(editor) == before
        else:
            assert editor.project == expected
            assert editor.revision == before[1][2] + bool(delta)
            if not delta:
                assert raw_state(editor) == before and editor.can_redo
        assert editor.project.av_links[0].alignment == AV_LINK_AFFINE_ALIGNMENT
    else:
        before = raw_state(editor)
        result = actions[operation]()
        assert raw_state(editor) == before and editor.can_redo
        if operation == 'split-candidate':
            assert len(result.clips) == len(editor.project.clips)+2
            assert result.av_links[-1].alignment == AV_LINK_AFFINE_ALIGNMENT
            assert result.clips[1].start == result.clips[3].start == 15
        elif operation == 'split-plan':
            assert result.clip_id == role and result.frame == 15
        else:
            assert result is None


@pytest.mark.parametrize('affine', [('left',), ('right',), ('left', 'right')],
                         ids=['left-v2', 'right-v2', 'both-v2'])
@pytest.mark.parametrize('operation', ['resolve', 'candidate', 'preview', 'editor', 'noop'])
def test_joint_roll_checks_every_pair_mode_even_when_edges_align(affine, operation):
    editor = fixture(affine=affine)
    actions = {
        'resolve': lambda: resolve_linked_av_roll(editor.project, IDS),
        'candidate': lambda: linked_av_roll_candidate(editor.project, IDS, 26),
        'preview': lambda: editor.preview_roll_linked_av(IDS, 26),
        'editor': lambda: editor.roll_linked_av(IDS, 26),
        'noop': lambda: editor.roll_linked_av(IDS, 25),
    }
    assert_rejected(editor, actions[operation])


def synthetic_plan(editor, command):
    # Explicitly inject an otherwise valid affine snapshot to exercise the
    # worker/Apply boundary independently of the planner's earlier refusal.
    from lumen.clip_copy_paste import prepare_clip_copy
    from lumen.source_overwrite import _signature
    request = prepare_clip_copy(editor, ['lv'])
    common = (editor.session_id, editor.revision, _signature(editor.project))
    if command == 'slip':
        return LinkedAVSlipPlan(*common, 'lv', '50', request, 'not-reached', 'test-token')
    return LinkedAVSplitPlan(*common, 'lv', 15, ('new-v', 'new-a', 'new-link'),
                            request, 'not-reached', 'test-token')


@pytest.mark.parametrize('skew', [False, True], ids=['aligned-v2', 'unequal-edges-v2'])
@pytest.mark.parametrize('command', ['slip', 'split'])
@pytest.mark.parametrize('phase', ['worker', 'apply'])
def test_worker_and_apply_refuse_injected_v2_before_source_observation(monkeypatch, skew, command, phase):
    import lumen.clip_copy_paste as copy_module
    import lumen.source_viewer as viewer
    editor = fixture(skew=skew)
    plan = synthetic_plan(editor, command)
    def no_io(*args, **kwargs):
        raise AssertionError('Mode refusal must precede worker/source access')
    monkeypatch.setattr(copy_module, '_dependencies', no_io)
    monkeypatch.setattr(viewer, 'SourcePreviewBackend', no_io)
    if command == 'slip':
        # Affine slip reaches its existing worker check but never the split
        # source-preview backend; Apply still needs a fresh matching receipt.
        plan = plan_linked_av_slip(editor, 'lv', 50)
        before = raw_state(editor)
        observed = []
        monkeypatch.setattr(copy_module, '_dependencies', lambda *args: observed.append(args) or ())
        if phase == 'worker':
            receipt = verify_linked_av_slip(plan)
            assert receipt.phase == 'preview' and len(observed) == 1
        else:
            with pytest.raises(ValueError, match='fresh verified'):
                apply_linked_av_slip(editor, plan, None)
            assert not observed
        assert raw_state(editor) == before
    else:
        # Affine is now eligible. Worker must reach fresh source observation;
        # Apply must still reject a missing receipt before any source I/O.
        plan = plan_linked_av_split(editor, 'lv', 15)
        before = raw_state(editor)
        if phase == 'worker':
            with pytest.raises(AssertionError, match='worker/source access'):
                verify_linked_av_split(plan)
        else:
            with pytest.raises(ValueError, match='fresh verified'):
                apply_linked_av_split(editor, plan, None)
        assert raw_state(editor) == before


def test_direct_split_source_backend_rejects_v2_before_its_begin_hook():
    from lumen.source_viewer import SourcePreviewBackend
    editor = fixture()
    def no_begin(*args, **kwargs):
        raise AssertionError('Mode refusal must precede backend acquisition')
    backend = SimpleNamespace(_begin=no_begin)
    before = raw_state(editor)
    with pytest.raises(AssertionError, match='backend acquisition'):
        SourcePreviewBackend.verify_linked_av_split_source(backend, editor.project, 'lv', 15)
    assert raw_state(editor) == before


@pytest.mark.parametrize('alignment, enabled', [(AV_LINK_ALIGNMENT, True),
                                               (AV_LINK_AFFINE_ALIGNMENT, True)])
def test_ui_slip_capability_is_mode_specific_without_changing_unlink(alignment, enabled):
    from lumen.ui_linked_av import LinkedAVMixin
    from PySide6.QtMultimedia import QMediaPlayer
    editor = fixture(affine=() if alignment == AV_LINK_ALIGNMENT else ('left',))
    window = SimpleNamespace(project=editor.project, selection='lv',
        linked_av_selection=lambda: (('lv', 'la'), None),
        isEnabled=lambda: True, centralWidget=lambda: SimpleNamespace(isEnabled=lambda: True),
        player=SimpleNamespace(playbackState=lambda: QMediaPlayer.PlaybackState.StoppedState))
    assert editor.project.av_links[0].alignment == alignment
    assert LinkedAVMixin.can_slip_linked_av(window) is enabled
    assert LinkedAVMixin.can_unlink_selected_av(window) is True
