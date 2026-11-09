"""Per-operation participants are explicit, immutable-previewed and race-safe."""
from copy import deepcopy
from pathlib import Path
from string import Formatter
import ast
import threading

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lumen.i18n import set_locale, tr_error
from lumen.model import Caption, Clip, Editor, Media, Overlay, Project
from lumen import ui_source_viewer as ui
from test_source_viewer_ui import app, window, opened, pump


def choices(window, app, monkeypatch, *, preview=True):
    window.project.media.append(Media('sound', str((Path(window.project.media[0].path).parent / 'missing-sound.wav').resolve()), 'Sound', 4,
        has_video=False, has_audio=True))
    window.project.clips.extend([
        Clip('fixed-picture', 'other', 1, 5, 0, 80),
        Clip('follow-sound', 'sound', 2, 30, 0, 40),
        Clip('early-sound', 'sound', 2, 0, 0, 10),
        Clip('fixed-sound', 'sound', 3, 25, 0, 40)])
    window.project.captions.extend([Caption('caption', 30, 10, 'Follow'), Caption('early-caption', 2, 4, 'Fixed')])
    window.project.overlays.extend([Overlay('sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 35, 8),
                                    Overlay('early-sticker', str((Path(window.project.media[0].path).parent / 'missing.png').resolve()), 'a' * 64, 2, 4)])
    window.project.validate(); window.refresh(); window.bin.setCurrentRow(0)
    d = opened(window, app)
    monkeypatch.setattr(d.backend, 'verify_insert_sources', d.backend.verify_overwrite_sources, raising=False)
    d.mode_choice.setCurrentIndex(d.mode_choice.findData('insert'))
    d.seek(10); d.mark_in(); d.seek(19); d.mark_out()
    window.timeline.playhead = 20; d.capture_playhead()
    d.insert_track_checks[2].setChecked(True)
    d.follow_captions_check.setChecked(True); d.follow_stickers_check.setChecked(True)
    if preview:
        d.preview_source_edit(); pump(app, lambda: not window.jobs)
        assert d.plan is not None, d.status.text()
    return d


def test_defaults_destination_mandatory_choices_session_only(window, app):
    d = opened(window, app)
    assert d.participation_panel.isHidden()
    assert d._participation() == ((0,), False, False)
    d.mode_choice.setCurrentIndex(d.mode_choice.findData('insert'))
    assert d.participation_panel.isVisible()
    assert d.insert_track_checks[0].isChecked() and not d.insert_track_checks[0].isEnabled()
    d.insert_track_checks[0].setChecked(False)
    assert d.insert_track_checks[0].isChecked() and d._participation()[0] == (0,)
    d.insert_track_checks[2].setChecked(True)
    assert d._participation()[0] == (0, 2)
    d.track_choice.setCurrentIndex(1)
    assert d._participation()[0] == (1, 2)
    assert not d.insert_track_checks[0].isChecked() and d.insert_track_checks[1].isChecked()
    d.follow_captions_check.setChecked(True)
    before = deepcopy(window.project)
    d.reject(); pump(app, lambda: not window.jobs)
    fresh = opened(window, app)
    assert fresh._participation() == ((0,), False, False)
    assert window.project == before


def test_preview_lists_each_fixed_move_and_split_and_single_atomic_apply(window, app, monkeypatch, tmp_path):
    d = choices(window, app, monkeypatch)
    before = deepcopy(window.project); revision = window.editor.revision
    plan = d.plan
    assert plan.participating_tracks == (0, 2) and plan.follow_captions and plan.follow_stickers
    assert 'chosen participants' in d.mode_choice.currentText()
    assert 'not persistent Sync Lock' in d.insert_warning.text()
    impact = d.impact.text()
    for required in ('CHOSEN PARTICIPANTS ONLY', 'Clip track 1', 'Clip track 2', 'Clip track 3',
                     'Clip track 4', 'Captions', 'Stickers', 'Fixed ', 'Move ', 'Split ',
                     '[30, 70) → [40, 80)', '[30, 40) → [40, 50)', '[35, 43) → [45, 53)',
                     '[5, 85) → unchanged', '[25, 65) → unchanged'):
        assert required in impact
    assert all(item.item_id in d.impact.accessibleDescription() for item in plan.item_impacts)
    assert d.impact.textFormat() == Qt.TextFormat.PlainText
    d.apply_source_edit(); d.apply_source_edit(); d.apply_button.click()
    pump(app, lambda: not window.jobs)
    assert window.editor.revision == revision + 1 and d.backend.verify_calls == 1
    assert window.editor._clip('follow-sound').start == 40
    assert window.editor._clip('early-sound').start == 0
    assert window.editor._clip('fixed-picture').start == 5
    assert window.editor._clip('fixed-sound').start == 25
    assert [x.start for x in window.project.captions] == [40, 2]
    assert [x.start for x in window.project.overlays] == [45, 2]
    after = deepcopy(window.project)
    path = tmp_path / 'participants.lumen'; window.project.save(path)
    assert Project.load(path) == after
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    d.apply_source_edit(); assert window.project == after and not window.jobs


@pytest.mark.parametrize('control', ['track', 'captions', 'stickers', 'destination'])
@pytest.mark.parametrize('stage', ['preview', 'apply'])
def test_choice_changes_invalidate_pending_results_and_never_commit(window, app, monkeypatch, control, stage):
    d = choices(window, app, monkeypatch, preview=stage == 'apply')
    gate = threading.Event(); d.backend.started.clear()
    if stage == 'apply': d.backend.verify_gate = gate; d.apply_source_edit()
    else: d.backend.gate = gate; d.preview_source_edit()
    pump(app, d.backend.started.is_set)
    token, job = d._token(), d.job
    before = deepcopy(window.project)
    if stage == 'apply':
        assert not d.follow_captions_check.isEnabled() and not d.follow_stickers_check.isEnabled()
        assert not d.insert_track_checks[2].isEnabled()
    try:
        if control == 'track': d.insert_track_checks[2].setChecked(False)
        elif control == 'captions': d.follow_captions_check.setChecked(False)
        elif control == 'stickers': d.follow_stickers_check.setChecked(False)
        else: d.track_choice.setCurrentIndex(1)
        assert job.cancel.is_set() and not d._current(token)
        assert d.plan is None and not d.apply_button.isEnabled()
    finally:
        gate.set(); pump(app, lambda: not window.jobs)
    assert window.project == before and d.plan is None


@pytest.mark.parametrize('change', ['cancel', 'close', 'selection', 'follower-lock', 'undo', 'relink', 'new-editor', 'new-project', 'open-project'])
def test_participating_apply_context_races_are_atomic(window, app, monkeypatch, change):
    d = choices(window, app, monkeypatch)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    gate = threading.Event(); d.backend.verify_gate = gate; d.backend.started.clear()
    d.apply_source_edit(); pump(app, d.backend.started.is_set)
    job = d.job
    try:
        if change == 'cancel': d.cancel_work()
        elif change == 'close': d.reject()
        elif change == 'selection': window.bin.setCurrentRow(1)
        elif change == 'follower-lock': window.editor.set_track_edit_lock(2, True)
        elif change == 'undo': window.undo()
        elif change == 'relink':
            monkeypatch.setattr(window.project, 'missing_media', lambda: [])
            monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
            window.relink_dialog()
        elif change == 'new-editor': window.editor = Editor(deepcopy(window.project))
        elif change == 'new-project':
            monkeypatch.setattr(window, 'confirm_discard', lambda: True)
            monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: ('', False))
            window.new_project()
        else:
            monkeypatch.setattr(window, 'confirm_discard', lambda: True)
            monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('', ''))
            window.open_project()
        d.check_current(); expected = deepcopy(window.project)
        assert job.cancel.is_set()
    finally:
        gate.set(); pump(app, lambda: not window.jobs)
    assert window.project == expected and d.plan is None


