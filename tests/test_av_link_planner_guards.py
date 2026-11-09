"""Legacy planners reject every edited linked participant, not unrelated links.

These tests perform no source I/O. Rebound receipts deliberately model a forged
plan so Apply must repeat the relationship qualification rather than trusting a
preview's affected list or waiting for the central commit fence to reject it.
"""
from copy import deepcopy
from dataclasses import replace

import pytest

from lumen.model import (AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT, AVLink,
                         Clip, Editor, Media, Project)
from lumen.source_overwrite import _signature


MODES = (AV_LINK_ALIGNMENT, AV_LINK_AFFINE_ALIGNMENT)
OPERATIONS = ('overwrite', 'insert', 'gap', 'ripple')
LINK_ERROR = 'Unlink A/V clips before independent timing, track or source edits'


def sample(mode, *, linked=True, muted=False):
    # For v2 the audio begins two frames after picture, but both map timeline
    # frame 42 to source frame 82. No speed/intercept approximation is involved.
    audio_start = 40 if mode == AV_LINK_ALIGNMENT else 42
    source = Media('m', '/not-opened.mov', 'Source', 100, 64, 48, True, True, 24, 1)
    clips = [
        Clip('v', 'm', 0, 40, 80, 20, source_audio_enabled=False),
        Clip('a', 'm', 2, audio_start, audio_start + 40, 60 - audio_start, muted=muted),
        Clip('left', 'm', 1, 0, 0, 10),
        Clip('right', 'm', 1, 20, 20, 10),
        Clip('victim', 'm', 3, 0, 10, 10),
    ]
    links = [AVLink('link', 'v', 'a', 'm', mode)] if linked else []
    return Editor(Project(fps_num=24, fps_den=1, media=[source], clips=clips, av_links=links))


def plan(editor, operation, *, affected=True):
    if operation == 'overwrite':
        # Shortening only the audio's right tail still satisfies affine sync;
        # this is exactly the v2 case final Project.validate cannot forbid.
        return editor.plan_source_overwrite('m', 2 if affected else 1,
                                            55 if affected else 0, 0, 5 if affected else 10)
    if operation == 'insert':
        return editor.plan_source_insert('m', 1, 30 if affected else 0, 0, 5,
            participating_tracks=(0, 1, 2) if affected else (1,))
    if operation == 'gap':
        return editor.plan_gap_close(1, 12,
            participating_tracks=(0, 1, 2) if affected else (1,))
    return editor.plan_ripple_delete('victim',
        participating_tracks=(0, 2, 3) if affected else (3,))


def apply(editor, operation, receipt):
    return {'overwrite': editor.apply_source_overwrite,
            'insert': editor.apply_source_insert,
            'gap': editor.apply_gap_close,
            'ripple': editor.apply_ripple_delete}[operation](receipt)


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo,
                     editor.revision, editor.session_id))


def rejected(editor, call, message=LINK_ERROR):
    before = state(editor)
    project = editor.project
    records = tuple(editor.project.clips)
    links = tuple(editor.project.av_links)
    with pytest.raises(ValueError, match=message):
        call()
    assert state(editor) == before
    assert editor.project is project
    assert all(actual is old for actual, old in zip(editor.project.clips, records))
    assert all(actual is old for actual, old in zip(editor.project.av_links, links))


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('muted', (False, True))
def test_preview_rejects_linked_participants_even_complete_affine_preserving_move(mode, operation, muted):
    editor = sample(mode, muted=muted)
    editor.update_clip('left', gain_db=-3)
    assert editor.undo() and editor.can_redo
    rejected(editor, lambda: plan(editor, operation))


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('role', ('v', 'a'))
def test_selected_linked_ripple_deletion_rejects_at_planning_boundary(mode, role):
    editor = sample(mode)
    rejected(editor, lambda: editor.plan_ripple_delete(role))


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('role,track', (('v', 0), ('a', 2)))
def test_overwrite_rejects_either_role_before_other_source_restrictions(mode, role, track):
    editor = sample(mode)
    rejected(editor, lambda: editor.plan_source_overwrite('m', track, 55, 0, 5))
    assert editor._clip(role).end == 60


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('operation', OPERATIONS)
def test_apply_requalifies_linked_participants_despite_rebound_receipt(mode, operation):
    editor = sample(mode, linked=False)
    receipt = plan(editor, operation)
    editor.project.av_links = [AVLink('link', 'v', 'a', 'm', mode)]
    editor.project.validate()
    # Deliberately repair only freshness to exercise the Apply candidate guard.
    # Normal old receipts remain stale, covered separately below.
    forged = replace(receipt, project_signature=_signature(editor.project))
    rejected(editor, lambda: apply(editor, operation, forged))


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('operation', OPERATIONS)
def test_old_receipt_is_stale_when_relationship_added_without_revision_change(mode, operation):
    editor = sample(mode, linked=False)
    receipt = plan(editor, operation)
    editor.project.av_links = [AVLink('link', 'v', 'a', 'm', mode)]
    editor.project.validate()
    rejected(editor, lambda: apply(editor, operation, receipt), 'stale')


@pytest.mark.parametrize('mode', MODES)
@pytest.mark.parametrize('operation', OPERATIONS)
def test_unrelated_link_does_not_block_plan_apply_or_history(mode, operation):
    editor = sample(mode)
    original = deepcopy(editor.project)
    live = {clip.id: clip for clip in editor.project.clips}
    link = editor.project.av_links[0]
    before = state(editor)
    receipt = plan(editor, operation, affected=False)
    assert state(editor) == before
    apply(editor, operation, receipt)
    assert editor._clip('v') is live['v'] and editor._clip('a') is live['a']
    assert editor._clip('v') == original.clips[0] and editor._clip('a') == original.clips[1]
    assert editor.project.av_links == original.av_links
    assert editor.project.av_links[0] is link
    assert editor.revision == 1 and len(editor._undo) == 1
    after = deepcopy(editor.project)
    assert editor.undo() and editor.project == original
    assert editor.redo() and editor.project == after
