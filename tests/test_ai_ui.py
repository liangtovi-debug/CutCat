from copy import deepcopy
from types import SimpleNamespace
from dataclasses import dataclass
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from lumen.ui import MainWindow
from lumen.model import Project,Editor,Caption
from lumen.local_ai import ModelRegistry,inspect_model_files

@pytest.fixture(scope='module')
def app():return QApplication.instance() or QApplication([])
@pytest.fixture
def window(app,tmp_path,monkeypatch):
    monkeypatch.setattr(QMessageBox,'warning',lambda *_:QMessageBox.StandardButton.Ok)
    w=MainWindow();w.model_registry=ModelRegistry(tmp_path/'models.json')
    w.editor=Editor(Project(captions=[Caption('one',10,20,'original text'),Caption('two',40,20,'coffee shop')]))
    w.refresh();yield w
    w.dirty=False;w.close();app.processEvents()

def suggestion(w,text='Corrected text'):
    c=deepcopy(w.project.captions[0]);w.ai_caption_choice.setCurrentIndex(w.ai_caption_choice.findData(c.id))
    w.ai_pending=(w.revision,c,SimpleNamespace(suggested_text=text,warnings=()))
    w.ai_original.setPlainText(c.text);w.ai_proposed.setPlainText(text);w.update_ai_controls()

def test_suggestion_stages_diff_without_mutation(window):
    before=deepcopy(window.project);suggestion(window)
    assert window.project==before
    assert '-original text' in window.ai_diff.toPlainText()
    assert '+Corrected text' in window.ai_diff.toPlainText()
    assert window.ai_apply_button.isEnabled()

def test_apply_is_explicit_text_only_and_one_undo(window,monkeypatch):
    before=deepcopy(window.project);suggestion(window)
    monkeypatch.setattr(QMessageBox,'question',lambda *_:QMessageBox.StandardButton.Yes)
    window.apply_ai_suggestion();c=window.project.captions[0]
    assert (c.id,c.start,c.duration,c.language)==('one',10,20,'und')
    assert c.text=='Corrected text';assert window.ai_pending is None
    window.undo();assert window.project==before

def test_rejection_keeps_project(window,monkeypatch):
    before=deepcopy(window.project);suggestion(window)
    monkeypatch.setattr(QMessageBox,'question',lambda *_:QMessageBox.StandardButton.No)
    window.apply_ai_suggestion();assert window.project==before

def test_stale_project_or_selection_clears_proposal(window):
    suggestion(window);window.edit(lambda:window.editor.update_caption('two',text='another edit'))
    assert window.ai_pending is None;assert window.project.captions[0].text=='original text'
    suggestion(window);window.ai_caption_choice.setCurrentIndex(1);assert window.ai_pending is None

def test_changed_during_confirmation_is_rejected(window,monkeypatch):
    suggestion(window)
    def ask(*args):
        window.edit(lambda:window.editor.update_caption('one',text='User correction'))
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox,'question',ask);window.apply_ai_suggestion()
    assert window.project.captions[0].text=='User correction'

class Index:
    def __init__(self,captions):self.captions=deepcopy(captions)
    def matches(self,captions):return self.captions==captions

def test_index_invalidates_for_text_timing_and_undo(window):
    window.search_index=Index(window.project.captions)
    window.edit(lambda:window.editor.update_caption('two',start=45))
    assert window.search_index is None
    assert 'changed' in window.ai_search_status.text()
    window.undo();assert window.search_index is None

def test_search_jump_targets_actual_caption(window):
    from PySide6.QtWidgets import QListWidgetItem
    from PySide6.QtCore import Qt
    window.search_index=Index(window.project.captions)
    item=QListWidgetItem('Result');item.setData(Qt.ItemDataRole.UserRole,SimpleNamespace(id='two',text='coffee shop',start=40,duration=20,score=.7))
    window.jump_to_search_result(item)
    assert window.timeline.playhead==40;assert window.selected_caption().id=='two'
    window.project.captions[1].text='changed';window.jump_to_search_result(item)
    assert window.search_index is None

def test_model_paths_change_invalidates_verified_state(window,tmp_path):
    model=tmp_path/'model';model.write_bytes(b'model')
    window.ai_records['semantic-search']=inspect_model_files('semantic-search',{'model':model})
    window.search_index=Index(window.project.captions)
    window.ai_fields['semantic-search']['model'].setText('other')
    assert 'semantic-search' not in window.ai_records and window.search_index is None

def test_local_import_snapshot_rejects_changed_paths(window,tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'a');b.write_bytes(b'b')
    fields=window.ai_fields['semantic-search'];fields['model'].setText(str(a));fields['tokenizer'].setText(str(b))
    captured={}
    def start(label,operation,success,kind):captured.update(operation=operation,success=success)
    window.start_job=start;window.import_ai_model('semantic-search')
    result=captured['operation'](None,lambda _:None);fields['model'].setText(str(b));captured['success'](result)
    assert 'semantic-search' not in window.ai_records

def test_heavy_job_disables_other_ai_controls(window):
    window.ai_records['subtitle-assistant']=object();window.search_index=Index(window.project.captions)
    window.tts_job=object();window.update_ai_controls()
    assert not window.ai_suggest_button.isEnabled();assert not window.ai_search_button.isEnabled()
    window.tts_job=None

def test_empty_proposal_not_applicable(window):
    suggestion(window,'   ');assert not window.ai_apply_button.isEnabled()

def test_index_build_result_rejected_after_model_selection_changes(window,tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'a');b.write_bytes(b'b')
    record=inspect_model_files('semantic-search',{'model':a,'tokenizer':b})
    window.ai_records['semantic-search']=record
    captured={};window.start_job=lambda label,operation,success,kind:captured.update(success=success)
    window.build_semantic_index();index=Index(window.project.captions)
    window.ai_fields['semantic-search']['model'].setText('changed-model')
    captured['success'](index)
    assert window.search_index is None and not window.ai_search_button.isEnabled()

def test_suggestion_rejected_after_model_selection_changes(window,tmp_path):
    a=tmp_path/'a';b=tmp_path/'b';a.write_bytes(b'a');b.write_bytes(b'b')
    window.ai_records['subtitle-assistant']=inspect_model_files('subtitle-assistant',{'runtime':a,'model':b})
    captured={};window.start_job=lambda label,operation,success,kind:captured.update(success=success)
    window.suggest_subtitle();window.ai_fields['subtitle-assistant']['model'].setText('new-model')
    captured['success'](SimpleNamespace(suggested_text='changed',warnings=()))
    assert window.ai_pending is None and window.project.captions[0].text=='original text'

def test_query_result_rejected_after_index_invalidation(window):
    window.search_index=Index(window.project.captions);window.ai_query.setText('coffee')
    captured={};window.start_job=lambda label,operation,success,kind:captured.update(success=success)
    window.search_subtitles();window.ai_fields['semantic-search']['model'].setText('new-model')
    captured['success']([SimpleNamespace(id='two',text='coffee shop',start=40,duration=20,score=.7)])
    assert window.ai_results.count()==0
