"""Explicit offline edit proposals. This panel is not a language-model agent.

Only immutable selected text/gain snapshots reach a proposal adapter. The GUI
owns the gate and remains the only place that can confirm and apply a preview.
"""
from __future__ import annotations

import json
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QMessageBox
from .i18n import tr, tr_error
from .i18n_widgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox,
                          QLabel, QPlainTextEdit)


class ControlledAssistantMixin:
    def build_controlled_assistant(self):
        self.review_gate = None
        self.review_editor = None
        self.controlled_proposal = None
        self.controlled_snapshot = None
        self._review_loading = False
        # Keep the non-wrapping native group title short. The full experimental
        # disclosure belongs in the wrapping description, not a width constraint.
        group = QGroupBox(tr('Controlled edits · offline'))
        layout = QVBoxLayout(group)
        note = QLabel(tr('Experimental offline preview. Deterministic form, not natural-language intelligence. No API, network, model download, or media upload. Choose existing targets, enter exact changes, then preview.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.controlled_provider = QComboBox()
        self.controlled_provider.addItem(tr('Manual offline · deterministic'), 'manual-offline')
        layout.addWidget(self.controlled_provider)
        self.controlled_caption_enabled = QCheckBox(tr('Replace one saved subtitle’s text'))
        self.controlled_caption_enabled.setChecked(True)
        layout.addWidget(self.controlled_caption_enabled)
        self.controlled_caption_choice = QComboBox()
        layout.addWidget(self.controlled_caption_choice)
        self.controlled_text = QPlainTextEdit()
        self.controlled_text.setPlaceholderText(tr('Enter the exact replacement text. Timing, ID and language stay unchanged.'))
        self.controlled_text.setMaximumHeight(85)
        layout.addWidget(self.controlled_text)
        self.controlled_gain_enabled = QCheckBox(tr('Set one audio clip’s gain'))
        layout.addWidget(self.controlled_gain_enabled)
        self.controlled_clip_choice = QComboBox()
        layout.addWidget(self.controlled_clip_choice)
        gain_row = QHBoxLayout()
        gain_row.addWidget(QLabel(tr('New gain (dB)')))
        self.controlled_gain = QDoubleSpinBox()
        self.controlled_gain.setRange(-96, 24)
        self.controlled_gain.setDecimals(1)
        self.controlled_gain.setSingleStep(1)
        gain_row.addWidget(self.controlled_gain)
        layout.addLayout(gain_row)
        # Stack review actions so native button minima do not add together
        # and force horizontal scrolling in a narrow inspector.
        actions = QVBoxLayout()
        self.controlled_preview_button = self._button(tr('Preview exact changes'), self.preview_controlled_edits, True)
        self.controlled_reject_button = self._button(tr('Reject / cancel'), self.cancel_controlled_edits)
        actions.addWidget(self.controlled_preview_button)
        actions.addWidget(self.controlled_reject_button)
        layout.addLayout(actions)
        self.controlled_diff = QPlainTextEdit()
        self.controlled_diff.setReadOnly(True)
        self.controlled_diff.setMinimumHeight(130)
        self.controlled_diff.setMaximumHeight(210)
        layout.addWidget(self.controlled_diff)
        self.controlled_status = QLabel(tr('Nothing applied. Preview is required before confirmation.'))
        self.controlled_status.setWordWrap(True)
        layout.addWidget(self.controlled_status)
        self.controlled_apply_button = self._button(tr('Confirm and apply preview'), self.apply_controlled_edits)
        layout.addWidget(self.controlled_apply_button)
        self.controlled_provider.currentIndexChanged.connect(self.controlled_inputs_changed)
        self.controlled_caption_enabled.toggled.connect(self.controlled_inputs_changed)
        self.controlled_gain_enabled.toggled.connect(self.controlled_inputs_changed)
        self.controlled_caption_choice.currentIndexChanged.connect(self.controlled_caption_changed)
        self.controlled_clip_choice.currentIndexChanged.connect(self.controlled_clip_changed)
        self.controlled_text.textChanged.connect(self.controlled_inputs_changed)
        self.controlled_gain.valueChanged.connect(self.controlled_inputs_changed)
        if hasattr(self, 'caption_list'):
            self.caption_list.itemSelectionChanged.connect(self.invalidate_review_selection)
        self.controlled_group = group
        self.update_controlled_controls()
        return group

    def _get_review_gate(self):
        from .controlled_assistant import ProposalGate
        if self.review_editor is not self.editor:
            self.invalidate_controlled_reviews()
            self.review_editor = self.editor
            self.review_gate = ProposalGate(self.editor)
        return self.review_gate

    def invalidate_controlled_reviews(self, *_):
        """Invalidation is shared by manual and local-model review surfaces."""
        gate = getattr(self, 'review_gate', None)
        if gate is not None:
            gate.cancel()
        if getattr(self, 'controlled_proposal', None) is not None and hasattr(self, 'controlled_diff'):
            self.controlled_diff.clear()
            self.controlled_status.setText(tr('Preview invalidated. Create a new preview before applying.'))
        self.controlled_proposal = None
        self.controlled_snapshot = None
        self.ai_review_proposal = None
        self.ai_review_request = None
        self.ai_review_editor = None
        self.ai_generation = getattr(self, 'ai_generation', 0) + 1
        if hasattr(self, 'controlled_apply_button'):
            self.controlled_apply_button.setEnabled(False)
        if hasattr(self, 'ai_apply_button'):
            self.ai_apply_button.setEnabled(False)

    def invalidate_review_selection(self, *_):
        if not getattr(self, '_review_loading', False):
            self.invalidate_controlled_reviews()
            if hasattr(self, 'controlled_status'):
                self.controlled_status.setText(tr('Selection changed. Preview again before applying.'))
            if getattr(self, 'ai_pending', None) is not None:
                self.clear_ai_suggestion()

    def _controlled_scope(self):
        captions = (self.controlled_caption_choice.currentData(),) if self.controlled_caption_enabled.isChecked() and self.controlled_caption_choice.currentData() else ()
        clips = (self.controlled_clip_choice.currentData(),) if self.controlled_gain_enabled.isChecked() and self.controlled_clip_choice.currentData() else ()
        return captions, clips

    def _controlled_inputs(self):
        return (self._controlled_scope(), self.controlled_provider.currentData(),
                self.controlled_caption_enabled.isChecked(), self.controlled_gain_enabled.isChecked(),
                self.controlled_text.toPlainText(), self.controlled_gain.value())

    def controlled_inputs_changed(self, *_):
        if self._review_loading:
            return
        self.invalidate_controlled_reviews()
        self.controlled_diff.clear()
        self.controlled_status.setText(tr('Changes revised. Create a new preview before applying.'))
        self.update_controlled_controls()

    def controlled_caption_changed(self, *_):
        self._review_loading = True
        caption = next((c for c in self.project.captions if c.id == self.controlled_caption_choice.currentData()), None)
        self.controlled_text.setPlainText(caption.text if caption else '')
        self._review_loading = False
        self.controlled_inputs_changed()

    def controlled_clip_changed(self, *_):
        self._review_loading = True
        clip = next((c for c in self.project.clips if c.id == self.controlled_clip_choice.currentData()), None)
        self.controlled_gain.setValue(clip.gain_db if clip else 0)
        self._review_loading = False
        self.controlled_inputs_changed()

    def refresh_controlled_assistant(self):
        if not hasattr(self, 'controlled_group'):
            return
        changed_editor = self.review_editor is not self.editor
        if changed_editor:
            self._get_review_gate()
        old_caption = self.controlled_caption_choice.currentData()
        old_clip = self.controlled_clip_choice.currentData()
        self._review_loading = True
        for combo in (self.controlled_caption_choice, self.controlled_clip_choice):
            combo.blockSignals(True)
            combo.clear()
        for caption in self.project.captions:
            self.controlled_caption_choice.addItem(tr('{id} · f{frame} · {text}', id=caption.id, frame=caption.start, text=' / '.join(caption.text.splitlines())[:45]), caption.id)
        media = {m.id: m for m in self.project.media}
        for clip in self.project.clips:
            source = media.get(clip.media_id)
            if source and source.has_audio:
                self.controlled_clip_choice.addItem(tr('{id} · {name} · {gain:g} dB', id=clip.id, name=source.name, gain=clip.gain_db), clip.id)
        for combo, old in ((self.controlled_caption_choice, old_caption), (self.controlled_clip_choice, old_clip)):
            index = combo.findData(old)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self._review_loading = False
        if changed_editor or old_caption != self.controlled_caption_choice.currentData():
            self.controlled_caption_changed()
        if changed_editor or old_clip != self.controlled_clip_choice.currentData():
            self.controlled_clip_changed()
        if self.controlled_proposal is not None:
            try:
                self._check_controlled_current(self.controlled_proposal)
            except Exception:
                self.controlled_inputs_changed()
                self.controlled_status.setText(tr('Project changed. Preview again before applying.'))
        self.update_controlled_controls()

    def update_controlled_controls(self):
        if not hasattr(self, 'controlled_apply_button'):
            return
        busy = any(getattr(self, name, None) for name in ('assistant_job', 'semantic_job', 'modelverify_job', 'tts_job', 'asr_job'))
        captions, clips = self._controlled_scope()
        valid = bool(captions or clips)
        valid = valid and (not self.controlled_caption_enabled.isChecked() or bool(captions and self.controlled_text.toPlainText().strip()))
        valid = valid and (not self.controlled_gain_enabled.isChecked() or bool(clips))
        valid = valid and self.controlled_provider.currentData() == 'manual-offline'
        self.controlled_caption_choice.setEnabled(self.controlled_caption_enabled.isChecked())
        self.controlled_text.setEnabled(self.controlled_caption_enabled.isChecked())
        self.controlled_clip_choice.setEnabled(self.controlled_gain_enabled.isChecked())
        self.controlled_gain.setEnabled(self.controlled_gain_enabled.isChecked())
        self.controlled_preview_button.setEnabled(valid and not busy)
        self.controlled_apply_button.setEnabled(self.controlled_proposal is not None and valid and not busy)

    def preview_controlled_edits(self):
        if not self.controlled_preview_button.isEnabled():
            return
        self.invalidate_controlled_reviews()
        # A local model suggestion and a manual proposal cannot both be active.
        if getattr(self, 'ai_pending', None) is not None:
            self.clear_ai_suggestion()
        captions, clips = self._controlled_scope()
        commands = []
        if captions:
            commands.append({'op': 'caption.replace_text', 'target_id': captions[0], 'text': self.controlled_text.toPlainText()})
        if clips:
            commands.append({'op': 'clip.set_gain_db', 'target_id': clips[0], 'gain_db': self.controlled_gain.value()})
        try:
            gate = self._get_review_gate()
            request = gate.begin(caption_ids=captions, clip_ids=clips, adapter='manual-offline')
            self.controlled_proposal = gate.prepare(request, json.dumps({'schema_version': 1, 'commands': commands}, ensure_ascii=False))
            self.controlled_snapshot = self._controlled_inputs()
            self.controlled_diff.setPlainText(_proposal_diff(self.controlled_proposal))
            self.controlled_status.setText(tr('Preview only. Applying these exact changes creates one undo step. Any revision requires a new preview.'))
        except Exception as exc:
            self.invalidate_controlled_reviews()
            self.controlled_status.setText(tr('Proposal rejected: {error}', error=_review_error(exc)))
        self.update_controlled_controls()

    def _check_controlled_current(self, proposal):
        if self.review_editor is not self.editor or self.controlled_proposal is not proposal or self.controlled_snapshot != self._controlled_inputs():
            raise ValueError('Project, selection or proposed values changed. Preview again.')
        captions, clips = self._controlled_scope()
        self.review_gate.check_current(proposal, caption_ids=captions, clip_ids=clips)

    def apply_controlled_edits(self):
        proposal = self.controlled_proposal
        if proposal is None:
            return
        try:
            self._check_controlled_current(proposal)
            message = tr('Apply only the exact subtitle text / clip gain changes shown below? Timing, IDs, source files and other properties stay unchanged. One Undo restores the whole batch.\n\n{diff}', diff=_proposal_diff(proposal))
            if QMessageBox.question(self, tr('Apply this reviewed preview?'), message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            # A modal dialog can reenter application code: recheck after consent.
            self._check_controlled_current(proposal)
            approval = self.review_gate.approve(proposal)
            captions, clips = self._controlled_scope()
            receipt = self.review_gate.apply(proposal, approval, caption_ids=captions, clip_ids=clips)
            self.invalidate_controlled_reviews()
            if receipt.changed:
                self.changed()
            self.controlled_status.setText(tr('Reviewed changes applied. One Undo restores the entire batch.') if receipt.changed else tr('No change was needed. Project and undo history are unchanged.'))
        except Exception as exc:
            self.invalidate_controlled_reviews()
            self.controlled_status.setText(tr('Proposal rejected: {error}', error=_review_error(exc)))
        self.update_controlled_controls()

    def cancel_controlled_edits(self):
        job = getattr(self, 'assistant_job', None)
        if job is not None and hasattr(job, 'cancel'):
            job.cancel.set()
        self.clear_ai_suggestion()
        self.controlled_diff.clear()
        self.controlled_status.setText(tr('Canceled. No proposal was applied; late results are ignored.'))
        self.update_controlled_controls()


def _proposal_diff(proposal):
    """Translate labels, never the user’s original or replacement text."""
    parts = []
    for change in proposal.diff:
        if change.op == 'caption.replace_text':
            part = tr('Subtitle {id}\n− Before: {before}\n+ After: {after}', id=change.target_id, before=change.before, after=change.after)
        else:
            part = tr('Audio clip {id}\n− Gain: {before:g} dB\n+ Gain: {after:g} dB', id=change.target_id, before=change.before, after=change.after)
        parts.append(part)
    if not parts:
        return tr('No differences. Applying this preview will not change the project.')
    result = parts[-1]
    for part in reversed(parts[:-1]):
        result = tr('{first}\n\n{rest}', first=part, rest=result)
    return result


def _review_error(exc):
    from .i18n_catalog_controlled_assistant import ZH_CN
    message = str(exc)
    return tr(message) if message in ZH_CN else tr_error(exc)