def test_latest_participation_preview_wins_without_parallel_worker(window, app, monkeypatch):
    d = choices(window, app, monkeypatch, preview=False)
    gate = threading.Event(); d.backend.gate = gate; d.backend.started.clear()
    d.preview_source_edit(); pump(app, d.backend.started.is_set)
    d.follow_stickers_check.setChecked(False); d.preview_source_edit()
    d.follow_captions_check.setChecked(False); d.insert_track_checks[2].setChecked(False)
    d.preview_source_edit(); gate.set(); pump(app, lambda: not window.jobs)
    assert d.backend.max_active == 1
    assert d.plan is not None and not d.plan.cross_track
    assert d.plan.participating_tracks == (0,)
    assert 'THIS TRACK ONLY' in d.impact.text()
    assert 'Fixed caption:' in d.impact.text() and 'Fixed sticker:' in d.impact.text()


@pytest.mark.parametrize('category', ['clip', 'caption', 'sticker'])
def test_straddler_blockers_are_shown_localized_and_recover_by_explicit_exclusion(window, app, monkeypatch, category):
    d = choices(window, app, monkeypatch, preview=False)
    if category == 'clip': item = window.editor._clip('follow-sound')
    elif category == 'caption': item = window.project.captions[0]
    else: item = window.project.overlays[0]
    item.start = 15; item.duration = 20
    item.id += ' <b>keep-id</b>'
    before = deepcopy(window.project)
    d.preview_source_edit()
    assert not window.jobs and d.plan is None and not d.apply_button.isEnabled()
    assert 'Insert blocked:' in d.impact.text() and 'spans the insertion point' in d.impact.text()
    assert item.id in d.impact.text() and window.project == before
    set_locale('zh-CN'); app.processEvents()
    assert '插入被阻止' in d.impact.text() and '跨越插入点' in d.impact.text()
    assert item.id in d.impact.text() and 'spans the insertion point' not in d.impact.text()
    if category == 'clip': d.insert_track_checks[2].setChecked(False)
    elif category == 'caption': d.follow_captions_check.setChecked(False)
    else: d.follow_stickers_check.setChecked(False)
    d.preview_source_edit(); pump(app, lambda: not window.jobs)
    assert d.plan is not None and window.project == before
    assert '不变' in d.impact.text()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('mode', ['simple', 'professional'])
