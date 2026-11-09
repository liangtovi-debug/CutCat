"""Real Qt review controls and trust-boundary integration (offscreen, not native QA)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from copy import deepcopy
from types import SimpleNamespace
import threading

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from lumen.i18n import set_locale
from lumen.local_ai import ModelRegistry, inspect_model_files
from lumen.model import Caption, Clip, Editor, Media, Project
from lumen.ui import MainWindow


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    settings = QSettings(str(tmp_path / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.setValue('mode', 'professional')
    window = MainWindow(workspace_settings=settings, ui_language='en')
    window.model_registry = ModelRegistry(tmp_path / 'registry.json')
    window.editor = Editor(Project(
        media=[Media('source', '/missing/fixture.wav', 'Interview', 10, has_video=False, has_audio=True),
               Media('silent', '/missing/fixture.mp4', 'Silent video', 10, 640, 360, True, False)],
        clips=[Clip('voice-a', 'source', 2, 0, 0, 60), Clip('voice-b', 'source', 2, 80, 0, 60),
               Clip('silent-clip', 'silent', 0, 0, 0, 60)],
        captions=[Caption('caption-a', 0, 30, 'We paid 15 dollars.'),
                  Caption('caption-b', 40, 30, 'Coffee with friends.')]))
    window.refresh()
    window.test_warnings = warnings
    yield window
    window.assistant_job = None
    window.dirty = False
    window.close()
    app.processEvents()


def preview_both(window):
    window.controlled_text.setPlainText('We paid 15 dollars!')
    window.controlled_gain_enabled.setChecked(True)
    window.controlled_gain.setValue(-6)
    window.controlled_preview_button.click()
    assert window.controlled_proposal is not None


def model_callback(window, tmp_path):
    runtime = tmp_path / 'runtime'; runtime.write_bytes(b'runtime')
    model = tmp_path / 'model'; model.write_bytes(b'model')
    window.ai_records['subtitle-assistant'] = inspect_model_files('subtitle-assistant', {'runtime': runtime, 'model': model})
    captured = {}
    window.start_job = lambda label, operation, success, kind: captured.update(operation=operation, success=success)
    window.suggest_subtitle()
    return captured


def local_result(window, text):
    from lumen.subtitle_ai import AssistantProvenance, CaptionSuggestion, PROMPT_VERSION
    record = window.ai_records['subtitle-assistant']
    files = {f.role: f for f in record.files}
    provenance = AssistantProvenance('test-local-runtime', 'runtime', files['runtime'].sha256,
        'model', files['model'].sha256, 2, 4096, 512, PROMPT_VERSION, '2026-10-21T22:00:00+00:00')
    return CaptionSuggestion(window.ai_original.toPlainText(), text, 'proofread', provenance, ())


def test_combined_preview_no_mutation_then_atomic_apply_and_undo(window, monkeypatch):
    before = deepcopy(window.project)
    preview_both(window)
    assert window.project == before and not window.dirty
    assert window.editor.revision == 0
    diff = window.controlled_diff.toPlainText()
    assert 'caption-a' in diff and 'voice-a' in diff and '-6 dB' in diff
    assert 'We paid 15 dollars.' in diff and 'We paid 15 dollars!' in diff
    assert window.controlled_apply_button.isEnabled()
    confirmations = []
    def confirm(*args):
        confirmations.append(args[2])
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    window.controlled_apply_button.click()
    assert len(confirmations) == 1 and diff in confirmations[0]
    assert window.project.captions[0].text == 'We paid 15 dollars!'
    assert window.project.clips[0].gain_db == -6
    assert window.project.clips[1] == before.clips[1]
    assert window.editor.revision == 1 and window.dirty
    window.undo()
    assert window.project == before
    assert not window.editor.undo()


def test_only_audio_targets_offered_and_gain_only_batch(window, monkeypatch):
    assert [window.controlled_clip_choice.itemData(i) for i in range(window.controlled_clip_choice.count())] == ['voice-a', 'voice-b']
    window.controlled_caption_enabled.setChecked(False)
    window.controlled_gain_enabled.setChecked(True)
    window.controlled_clip_choice.setCurrentIndex(1)
    window.controlled_gain.setValue(-3)
    window.preview_controlled_edits()
    assert len(window.controlled_proposal.commands) == 1
    before = deepcopy(window.project.captions)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    window.apply_controlled_edits()
    assert window.project.clips[1].gain_db == -3
    assert window.project.clips[0].gain_db == 0
    assert window.project.captions == before


def test_revision_requires_fresh_preview_even_if_values_return(window, monkeypatch):
    preview_both(window)
    first = window.controlled_proposal
    window.controlled_gain.setValue(-5)
    assert window.controlled_proposal is None
    assert not window.controlled_apply_button.isEnabled()
    window.controlled_gain.setValue(-6)
    assert not window.controlled_apply_button.isEnabled()
    window.preview_controlled_edits()
    assert window.controlled_proposal.digest != first.digest
    assert window.controlled_proposal.proposal_id != first.proposal_id


def test_reject_cancel_and_no_confirmation_preserve_everything(window, monkeypatch):
    before = deepcopy(window.project)
    preview_both(window)
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.No)
    window.apply_controlled_edits()
    assert window.project == before and not window.dirty
    window.controlled_reject_button.click()
    assert window.controlled_proposal is None and not window.controlled_apply_button.isEnabled()
    window.apply_controlled_edits()
    assert window.project == before and window.editor.revision == 0


@pytest.mark.parametrize('change', ['caption', 'clip', 'provider', 'model', 'timeline', 'project-edit', 'new-editor'])
def test_selection_provider_model_and_project_invalidate(window, change):
    preview_both(window)
    if change == 'caption': window.controlled_caption_choice.setCurrentIndex(1)
    elif change == 'clip': window.controlled_clip_choice.setCurrentIndex(1)
    elif change == 'provider':
        window.controlled_provider.addItem('Unavailable provider', 'unavailable')
        window.controlled_provider.setCurrentIndex(1)
    elif change == 'model': window.ai_fields['subtitle-assistant']['model'].setText('changed-model')
    elif change == 'timeline': window.select_clip('voice-b')
    elif change == 'project-edit': window.edit(lambda: window.editor.update_caption('caption-b', text='Other edit.'))
    else:
        window.editor = Editor(deepcopy(window.project)); window.refresh()
    assert window.controlled_proposal is None
    assert not window.controlled_apply_button.isEnabled()
    assert window.project.captions[0].text == 'We paid 15 dollars.'
    assert window.project.clips[0].gain_db == 0


@pytest.mark.parametrize('change', ['revision', 'raw-mutation', 'new-editor', 'revised-preview', 'cancel'])
def test_modal_confirmation_rechecks_exact_preview(window, monkeypatch, change):
    preview_both(window)
    def confirm(*_):
        if change == 'revision': window.editor.update_caption('caption-b', text='User edit.')
        elif change == 'raw-mutation': window.project.captions[1].text = 'Direct edit.'
        elif change == 'new-editor': window.editor = Editor(deepcopy(window.project))
        elif change == 'revised-preview':
            window.controlled_gain.setValue(-4); window.preview_controlled_edits()
        else: window.cancel_controlled_edits()
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, 'question', confirm)
    window.apply_controlled_edits()
    assert window.project.captions[0].text == 'We paid 15 dollars.'
    assert window.project.clips[0].gain_db == 0
    assert window.controlled_proposal is None


def test_noop_does_not_dirty_or_add_history(window, monkeypatch):
    before = deepcopy(window.project)
    window.preview_controlled_edits()
    assert window.controlled_proposal is not None
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    window.apply_controlled_edits()
    assert window.project == before and not window.dirty
    assert window.revision == 0 and window.editor.revision == 0
    assert not window.editor.undo()
    assert 'No change' in window.controlled_status.text()


def test_empty_or_oversized_text_cannot_be_applied(window):
    window.controlled_text.setPlainText(' ')
    assert not window.controlled_preview_button.isEnabled()
    window.controlled_text.setPlainText('x' * 1001)
    window.preview_controlled_edits()
    assert window.controlled_proposal is None
    assert not window.controlled_apply_button.isEnabled()
    assert 'rejected' in window.controlled_status.text()


@pytest.mark.parametrize('text', ['We paid 150 dollars.', 'We paid 15 dollars.\nNew line', '我们支付了15美元。', 'Nothing is as it seems 15.'])
def test_local_result_cannot_bypass_proofreading_guards(window, tmp_path, text):
    captured = model_callback(window, tmp_path)
    captured['success'](local_result(window, text))
    assert window.ai_pending is None and window.ai_review_proposal is None
    assert not window.ai_apply_button.isEnabled()
    assert window.project.captions[0].text == 'We paid 15 dollars.'
    assert 'protected' in window.ai_warning.text()


def test_local_result_apply_uses_gate_and_human_revision_is_distinct(window, tmp_path, monkeypatch):
    captured = model_callback(window, tmp_path)
    captured['success'](local_result(window, 'We paid 15 dollars!'))
    assert window.ai_review_proposal.adapter == 'local-proofreader'
    assert window.ai_apply_button.isEnabled()
    window.ai_proposed.setPlainText('We paid 150 dollars.')
    assert window.ai_review_proposal is None and not window.ai_apply_button.isEnabled()
    assert 'Human revision' in window.ai_warning.text()
    window.ai_revision_button.click()
    assert window.ai_review_proposal.adapter == 'manual-offline'
    assert window.ai_apply_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window.editor, 'update_caption', lambda *_args, **_kwargs: pytest.fail('Old direct mutation path was called'))
    window.ai_apply_button.click()
    assert window.project.captions[0].text == 'We paid 150 dollars.'
    assert window.editor.revision == 1
    window.undo()
    assert window.project.captions[0].text == 'We paid 15 dollars.'


@pytest.mark.parametrize('invalidate', ['cancel', 'new-editor', 'selection', 'provider', 'model', 'new-preview', 'revision'])
def test_late_local_completion_is_discarded(window, tmp_path, invalidate):
    captured = model_callback(window, tmp_path)
    result = local_result(window, 'We paid 15 dollars!')
    if invalidate == 'cancel':
        window.assistant_job = SimpleNamespace(cancel=threading.Event())
        window.cancel_controlled_edits()
        assert window.assistant_job.cancel.is_set()
        window.assistant_job = None
    elif invalidate == 'new-editor': window.editor = Editor(deepcopy(window.project))
    elif invalidate == 'selection': window.ai_caption_choice.setCurrentIndex(1)
    elif invalidate == 'provider':
        window.controlled_provider.addItem('Unavailable provider', 'unavailable')
        window.controlled_provider.setCurrentIndex(1)
    elif invalidate == 'model': window.ai_fields['subtitle-assistant']['model'].setText('other')
    elif invalidate == 'new-preview': preview_both(window)
    else: window.editor.update_caption('caption-b', text='User edit.')
    captured['success'](result)
    assert window.ai_pending is None and window.ai_review_proposal is None
    assert window.project.captions[0].text == 'We paid 15 dollars.'


def test_shared_gate_does_not_keep_other_review_applicable(window, tmp_path):
    captured = model_callback(window, tmp_path)
    captured['success'](local_result(window, 'We paid 15 dollars!'))
    assert window.ai_apply_button.isEnabled()
    preview_both(window)
    assert window.ai_pending is None and not window.ai_apply_button.isEnabled()
    assert window.controlled_apply_button.isEnabled()


def test_locale_changes_preserve_real_user_text_and_preview_identity(window, app):
    preview_both(window)
    proposal = window.controlled_proposal
    before = deepcopy(window.project)
    set_locale('zh-CN'); app.processEvents()
    assert window.controlled_group.title() == '受控编辑 · 离线'
    assert window.controlled_preview_button.text() == '预览准确修改'
    assert '修改前' in window.controlled_diff.toPlainText()
    assert window.controlled_text.toPlainText() == 'We paid 15 dollars!'
    assert window.controlled_proposal is proposal
    assert window.controlled_apply_button.isEnabled()
    set_locale('en'); app.processEvents()
    assert window.controlled_provider.currentData() == 'manual-offline'
    assert 'Before' in window.controlled_diff.toPlainText()
    assert window.controlled_proposal is proposal and window.project == before


def test_gate_error_has_chinese_presentation(window):
    set_locale('zh-CN')
    window.controlled_text.setPlainText('x' * 1001)
    window.preview_controlled_edits()
    assert '超过安全限制' in window.controlled_status.text()
    assert 'Replacement text' not in window.controlled_status.text()


@pytest.mark.parametrize('field', ['runtime_sha256', 'model_sha256', 'prompt_version'])
def test_actual_model_provenance_must_match_request(window, tmp_path, field):
    from dataclasses import replace
    captured = model_callback(window, tmp_path)
    result = local_result(window, 'We paid 15 dollars!')
    result = replace(result, provenance=replace(result.provenance, **{field: 'changed'}))
    captured['success'](result)
    assert window.ai_pending is None and window.ai_review_proposal is None
    assert window.project.captions[0].text == 'We paid 15 dollars.'
    assert 'identity changed' in window.ai_warning.text()


@pytest.mark.parametrize('mode', ['simple', 'professional'])
@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
def test_bilingual_review_fits_narrow_inspector(window, app, mode, locale):
    from PySide6.QtWidgets import QLabel
    from lumen.ui_style import apply_ui_layout_tokens
    window.set_workspace_mode(mode, persist=False)
    window.resize(1366, 768)
    window.show()
    window.main_split.setSizes([200, 806, 320])
    window.side_tabs.setCurrentWidget(window.ai_panel)
    preview_both(window)
    proposal = window.controlled_proposal
    before = deepcopy(window.project)
    set_locale(locale)
    apply_ui_layout_tokens(window)
    for _ in range(12):
        app.processEvents()
    scroll = window.ai_panel
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.widget().width() == scroll.viewport().width()
    assert window.controlled_group.minimumSizeHint().width() <= window.controlled_group.width()
    descriptions = [label.text() for label in window.controlled_group.findChildren(QLabel) if label.wordWrap()]
    assert any(('Experimental offline preview' if locale == 'en' else '实验性离线预览') in text for text in descriptions)
    assert window.controlled_proposal is proposal
    assert window.project == before and not window.dirty
