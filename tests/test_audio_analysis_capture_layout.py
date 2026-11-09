"""Deterministic queued freshness/status change between capture label checks.

The source checker stays blocked until after the checking-text geometry is
asserted. A shorter current-result state therefore cannot make this pass.
"""
from copy import deepcopy
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from scripts.capture_audio_analysis_ui import synchronize_capture_layout
from lumen.i18n import set_locale
import lumen.ui_audio_analysis as module
from test_audio_analysis_ui import app, window, completed, settle


def checking_between_label_assertions(window, monkeypatch, *, at_bottom=False):
    dialog = completed(window, monkeypatch)
    set_locale('zh-CN')
    font = QFont(dialog.font()); font.setFamily('Noto Sans CJK SC'); font.setPointSize(13)
    dialog.setFont(font); dialog.resize(360, 520)
    QTest.qWait(40)
    synchronize_capture_layout(dialog)
    assert dialog.state == 'current' and dialog.timer.isActive()
    assert dialog.status.height() + 1 >= dialog.status.heightForWidth(dialog.status.width())
    started, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    def checker(*_args):
        assert threading.get_ident() != owner
        started.set()
        assert release.wait(5), 'Test did not release held freshness checker'
        return True
    monkeypatch.setattr(module, 'result_is_current', checker)
    dialog._last_verified = 0
    if at_bottom:
        dialog.scroll.verticalScrollBar().setValue(dialog.scroll.verticalScrollBar().maximum())
    else:
        dialog.scroll.ensureWidgetVisible(dialog.stage)
    QTimer.singleShot(0, dialog.check_current)
    QApplication.processEvents()  # Exact stage→status boundary of the capture.
    assert started.wait(1)
    assert dialog.state == 'checking' and dialog.result is None
    assert '正在检查音频源文件' in dialog.status.text()
    return dialog, release


def test_capture_sync_applies_current_checking_text_layout_without_releasing_worker(window, monkeypatch):
    before = deepcopy(window.project)
    dialog, release = checking_between_label_assertions(window, monkeypatch)
    try:
        text = dialog.status.text()
        # Reproduce the original strict failure BEFORE the new synchronization.
        assert dialog.status.height() + 1 < dialog.status.heightForWidth(dialog.status.width())
        general_events = []
        QTimer.singleShot(0, lambda: general_events.append('must not run during layout-only sync'))
        synchronize_capture_layout(dialog)
        assert dialog.status.text() == text and dialog.state == 'checking'
        assert dialog.status.height() + 1 >= dialog.status.heightForWidth(dialog.status.width())
        assert not release.is_set() and window.audio_analysis_freshness.worker.is_alive()
        assert dialog.timer.isActive() and not general_events
        assert window.project == before and not window.editor.can_undo
    finally:
        release.set(); settle(window)
    assert dialog.state == 'current'


def test_layout_sync_does_not_hide_persistent_clipping_or_wait_for_shorter_text(window, monkeypatch):
    dialog, release = checking_between_label_assertions(window, monkeypatch)
    try:
        text = dialog.status.text()
        dialog.status.setFixedHeight(23)  # Deliberate real layout defect, not a race.
        synchronize_capture_layout(dialog)
        assert dialog.state == 'checking' and dialog.status.text() == text
        assert not release.is_set() and dialog.timer.isActive()
        with pytest.raises(AssertionError):
            assert dialog.status.height() + 1 >= dialog.status.heightForWidth(dialog.status.width())
    finally:
        release.set(); settle(window)


def test_capture_bottom_reanchors_after_checking_text_changes_scroll_range(window, monkeypatch):
    dialog, release = checking_between_label_assertions(window, monkeypatch, at_bottom=True)
    try:
        text = dialog.status.text()
        synchronize_capture_layout(dialog)
        bar = dialog.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        assert dialog.state == 'checking' and dialog.status.text() == text
        assert not release.is_set() and dialog.timer.isActive()
        assert dialog.next_step.visibleRegion().boundingRect() == dialog.next_step.rect()
        assert dialog.status.height() + 1 >= dialog.status.heightForWidth(dialog.status.width())
    finally:
        release.set(); settle(window)