@pytest.mark.parametrize('font_size', [10, 15])
def test_choice_accessibility_keyboard_localization_and_narrow_layout(window, app, monkeypatch, locale, mode, font_size):
    window.set_workspace_mode(mode)
    d = choices(window, app, monkeypatch)
    plan, token = d.plan, d._token()
    set_locale(locale)
    font = QFont(d.font()); font.setPointSize(font_size)
    window.setFont(font); d.setFont(font)
    d.fit_available_screen(QRect(0, 0, 360, 420))
    for _ in range(5): app.processEvents()
    assert d.plan is plan and d._current(token)
    assert d.scroll.horizontalScrollBar().maximum() == 0
    for control in (*d.insert_track_checks, d.follow_captions_check, d.follow_stickers_check,
                    d.apply_button, d.close_button):
        assert control.font().pointSize() == font_size
        assert control.accessibleName()
        d.scroll.ensureWidgetVisible(control); app.processEvents()
        assert d.scroll.viewport().rect().contains(control.mapTo(d.scroll.viewport(), control.rect().center()))
    if locale == 'zh-CN':
        assert '参与项' in d.mode_choice.currentText() and '同步锁' in d.participation_note.text()
        assert d.follow_captions_check.text() == '顺移字幕'
        assert '片段轨道 3' in d.impact.text() and '固定' in d.impact.text()
        assert '整数采样位移' in d.proof_note.text()
    else:
        assert 'chosen participants' in d.mode_choice.currentText()
    d.scroll.ensureWidgetVisible(d.follow_captions_check)
    d.activateWindow(); d.follow_captions_check.setFocus(); app.processEvents()
    QTest.keyClick(d.follow_captions_check, Qt.Key.Key_Space); app.processEvents()
    assert not d.follow_captions_check.isChecked() and d.plan is None
    assert not d._current(token) and not d.apply_button.isEnabled()
    QTest.keyClick(d.follow_captions_check, Qt.Key.Key_Return); app.processEvents()
    assert not window.jobs


