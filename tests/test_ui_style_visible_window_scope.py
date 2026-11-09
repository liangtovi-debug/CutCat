"""Queued form reflow scopes work, not visibility or application values.

These are real offscreen Qt controls, not performance thresholds or native UI
acceptance. Existing full-tree layout assertions remain in test_ui_style.py.
"""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if __name__ == '__main__':
    # Fresh-process DPR capture imports the same candidate as the parent test.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QImage, QTextLayout
from PySide6.QtWidgets import (
    QApplication, QDialog, QFormLayout, QGroupBox, QLineEdit, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)
from shiboken6 import delete, isValid

from lumen.i18n import get_locale, set_locale, tr
from lumen.i18n_widgets import QComboBox, QLabel
import lumen.ui_style as style


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def settle(app):
    # Same bounded event settling as existing layout controls, no timed waits.
    for _ in range(12):
        app.processEvents()


def add_form(parent, title):
    host = QGroupBox(title, parent)
    form = QFormLayout(host)
    number = QSpinBox()
    number.setRange(0, 10000)
    number.setValue(1234)
    editor = QLineEdit('User text / 用户内容')
    combo = QComboBox()
    combo.addItems([tr('Deblock'), tr('Denoise')])
    combo.setToolTip(tr('Deblock and denoise can soften real detail. Sharpening can amplify noise and halos. Start with low strengths.'))
    label = QLabel(tr('Timeline start'))
    form.addRow(label, number)
    form.addRow('Long descriptive label / 中文说明', editor)
    form.addRow('Source processing', combo)
    parent.layout().addWidget(host)
    return SimpleNamespace(host=host, form=form, number=number, editor=editor,
                           combo=combo, label=label)


def add_dialog(parent, title):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    QVBoxLayout(dialog)
    entry = add_form(dialog, title)
    dialog.resize(600, 500)
    return dialog, entry


@pytest.fixture
def scene(app):
    root = QWidget()
    root.setStyleSheet(style.STYLE)
    QVBoxLayout(root)
    main = add_form(root, 'Main window form')
    dialog, child = add_dialog(root, 'Child dialog without own controller')
    style.apply_ui_layout_tokens(root)
    root.resize(700, 600)
    yield SimpleNamespace(root=root, main=main, dialog=dialog, child=child,
                          controller=root._lumen_layout_controller)
    if isValid(root):
        root.close()
        delete(root)
    settle(app)


def record_work(monkeypatch, controller):
    work = SimpleNamespace(forms=[], fitted=[], propagated=[], combos=[])
    original_format = controller._format_form
    original_fit = controller._fit_label_heights
    original_propagate = controller._propagate_form_minimums
    original_combo = controller._format_combo

    def format_form(form, tokens):
        work.forms.append(form)
        return original_format(form, tokens)

    def fit(tokens, forms=None):
        work.fitted.append(tuple(controller.forms if forms is None else forms))
        return original_fit(tokens, forms)

    def propagate(forms=None):
        work.propagated.append(tuple(controller.forms if forms is None else forms))
        return original_propagate(forms)

    def combo(control):
        work.combos.append(control)
        return original_combo(control)

    monkeypatch.setattr(controller, '_format_form', format_form)
    monkeypatch.setattr(controller, '_fit_label_heights', fit)
    monkeypatch.setattr(controller, '_propagate_form_minimums', propagate)
    monkeypatch.setattr(controller, '_format_combo', combo)
    return work


def assert_work(work, forms, combos):
    assert set(work.forms) == set(forms)
    assert len(work.forms) == len(forms)
    assert work.fitted == [tuple(forms)]
    assert work.propagated == [tuple(forms)]
    assert set(work.combos) == set(combos)
    assert len(work.combos) == len(combos)


def test_constructor_and_explicit_refresh_keep_full_hidden_tree(scene, app, monkeypatch):
    s = scene
    assert not s.root.isVisible() and not s.dialog.isVisible()
    for entry in (s.main, s.child):
        assert entry.label.property('formLabel')
        assert entry.combo.property('layoutTokensApplied')
        assert entry.host in s.controller._watched
    s.dialog.show()
    settle(app)
    work = record_work(monkeypatch, s.controller)
    style.apply_ui_layout_tokens(s.root)
    assert_work(work, [s.main.form, s.child.form], [s.main.combo, s.child.combo])
    assert not hasattr(s.dialog, '_lumen_layout_controller')


