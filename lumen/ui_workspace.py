"""Presentation-only workspace modes; never part of a project or render graph.

Both modes keep the same widgets and editor. Hidden advanced controls retain
their values and staged edits. QSettings stores only this application's layout
preference, independently of project files and undo history.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout
from .i18n_widgets import QLabel, QPushButton, QWidget
from .i18n import tr

from .model import Grade


WORKSPACE_MODES = ('simple', 'professional')
BASIC_GRADE_FIELDS = (
    'linear_exposure', 'contrast', 'contrast_pivot', 'saturation', 'temperature', 'tint',
    'shadows', 'highlights',
)
ADVANCED_GRADE_FIELDS = (
    'exposure', 'lift', 'gamma', 'gain', 'master_curve', 'red_curve',
    'green_curve', 'blue_curve', 'hue_hue_curve', 'hue_sat_curve', 'qualifier', 'lut_path',
)


def hidden_settings_summary(project):
    """Describe retained project settings honestly, including bypassed values."""
    default = Grade()
    active_color = bypassed_color = enabled_effects = disabled_effects = 0
    animated = hidden_animation = input_normalization = 0
    media = {item.id: item for item in project.media}
    for clip in project.clips:
        source = media.get(clip.media_id)
        # An audio/music clip can retain old color data, but the picture
        # renderer never applies it. Do not claim those settings are active.
        if not source or not source.has_video or clip.track >= 2:
            continue
        advanced = any(getattr(clip.grade, key) != getattr(default, key)
                       for key in ADVANCED_GRADE_FIELDS)
        if advanced:
            if clip.grade.enabled:
                active_color += 1
            else:
                bypassed_color += 1
        # Input normalization is independent of creative grade bypass when
        # present in the color engine. Older projects use the Rec.709 default.
        if getattr(clip.grade, 'input_encoding', 'rec709') != 'rec709':
            input_normalization += 1
        for effect in clip.effects:
            if effect.enabled:
                enabled_effects += 1
            else:
                disabled_effects += 1
    for overlay in project.overlays:
        if len(overlay.keyframes) > 1:
            if overlay.enabled:
                animated += 1
            else:
                hidden_animation += 1
    active = []
    stored = []
    if active_color:
        active.append(tr('advanced color / LUT on {count} clip(s)', count=active_color))
    if input_normalization:
        active.append(tr('input normalization on {count} clip(s)', count=input_normalization))
    if enabled_effects:
        active.append(tr('{count} enabled clip effect(s)', count=enabled_effects))
    if animated:
        active.append(tr('{count} animated sticker(s)', count=animated))
    if bypassed_color:
        stored.append(tr('{count} bypassed advanced grade(s)', count=bypassed_color))
    if disabled_effects:
        stored.append(tr('{count} disabled clip effect(s)', count=disabled_effects))
    if hidden_animation:
        stored.append(tr('{count} hidden sticker animation(s)', count=hidden_animation))
    parts = []
    if active:
        parts.append(tr('Still used in After preview and export: {settings}.', settings='; '.join(active)))
    if stored:
        parts.append(tr('Also preserved: {settings}.', settings='; '.join(stored)))
    return ' '.join(parts)


class WorkspaceModeMixin:
    def init_workspace(self, settings=None):
        self.workspace_settings = settings if settings is not None else QSettings('Lumen Edit', 'Workspace')
        saved = self.workspace_settings.value('mode', 'simple')
        self.workspace_mode = saved if saved in WORKSPACE_MODES else 'simple'
        self._professional_color_tab = 0
        self._professional_effects_panel_selected = False

    def build_workspace_switch(self):
        control = QWidget()
        layout = QHBoxLayout(control)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(QLabel(tr('Workspace')))
        self.workspace_buttons = {}
        self.workspace_button_group = QButtonGroup(control)
        self.workspace_button_group.setExclusive(True)
        hints = {
            'simple': tr('Editing, basic color, captions, local voices and stickers. Advanced project settings are preserved.'),
            'professional': tr('Also show color wheels, curves, LUTs, scopes, ordered effects and sticker keyframes.'),
        }
        for mode in WORKSPACE_MODES:
            button = QPushButton(tr('Simple') if mode == 'simple' else tr('Professional'))
            button.setObjectName('workspaceMode')
            button.setCheckable(True)
            button.setAccessibleName(tr('Simple workspace') if mode == 'simple' else tr('Professional workspace'))
            button.setToolTip(tr('{hint} Switching never changes the project or export.', hint=hints[mode]))
            button.clicked.connect(lambda checked=False, mode=mode: self.set_workspace_mode(mode))
            self.workspace_button_group.addButton(button)
            self.workspace_buttons[mode] = button
            layout.addWidget(button)
        return control

    def build_workspace_notice(self):
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_note = QLabel()
        self.workspace_note.setWordWrap(True)
        self.workspace_note.setStyleSheet('color: #aabcca; font-size: 9pt;')
        layout.addWidget(self.workspace_note, 1, Qt.AlignmentFlag.AlignTop)
        self.advanced_settings_notice = QLabel()
        self.advanced_settings_notice.setWordWrap(True)
        self.advanced_settings_notice.setStyleSheet('color: #eed39c; font-size: 9pt;')
        layout.addWidget(self.advanced_settings_notice, 2, Qt.AlignmentFlag.AlignTop)
        return panel

    def finish_workspace_ui(self):
        # The existing detail toggles retain their checked state in Simple.
        # This final handler ensures a hidden section cannot be revealed there.
        self.color_details_toggle.toggled.connect(self.sync_workspace_details)
        self.keyframe_toggle.toggled.connect(self.sync_workspace_details)
        self.set_workspace_mode(self.workspace_mode, persist=False)

    def set_workspace_mode(self, mode, *, persist=True):
        """Only change visibility. No Editor commands, refresh, or render work."""
        if mode not in WORKSPACE_MODES:
            raise ValueError('Workspace mode must be simple or professional.')
        if self.workspace_mode == 'professional' and mode == 'simple':
            self._professional_color_tab = self.color_tabs.currentIndex()
            self._professional_effects_panel_selected = (
                self.side_tabs.currentWidget() is self.clip_effects_panel)
        returning_to_professional = self.workspace_mode != mode and mode == 'professional'
        self.workspace_mode = mode
        professional = mode == 'professional'
        self.workspace_buttons[mode].setChecked(True)
        # Keep widget instances and stable tab indices, including staged values.
        for index in (1, 2):
            self.color_tabs.setTabVisible(index, professional)
        self.color_tabs.tabBar().setVisible(professional)
        # Effects and stickers are peers, never a nested or combined list.
        # When Simple hides the current Effects panel, show Stickers and retain
        # the Professional destination without reloading either panel's values.
        if not professional and self.side_tabs.currentWidget() is self.clip_effects_panel:
            self.side_tabs.setCurrentWidget(self.stickers_panel)
        self.side_tabs.setTabVisible(self.side_tabs.indexOf(self.clip_effects_panel), professional)
        self.scopes.setVisible(professional)
        self.input_color_group.setVisible(professional)
        self.lut_controls.setVisible(professional)
        self.qualifier_button.setVisible(professional)
        self.window_animation_button.setVisible(professional)
        self.color_details_toggle.setVisible(professional)
        self.keyframe_toggle.setVisible(professional)
        self.sync_workspace_details()
        self.reset_color_button.setText(tr('Reset all color') if professional else tr('Reset basic color'))
        self.reset_color_button.setToolTip(
            tr('Reset creative color, including advanced controls and LUT. Source input interpretation is preserved.') if professional
            else tr('Reset only the basic color controls, including contrast pivot. Advanced color, LUT and input settings are preserved.'))
        if returning_to_professional:
            self.color_tabs.setCurrentIndex(self._professional_color_tab)
            if self._professional_effects_panel_selected:
                self.side_tabs.setCurrentWidget(self.clip_effects_panel)
        self.refresh_workspace_notice()
        if persist:
            self.workspace_settings.setValue('mode', mode)
            self.workspace_settings.sync()
            if self.workspace_settings.status() != QSettings.Status.NoError:
                self.statusBar().showMessage(tr('Workspace changed for this session; the preference could not be saved.'), 10000)

    def sync_workspace_details(self, *_):
        professional = self.workspace_mode == 'professional'
        self.color_details.setVisible(professional and self.color_details_toggle.isChecked())
        self.keyframe_panel.setVisible(professional and self.keyframe_toggle.isChecked())

    def refresh_workspace_notice(self):
        simple = self.workspace_mode == 'simple'
        self.workspace_note.setText(
            tr('Simple · edit, basic color, captions, local voices and stickers. '
            'Professional shows advanced controls; switching preserves every edit.') if simple else
            tr('Professional · wheels, curves, LUTs, SDR scopes, clip effects and sticker keyframes. '
            'Same project and render engine as Simple.'))
        summary = hidden_settings_summary(self.project) if simple else ''
        self.advanced_settings_notice.setText(tr('{summary} Switch to Professional to inspect.', summary=summary) if summary else '')
        self.advanced_settings_notice.setVisible(bool(summary))
