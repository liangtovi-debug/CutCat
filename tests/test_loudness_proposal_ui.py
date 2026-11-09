"""Transactional measured drafts: worker lifetime and explicit atomic Apply."""
from copy import deepcopy
from types import SimpleNamespace
import threading
import time
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_master_gain_ui import app, window
from lumen.i18n import set_locale
from lumen.ui_audio_analysis import open_audio_analysis
from lumen.ui_loudness_proposal import open_loudness_proposal
import lumen.ui_loudness_proposal as module


def verified():
    return SimpleNamespace(proposal=SimpleNamespace(proposed_master_gain_db=-3., delta_db=-3.,
        target_lufs=-18., ceiling_dbtp=-2., peak_limited=True),
        draft_report=SimpleNamespace(integrated_lufs=-20., sample_peak_dbfs=(-2.5, -3.5), estimated_true_peak_dbtp=(-2., -3.)))


def settle(window):
    until = time.monotonic() + 5
    while window.jobs and time.monotonic() < until:
        QTest.qWait(10)
    QApplication.processEvents()
    assert not window.jobs


def ready(window, monkeypatch):
    value = verified()
    monkeypatch.setattr(module, 'verify_loudness_proposal', lambda *a, **kw: value)
    dialog = open_loudness_proposal(window, object())
    dialog.measure(); settle(window)
    assert dialog.state == 'ready' and dialog.verified is value
    return dialog


def test_entry_current_only_and_no_fixed_timeline_row(window):
    height = window.timeline.height()
    analysis = open_audio_analysis(window)
    assert not analysis.proposal_button.isEnabled()
    assert analysis.open_proposal() is None
    assert window.timeline.height() == height


def test_measure_is_detached_apply_rechecks_in_worker_and_one_undo(window, monkeypatch):
    before = deepcopy(window.project)
    gui_thread = threading.get_ident()
    checks = []
    def check(project, value, cancel=None):
        assert threading.get_ident() != gui_thread
        assert project is not window.project and project == before
        checks.append(value)
    monkeypatch.setattr(module, 'validate_verified_proposal', check)
    dialog = ready(window, monkeypatch)
    assert window.project == before and not window.editor.can_undo
    assert '-20.0 LUFS' in dialog.measurements.text() and 'Peak-constrained' in dialog.status.text()
    window.preview_revision = window.revision
    dialog.apply_settings(); settle(window)
    assert len(checks) == 1 and dialog._closed_flag
    assert window.project.master_gain_db == -3 and len(window.editor._undo) == 1
    assert window.project.clips == before.clips and window.dirty
    assert window.preview_revision != window.revision
    dialog.apply_settings(); assert len(window.editor._undo) == 1
    window.undo(); assert window.project == before
    window.redo(); assert window.project.master_gain_db == -3


@pytest.mark.parametrize('phase', ['measure', 'apply'])
@pytest.mark.parametrize('action', ['close', 'cancel', 'target', 'undo_aba', 'session', 'source', 'new', 'open'])
def test_late_worker_never_publishes_or_commits(window, monkeypatch, phase, action):
    started, release = threading.Event(), threading.Event()
    value = verified()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(4)
        return value if phase == 'measure' else None
    if phase == 'apply':
        dialog = ready(window, monkeypatch)
        monkeypatch.setattr(module, 'validate_verified_proposal', blocked)
        dialog.apply_settings()
    else:
        monkeypatch.setattr(module, 'verify_loudness_proposal', blocked)
        dialog = open_loudness_proposal(window, object()); dialog.measure()
    try:
        assert started.wait(2)
        before_gain = window.project.master_gain_db
        if action == 'close': dialog.reject()
        elif action == 'cancel': window.cancel_jobs()
        elif action == 'target': dialog.target.setValue(-22)
        elif action == 'undo_aba':
            window.editor.set_master_gain(-1); window.undo()
        elif action == 'session': window.editor.session_id = 'new-session'
        elif action == 'source': window.project.media[0].path += '.changed'
        elif action == 'new': window.new_project()
        elif action == 'open': window.open_project()
        dialog.check_current()
        assert not dialog.apply_button.isEnabled()
    finally:
        release.set(); settle(window)
    assert window.project.master_gain_db == before_gain
    assert dialog.verified is None
    assert not window.test_warnings


def test_pending_keyboard_targets_revoke_ready_proposal(window, monkeypatch):
    dialog = ready(window, monkeypatch)
    editor = dialog.target.lineEdit(); editor.setFocus(); editor.selectAll()
    QTest.keyClicks(editor, '-21')
    assert dialog.verified is None and not dialog.apply_button.isEnabled()
    assert not window.editor.can_undo