@pytest.mark.parametrize('observer', ['instance_wrapper', 'class_override'])
def test_real_queued_refresh_uses_late_bound_public_dispatch(scene, app, monkeypatch, observer):
    s = scene
    s.dialog.show()
    settle(app)
    controller = s.controller
    refresh = controller.refresh
    calls = []
    work = record_work(monkeypatch, controller)
    values = [(e.number.value(), e.editor.text(), e.combo.currentIndex())
              for e in (s.main, s.child)]
    controller.schedule()
    controller.schedule()
    assert controller._pending
    # Replace the public dispatch only AFTER enqueue, before the real Qt event.
    if observer == 'instance_wrapper':
        def observed():
            calls.append(controller)
            return refresh()
        monkeypatch.setattr(controller, 'refresh', observed)
    else:
        def observed(self):
            calls.append(self)
            return refresh()
        monkeypatch.setattr(type(controller), 'refresh', observed)
    settle(app)
    assert calls == [controller]
    assert_work(work, [s.child.form], [s.child.combo])
    assert not controller._pending and not controller._refreshing
    assert not controller._queued_refresh
    settled = (len(calls), len(work.forms))
    for _ in range(80):
        app.processEvents()
    assert (len(calls), len(work.forms)) == settled
    assert [(e.number.value(), e.editor.text(), e.combo.currentIndex())
            for e in (s.main, s.child)] == values


def test_explicit_refresh_with_pending_callback_still_visits_full_tree(scene, app, monkeypatch):
    s = scene
    s.dialog.show()
    settle(app)
    queued = []
    monkeypatch.setattr(style, 'QTimer', SimpleNamespace(singleShot=lambda *args: queued.append(args)))
    s.controller.schedule()
    assert s.controller._pending and len(queued) == 1
    work = record_work(monkeypatch, s.controller)
    s.controller.refresh()
    assert_work(work, [s.main.form, s.child.form], [s.main.combo, s.child.combo])
    for records in vars(work).values():
        records.clear()
    queued[0][2]()
    assert_work(work, [s.child.form], [s.child.combo])
    assert not s.controller._pending and not s.controller._refreshing
    assert not getattr(s.controller, '_queued_refresh', False)


def test_explicit_reentrant_refresh_does_not_inherit_queued_scope(scene, app, monkeypatch):
    s = scene
    s.dialog.show()
    settle(app)
    refresh = s.controller._refresh
    scopes = []
    work = record_work(monkeypatch, s.controller)

    def observed(*, queued=False):
        scopes.append(queued)
        if queued:
            s.controller.refresh()
        return refresh(queued=queued)

    monkeypatch.setattr(s.controller, '_refresh', observed)
    s.controller._refresh_scheduled()
    assert scopes == [True, False]
    assert work.forms.count(s.main.form) == 1
    assert work.forms.count(s.child.form) == 2
    assert not s.controller._pending and not s.controller._refreshing
    assert not getattr(s.controller, '_queued_refresh', False)


def test_nested_scheduled_public_override_restores_outer_dispatch_context(scene, app, monkeypatch):
    s = scene
    s.dialog.show()
    settle(app)
    refresh = s.controller.refresh
    public_calls = []
    nesting = False
    work = record_work(monkeypatch, s.controller)

    def observed():
        nonlocal nesting
        public_calls.append(nesting)
        if not nesting:
            nesting = True
            try:
                s.controller._refresh_scheduled()
            finally:
                nesting = False
        return refresh()

    monkeypatch.setattr(s.controller, 'refresh', observed)
    s.controller._refresh_scheduled()
    assert public_calls == [False, True]
    assert work.forms == [s.child.form, s.child.form]
    assert work.fitted == [(s.child.form,), (s.child.form,)]
    assert work.propagated == [(s.child.form,), (s.child.form,)]
    assert not s.controller._pending and not s.controller._refreshing
    assert not s.controller._queued_refresh