def test_participation_catalog_placeholders_help_and_all_literal_model_errors():
    from lumen.i18n_catalog_source_overwrite import ZH_CN, ERROR_ZH_CN
    from lumen.ui_help import help_text
    fields = lambda value: {field for _, field, _, _ in Formatter().parse(value) if field}
    for source, translation in ZH_CN.items(): assert fields(source) == fields(translation)
    tree = ast.parse((Path(ui.__file__).parent / 'source_insert.py').read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and node.exc.args and isinstance(node.exc.args[0], ast.Constant)):
            message = node.exc.args[0].value
            assert message in ERROR_ZH_CN
            set_locale('zh-CN'); assert tr_error(message) != message
    set_locale('en'); text = help_text()
    for word in ('per-operation choices', 'one common integer sample displacement', 'combined 30 seconds',
                 '120 seconds', '30 minutes', '48 MiB', 'not persistent Sync Lock'):
        assert word in text
    set_locale('zh-CN'); assert '同一个整数采样位移' in help_text()


@pytest.mark.parametrize('tracks,captions,stickers', [(t, c, s) for t in (False, True)
    for c in (False, True) for s in (False, True)])
def test_each_participation_subset_applies_only_its_explicit_collections(window, app, monkeypatch, tracks, captions, stickers):
    d = choices(window, app, monkeypatch, preview=False)
    d.insert_track_checks[2].setChecked(tracks)
    d.follow_captions_check.setChecked(captions); d.follow_stickers_check.setChecked(stickers)
    before = deepcopy(window.project); revision = window.editor.revision
    d.preview_source_edit(); pump(app, lambda: not window.jobs)
    assert d.plan is not None
    assert d.plan.participating_tracks == ((0, 2) if tracks else (0,))
    assert (d.plan.follow_captions, d.plan.follow_stickers) == (captions, stickers)
    d.apply_source_edit(); pump(app, lambda: not window.jobs)
    assert window.editor.revision == revision + 1
    assert window.editor._clip('follow-sound').start == (40 if tracks else 30)
    assert window.project.captions[0].start == (40 if captions else 30)
    assert window.project.overlays[0].start == (45 if stickers else 35)
    assert window.editor._clip('fixed-picture') == next(c for c in before.clips if c.id == 'fixed-picture')
    assert window.editor._clip('fixed-sound') == next(c for c in before.clips if c.id == 'fixed-sound')
    window.undo(); assert window.project == before


def test_fractional_common_shift_blocker_is_visible_without_verification_or_mutation(window, app):
    project = window.project
    project.fps_num, project.fps_den = 30000, 1001
    project.clips[0].duration = 100
    for media in project.media: media.fps_num, media.fps_den = 30000, 1001
    project.media.append(Media('sound', '/missing/sound.wav', 'Sound', 4,
        has_video=False, has_audio=True))
    project.clips.append(Clip('phase-two', 'sound', 2, 2, 0, 5))
    window.refresh(); window.bin.setCurrentRow(0)
    d = opened(window, app)
    d.mode_choice.setCurrentIndex(d.mode_choice.findData('insert'))
    d.seek(10); d.mark_in(); d.mark_out()
    window.timeline.playhead = 1; d.capture_playhead()
    d.insert_track_checks[2].setChecked(True)
    before = deepcopy(project)
    d.preview_source_edit()
    assert d.plan is None and not window.jobs and window.project == before
    assert 'relative audio synchronization' in d.impact.text()
    set_locale('zh-CN'); app.processEvents()
    assert '相对同步' in d.impact.text() and '采样网格相位' in d.impact.text()