def test_failure_and_late_failure_do_not_mutate(window, monkeypatch):
    dialog = ready(window, monkeypatch)
    def fail(*args, **kwargs): raise ValueError('source mismatch')
    monkeypatch.setattr(module, 'validate_verified_proposal', fail)
    dialog.apply_settings(); settle(window)
    assert dialog.state == 'failed' and dialog.verified is None
    assert 'source mismatch' in dialog.status.text()
    assert not window.editor.can_undo and window.project.master_gain_db == 0


def test_locale_switch_preserves_values_and_translates_measured_result(window, monkeypatch):
    dialog = ready(window, monkeypatch)
    set_locale('zh-CN'); QApplication.processEvents()
    assert '实测草稿' in dialog.measurements.text()
    assert '应用' in dialog.apply_button.text()
    assert dialog.target.value() == -18 and dialog.ceiling.value() == -2
    assert not window.editor.can_undo
    set_locale('en')


def test_out_of_ceiling_measurement_cannot_apply(window, monkeypatch):
    value = verified(); value.draft_report.estimated_true_peak_dbtp = (-1., -3.)
    monkeypatch.setattr(module, 'verify_loudness_proposal', lambda *a, **kw: value)
    dialog = open_loudness_proposal(window, object()); dialog.measure(); settle(window)
    assert dialog.state == 'failed' and dialog.verified is None
    assert not dialog.apply_button.isEnabled() and not window.editor.can_undo


def test_real_analysis_measure_apply_roundtrip(window, tmp_path):
    import math
    import struct
    import wave
    from lumen.model import Clip, Editor, Media, Project
    path = tmp_path / 'real-stereo.wav'
    with wave.open(str(path), 'wb') as source:
        source.setnchannels(2); source.setsampwidth(2); source.setframerate(48000)
        source.writeframes(b''.join(struct.pack('<hh', int(8000 * math.sin(i * 2 * math.pi * 997 / 48000)),
            int(6000 * math.sin(i * 2 * math.pi * 997 / 48000))) for i in range(96000)))
    window.editor = Editor(Project(media=[Media('real', str(path), 'Measured tone', 2,
        has_video=False, has_audio=True)], clips=[Clip('tone', 'real', 2, 0, 0, 60)]))
    window.refresh()
    before = deepcopy(window.project)
    analysis = open_audio_analysis(window)
    analysis.analyze()
    until = time.monotonic() + 20
    while (window.jobs or analysis.state == 'checking') and time.monotonic() < until:
        QTest.qWait(10)
    assert analysis.state == 'current'
    dialog = analysis.open_proposal()
    assert dialog is not None
    dialog.measure(); settle(window)
    assert dialog.state == 'ready'
    actual = dialog.verified.draft_report
    gain = dialog.verified.proposal.proposed_master_gain_db
    assert actual.integrated_lufs is not None and max(actual.estimated_true_peak_dbtp) <= -2
    assert window.project == before and not window.editor.can_undo
    window.preview_revision = window.revision
    dialog.apply_settings(); settle(window)
    assert window.project.master_gain_db == gain and len(window.editor._undo) == 1
    assert window.project.clips == before.clips
    assert analysis.result is None and analysis.state == 'stale'
    assert window.preview_revision != window.revision
    assert not window.test_warnings
    assert 'Canceled' not in window.progress_label.text()
    window.undo(); assert window.project == before


def test_all_backend_errors_have_chinese_allowlist_and_dynamic_wrapper(window):
    import ast
    from pathlib import Path
    from lumen.i18n import tr_error
    from lumen.i18n_catalog_loudness_proposal_errors import ZH_CN
    from lumen import loudness_proposal
    tree = ast.parse(Path(loudness_proposal.__file__).read_text())
    messages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == '_fail' and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                messages.add(node.args[1].value)
            if node.func.id == '_finite' and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                messages.add(node.args[1].value + ' must be a finite number.')
    assert messages and messages <= ZH_CN.keys()
    set_locale('zh-CN')
    for message in messages:
        assert str(tr_error(message)) == ZH_CN[message]
    set_locale('en')


def test_failure_reason_translates_when_switching_language(window, monkeypatch):
    dialog = ready(window, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError('Audio source contents no longer match the measured report.')
    monkeypatch.setattr(module, 'validate_verified_proposal', fail)
    dialog.apply_settings(); settle(window)
    assert 'Audio source contents' in dialog.status.text()
    set_locale('zh-CN'); QApplication.processEvents()
    assert '音频源文件内容' in dialog.status.text()
    assert 'Audio source contents' not in dialog.status.text()
    set_locale('en')