@pytest.mark.parametrize('failure_site', ['public_override', 'private_work'])
def test_scheduled_exception_does_not_leak_context_into_next_explicit_refresh(scene, app, monkeypatch, failure_site):
    s = scene
    s.dialog.show()
    settle(app)
    refresh = s.controller.refresh
    format_form = s.controller._format_form

    def fail(*args):
        raise RuntimeError('deliberate dispatch regression probe')

    if failure_site == 'public_override':
        monkeypatch.setattr(s.controller, 'refresh', fail)
    else:
        monkeypatch.setattr(s.controller, '_format_form', fail)
    # Invoke the same zero-argument callback synchronously so the expected
    # exception is asserted rather than swallowed by Qt's event delivery.
    with pytest.raises(RuntimeError, match='deliberate dispatch regression probe'):
        s.controller._refresh_scheduled()
    assert not getattr(s.controller, '_queued_refresh', False)
    assert not s.controller._refreshing
    # A public override that fails before private work need not clear an
    # already-pending request; this test asserts context restoration instead.
    monkeypatch.setattr(s.controller, 'refresh', refresh)
    monkeypatch.setattr(s.controller, '_format_form', format_form)
    work = record_work(monkeypatch, s.controller)
    s.controller.refresh()
    assert_work(work, [s.main.form, s.child.form], [s.main.combo, s.child.combo])
    assert not s.controller._pending and not s.controller._refreshing


def test_hidden_root_queued_work_scopes_helpers_controls_but_not_inventory(scene, app, monkeypatch):
    s = scene
    s.dialog.show()
    settle(app)
    # New hidden forms/scrolls must still be discovered and watched, although
    # their formatting waits for a full pass or a visible containing window.
    extra = add_form(s.root, 'New hidden form')
    scroll = QScrollArea(s.root)
    s.root.layout().addWidget(scroll)
    scroll.setWidget(QWidget())
    work = record_work(monkeypatch, s.controller)
    s.controller._refresh_scheduled()
    assert_work(work, [s.child.form], [s.child.combo])
    assert set(s.controller.forms) == {s.main.form, s.child.form, extra.form}
    assert extra.host in s.controller._watched
    assert scroll.viewport() in s.controller._watched
    assert not extra.combo.property('layoutTokensApplied')
    assert not s.root.isVisible() and s.dialog.isVisible()


def test_multiple_nested_windows_and_hidden_tabs_are_included_once(scene, app, monkeypatch):
    s = scene
    second, second_entry = add_dialog(s.root, 'Second window')
    nested, nested_entry = add_dialog(s.dialog, 'Nested window')
    hidden_window, hidden_window_entry = add_dialog(s.dialog, 'Hidden owned window')
    tabs = QTabWidget(s.dialog)
    s.dialog.layout().addWidget(tabs)
    tabs.addTab(QWidget(), 'Visible page')
    hidden_page = QWidget()
    QVBoxLayout(hidden_page)
    hidden_entry = add_form(hidden_page, 'Never selected page')
    tabs.addTab(hidden_page, 'Hidden page')
    for window in (s.dialog, second, nested):
        window.show()
    settle(app)
    assert not hidden_page.isVisible()
    assert s.controller._inside_scopes(nested_entry.host, [s.dialog])
    work = record_work(monkeypatch, s.controller)
    s.controller._refresh_scheduled()
    expected = [form for form in s.controller.forms if form is not s.main.form]
    assert_work(work, expected, [s.child.combo, second_entry.combo,
                                 nested_entry.combo, hidden_window_entry.combo, hidden_entry.combo])
    assert hidden_entry.label.property('formLabel')
    assert hidden_window_entry.label.property('formLabel')
    assert not hidden_window.isVisible()
    assert not hidden_page.isVisible()


def test_no_visible_descendant_preserves_full_legacy_fallback(scene, monkeypatch):
    s = scene
    work = record_work(monkeypatch, s.controller)
    s.controller._refresh_scheduled()
    assert_work(work, [s.main.form, s.child.form], [s.main.combo, s.child.combo])


def test_visible_root_keeps_hidden_child_forms_in_full_pass(scene, app, monkeypatch):
    s = scene
    s.root.show()
    settle(app)
    assert not s.dialog.isVisible()
    work = record_work(monkeypatch, s.controller)
    s.controller._refresh_scheduled()
    assert_work(work, [s.main.form, s.child.form], [s.main.combo, s.child.combo])


def test_visible_containing_window_not_hidden_controller_root_decides_scope(app, monkeypatch):
    outer = QWidget()
    QVBoxLayout(outer)
    tabs = QTabWidget(outer)
    outer.layout().addWidget(tabs)
    tabs.addTab(QWidget(), 'Active')
    page = QWidget()
    QVBoxLayout(page)
    entry = add_form(page, 'Hidden controller root')
    tabs.addTab(page, 'Hidden')
    dialog, child = add_dialog(page, 'Child')
    style.apply_ui_layout_tokens(page)
    outer.show()
    dialog.show()
    settle(app)
    try:
        assert not page.isVisible() and page.window().isVisible()
        controller = page._lumen_layout_controller
        work = record_work(monkeypatch, controller)
        controller._refresh_scheduled()
        assert_work(work, [entry.form, child.form], [entry.combo, child.combo])
    finally:
        delete(outer)
        settle(app)


