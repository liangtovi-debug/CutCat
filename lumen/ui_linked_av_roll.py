"""Captured, pure inspector preview for one cut between two linked A/V pairs.

The four-role selection is not a parameter-edit selection. Only this existing
roll section is enabled; no source observation, rendering or audition occurs.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .i18n import tr, tr_error

ROLL_SELECTION = 'Select exactly two complete adjacent linked A/V pairs, with no extra clips.'
ROLL_STALE = 'The project, selection or active role changed. Cancel this roll draft and review the current cut again.'
ROLL_SCOPE = ('Only these four linked members change. Both tracks share the new cut; outer endpoints, links and other items stay fixed. '
              'Fades keep their lengths and follow the edited edge. Audio may change; rebuild preview to see and hear the result.')
ROLL_TOOLTIP = ('Absolute timeline frame for the shared cut between exactly two complete adjacent linked A/V pairs. '
                'Both video members must share one video track and both audio members one audio track.')


def _context(window):
    return (id(window.editor), window.editor.session_id, window.editor.revision,
            id(window.project), window.revision, window.selection_generation,
            tuple(window.selected_clip_ids), window.selection, window.active_selection_type)


@dataclass
class _RollCapture:
    context: tuple
    project: object
    clip_ids: tuple[str, ...]
    old_cut: int
    stale: bool = False


class LinkedAVRollMixin:
    def init_linked_roll(self):
        self._linked_roll_capture = None
        self._linked_roll_timer = QTimer(self)
        self._linked_roll_timer.setInterval(150)
        self._linked_roll_timer.timeout.connect(self.check_linked_roll_current)

    def linked_roll_selection(self):
        """Route multi-pair intent without silently discarding extra members."""
        ids = self.selected_clip_ids
        return (len(ids) > 2 and any(set(ids).intersection(link.clip_ids)
                                   for link in self.project.av_links))

    def prepare_linked_roll_inspector(self):
        """A refresh discards any staged cut; it cannot re-authorize that cut."""
        self._linked_roll_capture = None
        self._linked_roll_timer.stop()
        if (self.active_selection_type != 'clip' or len(self.selected_clip_ids) != 4
                or self.selection not in self.selected_clip_ids):
            return False
        from .linked_av_roll import resolve_linked_av_roll
        try:
            left, _ = resolve_linked_av_roll(self.project, self.selected_clip_ids)
            old_cut = next(c.end for c in self.project.clips if c.id == left.video_clip_id)
        except (ValueError, TypeError):
            return False
        self._linked_roll_capture = _RollCapture(_context(self), deepcopy(self.project),
                                                 tuple(self.selected_clip_ids), old_cut)
        self.roll_cut_spin.blockSignals(True)
        self.roll_cut_spin.setValue(min(old_cut, self.roll_cut_spin.maximum()))
        self.roll_cut_spin.blockSignals(False)
        return True

    def can_inspect_linked_roll(self):
        """Keep the captured four-role shell navigable, not its edits authorized.

        An unlocked stale draft still exposes Cancel. Track locks independently
        disable editing leaves, and Apply still checks the full capture.
        """
        capture = self._linked_roll_capture
        return (capture is not None and self.active_selection_type == 'clip'
                and tuple(self.selected_clip_ids) == capture.clip_ids
                and self.selection in capture.clip_ids)

    def _linked_roll_current(self):
        capture = self._linked_roll_capture
        return (capture is not None and not capture.stale and not self.closing
                and capture.context == _context(self) and capture.project == self.project)

    def _linked_roll_available(self):
        return (not self.closing and not self.jobs and self.isEnabled()
                and self.centralWidget().isEnabled() and self.active_selection_type == 'clip'
                and not self.selection_edit_locked()
                and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None
                and getattr(getattr(self, 'shortcuts', None), '_composing', None) is None)

    def check_linked_roll_current(self):
        capture = self._linked_roll_capture
        if capture is None:
            self._linked_roll_timer.stop(); return
        if not self._linked_roll_current():
            capture.stale = True
            self._linked_roll_timer.stop()
            self.refresh_linked_roll_range()

    def _linked_roll_feedback(self, text):
        self.roll_range_note.setText(text)
        self.roll_range_note.setToolTip(text)
        self.roll_range_note.setAccessibleDescription(text)

    def refresh_linked_roll_range(self):
        """Return True only when handling linked multi-selection intent."""
        capture = self._linked_roll_capture
        if capture is None and not self.linked_roll_selection():
            self.cancel_roll_button.setVisible(False)
            return False
        self.cancel_roll_button.setVisible(True)
        self.apply_roll_button.setEnabled(False)
        self.roll_anchor_note.setText(tr(ROLL_SCOPE))
        self.roll_cut_spin.setToolTip(tr(ROLL_TOOLTIP))
        self.apply_roll_button.setText(tr('Apply joint roll edit'))
        self.cancel_roll_button.setEnabled(capture is not None)
        if capture is None:
            self._linked_roll_feedback(tr(ROLL_SELECTION)); self.roll_cut_spin.setEnabled(False)
            return True
        if not self._linked_roll_current():
            capture.stale = True
            self._linked_roll_feedback(tr(ROLL_STALE)); self.roll_cut_spin.setEnabled(False)
            return True
        bounded = capture.old_cut <= self.roll_cut_spin.maximum()
        self.roll_cut_spin.setEnabled(bounded)
        if not bounded:
            self._linked_roll_feedback(tr_error('This roll control supports timeline cuts up to 2147483647 frames.'))
            return True
        try:
            roles = self.editor.preview_roll_linked_av(capture.clip_ids, self.roll_cut_spin.value())
        except (ValueError, TypeError) as exc:
            self._linked_roll_feedback(tr_error(exc))
            if self.roll_cut_spin.value() != capture.old_cut:
                self._linked_roll_timer.start()
            return True
        media = {m.id: m for m in self.project.media}
        headings = ('Left V', 'Left A', 'Right V', 'Right A')
        active = next(headings[i] for i, clip in enumerate(roles) if clip.id == self.selection)
        lines = [tr('Joint linked A/V roll · active role: {role}\nCut {old} → {new}; outer span [{start}, {end}) stays fixed.',
                    role=tr(active), old=capture.old_cut, new=self.roll_cut_spin.value(),
                    start=roles[0].start, end=roles[2].end)]
        for role, clip in zip(headings, roles):
            lines.append(tr('{role} · {name} · {clip_id}\nTimeline [{start}, {end}) · {duration} frames\nSource [{source_in}, {source_out})',
                role=tr(role), name=media[clip.media_id].name, clip_id=clip.id,
                start=clip.start, end=clip.end, duration=clip.duration,
                source_in=str(clip.source_in), source_out=str(clip.source_out)))
        self._linked_roll_feedback('\n\n'.join(lines))
        changed = self.roll_cut_spin.value() != capture.old_cut
        self.apply_roll_button.setEnabled(changed and self._linked_roll_available())
        self.cancel_roll_button.setEnabled(changed)
        if changed:
            self._linked_roll_timer.start()
        else:
            self._linked_roll_timer.stop()
        return True

    def apply_linked_roll(self):
        """Never renew a stale capture on Apply; recompute the exact candidate."""
        if self._linked_roll_capture is None and not self.linked_roll_selection():
            return False
        if not self._linked_roll_current() or not self._linked_roll_available():
            self.check_linked_roll_current()
            self.refresh_linked_roll_range()
            return True
        capture = self._linked_roll_capture
        cut = self.roll_cut_spin.value()
        if capture.old_cut <= self.roll_cut_spin.maximum() and cut != capture.old_cut:
            self.edit(lambda: self.editor.roll_linked_av(capture.clip_ids, cut))
        return True

    def cancel_roll_draft(self):
        """Explicitly discard the request without an edit or history mutation."""
        self.refresh_inspector()
