"""Shared, native-font-aware typography and form layout for the Qt editor.

The stylesheet supplies colours and states.  Geometry is based on the actual
font metrics, rather than a fixed pixel font or a fixed text height.  Forms keep
one label column while there is room, and become consistently stacked on narrow
panels.  No editor values, signals, or workspace visibility are changed here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRect, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QFormLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QWidget,
)

from .i18n import TranslatedText


STYLE = '''
QMainWindow, QWidget { background: #15191f; color: #e0e6ed; }
QLabel { background: transparent; font-weight: 400; }
QLabel[formLabel="true"] { color: #bfcbd8; font-weight: 400; }
QLabel[sectionHeading="true"] { color: #b5dcd1; font-weight: 600; }
QLabel[secondaryText="true"] { color: #aab8c8; }
QMenuBar, QMenu { background: #20262f; }
QMenuBar::item { padding: 5px 9px; }
QMenuBar::item:selected, QMenu::item:selected { background: #315750; }
QMenu::item { padding: 6px 22px; }
QToolBar { background: #20262f; border: 0; spacing: 8px; padding: 7px; }
QPushButton {
    background: #303946; border: 1px solid #465261; border-radius: 5px;
    padding: 6px 10px; font-weight: 400;
}
QPushButton:hover { background: #414f60; border-color: #687a8e; }
QPushButton:pressed { background: #26333d; }
QPushButton:focus { border-color: #9cddcc; }
QPushButton:disabled { color: #8b97a7; background: #222832; border-color: #35404b; }
QPushButton#primary { background: #327b6f; border-color: #58baa4; color: white; }
QPushButton#primary:hover { background: #3a8d7f; }
QPushButton#primary:disabled { color: #9ca9b5; background: #29443f; border-color: #405a54; }
QPushButton#workspaceMode:checked { background: #38564f; border-color: #79c4b2; color: #f1fff9; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
    background: #20262f; border: 1px solid #42505f; border-radius: 4px;
    padding: 5px 7px; selection-background-color: #386e62; selection-color: #ffffff;
    font-weight: 400;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color: #82cdb9; }
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    color: #8b97a7; background: #1b222a; border-color: #35404b;
}
QComboBox { padding-right: 22px; }
QComboBox::drop-down { width: 20px; border: 0; }
QComboBox::down-arrow { image: url("__DOWN_ARROW__"); width: 10px; height: 6px; }
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right; width: 20px;
    border-left: 1px solid #42505f; border-bottom: 1px solid #42505f;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right; width: 20px;
    border-left: 1px solid #42505f;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #354b48; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("__UP_ARROW__"); width: 8px; height: 5px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("__DOWN_ARROW__"); width: 8px; height: 5px; }
QComboBox QAbstractItemView { background: #242d38; selection-background-color: #315750; padding: 4px; }
QListWidget, QTableWidget { background: #20262f; border: 1px solid #384451; border-radius: 4px; }
QListWidget::item { padding: 8px; border-bottom: 1px solid #303946; }
QListWidget::item:selected { background: #315750; color: #f1fff9; }
QListWidget::item:focus { border: 1px solid #82cdb9; }
QHeaderView::section { background: #26313d; color: #c8d4df; padding: 6px 8px; border: 0; border-bottom: 1px solid #42505f; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: #1b222b; width: 12px; margin: 0; }
QScrollBar:horizontal { background: #1b222b; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #566575; min-height: 28px; border: 2px solid #1b222b; border-radius: 5px; }
QScrollBar::handle:horizontal { background: #566575; min-width: 28px; border: 2px solid #1b222b; border-radius: 5px; }
QScrollBar::handle:hover { background: #83b8aa; }
QScrollBar::handle:pressed { background: #9cddcc; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent; border: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QGroupBox {
    border: 1px solid #35414e; border-radius: 6px;
    margin-top: 12px; padding: 12px 4px 4px 4px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 3px; color: #cbd9e5; }
QTabWidget::pane { border: 1px solid #35414e; border-radius: 4px; }
QTabBar::tab { background: #242d38; color: #afbdcc; padding: 7px 11px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { background: #2b403b; color: #e3f7f0; border-bottom-color: #79c4b2; }
QTabBar::tab:hover:!selected { background: #303c49; color: #e0e6ed; }
QTabBar::tab:disabled { color: #7c8898; }
QCheckBox { spacing: 7px; font-weight: 400; }
QCheckBox:focus { color: #a8e5d3; }
QCheckBox:disabled { color: #8b97a7; }
QProgressBar { border: 1px solid #42505e; border-radius: 3px; text-align: center; background: #20262f; }
QProgressBar::chunk { background: #357c6d; }
QSlider::groove:horizontal { background: #35414c; height: 4px; }
QSlider::handle:horizontal { background: #68c7b1; width: 12px; margin: -5px 0; border-radius: 6px; }
QSlider::handle:horizontal:focus { background: #a8e5d3; }
QSplitter::handle { background: #1b222b; }
QSplitter::handle:hover { background: #425c57; }
QStatusBar { color: #acbac9; border-top: 1px solid #303b47; }
QStatusBar::item { border: 0; }
QToolTip { color: #eff6fb; background: #2b3643; border: 1px solid #627387; padding: 5px; }
'''

_ICON_DIRECTORY = Path(__file__).resolve().parent / 'assets' / 'ui'
STYLE = STYLE.replace('__DOWN_ARROW__', (_ICON_DIRECTORY / 'chevron-down.svg').as_posix())
STYLE = STYLE.replace('__UP_ARROW__', (_ICON_DIRECTORY / 'chevron-up.svg').as_posix())

_GENERIC_UI_FAMILIES = {'sans serif', 'sans-serif', 'system', 'default'}


def _resolve_native_ui_font(font: QFont) -> QFont:
    """Resolve Windows' generic offscreen alias to an installed UI family.

    The Windows offscreen platform can report "Sans Serif" instead of its
    installed UI face.  Its fallback metrics can differ substantially from the
    actual desktop font.  Keep explicit user families and secondary CJK fallback
    families; use no downloaded, bundled, or test-only font.
    """
    resolved = QFont(font)
    if sys.platform != 'win32' or resolved.family().casefold() not in _GENERIC_UI_FAMILIES:
        return resolved
    installed = {family.casefold(): family for family in QFontDatabase.families()}
    for candidate in ('Segoe UI', 'Arial'):
        if candidate.casefold() in installed:
            resolved.setFamilies([installed[candidate.casefold()], *resolved.families()[1:]])
            break
    return resolved


@dataclass(frozen=True)
class LayoutTokens:
    """Logical pixels derived from the resolved native UI font."""

    spacing: int
    label_width: int
    control_height: int
    column_breakpoint: int

    @classmethod
    def from_font(cls, font: QFont) -> 'LayoutTokens':
        metrics = QFontMetrics(font)
        line = metrics.lineSpacing()
        spacing = max(6, math.ceil(line / 2))
        return cls(
            spacing=spacing,
            label_width=math.ceil(line * 7.5),
            control_height=line + 2 * spacing,
            column_breakpoint=math.ceil(line * 22),
        )


class _FormLayoutController(QObject):
    """Reflow labels after layout/resize/font changes, without touching values."""

    def __init__(self, root: QWidget):
        super().__init__(root)
        self.root = root
        self.forms: list[QFormLayout] = []
        self._watched: set[QWidget] = set()
        self._pending = False
        self._refreshing = False
        self._queued_refresh = False
        root.installEventFilter(self)
        self.refresh()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ToolTip and isinstance(watched, QComboBox):
            self._refresh_combo_tip(watched)
        if event.type() in (
            QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show,
            QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange,
        ):
            self.schedule()
        return False

    def schedule(self):
        if not self._pending and not self._refreshing:
            self._pending = True
            QTimer.singleShot(0, self, self._refresh_scheduled)

    def refresh(self):
        # Construction and explicit callers retain the full eager pass, even
        # before a window is shown or while no child window is visible.
        # Consume the dispatch hint before work: reentrant explicit refreshes
        # must not inherit the scheduled pass's visibility scope.
        queued = self._queued_refresh
        self._queued_refresh = False
        self._refresh(queued=queued)

    def _refresh_scheduled(self):
        # Decide from visibility at execution, never from enqueue-time state.
        # Keep the QObject context and zero-argument bound callback unchanged.
        # Public refresh remains the dispatch point for observers/overrides.
        previous = self._queued_refresh
        self._queued_refresh = True
        try:
            self.refresh()
        finally:
            self._queued_refresh = previous

    @staticmethod
    def _inside_scopes(widget, scopes):
        if scopes is None:
            return True
        # QWidget.isAncestorOf stops at child-window boundaries. Ownership
        # still includes nested dialogs/popups, even while those are hidden.
        while widget is not None:
            if widget in scopes:
                return True
            widget = widget.parentWidget()
        return False

    def _scheduled_scopes(self):
        # Dialogs may store their owner in a Python attribute named window.
        if not self.root.isWindow() or QWidget.window(self.root).isVisible():
            # Non-window roots retain full work: showing their containing
            # window need not send Show to a still-hidden controlled tab.
            # Hidden tabs still constrain a visible window's minimum geometry.
            return None
        windows = [widget for widget in self.root.findChildren(QWidget)
                   if widget.isWindow() and widget.isVisible()]
        # No visible child retains the previous full hidden-window behaviour.
        # Otherwise include each selected window's whole subtree, not merely
        # widgets whose own visibility happens to be true. This includes hidden
        # nested child windows owned by a selected visible window.
        if not windows:
            return None
        return [window for window in windows
                if not self._inside_scopes(window.parentWidget(), windows)]

    def _refresh(self, *, queued=False):
        self._pending = False
        self._refreshing = True
        try:
            tokens = LayoutTokens.from_font(self.root.font())
            ownership = {}
            for scroll in self.root.findChildren(QScrollArea):
                if not self._owns(scroll, ownership):
                    continue
                viewport = scroll.viewport()
                if viewport not in self._watched:
                    viewport.installEventFilter(self)
                    self._watched.add(viewport)
            self.forms = [form for form in self.root.findChildren(QFormLayout)
                          if form.parentWidget() is not None and self._owns(form.parentWidget(), ownership)]
            scopes = self._scheduled_scopes() if queued else None
            forms = (self.forms if scopes is None else
                     [form for form in self.forms if form.parentWidget() is not None
                      and self._inside_scopes(form.parentWidget(), scopes)])
            selected_forms = set(forms)
            # Reflow descendants before forms containing their sections.
            for form in reversed(self.forms):
                host = form.parentWidget()
                if host is None:
                    continue
                if host not in self._watched:
                    host.installEventFilter(self)
                    self._watched.add(host)
                if form in selected_forms:
                    self._format_form(form, tokens)
                    ownership.clear()
            # Formatting can synchronously create a child governor or reparent
            # controls. Never carry read-only ancestry results across Qt writes.
            ownership.clear()
            # Preserve post-form discovery of controls that Qt creates lazily
            # during formatting; complete form/viewport watching is above.
            for control in self.root.findChildren(QWidget):
                if not self._owns(control, ownership):
                    continue
                if not self._inside_scopes(control, scopes):
                    continue
                # Fixed-height timeline headers contain compact, keyboard-
                # accessible controls; body-form minimums overflow those rows.
                if isinstance(control, QPushButton) and control.property('compactTimelineControl'):
                    continue
                # Stylesheet font-weight rules can otherwise retain the 9pt
                # application font despite a new font on the window.  Resolve
                # the body point size explicitly while retaining each widget's
                # family/weight and any intentional inline heading/note size.
                if 'font-size' not in control.styleSheet():
                    font = QFont(control.font())
                    changed = False
                    if (font.family().casefold() in _GENERIC_UI_FAMILIES
                            and self.root.font().family().casefold() not in _GENERIC_UI_FAMILIES):
                        font.setFamilies(self.root.font().families())
                        changed = True
                    if font.pointSizeF() != self.root.font().pointSizeF():
                        font.setPointSizeF(self.root.font().pointSizeF())
                        changed = True
                    if changed:
                        control.setFont(font)
                        ownership.clear()
                if isinstance(control, (QLineEdit, QAbstractSpinBox, QComboBox, QPushButton)):
                    # Spinbox editors inherit their parent's height and must not
                    # add a second minimum-height constraint inside its frame.
                    if isinstance(control, QLineEdit) and isinstance(control.parentWidget(), QAbstractSpinBox):
                        continue
                    control_tokens = LayoutTokens.from_font(control.font())
                    if not hasattr(control, '_lumen_original_min_height'):
                        control._lumen_original_min_height = control.minimumHeight()
                    minimum = max(control._lumen_original_min_height, control_tokens.control_height)
                    if control.minimumHeight() != minimum:
                        control.setMinimumHeight(minimum)
                        ownership.clear()
                if isinstance(control, QComboBox):
                    self._format_combo(control)
                    ownership.clear()
                if (isinstance(control, QLabel) and not control.wordWrap()
                        and len(control.text()) > 24 and self._enclosing_scroll(control)):
                    # Explanatory text must not establish the entire inspector's
                    # minimum width (native font advances vary considerably).
                    control.setWordWrap(True)
                    ownership.clear()
            self._fit_label_heights(tokens, forms)
            self._propagate_form_minimums(forms)
        finally:
            self._refreshing = False

    def _owns(self, widget, cache=None):
        # Memoize ancestry only within one refresh. Shared parent chains should
        # be walked once, while later reparenting/new child governors must be
        # observed on the next pass. Callers also clear after synchronous Qt
        # mutations, which can change ancestry/ownership inside this pass.
        cache = {} if cache is None else cache
        trail = []
        while widget is not None:
            if widget in cache:
                owned = cache[widget]
                break
            if widget is self.root:
                owned = True
                break
            trail.append(widget)
            if getattr(widget, '_lumen_layout_controller', None) is not None:
                owned = False
                break
            widget = widget.parentWidget()
        else:
            owned = False
        for ancestor in trail:
            cache[ancestor] = owned
        return owned

    def _propagate_form_minimums(self, forms=None):
        """Invalidate stale hidden-tab QWidgetItem caches only after a change.

        A nested QBoxLayout can retain a section's old two-column minimum even
        after its form has stacked. Hidden tab pages still constrain every tab.
        Propagate the actual minima bottom-up; the stored signature prevents
        LayoutRequest events from producing a perpetual refresh loop.
        """
        affected_scrolls = set()
        for form in reversed(self.forms if forms is None else forms):
            host = form.parentWidget()
            if host is None:
                continue
            minimum = (host.minimumSizeHint().toTuple(), form.minimumSize().toTuple())
            if getattr(form, '_lumen_minimum_signature', None) == minimum:
                continue
            form._lumen_minimum_signature = minimum
            widget = host
            while widget is not None:
                layout = widget.layout()
                if layout is not None:
                    layout.invalidate()
                widget.updateGeometry()
                if isinstance(widget, QScrollArea) and self._owns(widget):
                    affected_scrolls.add(widget)
                widget = widget.parentWidget()

        def depth(widget):
            result = 0
            while widget is not None:
                result += 1
                widget = widget.parentWidget()
            return result

        # A scroll area otherwise keeps its old two-column body width until
        # the *next* event-loop turn, even though the form has already stacked.
        # Publish just these layout changes, inner scrolls before outer ones.
        # Do not pump the event queue: worker or playback callbacks must not
        # reenter layout, and unrelated windows/independent governors stay alone.
        for scroll in sorted((scroll for scroll in affected_scrolls if isValid(scroll)),
                             key=depth, reverse=True):
            if isValid(scroll) and self._owns(scroll):
                QCoreApplication.sendEvent(scroll, QEvent(QEvent.Type.LayoutRequest))

    def _fit_label_heights(self, tokens: LayoutTokens, forms=None):
        # QFormLayout can calculate a stacked label's height for the entire
        # row, then allocate only its smaller sizeHint width. Mixed CJK/Latin
        # fallback fonts also sometimes need one extra pixel beyond lineSpacing.
        # Measure the actual allocated text width to avoid clipping either case.
        for form in (self.forms if forms is None else forms):
            stacked = form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapAllRows
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                label = item.widget() if item else None
                if not isinstance(label, QLabel):
                    continue
                margins = label.contentsMargins()
                width = max(1, label.width() - margins.left() - margins.right() - 2 * label.margin())
                bounds = label.fontMetrics().boundingRect(
                    QRect(0, 0, width, 100000),
                    Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs | Qt.TextFlag.TextShowMnemonic,
                    label.text(),
                )
                required = bounds.height() + margins.top() + margins.bottom() + 2 * label.margin()
                minimum = max(QFontMetrics(label.font()).height() if stacked else tokens.control_height, required)
                if label.minimumHeight() != minimum:
                    label.setMinimumHeight(minimum)

    def _format_combo(self, combo: QComboBox):
        if not hasattr(combo, '_lumen_original_min_width'):
            combo._lumen_original_min_width = combo.minimumWidth()
        minimum = max(combo._lumen_original_min_width, QFontMetrics(combo.font()).lineSpacing() *
                      (4 if combo.property('compactMediaControl') else 7))
        if combo.minimumWidth() != minimum:
            combo.setMinimumWidth(minimum)
        if combo.property('layoutTokensApplied'):
            self._refresh_combo_tip(combo)
            return
        combo.setProperty('layoutTokensApplied', True)
        # A descriptive selected item must not dictate the minimum width of
        # every tab.  The popup and tooltip retain the complete text.
        combo.setMinimumContentsLength(min(combo.minimumContentsLength() or 12, 12))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        # Qt may retain its original content-based minimumSizeHint even after
        # changing SizeAdjustPolicy.  Ignore that stale hint, with an explicit
        # font-relative usable minimum instead of allowing it to force overflow.
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo._lumen_original_tooltip = getattr(combo, '_translation_bindings', {}).get(
            'setToolTip', combo.toolTip())
        combo.installEventFilter(self)
        combo.currentTextChanged.connect(lambda *_: self._refresh_combo_tip(combo))
        # Model signals still run while callers block the combo's own signals
        # during list refresh.  Hover also reads the live current text, covering
        # a signal-blocked index change with no layout/model change at all.
        for signal in (combo.model().rowsInserted, combo.model().rowsRemoved,
                       combo.model().modelReset, combo.model().dataChanged):
            signal.connect(self.schedule)
        self._refresh_combo_tip(combo)

    @staticmethod
    def _refresh_combo_tip(combo: QComboBox):
        text = combo.currentText()
        original_tip = getattr(combo, '_translation_bindings', {}).get(
            'setToolTip', getattr(combo, '_lumen_original_tooltip', ''))
        if isinstance(original_tip, TranslatedText):
            original_tip = original_tip.render()
        tooltip = f'{text}\n\n{original_tip}' if original_tip and text else (text or original_tip)
        if combo.toolTip() != tooltip:
            # Composing the selected-item hint is presentation-only. Calling
            # the translated wrapper would discard its explicit source binding.
            QComboBox.setToolTip(combo, tooltip)

    @staticmethod
    def _enclosing_scroll(widget: QWidget):
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    @classmethod
    def _form_available_width(cls, form: QFormLayout):
        host = form.parentWidget()
        available = form.contentsRect().width()
        if available <= 0:
            available = host.contentsRect().width()
        scroll = cls._enclosing_scroll(host)
        if scroll is None or scroll.widget() is None:
            return available
        content = scroll.widget()
        if form.geometry().isValid() and host.isVisibleTo(content):
            left = host.mapTo(content, form.contentsRect().topLeft()).x()
            right = max(0, content.width() - left - form.contentsRect().width())
            return min(available, max(1, scroll.viewport().width() - left - right))

        # Hidden tab pages often retain their initial 640px geometry. Their
        # minimum sizes still contribute to QStackedWidget, so they must reflow
        # using the real scroll viewport before they have ever been selected.
        margins = form.contentsMargins()
        inset = margins.left() + margins.right()
        widget = host
        while widget is not None:
            margins = widget.contentsMargins()
            inset += margins.left() + margins.right()
            layout = widget.layout()
            if layout is not None and layout is not form:
                margins = layout.contentsMargins()
                inset += margins.left() + margins.right()
            if widget is content:
                break
            widget = widget.parentWidget()
        return min(available, max(1, scroll.viewport().width() - inset))

    @classmethod
    def _format_form(cls, form: QFormLayout, tokens: LayoutTokens):
        # Use the layout's own available width so nested forms and group boxes
        # respond to their actual inset instead of the main window's width.
        available = cls._form_available_width(form)
        widest_field = max((form.itemAt(row, QFormLayout.ItemRole.FieldRole).minimumSize().width()
                            for row in range(form.rowCount())
                            if form.itemAt(row, QFormLayout.ItemRole.LabelRole) is not None
                            and form.itemAt(row, QFormLayout.ItemRole.FieldRole) is not None), default=0)
        required = max(tokens.column_breakpoint, tokens.label_width + tokens.spacing + widest_field)
        stacked = available < required
        state = (tokens, stacked, form.rowCount())
        if getattr(form, '_lumen_layout_state', None) == state:
            return
        form._lumen_layout_state = state
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        if form.horizontalSpacing() != tokens.spacing:
            form.setHorizontalSpacing(tokens.spacing)
        if form.verticalSpacing() != tokens.spacing:
            form.setVerticalSpacing(tokens.spacing)
        wrap = (QFormLayout.RowWrapPolicy.WrapAllRows if stacked
                else QFormLayout.RowWrapPolicy.DontWrapRows)
        if form.rowWrapPolicy() != wrap:
            form.setRowWrapPolicy(wrap)

        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            label = item.widget() if item else None
            if not isinstance(label, QLabel):
                continue
            if not label.property('formLabel'):
                label.setProperty('formLabel', True)
                label.style().unpolish(label)
                label.style().polish(label)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            padding = 0 if stacked else tokens.spacing
            label.setContentsMargins(0, padding, 0, padding)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            minimum = 0 if stacked else tokens.label_width
            maximum = 16777215 if stacked else tokens.label_width
            if label.minimumWidth() != minimum:
                label.setMinimumWidth(minimum)
            if label.maximumWidth() != maximum:
                label.setMaximumWidth(maximum)
            # First-line text aligns with an editor's text. Long labels retain
            # QLabel's height-for-width and grow without moving that baseline.
            height = QFontMetrics(label.font()).lineSpacing() if stacked else tokens.control_height
            if label.minimumHeight() != height:
                label.setMinimumHeight(height)
        form.invalidate()


def apply_ui_layout_tokens(root: QWidget) -> None:
    """Apply once after building a window; safe to call again after new forms.

    Keep the operating system's UI font family and Qt's automatic CJK fallback.
    Point sizes respect display scaling; a larger app/user font is preserved.
    """
    controller = getattr(root, '_lumen_layout_controller', None)
    if controller is not None:
        controller.refresh()
        return
    font = QFont(root.font())
    if font.pointSizeF() <= 0:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font = _resolve_native_ui_font(font)
    font.setPointSizeF(max(10.0, font.pointSizeF()))
    root.setFont(font)
    root._lumen_layout_controller = _FormLayoutController(root)