def test_hidden_nonwindow_root_uses_full_fallback_before_parent_show(app, monkeypatch):
    outer = QWidget()
    QVBoxLayout(outer)
    scroll = QScrollArea(outer)
    scroll.setWidgetResizable(True)
    outer.layout().addWidget(scroll)
    tabs = QTabWidget()
    scroll.setWidget(tabs)
    tabs.addTab(QWidget(), 'Active')
    page = QWidget()
    QVBoxLayout(page)
    entry = add_form(page, 'Hidden controlled tab')
    tabs.addTab(page, 'Hidden')
    dialog, child = add_dialog(page, 'Visible child of hidden tab')
    style.apply_ui_layout_tokens(page)
    dialog.show()
    settle(app)
    controller = page._lumen_layout_controller
    work = record_work(monkeypatch, controller)
    try:
        assert not outer.isVisible() and not page.isVisible() and dialog.isVisible()
        extra = add_form(page, 'New form must not be deferred')
        font = QFont(page.font())
        font.setPointSize(14)
        page.setFont(font)  # Real watched event; no direct private refresh call.
        settle(app)
        assert extra.form in work.forms and entry.form in work.forms
        assert extra.combo.property('layoutTokensApplied')
        assert extra.editor.font().pointSizeF() == 14
        outer.resize(320, 700)
        outer.show()  # The controlled tab remains hidden and may get no Show.
        settle(app)
        assert not page.isVisible() and outer.isVisible()
        before = (presentation(entry), presentation(extra), page.minimumSizeHint().toTuple())
        controller.refresh()
        settle(app)
        assert (presentation(entry), presentation(extra), page.minimumSizeHint().toTuple()) == before
        assert set(controller.forms) == {entry.form, extra.form, child.form}
    finally:
        delete(outer)
        settle(app)


@pytest.mark.parametrize('transition', ['root_show', 'root_show_hide', 'child_hide', 'child_hide_show'])
def test_zero_argument_coalesced_callback_uses_execution_visibility(scene, app, monkeypatch, transition):
    s = scene
    s.dialog.show()
    settle(app)
    queued = []
    monkeypatch.setattr(style, 'QTimer', SimpleNamespace(singleShot=lambda *args: queued.append(args)))
    s.controller.schedule()
    s.controller.schedule()
    assert len(queued) == 1
    delay, context, callback = queued[0]
    assert delay == 0 and context is s.controller
    assert callback.__self__ is s.controller
    if transition.startswith('root_show'):
        s.root.show()
        if transition == 'root_show_hide':
            s.root.hide()
            # Hiding a parent may hide its dialogs; this is the final state.
            s.dialog.show()
    else:
        s.dialog.hide()
        if transition == 'child_hide_show':
            s.dialog.show()
    work = record_work(monkeypatch, s.controller)
    callback()  # Must require no signal payload or event argument.
    scoped = transition in ('root_show_hide', 'child_hide_show')
    entries = [s.child] if scoped else [s.main, s.child]
    assert_work(work, [entry.form for entry in entries], [entry.combo for entry in entries])
    assert not s.controller._pending and not s.controller._refreshing


def test_show_catches_up_hidden_form_and_keeps_convergence_queue(scene, app, monkeypatch):
    s = scene
    s.dialog.show()
    settle(app)
    extra = add_form(s.root, 'Added while hidden')
    s.controller._refresh_scheduled()
    assert not extra.combo.property('layoutTokensApplied')
    work = record_work(monkeypatch, s.controller)
    s.root.show()
    settle(app)
    assert extra.combo.property('layoutTokensApplied')
    assert extra.form in work.forms and s.main.form in work.forms
    # No first-pass-only suppression: a later real layout event still queues
    # the same normal convergence pass, with the full inventory intact.
    work.forms.clear()
    QApplication.sendEvent(s.root, QEvent(QEvent.Type.LayoutRequest))
    assert s.controller._pending
    settle(app)
    assert set(work.forms) == set(s.controller.forms)
    assert not s.controller._pending


