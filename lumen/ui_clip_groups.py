"""Persistent clip grouping presentation, without nested or cross-item groups."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QAbstractItemView

from .i18n import tr
from .model import expanded_clip_ids


GROUP_HELP = ('Edit → Group clips saves a group of two or more ungrouped timeline clips. '
              'Click any member to select the whole group; Shift-click toggles the whole group. '
              'Move or delete complete groups together. Edit → Ungroup clips restores separate editing. '
              'Ungroup before trimming, splitting or changing one member. Captions and stickers are not group members; '
              'nested groups, merging groups and temporary group bypass are not supported.')
GROUP_TRIM_HELP = 'Grouped clips cannot be trimmed separately. Choose Edit → Ungroup clips first.'


def clip_group(project, clip_id):
    return next((group for group in project.clip_groups if clip_id in group.clip_ids), None)


def group_membership_token(project):
    """Explicit membership guard in addition to revision/session/fingerprint."""
    return (tuple((group.id, tuple(group.clip_ids)) for group in project.clip_groups),
            tuple((link.id, link.video_clip_id, link.audio_clip_id, link.media_id, link.alignment) for link in project.av_links))


def reconcile_group_ids(project, ids):
    """Keep selection order, append missing group members in project order."""
    existing = {clip.id for clip in project.clips}
    selected = tuple(cid for cid in dict.fromkeys(ids) if cid in existing)
    expanded = expanded_clip_ids(project, selected)
    return selected + tuple(cid for cid in expanded if cid not in selected)


class ClipGroupsMixin:
    def _clip_group_focus_blocked(self):
        if self._destructive_focus_blocked():
            return True
        focus = QApplication.focusWidget()
        while focus is not None and focus is not self:
            if isinstance(focus, QAbstractItemView):
                return True
            focus = focus.parentWidget()
        return False

    def can_group_selected_clips(self):
        ids = self.selected_clip_ids
        return (not self.selection_edit_locked() and self.active_selection_type == 'clip' and len(ids) >= 2
                and set(ids) <= {clip.id for clip in self.project.clips}
                and not any(clip_group(self.project, cid) for cid in ids)
                and not any(set(ids) & {link.video_clip_id, link.audio_clip_id} for link in self.project.av_links))

    def can_ungroup_selected_clips(self):
        return (not self.selection_edit_locked() and self.active_selection_type == 'clip'
                and any(clip_group(self.project, cid) for cid in self.selected_clip_ids))

    def group_selected_clips(self):
        if self._clip_group_focus_blocked() or not self.can_group_selected_clips():
            return
        self.timeline.cancel_gestures()
        self.edit(lambda: self.editor.group_clips(self.selected_clip_ids))

    def ungroup_selected_clips(self):
        if self._clip_group_focus_blocked() or not self.can_ungroup_selected_clips():
            return
        self.timeline.cancel_gestures()
        self.edit(lambda: self.editor.ungroup_clips(self.selected_clip_ids))

    def explain_grouped_trim(self):
        self._last_group_notice = str(tr(GROUP_TRIM_HELP))
        self.statusBar().showMessage(self._last_group_notice, 6000)

    def retranslate_clip_group_notice(self):
        if (getattr(self, '_last_group_notice', None)
                and self.statusBar().currentMessage() == self._last_group_notice):
            self.explain_grouped_trim()
