"""Reviewed subtitle suggestions and actual-caption semantic search, local only."""
import copy
from dataclasses import asdict
import difflib
import json
from pathlib import Path
from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtWidgets import (QComboBox,QFileDialog,QFormLayout,QGroupBox,QHBoxLayout,QLabel,QLineEdit,QListWidget,QListWidgetItem,QMessageBox,QPlainTextEdit,QScrollArea,QVBoxLayout,QWidget)
from .i18n import tr, tr_error
from .i18n_widgets import QComboBox, QFormLayout, QGroupBox, QLabel, QLineEdit, QListWidgetItem, QPlainTextEdit
from .local_ai import ModelRegistry,inspect_model_files,verify_registered
from .ui_component_setup import open_component_setup
from .ui_controlled_assistant import ControlledAssistantMixin, _review_error

class LocalAIPanelMixin(ControlledAssistantMixin):
    def build_ai_panel(self):
        from .api_settings import ApiSettingsSession
        self.api_settings_session = ApiSettingsSession()
        self.assistant_job=None;self.semantic_job=None;self.modelverify_job=None
        self.ai_records={};self.ai_pending=None;self.search_index=None
        self.ai_review_request=None;self.ai_review_proposal=None;self.ai_review_editor=None;self.ai_generation=0;self._ai_loading=False
        self.model_registry=ModelRegistry(Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))/'local-models.json')
        scroll=QScrollArea();scroll.setWidgetResizable(True);panel=QWidget();layout=QVBoxLayout(panel);scroll.setWidget(panel)
        self.api_settings_button = self._button(tr('Custom model API'), self.show_api_settings)
        layout.addWidget(self.api_settings_button)
        layout.addWidget(self.build_controlled_assistant())
        note=QLabel(tr('Optional local models · no downloads or cloud fallback\nOne AI task at a time. Review every suggestion; a small model can make mistakes.'));note.setWordWrap(True);layout.addWidget(note)
        self.ai_model_status=QLabel(tr('Select existing model/runtime files, then verify local import.'));self.ai_model_status.setWordWrap(True);layout.addWidget(self.ai_model_status)
        self.component_setup_button=self._button(tr("Local AI component setup"),lambda:open_component_setup(self))
        layout.addWidget(self.component_setup_button)
        self.ai_fields={}
        for kind,title,roles in [('subtitle-assistant',tr('Subtitle assistant · Qwen'),('runtime','model')),('semantic-search',tr('Semantic search · MiniLM'),('model','tokenizer'))]:
            group=QGroupBox(title);form=QFormLayout(group);fields={}
            for role in roles:
                field=QLineEdit();field.setPlaceholderText(tr('Existing local {role} file', role=tr(role.title())));fields[role]=field
                row=QHBoxLayout();row.addWidget(field);row.addWidget(self._button(tr('Choose…'),lambda checked=False,f=field:self.choose_ai_file(f)));form.addRow(tr(role.title()),row)
                field.textChanged.connect(lambda value,k=kind:self.ai_paths_changed(k))
            self.ai_fields[kind]=fields
            form.addRow(self._button(tr('Verify local files'),lambda checked=False,k=kind:self.import_ai_model(k)))
            layout.addWidget(group)
        self.ai_caption_choice=QComboBox();self.ai_caption_choice.currentIndexChanged.connect(self.clear_ai_suggestion);layout.addWidget(QLabel(tr('Saved subtitle to proofread')));layout.addWidget(self.ai_caption_choice)
        self.ai_suggest_button=self._button(tr('Suggest correction'),self.suggest_subtitle,True);layout.addWidget(self.ai_suggest_button)
        layout.addWidget(QLabel(tr('Original · IDs and times stay unchanged')))
        self.ai_original=QPlainTextEdit();self.ai_original.setReadOnly(True);self.ai_original.setMaximumHeight(65);layout.addWidget(self.ai_original)
        layout.addWidget(QLabel(tr('Proposed text · review and edit before applying')))
        self.ai_proposed=QPlainTextEdit();self.ai_proposed.setMaximumHeight(85);self.ai_proposed.textChanged.connect(self.refresh_ai_diff);layout.addWidget(self.ai_proposed)
        self.ai_diff=QPlainTextEdit();self.ai_diff.setReadOnly(True);self.ai_diff.setMaximumHeight(95);layout.addWidget(self.ai_diff)
        self.ai_warning=QLabel(tr('No suggestion has been applied.'));self.ai_warning.setWordWrap(True);layout.addWidget(self.ai_warning)
        self.ai_revision_button=self._button(tr('Preview revised text'),self.preview_ai_revision);layout.addWidget(self.ai_revision_button)
        self.ai_apply_button=self._button(tr('Apply reviewed text only'),self.apply_ai_suggestion);layout.addWidget(self.ai_apply_button)
        layout.addWidget(self._button(tr('Reject / cancel subtitle suggestion'),self.cancel_controlled_edits))
        search=QGroupBox(tr('Semantic subtitle search'));form=QFormLayout(search)
        self.ai_index_button=self._button(tr('Build / refresh subtitle index'),self.build_semantic_index);form.addRow(self.ai_index_button)
        self.ai_query=QLineEdit();self.ai_query.setPlaceholderText(tr('Find a topic, even with different words'));form.addRow(self.ai_query)
        self.ai_search_button=self._button(tr('Search saved subtitles'),self.search_subtitles);form.addRow(self.ai_search_button)
        self.ai_search_status=QLabel(tr('No index yet. Results always point to real saved subtitles.'));self.ai_search_status.setWordWrap(True);form.addRow(self.ai_search_status)
        self.ai_results=QListWidget();self.ai_results.setWordWrap(True);self.ai_results.setMinimumHeight(130);self.ai_results.itemDoubleClicked.connect(self.jump_to_search_result);form.addRow(self.ai_results)
        form.addRow(self._button(tr('Jump to selected result'),lambda:self.jump_to_search_result(self.ai_results.currentItem())));layout.addWidget(search);layout.addStretch()
        try:
            records=self.model_registry.records()
            for record in records:
                if record.kind in self.ai_fields:
                    for f in record.files:
                        if f.role in self.ai_fields[record.kind]:self.ai_fields[record.kind][f.role].setText(f.path)
            if records:self.ai_model_status.setText(tr('Saved local paths loaded. Verify files again before inference.'))
        except Exception as exc:self.ai_model_status.setText(tr('Local registry needs attention: {error}', error=tr_error(exc)))
        return scroll

    def show_api_settings(self):
        from .ui_api_settings import open_api_settings
        previous = self.api_settings_session
        session = open_api_settings(self, previous)
        if session is not previous:
            self.api_settings_session = session
            # Configuration never selects a live provider. It does revoke
            # earlier review context, and is deliberately not part of Project.
            self.clear_ai_suggestion()

    def choose_ai_file(self,field):
        path,_=QFileDialog.getOpenFileName(self,tr('Choose an existing local model/runtime file'),'',tr('All files (*)'))
        if path:field.setText(path)
    def ai_paths_changed(self,kind):
        self.invalidate_controlled_reviews()
        self.ai_records.pop(kind,None)
        if kind=='semantic-search':self.search_index=None
        elif hasattr(self,'ai_original'):self.clear_ai_suggestion()
        if hasattr(self,'ai_results'):
            if kind=='semantic-search':self.ai_results.clear();self.ai_search_status.setText(tr('Model paths changed. Verify and rebuild the index.'))
            self.update_ai_controls()
    def import_ai_model(self,kind):
        if self.modelverify_job:return
        files={role:f.text().strip() for role,f in self.ai_fields[kind].items()}
        if not all(files.values()):QMessageBox.warning(self,tr('Local files required'),tr('Choose every listed file. Nothing is downloaded.'));return
        def operation(cancel,progress):return inspect_model_files(kind,files,label=kind,cancel=cancel)
        def success(record):
            if files!={role:f.text().strip() for role,f in self.ai_fields[kind].items()}:return
            try:self.model_registry.register(record)
            except Exception as exc:QMessageBox.warning(self,tr('Cannot save local model registration'),tr_error(exc));return
            self.ai_records[kind]=record
            self.ai_model_status.setText(tr('Verified {kind} locally. {size} MiB. License and runtime compatibility still require their documented checks.', kind=tr('Subtitle assistant · Qwen') if kind == 'subtitle-assistant' else tr('Semantic search · MiniLM'), size=sum(f.size_bytes for f in record.files)//1024**2))
            self.update_ai_controls()
        self.start_job(tr('Verifying local model files…'),operation,success,'modelverify')
    def clear_ai_suggestion(self,*_):
        self.invalidate_controlled_reviews()
        self.ai_pending=None
        if hasattr(self,'ai_original'):
            self._ai_loading=True
            self.ai_original.clear();self.ai_proposed.clear();self.ai_diff.setPlainText('')
            self._ai_loading=False
            self.ai_warning.setText(tr('No suggestion has been applied.'));self.update_ai_controls()
    def refresh_ai_diff(self):
        if not hasattr(self,'ai_diff'):return
        old=self.ai_original.toPlainText();new=self.ai_proposed.toPlainText()
        diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile='original', tofile='reviewed proposal', lineterm=''))
        self.ai_diff.setPlainText(tr('--- original\n+++ reviewed proposal\n{body}', body='\n'.join(diff[2:])) if diff else '')
        if not self._ai_loading and self.ai_review_proposal is not None:
            expected=self.ai_review_proposal.commands[0].value
            if new != expected:
                # Human edits are a different operation, never disguised as a
                # model correction. They need a fresh manual request + preview.
                self.invalidate_controlled_reviews()
                self.ai_warning.setText(tr('Human revision · not model proofreading. Click Preview revised text, then review and confirm again.'))
        # Older callers can stage a manual review tuple. Actual model completion
        # always sets ai_review_request first and passes local-proofreader gate.
        if (not self._ai_loading and self.ai_pending is not None
                and self.ai_review_request is None
                and getattr(self, '_ai_manual_tuple', None) is not self.ai_pending
                and new == self.ai_pending[2].suggested_text and new.strip()):
            self._ai_manual_tuple=self.ai_pending
            self.preview_ai_revision()
        self.update_ai_controls()

    def _check_ai_current(self, proposal):
        if self.ai_pending is None or self.ai_review_editor is not self.editor or proposal is not self.ai_review_proposal:
            raise ValueError('Project, selection or proposed values changed. Preview again.')
        revision,caption,_=self.ai_pending
        current=next((c for c in self.project.captions if c.id==caption.id),None)
        if (revision != self.revision or current != caption or self.ai_caption_choice.currentData()!=caption.id
                or self.ai_proposed.toPlainText()!=proposal.commands[0].value):
            raise ValueError('Project, selection or proposed values changed. Preview again.')
        self.review_gate.check_current(proposal,caption_ids=(caption.id,),clip_ids=())

    def preview_ai_revision(self):
        if self.ai_pending is None or not self.ai_proposed.toPlainText().strip():return
        if getattr(self, '_ai_pending_identity', None) is self.ai_pending and self._ai_pending_editor is not self.editor:
            self.clear_ai_suggestion();return
        revision,caption,_=self.ai_pending
        current=next((c for c in self.project.captions if c.id==caption.id),None)
        if revision!=self.revision or current!=caption or self.ai_caption_choice.currentData()!=caption.id:
            self.clear_ai_suggestion();return
        try:
            self.invalidate_controlled_reviews()
            gate=self._get_review_gate()
            self.ai_review_request=gate.begin(caption_ids=(caption.id,),clip_ids=(),adapter='manual-offline')
            raw=json.dumps({'schema_version':1,'commands':[{'op':'caption.replace_text','target_id':caption.id,'text':self.ai_proposed.toPlainText()}]},ensure_ascii=False)
            self.ai_review_proposal=gate.prepare(self.ai_review_request,raw)
            self.ai_review_editor=self.editor
            self._ai_manual_tuple=self.ai_pending
            self._ai_pending_identity=self.ai_pending;self._ai_pending_editor=self.editor
            self.ai_warning.setText(tr('Human revision preview · exact text only. Confirm to apply; one Undo restores the subtitle.'))
        except Exception as exc:
            self.invalidate_controlled_reviews()
            self.ai_warning.setText(tr('Proposal rejected: {error}',error=_review_error(exc)))
        self.update_ai_controls()
    def refresh_ai(self):
        self.refresh_controlled_assistant()
        cid=self.ai_caption_choice.currentData();self.ai_caption_choice.blockSignals(True);self.ai_caption_choice.clear()
        for c in self.project.captions:
            display_text = ' / '.join(c.text.splitlines())[:65]
            self.ai_caption_choice.addItem(tr('f{frame} · {text}', frame=c.start, text=display_text), c.id)
        index=self.ai_caption_choice.findData(cid)
        if index>=0:self.ai_caption_choice.setCurrentIndex(index)
        self.ai_caption_choice.blockSignals(False)
        if self.ai_pending and (self.ai_pending[0]!=self.revision or self.ai_review_editor is not self.editor):self.clear_ai_suggestion()
        if self.search_index is not None and not self.search_index.matches(self.project.captions):
            self.search_index=None;self.ai_results.clear();self.ai_search_status.setText(tr('Subtitles changed. Rebuild the local index.'))
        self.update_ai_controls()
    def update_ai_controls(self):
        if not hasattr(self,'ai_suggest_button'):return
        busy=any(getattr(self,name,None) for name in ('assistant_job','semantic_job','modelverify_job','tts_job','asr_job'))
        self.ai_suggest_button.setEnabled(not busy and 'subtitle-assistant' in self.ai_records and self.ai_caption_choice.count()>0)
        self.ai_apply_button.setEnabled(self.ai_review_proposal is not None and self.ai_pending is not None and bool(self.ai_proposed.toPlainText().strip()) and not busy)
        self.ai_revision_button.setEnabled(self.ai_pending is not None and bool(self.ai_proposed.toPlainText().strip()) and not busy)
        self.update_controlled_controls()
        self.ai_index_button.setEnabled(not busy and 'semantic-search' in self.ai_records and bool(self.project.captions))
        self.ai_search_button.setEnabled(not busy and self.search_index is not None)
    def suggest_subtitle(self):
        if self.assistant_job:return
        from .subtitle_ai import AssistantConfig,suggest_caption,PROMPT_VERSION
        caption=next((copy.deepcopy(c) for c in self.project.captions if c.id==self.ai_caption_choice.currentData()),None)
        record=self.ai_records.get('subtitle-assistant')
        if caption is None or record is None:return
        fields={f.role:f.path for f in record.files};config=AssistantConfig(fields['runtime'],fields['model'],threads=2)
        self.clear_ai_suggestion()
        try:
            gate=self._get_review_gate()
            provenance=tuple((f.role+'_sha256',f.sha256) for f in record.files if f.role in ('runtime','model'))+(('prompt_version',PROMPT_VERSION),)
            request=gate.begin(caption_ids=(caption.id,),clip_ids=(),adapter='local-proofreader',provenance=provenance)
        except Exception as exc:
            self.ai_warning.setText(tr('Proposal rejected: {error}',error=_review_error(exc)));return
        revision=self.revision;editor=self.editor;generation=self.ai_generation
        self.ai_review_request=request;self.ai_review_editor=editor
        # The operation receives only text/config, never Editor or the gate.
        source_text=json.loads(request.payload_json)['captions'][0]['text']
        def operation(cancel,progress):verify_registered(record,cancel);return suggest_caption(source_text,config,cancel=cancel,progress=progress)
        def success(result):
            if (self.editor is not editor or self.ai_generation!=generation or self.ai_review_request is not request
                    or self.revision!=revision or self.ai_caption_choice.currentData()!=caption.id
                    or self.ai_records.get('subtitle-assistant') is not record):
                self.ai_warning.setText(tr('Project/selection changed; suggestion discarded.'));return
            try:
                actual=getattr(result,'provenance',None)
                if any(getattr(actual,key,None)!=value for key,value in request.provenance):
                    raise ValueError('Local model identity changed; generate a new suggestion')
                raw=json.dumps({'schema_version':1,'commands':[{'op':'caption.replace_text','target_id':caption.id,'text':result.suggested_text}]},ensure_ascii=False)
                proposal=gate.prepare(request,raw)
                gate.check_current(proposal,caption_ids=(caption.id,),clip_ids=())
            except Exception as exc:
                self.clear_ai_suggestion();self.ai_warning.setText(tr('Proposal rejected: {error}',error=_review_error(exc)));return
            self.ai_pending=(revision,caption,result);self.ai_review_proposal=proposal
            self._ai_pending_identity=self.ai_pending;self._ai_pending_editor=editor
            self._ai_manual_tuple=self.ai_pending;self._ai_loading=True
            self.ai_original.setPlainText(caption.text);self.ai_proposed.setPlainText(result.suggested_text)
            self._ai_loading=False;self.refresh_ai_diff()
            self.ai_warning.setText(tr('Review meaning and facts before applying. {warnings}', warnings=_translated_warnings(result.warnings)))
            if hasattr(result,'provenance'):self.ai_warning.setToolTip(json.dumps(asdict(result.provenance),ensure_ascii=False,indent=2))
            self.update_ai_controls()
        self.start_job(tr('Generating a local subtitle suggestion…'),operation,success,'assistant')
    def apply_ai_suggestion(self):
        proposal=self.ai_review_proposal
        if proposal is None:return
        try:
            self._check_ai_current(proposal)
            message=tr('Replace only this subtitle’s text? Its ID, timing and language stay unchanged.')+'\n\n'+self.ai_diff.toPlainText()
            if QMessageBox.question(self,tr('Apply reviewed subtitle text?'),message,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            self._check_ai_current(proposal)
            approval=self.review_gate.approve(proposal)
            receipt=self.review_gate.apply(proposal,approval,caption_ids=(self.ai_pending[1].id,),clip_ids=())
            self.clear_ai_suggestion()
            if receipt.changed:self.changed()
            self.ai_warning.setText(tr('Reviewed text applied. Undo restores the original subtitle.') if receipt.changed else tr('No change was needed. Project and undo history are unchanged.'))
        except Exception as exc:
            self.clear_ai_suggestion();self.ai_warning.setText(tr('Proposal rejected: {error}',error=_review_error(exc)))
    def build_semantic_index(self):
        if self.semantic_job:return
        from .semantic_search import SearchConfig,SemanticIndex
        record=self.ai_records.get('semantic-search')
        if record is None:return
        fields={f.role:f.path for f in record.files};config=SearchConfig(fields['model'],fields['tokenizer'],threads=2)
        captions=copy.deepcopy(self.project.captions);revision=self.revision
        def operation(cancel,progress):verify_registered(record,cancel);return SemanticIndex.build(captions,config,cancel=cancel,progress=progress)
        def success(index):
            if revision!=self.revision or self.ai_records.get('semantic-search') is not record or not index.matches(self.project.captions):self.ai_search_status.setText(tr('Subtitles changed during indexing. Rebuild.'));return
            self.search_index=index;self.ai_results.clear();self.ai_search_status.setText(tr('Indexed {count} saved subtitles locally. {truncated} long captions use only their first 128 model tokens.', count=len(captions), truncated=index.provenance.truncated_captions));self.update_ai_controls()
        self.start_job(tr('Building local semantic index…'),operation,success,'semantic')
    def search_subtitles(self):
        if self.semantic_job or self.search_index is None:return
        index=self.search_index;query=self.ai_query.text().strip();revision=self.revision
        if not query:return
        if not index.matches(self.project.captions):self.refresh_ai();return
        def operation(cancel,progress):return index.search(query,limit=5,cancel=cancel,progress=progress)
        def success(results):
            if revision!=self.revision or self.search_index is not index or query!=self.ai_query.text().strip() or not index.matches(self.project.captions):return
            self.ai_results.clear()
            for r in results:
                item=QListWidgetItem(tr('f{start}–{end} · similarity {score:.3f}\n{text}', start=r.start, end=r.start+r.duration, score=r.score, text=r.text));item.setData(Qt.ItemDataRole.UserRole,r);self.ai_results.addItem(item)
            self.ai_search_status.setText(tr('Similarity is not confidence. {count} long captions were truncated at 128 model tokens. Double-click to jump.', count=index.provenance.truncated_captions))
        self.start_job(tr('Searching subtitles locally…'),operation,success,'semantic')
    def jump_to_search_result(self,item):
        if item is None or self.search_index is None:return
        if not self.search_index.matches(self.project.captions):self.refresh_ai();return
        result=item.data(Qt.ItemDataRole.UserRole)
        current=next((c for c in self.project.captions if c.id==result.id),None)
        if current is None or (current.text,current.start,current.duration)!=(result.text,result.start,result.duration):return
        self.select_caption(current.id);self.seek_frame(current.start)


def _translated_warnings(warnings):
    """Keep nested translation metadata without altering model-generated content."""
    if not warnings:
        return ''
    result = tr(warnings[-1])
    for warning in reversed(warnings[:-1]):
        result = tr('{first}; {rest}', first=tr(warning), rest=result)
    return result