@pytest.mark.parametrize('deleted', [False, True])
def test_closed_or_deleted_child_before_dispatch_uses_fresh_descendants(scene, app, monkeypatch, deleted):
    s = scene
    s.dialog.show()
    settle(app)
    queued = []
    monkeypatch.setattr(style, 'QTimer', SimpleNamespace(singleShot=lambda *args: queued.append(args)))
    s.controller.schedule()
    if deleted:
        delete(s.dialog)
    else:
        s.dialog.close()
    work = record_work(monkeypatch, s.controller)
    queued[0][2]()
    entries = [s.main] if deleted else [s.main, s.child]
    assert_work(work, [entry.form for entry in entries], [entry.combo for entry in entries])


def test_deleting_root_cancels_real_context_bound_pending_callback(scene, app, monkeypatch):
    s = scene
    settle(app)
    calls = []
    original = s.controller._refresh_scheduled

    def callback():
        calls.append(True)
        original()

    monkeypatch.setattr(s.controller, '_refresh_scheduled', callback)
    s.controller.schedule()
    assert s.controller._pending
    delete(s.root)
    settle(app)
    assert not calls
    assert not isValid(s.controller)


def presentation(entry):
    form = entry.form
    label = entry.label
    return (form.rowWrapPolicy(), form.horizontalSpacing(), form.verticalSpacing(),
            label.minimumSize().toTuple(), label.maximumWidth(),
            label.contentsMargins().left(), label.contentsMargins().top(),
            label.font().pointSizeF(), entry.editor.minimumHeight(),
            entry.combo.minimumWidth(), entry.combo.toolTip())


def test_visible_dialog_font_locale_resize_matches_full_pass_without_value_signals(scene, app):
    s = scene
    s.dialog.show()
    s.dialog.activateWindow()
    s.child.editor.setAccessibleName('Source notes')
    s.child.editor.setAccessibleDescription('Keep the original user text')
    s.child.editor.setFocus(Qt.FocusReason.OtherFocusReason)
    settle(app)
    changes = []
    s.child.number.valueChanged.connect(lambda *_: changes.append('number'))
    s.child.editor.textChanged.connect(lambda *_: changes.append('text'))
    s.child.combo.currentIndexChanged.connect(lambda *_: changes.append('combo'))
    before = (s.child.number.value(), s.child.editor.text(), s.child.combo.currentIndex())
    focus_before = s.child.editor.hasFocus()
    assert focus_before
    original_locale = get_locale()
    try:
        for size, width, locale in [(10, 720, 'en'), (14, 300, 'zh-CN'), (10, 720, 'en')]:
            font = QFont(s.root.font())
            font.setPointSize(size)
            s.root.setFont(font)
            set_locale(locale)
            s.dialog.resize(width, 700)
            settle(app)
            scoped = presentation(s.child)
            # Full eager legacy work is an oracle for the same visible child.
            s.controller.refresh()
            settle(app)
            assert presentation(s.child) == scoped
            assert s.child.editor.font().pointSizeF() == size
            assert s.child.combo.currentText() in s.child.combo.toolTip()
            assert not s.root.isVisible() and s.dialog.isVisible()
            assert s.child.editor.hasFocus() == focus_before
            assert s.child.editor.accessibleName() == 'Source notes'
            assert s.child.editor.accessibleDescription() == 'Keep the original user text'
            assert (s.child.number.value(), s.child.editor.text(), s.child.combo.currentIndex()) == before
        assert not changes
    finally:
        set_locale(original_locale)



