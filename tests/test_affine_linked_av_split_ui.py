"""Affine split UI and real Qt focus/IME dispatch; source decoding is mocked.

Expected intervals are derived from captured pre-edit scalar records. Media
qualification is the existing explicitly mocked worker fixture, not a codec test.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import sys
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale
from lumen.model import AV_LINK_AFFINE_ALIGNMENT
from lumen.timing import ClipTimeMap
from test_linked_av_ui import app, window, pair_window
from test_linked_av_split_ui import (split_window, verified_worker, open_dialog,
    preview, assert_two_off_gui_verifications)
from test_clip_copy_paste_ui import state, settle
from test_shortcuts_dispatch import activate, CTRL


def test_dialog_ime_and_keys_keep_native_window_method(split_window, app, monkeypatch):
    """Real focused dialog events previously called the shadowed window()."""
    w = split_window; d = open_dialog(w, app); before = state(w)
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *error: errors.append(error))
    activate(app, d, d.preview_button)
    QApplication.sendEvent(d, QInputMethodEvent('组字', []))
    QTest.keyClick(d, Qt.Key.Key_K, CTRL)
    QApplication.sendEvent(d, QInputMethodEvent('', []))
    app.processEvents()
    assert not errors, [str(error[1]) for error in errors]
    assert callable(d.window) and d.window() is d
    assert d.local_owner_window is w
    assert QApplication.focusWidget() is d.preview_button
    assert state(w) == before and not w.jobs and w.linked_av_split_dialog is d
    QTest.keyClick(d, Qt.Key.Key_Escape); app.processEvents()
    assert d._closed_flag and w.linked_av_split_dialog is None


@pytest.fixture
def affine_split_window(split_window, app, monkeypatch):
    w = split_window
    monkeypatch.setattr(w.timeline.waveforms, 'request', lambda *_: None)
    v, a = w.editor._clip('video-id'), w.editor._clip('audio-id')
    v.time_map = ClipTimeMap(12, Fraction(167, 4))
    a.start, a.source_in, a.duration = 3, Fraction(10), 34
    a.time_map = ClipTimeMap(10, Fraction(87, 2))
    w.project.av_links[0] = replace(w.project.av_links[0], alignment=AV_LINK_AFFINE_ALIGNMENT)
    w.project.validate(); w.refresh(); w.select_clip(v.id); w.seek_frame(20)
    activate(app, w, w.timeline)
    return w


@pytest.mark.parametrize('role', ['video-id', 'audio-id'])
@pytest.mark.parametrize('entry', ['button', 'menu', 'shortcut', 'direct'])
@pytest.mark.parametrize('geometry', ['leading', 'trailing'])
def test_affine_entry_preview_apply_own_edges_one_history(
        affine_split_window, app, verified_worker, role, entry, geometry):
    w = affine_split_window
    if geometry == 'trailing':
        audio = w.editor._clip('audio-id')
        audio.start, audio.source_in, audio.duration = 7, Fraction(14), 32
        audio.time_map = ClipTimeMap(14, Fraction(91, 2))
        w.project.validate(); w.refresh()
    w.select_clip(role); activate(app, w, w.timeline)
    before = deepcopy(w.project); untouched = deepcopy(w.editor._clip('other-id'))
    history = len(w.editor._undo)
    assert w.can_split_selected() and w.split_clip_button.isEnabled()
    assert w.shortcuts.actions['edit.split'].isEnabled()
    if entry == 'button': w.split_clip_button.click()
    elif entry == 'menu': w.shortcuts.actions['edit.split'].trigger()
    elif entry == 'shortcut': QTest.keyClick(w.timeline, Qt.Key.Key_K, CTRL)
    else: w.split_selected()
    app.processEvents(); d = w.linked_av_split_dialog
    assert d is not None and d.clip_id == role
    plan = d.plan
    preview(w, app, d)
    assert w.project == before and len(w.editor._undo) == history
    d.apply(); d.apply(); settle(w, app)
    assert d._closed_flag and w.linked_av_split_dialog is None
    assert len(w.editor._undo) == history + 1
    assert set(w.selected_clip_ids) == set(plan.new_ids[:2])
    assert w.selection == plan.new_ids[0 if role == 'video-id' else 1]
    for old, right_id in zip(before.clips[:2], plan.new_ids[:2]):
        left, right = w.editor._clip(old.id), w.editor._clip(right_id)
        assert (left.start, left.end, right.start, right.end) == (old.start, 20, 20, old.end)
        assert (left.source_in, left.source_out, right.source_in, right.source_out) == (
            old.source_in, old.source_in+20-old.start, old.source_in+20-old.start, old.source_out)
        assert right.duration-(right.source_out-right.source_in) == old.duration-(old.source_out-old.source_in)
        assert left.audio_mix_anchor == right.audio_mix_anchor == old.audio_mix_anchor
        assert left.source_audio_enabled == right.source_audio_enabled == old.source_audio_enabled
    assert all(link.alignment == AV_LINK_AFFINE_ALIGNMENT for link in w.project.av_links)
    assert w.editor._clip('other-id') == untouched
    assert_two_off_gui_verifications(verified_worker, role, 20, threading.get_ident())
    after = deepcopy(w.project); w.undo(); assert w.project == before
    w.redo(); assert w.project == after and not w.test_warnings


@pytest.mark.parametrize('frame', [3, 4, 5, 35, 36, 37])
def test_affine_cut_outside_either_role_rejected_before_worker(affine_split_window, app, frame):
    w = affine_split_window; w.seek_frame(frame); before = state(w)
    assert not w.can_split_selected() and not w.split_clip_button.isEnabled()
    assert not w.shortcuts.actions['edit.split'].isEnabled()
    w.split_selected(); w.shortcuts.actions['edit.split'].trigger()
    assert w.linked_av_split_dialog is None and not w.jobs and state(w) == before


@pytest.mark.parametrize('change', ['seek_aba', 'role_aba', 'revision_aba', 'source_tail'])
def test_affine_captured_intent_rejects_stale_or_aba(
        affine_split_window, app, verified_worker, change):
    w = affine_split_window; d = open_dialog(w, app); plan = d.plan
    preview(w, app, d)
    if change == 'seek_aba': w.seek_frame(21); w.seek_frame(20)
    elif change == 'role_aba': w.select_linked_role('audio'); w.select_linked_role('video')
    elif change == 'revision_aba': w.editor.update_clip('audio-id', gain_db=-4); w.editor.undo()
    else:
        audio = w.editor._clip('audio-id')
        audio.time_map = ClipTimeMap(audio.source_in, Fraction(175, 4))
    before = state(w); d.check_current(); d.apply(); d.preview()
    assert d._stale and d.plan is plan and not d.apply_button.isEnabled()
    assert state(w) == before and not w.jobs


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_affine_summary_retains_captured_own_intervals_after_expiry(
        affine_split_window, app, locale):
    w = affine_split_window; set_locale(locale); d = open_dialog(w, app)
    summary = d.source.text()
    assert '[5, 35)' in summary and '[3, 37)' in summary
    assert '[12, 167/4)' in summary and '[10, 87/2)' in summary
    assert 'J/L' in d.scope.text()
    assert ('partial tails' if locale == 'en' else '不足一帧的尾部') in d.limits.text()
    assert ('both members' if locale == 'en' else '双方') in d.limits.text()
    w.seek_frame(21); d.check_current()
    assert d._stale and d.source.text() == summary
