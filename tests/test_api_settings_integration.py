"""The visible API-settings entry point is memory-only and never enables a provider."""
from copy import deepcopy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from lumen.api_settings import ApiSettingsSession, validated_session
from lumen.model import Caption, Editor, Project
from lumen.ui import MainWindow


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    w.editor = Editor(Project(captions=[Caption('c', 0, 10, 'Before')]))
    w.refresh()
    w.controlled_text.setPlainText('After')
    w.preview_controlled_edits()
    yield w
    w.dirty = False; w.close(); app.processEvents()


def test_api_entry_cancel_preserves_preview_and_project(window, monkeypatch):
    from lumen import ui_api_settings
    before = deepcopy(window.project); proposal = window.controlled_proposal
    original = window.api_settings_session
    seen = []
    def dialog(parent, session):
        seen.append((parent, session)); return session
    monkeypatch.setattr(ui_api_settings, 'open_api_settings', dialog)
    window.api_settings_button.click()
    assert seen == [(window, original)]
    assert window.api_settings_session is original and window.controlled_proposal is proposal
    assert window.project == before and not window.dirty and not window.editor.can_undo


def test_api_entry_changed_session_invalidates_preview_without_enabling_network(window, monkeypatch, tmp_path):
    from lumen import ui_api_settings
    before = deepcopy(window.project)
    candidate = validated_session('Custom provider', 'https://example.invalid/v1', 'test-model', 'synthetic-memory-only-key')
    monkeypatch.setattr(ui_api_settings, 'open_api_settings', lambda parent, session: candidate)
    window.api_settings_button.click()
    assert window.api_settings_session is candidate
    assert window.controlled_proposal is None and window.ai_review_proposal is None
    assert not window.controlled_apply_button.isEnabled()
    assert window.controlled_provider.currentData() == 'manual-offline'
    assert window.project == before and not window.dirty and not window.editor.can_undo
    output = tmp_path/'settings-not-in-project.lumen'; window.project.save(output)
    assert 'synthetic-memory-only-key' not in output.read_text()
    assert 'example.invalid' not in output.read_text() and 'test-model' not in output.read_text()
    assert 'synthetic-memory-only-key' not in repr(candidate)