@pytest.mark.parametrize('root_kind', ['window', 'dialog', 'embedded'])
@pytest.mark.parametrize('visible', [False, True])
def test_python_window_owner_attribute_keeps_exact_scope_and_values(app, monkeypatch, root_kind, visible):
    owner = QWidget()
    QVBoxLayout(owner)
    if root_kind == 'dialog':
        root = QDialog(owner)
    elif root_kind == 'embedded':
        root = QWidget(owner)
        owner.layout().addWidget(root)
    else:
        root = QWidget()
    # Real application dialogs use this name for their MainWindow owner.
    # Qt's descriptor must still identify the actual containing window.
    root.window = owner
    QVBoxLayout(root)
    main = add_form(root, 'Root with a Python window owner')
    dialog, child = add_dialog(root, 'Visible owned child')
    child.editor.setAccessibleName('Stable shadow-owner notes')
    child.editor.setAccessibleDescription('Preserve the original user text')
    style.apply_ui_layout_tokens(root)
    controller = root._lumen_layout_controller
    try:
        assert root.window is owner and not callable(root.window)
        assert QWidget.window(root) is (owner if root_kind == 'embedded' else root)
        assert main.combo.property('layoutTokensApplied')
        assert child.combo.property('layoutTokensApplied')
        if visible:
            if root_kind == 'embedded':
                owner.show()
            root.show()
        dialog.show()
        settle(app)
        changes = []
        for entry in (main, child):
            entry.number.valueChanged.connect(lambda *_: changes.append('number'))
            entry.editor.textChanged.connect(lambda *_: changes.append('text'))
            entry.combo.currentIndexChanged.connect(lambda *_: changes.append('combo'))
        values = [(e.number.value(), e.editor.text(), e.combo.currentIndex()) for e in (main, child)]
        work = record_work(monkeypatch, controller)
        controller._refresh_scheduled()
        expected = [main, child] if visible or root_kind == 'embedded' else [child]
        assert_work(work, [e.form for e in expected], [e.combo for e in expected])
        assert set(controller.forms) == {main.form, child.form}
        assert main.host in controller._watched and child.host in controller._watched
        assert root.isVisible() is visible and dialog.isVisible()
        assert root.window is owner
        assert not controller._pending and not controller._refreshing
        assert [(e.number.value(), e.editor.text(), e.combo.currentIndex()) for e in (main, child)] == values
        assert child.editor.accessibleName() == 'Stable shadow-owner notes'
        assert child.editor.accessibleDescription() == 'Preserve the original user text'
        assert not changes
    finally:
        if isValid(root):
            delete(root)
        if isValid(owner):
            delete(owner)
        settle(app)


def test_shadowed_window_owner_show_catchup_and_real_queued_teardown(app, monkeypatch):
    from scripts.qt_test_cleanup import cleanup_test_windows
    existing = list(app.topLevelWidgets())
    owner = QWidget()
    root = QDialog(owner)
    root.window = owner
    QVBoxLayout(root)
    main = add_form(root, 'Hidden root')
    child, child_entry = add_dialog(root, 'Visible child')
    style.apply_ui_layout_tokens(root)
    controller = root._lumen_layout_controller
    try:
        child.show()
        settle(app)
        extra = add_form(root, 'Added while hidden')
        controller._refresh_scheduled()
        assert not extra.combo.property('layoutTokensApplied')
        work = record_work(monkeypatch, controller)
        root.show()
        settle(app)
        assert extra.combo.property('layoutTokensApplied')
        assert extra.form in work.forms and main.form in work.forms
        assert root.window is owner and QWidget.window(root) is root
        assert (extra.number.value(), extra.editor.text(), extra.combo.currentIndex()) == (1234, 'User text / 用户内容', 0)
        # Exercise the production test cleanup's real processEvents path with
        # a still-pending context-bound callback; do not delete the controller
        # or suppress its exception to make teardown pass.
        controller.schedule()
        root.close()
        assert controller._pending
        cleanup_test_windows(app, existing)
        assert not isValid(owner) and not isValid(root) and not isValid(controller)
    finally:
        if isValid(owner):
            delete(owner)
        settle(app)

def _scope_pixel_probe(output, scale, locale):
    """Bounded offscreen oracle, not a native-platform or performance claim."""
    import hashlib
    import json

    assert QApplication.instance() is None
    assert Path(style.__file__).resolve() == Path(__file__).resolve().parents[1] / 'lumen' / 'ui_style.py'
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    font = QFont(app.font())
    directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    font_id = None
    if directory is not None:
        from scripts.provision_test_font import verify_fixture
        verified = verify_fixture(directory)
        font_id = QFontDatabase.addApplicationFont(verified['font_file'])
        assert font_id >= 0
        families = QFontDatabase.applicationFontFamilies(font_id)
        assert families
        info = QFontInfo(font)
        if not info.family() or info.pixelSize() <= 0:
            font.setFamilies(list(dict.fromkeys([*families, *font.families()])))
    font.setPointSize(10)
    app.setFont(font)
    # Actual glyphs are required, never silently accept tofu/empty screenshots.
    for text in ('CutCatABC0123456789', '中文测试再见'):
        layout = QTextLayout(text, font)
        layout.beginLayout()
        layout.createLine().setLineWidth(2000)
        layout.endLayout()
        runs = layout.glyphRuns()
        assert runs and all(run.rawFont().isValid() for run in runs)
        indexes = [index for run in runs for index in run.glyphIndexes()]
        assert indexes and all(index != 0 for index in indexes)
    set_locale(locale)
    root = QWidget()
    root.setStyleSheet(style.STYLE)
    QVBoxLayout(root)
    main = add_form(root, 'Hidden main form')
    dialog = QDialog(root)
    QVBoxLayout(dialog)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    dialog.layout().addWidget(scroll)
    tabs = QTabWidget()
    scroll.setWidget(tabs)
    entries = []
    for title in ('Visible page', 'Hidden page'):
        page = QWidget()
        QVBoxLayout(page)
        entries.append(add_form(page, title))
        tabs.addTab(page, title)
    nested_window, nested_entry = add_dialog(dialog, 'Hidden owned nested dialog')
    entries.append(nested_entry)
    focus = QPushButton(tr('Cancel'), dialog)
    focus.setAutoDefault(False)
    focus.setAccessibleName('Stable oracle focus')
    focus.setAccessibleDescription('No blinking text caret')
    dialog.layout().addWidget(focus)
    style.apply_ui_layout_tokens(root)
    controller = root._lumen_layout_controller
    assert not hasattr(dialog, '_lumen_layout_controller')
    calls = []
    original_format = controller._format_form

    def format_form(form, tokens):
        calls.append(form)
        return original_format(form, tokens)

    controller._format_form = format_form
    changes = []
    for entry in entries:
        entry.number.valueChanged.connect(lambda *_: changes.append('number'))
        entry.editor.textChanged.connect(lambda *_: changes.append('text'))
        entry.combo.currentIndexChanged.connect(lambda *_: changes.append('combo'))
    focus.clicked.connect(lambda *_: changes.append('clicked'))
    dialog.resize(780, 720)
    dialog.show()
    dialog.activateWindow()
    QApplication.setActiveWindow(dialog)
    focus.setFocus(Qt.FocusReason.OtherFocusReason)
    settle(app)
    assert focus.hasFocus()
    values = [(e.number.value(), e.editor.text(), e.combo.currentIndex()) for e in entries]
    records = []

    def snapshot():
        widgets = [dialog, nested_window, scroll, scroll.viewport(), tabs, focus]
        for entry in entries:
            widgets.extend([entry.host, entry.number, entry.editor, entry.combo])
            widgets.extend(entry.form.itemAt(row, QFormLayout.ItemRole.LabelRole).widget()
                           for row in range(entry.form.rowCount()))
        return {
            'widgets': [dict(type=type(w).__name__, rect=w.geometry().getRect(),
                             minimum=w.minimumSize().toTuple(),
                             minimum_hint=w.minimumSizeHint().toTuple(),
                             maximum=w.maximumSize().toTuple(), font=w.font().toString(),
                             visible=w.isVisible(), focus=w.hasFocus(),
                             accessible_name=w.accessibleName(),
                             accessible_description=w.accessibleDescription()) for w in widgets],
            'forms': [dict(wrap=int(e.form.rowWrapPolicy().value),
                           minimum=e.form.minimumSize().toTuple(),
                           geometry=e.form.geometry().getRect(),
                           label_text=e.label.text(), combo_text=e.combo.currentText(),
                           tooltip=e.combo.toolTip()) for e in entries],
            'values': [(e.number.value(), e.editor.text(), e.combo.currentIndex()) for e in entries],
            'scroll': (scroll.horizontalScrollBar().maximum(), scroll.verticalScrollBar().maximum()),
            'root_visible': root.isVisible(), 'focus_target': focus.hasFocus(),
            'hidden_page_visible': tabs.widget(1).isVisible(),
            'hidden_owned_window_visible': nested_window.isVisible(), 'signals': list(changes),
        }

    def capture(name):
        image = dialog.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        assert image.devicePixelRatio() == scale
        assert not image.isNull() and image.width() > 0 and image.height() > 0
        assert (image.width(), image.height()) == (round(dialog.width() * scale), round(dialog.height() * scale))
        assert image.save(str(output / name))
        return dict(path=name, width=image.width(), height=image.height(),
                    device_pixel_ratio=image.devicePixelRatio(),
                    pixel_sha256=hashlib.sha256(bytes(image.constBits())).hexdigest())

    def observe(name):
        # QWidget.grab materializes even hidden owned layouts and posts reflow.
        # Normalize this single known capture boundary symmetrically. There is
        # no loop: a second capture-induced mutation must fail below.
        materialized = dialog.grab()
        assert not materialized.isNull()
        settle(app)
        before = snapshot()
        flags_before = (controller._pending, controller._refreshing)
        image = capture(name)
        after = snapshot()
        flags_after = (controller._pending, controller._refreshing)
        return dict(before=before, after=after, image=image,
                    flags_before=flags_before, flags_after=flags_after)

    try:
        for name, size, width in [('wide', 10, 780), ('narrow-large-font', 14, 390),
                                  ('wide-font-restored', 10, 780)]:
            calls.clear()
            changed_font = QFont(root.font())
            changed_font.setPointSize(size)
            root.setFont(changed_font)
            dialog.resize(width, 720)
            controller.schedule()
            settle(app)
            assert calls and main.form not in calls
            assert all(entry.form in calls for entry in entries)
            scoped_observation = observe(name + '-scoped.png')
            controller.refresh()  # Full eager path is the unchanged oracle.
            settle(app)
            full_observation = observe(name + '-full.png')
            before, after = scoped_observation['before'], full_observation['before']
            scoped_image, full_image = scoped_observation['image'], full_observation['image']
            record = dict(state=name, requested_width=width, font_points=size,
                          before=before, after=after, scoped=scoped_image, full=full_image,
                          observations=dict(scoped=scoped_observation, full=full_observation))
            records.append(record)
            # Always preserve evidence before asserting; no pixel tolerances,
            # masking, retries, adaptive settling, or hidden caret manipulation.
            (output / 'report.json').write_text(json.dumps(dict(
                status='checking', locale=locale, scale=scale, source_root=str(Path(__file__).resolve().parents[1]),
                ui_style_sha256=hashlib.sha256(Path(style.__file__).read_bytes()).hexdigest(),
                records=records), ensure_ascii=False, indent=2) + '\n')
            for observation in (scoped_observation, full_observation):
                assert observation['before'] == observation['after']
                assert observation['flags_before'] == observation['flags_after'] == (False, False)
            assert before == after
            assert scoped_image['pixel_sha256'] == full_image['pixel_sha256']
            assert (scoped_image['width'], scoped_image['height']) == (full_image['width'], full_image['height'])
            assert before['values'] == values and not changes
            assert before['focus_target'] and not before['root_visible'] and not before['hidden_page_visible']
            assert not before['hidden_owned_window_visible']
            assert before['scroll'][0] == 0
            assert dialog.width() == width
        report = json.loads((output / 'report.json').read_text())
        report.update(status='passed', actual_qt_dpr=dialog.devicePixelRatioF(),
                      native_platform_acceptance=False, exact_pixels=True,
                      screenshot_bytes=sum(p.stat().st_size for p in output.glob('*.png')))
        assert report['screenshot_bytes'] < 2 * 1024 * 1024
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    finally:
        delete(root)
        settle(app)
        if font_id is not None:
            QFontDatabase.removeApplicationFont(font_id)


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('scale', [1, 1.5, 2])
def test_scoped_dialog_exact_full_oracle_at_real_dpr(tmp_path, locale, scale):
    import hashlib
    import json
    import subprocess

    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'scope-pixels'
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).resolve()), '--scope-pixel-probe',
         str(output), str(scale), locale], cwd=root,
        env=dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR=str(scale),
                 PYTHONDONTWRITEBYTECODE='1',
                 PYTHONPATH=os.pathsep.join(filter(None, [str(root), os.environ.get('PYTHONPATH', '')]))),
        capture_output=True, text=True, timeout=30,
    )
    (tmp_path / 'child.stdout').write_text(result.stdout)
    (tmp_path / 'child.stderr').write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / 'report.json').read_text())
    assert report['status'] == 'passed' and report['exact_pixels']
    assert not report['native_platform_acceptance']
    assert report['actual_qt_dpr'] == report['scale'] == scale
    assert report['locale'] == locale and report['source_root'] == str(root)
    assert report['ui_style_sha256'] == hashlib.sha256(Path(style.__file__).read_bytes()).hexdigest()
    assert len(report['records']) == 3
    assert len(list(output.glob('*.png'))) == 6
    assert report['screenshot_bytes'] < 2 * 1024 * 1024


if __name__ == '__main__':
    assert len(sys.argv) == 5 and sys.argv[1] == '--scope-pixel-probe'
    _scope_pixel_probe(Path(sys.argv[2]), float(sys.argv[3]), sys.argv[4])
