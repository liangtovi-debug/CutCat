"""Real owned Qt workers, session-only marks and explicit one-shot overwrite."""
from copy import deepcopy
import os
from pathlib import Path
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from lumen.i18n import set_locale, tr_error
from lumen.model import Editor, Media, Project
from lumen.source_viewer import SourceFrameResult, SourceIdentity
from lumen.ui import MainWindow
from lumen import ui_source_viewer as ui


def pump(app, condition, seconds=5):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        app.processEvents(); time.sleep(.003)
    app.processEvents()
    assert condition()


class FakeBackend:
    def __init__(self):
        self.started = threading.Event()
        self.gate = None
        self.verify_gate = None
        self.changed = False
        self.closed = False
        self.threads = []
        self.verify_calls = 0
        self.active = 0
        self.max_active = 0

    def inspect_frame(self, session, cancel=None):
        self.threads.append(threading.get_ident())
        self.active += 1; self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.gate is not None: assert self.gate.wait(5)
            identity = SourceIdentity(session.media.path, (), 'fake', session.signature)
            return SourceFrameResult(2, 2, bytes([64, 128, 192]) * 4,
                                     session.cursor, session.generation, identity, session.token)
        finally:
            self.active -= 1

    def verify_overwrite_sources(self, session, identity, project_snapshot, plan, cancel=None):
        self.threads.append(threading.get_ident()); self.verify_calls += 1
        self.started.set()
        if self.verify_gate is not None: assert self.verify_gate.wait(5)
        if self.changed: raise ValueError('Source changed; refresh the source viewer before continuing.')
        return True

    def close(self):
        self.threads.append(threading.get_ident()); self.closed = True


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setattr(ui, 'SourcePreviewBackend', FakeBackend)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    w = MainWindow(workspace_settings=QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat),
                   recovery_root=tmp_path / 'recovery', ui_language='en')
    media = Media('source', str(tmp_path / 'source.mov'), 'Source α 素材', 4, 64, 36, True, False)
    other = Media('other', str(tmp_path / 'other.mov'), 'Other', 4, 64, 36, True, False)
    w.editor = Editor(Project(width=64, height=36, media=[media, other]))
    w.editor.add_clip(media.id, 0, 0)
    w.refresh(); w.bin.setCurrentRow(0)
    w.show(); app.processEvents()
    yield w
    for dialog in w.findChildren(ui.SourceViewerDialog):
        if getattr(dialog.backend, 'gate', None) is not None: dialog.backend.gate.set()
        if getattr(dialog.backend, 'verify_gate', None) is not None: dialog.backend.verify_gate.set()
        dialog.reject()
    w.cancel_jobs()
    pump(app, lambda: not w.jobs)
    w.dirty = False; w.closing = True; w.close(); w.deleteLater(); app.processEvents()
    set_locale('en')


def opened(window, app):
    dialog = window.open_source_viewer()
    assert dialog is not None
    pump(app, lambda: not window.jobs)
    return dialog


def planned(window, app):
    d = opened(window, app)
    d.seek(10); d.mark_in(); d.seek(19); d.mark_out()
    window.timeline.playhead = 20; d.capture_playhead()
    d.preview_overwrite()
    pump(app, lambda: not window.jobs)
    assert d.plan is not None and d.apply_button.isEnabled()
    return d


def test_explicit_modeless_session_marks_and_independent_playheads(window, app):
    before = deepcopy(window.project); rev = window.editor.revision
    d = opened(window, app)
    assert not d.isModal() and window.open_source_viewer() is d
    assert not d.canvas.image.isNull() and 'Session-only' in d.session_note.text()
    window.timeline.playhead = 35
    d.seek(7); d.mark_in(); d.seek(12); d.mark_out()
    assert d.session.selection() == (7, 13) and d.session.cursor == 12
    assert window.timeline.playhead == 35 and d.captured_start == 0
    d.capture_playhead(); window.timeline.playhead = 44
    assert d.captured_start == 35
    assert window.project == before and window.editor.revision == rev
    assert window.video.image.isNull() and not window.preview_paths
    assert all(t != threading.get_ident() for t in d.backend.threads)
    backend = d.backend; d.reject()
    pump(app, lambda: not window.jobs)
    assert backend.closed and window.source_viewer_dialog is None
    fresh = opened(window, app)
    assert fresh.session.selection() == (0, 120) and fresh.session.cursor == 0


def test_overwrite_preview_explicit_apply_once_and_undo(window, app):
    before = deepcopy(window.project); revision = window.editor.revision
    d = planned(window, app)
    assert window.project == before and window.editor.revision == revision
    assert 'Affected clips: 1' in d.impact.text() and 'Surviving pieces: 2' in d.impact.text()
    assert 'Replace ' in d.impact.text() and '[20, 30)' in d.impact.text()
    d.apply_overwrite(); d.apply_overwrite()
    pump(app, lambda: not window.jobs)
    assert window.editor.revision == revision + 1
    assert len(window.project.clips) == 3 and d.plan is None
    assert d.backend.verify_calls == 1 and not d.apply_button.isEnabled()
    assert 'applied once' in d.status.text()
    window.undo(); assert window.project == before


def test_source_shortcuts_are_only_on_focused_canvas(window, app):
    d = opened(window, app); d.seek(8)
    d.activateWindow(); d.canvas.setFocus(); app.processEvents()
    QTest.keyClick(d.canvas, Qt.Key.Key_I)
    assert d.session.in_frame == 8
    d.seek(12); d.canvas.setFocus(); QTest.keyClick(d.canvas, Qt.Key.Key_O)
    assert d.session.out_frame == 13
    d.cursor_spin.setFocus(); app.processEvents()
    QTest.keyClick(d.cursor_spin, Qt.Key.Key_I)
    assert d.session.in_frame == 8
    window.activateWindow(); window.media_search.setFocus(); app.processEvents()
    QTest.keyClick(window.media_search, Qt.Key.Key_I)
    assert d.session.in_frame == 8 and window.media_search.text() == 'i'
    window.media_search.clear(); window.timeline.setFocus(); app.processEvents()
    QTest.keyClick(window.timeline, Qt.Key.Key_O)
    assert d.session.out_frame == 13


@pytest.mark.parametrize('change', ['revision', 'selection', 'source', 'editor', 'lock', 'cancel', 'close'])
def test_delayed_decode_cannot_revive_stale_image(window, app, change):
    d = opened(window, app)
    gate = threading.Event(); d.backend.gate = gate; d.backend.started.clear()
    d.inspect_frame(); pump(app, d.backend.started.is_set)
    old_job = d.job
    if change == 'revision': window.revision += 1
    elif change == 'selection': window.bin.setCurrentRow(1)
    elif change == 'source': window.project.media[0].path += '.relinked'
    elif change == 'editor': window.editor = Editor(deepcopy(window.project))
    elif change == 'lock': window.editor.set_track_edit_lock(0, True)
    elif change == 'cancel': d.cancel_work()
    else: d.reject()
    d.check_current()
    assert old_job.cancel.is_set()
    gate.set(); pump(app, lambda: not window.jobs)
    assert d.canvas.image.isNull() and d.identity is None and d.plan is None


def test_newest_request_replaces_pending_without_parallel_decode(window, app):
    d = opened(window, app)
    gate = threading.Event(); d.backend.gate = gate; d.backend.started.clear()
    d.inspect_frame(); pump(app, d.backend.started.is_set)
    d.seek(3); d.inspect_frame()
    d.seek(6); d.inspect_frame()
    gate.set(); pump(app, lambda: not window.jobs)
    assert d.backend.max_active == 1 and d.session.cursor == 6
    assert 'Source frame 6' in d.status.text() and not d.canvas.image.isNull()


@pytest.mark.parametrize('change', ['lock', 'selection', 'undo', 'close', 'cancel'])
def test_late_apply_identity_check_cannot_commit_stale_plan(window, app, change):
    d = planned(window, app)
    gate = threading.Event(); d.backend.verify_gate = gate; d.backend.started.clear()
    before = deepcopy(window.project); revision = window.editor.revision
    d.apply_overwrite(); pump(app, d.backend.started.is_set)
    if change == 'lock': window.editor.set_track_edit_lock(0, True)
    elif change == 'selection': window.bin.setCurrentRow(1)
    elif change == 'undo': window.undo()
    elif change == 'close': d.reject()
    else: d.cancel_work()
    d.check_current(); expected = deepcopy(window.project)
    gate.set(); pump(app, lambda: not window.jobs)
    assert window.project == expected and len(window.project.clips) <= len(before.clips)
    assert d.plan is None and not d.apply_button.isEnabled()


def test_changed_source_identity_refuses_apply(window, app):
    d = planned(window, app); before = deepcopy(window.project)
    d.backend.changed = True; d.apply_overwrite()
    pump(app, lambda: not window.jobs)
    assert window.project == before and d.plan is None
    assert 'Source changed' in d.status.text()


def test_live_language_accessibility_and_narrow_scroll_layout(window, app):
    d = planned(window, app); state = d.session.selection(); plan = d.plan
    set_locale('zh-CN'); app.processEvents()
    assert '源监看器' in d.windowTitle() and d.mark_in_button.text() == '标记入点 · I'
    assert '受影响片段' in d.impact.text() and '源游标' in d.cursor_spin.accessibleName()
    assert d.session.selection() == state and d.plan is plan
    assert '源出点' in tr_error('Source Out must be after In (exclusive)')
    d.resize(320, 380); app.processEvents()
    assert d.width() <= 330 and d.scroll.verticalScrollBar().maximum() > 0
    assert d.scroll.horizontalScrollBar().maximum() == 0
    set_locale('en'); app.processEvents()
    assert d.mark_out_button.text() == 'Mark Out · O' and d.plan is plan
    from lumen.ui_help import help_text
    assert 'PCM sample grid' in help_text() and 'Source viewer:' in help_text()



@pytest.fixture
def source_layout_environment(app, request, qt_test_font):
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QStyleFactory

    style_name, point_size = request.param
    assert style_name in QStyleFactory.keys()
    family = 'Noto Sans CJK SC'
    if family not in QFontDatabase.families():
        pytest.skip('Provision the pinned CJK test font to exercise its real metrics.')
    old_font, old_style = QFont(app.font()), app.style().objectName()
    app.setStyle(style_name)
    app.setFont(QFont(family, point_size))
    try:
        yield style_name, point_size
    finally:
        app.setStyle(old_style)
        app.setFont(old_font)
        app.processEvents()


@pytest.mark.parametrize('source_layout_environment', [
    ('Windows', 10), ('Windows', 16), ('Fusion', 10), ('Fusion', 16),
], indirect=True)
def test_source_reflow_publishes_scroll_geometry_in_same_turn(
        app, request, source_layout_environment):
    import json
    from PySide6.QtGui import QFontInfo
    from PySide6.QtWidgets import QFormLayout

    # Construct the actual owned dialog only after the real font/style is set.
    window = request.getfixturevalue('window')
    d = planned(window, app)
    selection, plan = d.session.selection(), d.plan
    style_name, point_size = source_layout_environment
    for locale in ('zh-CN', 'en'):
        set_locale(locale)
        app.processEvents()
        for width in (320, 740, 320):
            d.resize(width, 380)
            app.processEvents()  # Exactly one turn, not a settle/retry loop.
            scroll, body = d.scroll, d.scroll.widget()
            forms = d.findChildren(QFormLayout)
            context = dict(locale=locale, style=style_name, requested_points=point_size,
                           actual_font=QFontInfo(d.font()).family(),
                           actual_points=QFontInfo(d.font()).pointSizeF(),
                           actual_pixels=QFontInfo(d.font()).pixelSize(),
                           dialog=d.size().toTuple(), viewport=scroll.viewport().size().toTuple(),
                           body=body.size().toTuple(), body_minimum=body.minimumSizeHint().toTuple(),
                           horizontal_max=scroll.horizontalScrollBar().maximum(),
                           forms=[dict(geometry=form.geometry().getRect(),
                                       minimum=form.minimumSize().toTuple(),
                                       wrap=form.rowWrapPolicy().name) for form in forms])
            if (context['horizontal_max'] != 0 or body.width() > scroll.viewport().width()
                    or any(form.geometry().width() <= 0 for form in forms)):
                directory = Path('build/ui-test-failures')
                directory.mkdir(parents=True, exist_ok=True)
                stem = f'source-{style_name}-{point_size}-{locale}-{width}'
                (directory / f'{stem}.json').write_text(
                    json.dumps(context, ensure_ascii=False, indent=2), encoding='utf-8')
                d.grab().save(str(directory / f'{stem}.png'))
            assert context['horizontal_max'] == 0, context
            assert body.width() <= scroll.viewport().width(), context
            assert all(form.geometry().width() > 0 for form in forms), context
            assert scroll.verticalScrollBar().maximum() > 0
            assert d.session.selection() == selection and d.plan is plan


def test_new_open_and_relink_cancel_source_work_before_dialogs(window, app, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    d = planned(window, app)
    monkeypatch.setattr(window, 'confirm_discard', lambda: True)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: ('', False))
    window.new_project(); assert d.plan is None
    d.preview_overwrite(); pump(app, lambda: not window.jobs); assert d.plan is not None
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: ('', ''))
    window.open_project(); assert d.plan is None
    d.preview_overwrite(); pump(app, lambda: not window.jobs); assert d.plan is not None
    monkeypatch.setattr(window.project, 'missing_media', lambda: [])
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    window.relink_dialog(); assert d.plan is None


def test_close_waits_for_owned_cleanup_without_spurious_work_prompt(window, app, monkeypatch):
    d = opened(window, app); backend = d.backend
    window.dirty = False
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: pytest.fail('Only cleanup, not active user work'))
    window.close()
    pump(app, lambda: not window.jobs and not window.isVisible())
    assert backend.closed and window.source_viewer_dialog is None


def test_every_new_source_error_has_explicit_chinese_translation():
    import ast
    from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN
    for name in ('source_viewer.py', 'source_overwrite.py'):
        tree = ast.parse((Path(ui.__file__).parent / name).read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id in ('SourceViewerError', 'SourceViewerCancelled', 'ValueError')
                    and node.exc.args):
                value = node.exc.args[0]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    assert value.value in ERROR_ZH_CN
                elif isinstance(value, ast.JoinedStr):
                    # Check each complete error grammar, not only its prefix;
                    # diagnostics remain verbatim inside named placeholders.
                    parts = []
                    for part in value.values:
                        if isinstance(part, ast.Constant):
                            parts.append(part.value)
                        else:
                            assert isinstance(part, ast.FormattedValue)
                            assert isinstance(part.value, ast.Name)
                            assert part.format_spec is None and part.conversion == -1
                            parts.append('{' + part.value.id + '}')
                    assert ''.join(parts) in ERROR_ZH_CN


def test_real_mov_source_inspect_proof_apply_undo_redo_and_public_preview(window, app, tmp_path, monkeypatch):
    from fractions import Fraction
    import numpy as np
    from lumen.media import probe, ffmpeg_available
    from lumen.render import RenderEngine
    from lumen.source_viewer import SourcePreviewBackend
    from test_retime_video import frame_id_rgb, ff, decode, assert_delivery_frames, WIDTH, HEIGHT
    if not ffmpeg_available(): pytest.skip('FFmpeg/ffprobe unavailable')
    original = frame_id_rgb(count=60)
    path = tmp_path / 'source-exact-grid.mov'
    ff('-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{WIDTH}x{HEIGHT}',
       '-r', '30', '-i', 'pipe:0', '-an', '-c:v', 'png', '-pix_fmt', 'rgb48be',
       '-video_track_timescale', '30000', '-color_range', 'pc', '-color_primaries', 'bt709',
       '-color_trc', 'bt709', '-colorspace', 'rgb', path, data=original.astype('<u2').tobytes())
    np.testing.assert_array_equal(decode(path), original)
    media = probe(path)
    window.editor = Editor(Project(width=WIDTH, height=HEIGHT, media=[media]))
    window.editor.add_clip(media.id, 0, 0)
    window.refresh(); window.bin.setCurrentRow(0)
    monkeypatch.setattr(ui, 'SourcePreviewBackend', SourcePreviewBackend)
    d = opened(window, app)
    d.seek(10); d.inspect_frame(); pump(app, lambda: not window.jobs)
    # The inspection image is independently checked against original RGB16 IDs.
    color = d.canvas.image.pixelColor(4, 4)
    expected_color = original[10, 4, 4] / 257
    assert max(abs(color.getRgb()[i] - expected_color[i]) for i in range(3)) <= 1
    d.mark_in(); d.seek(19); d.mark_out()
    window.timeline.playhead = 20; d.capture_playhead()
    before = deepcopy(window.project); revision = window.editor.revision
    d.preview_overwrite(); pump(app, lambda: not window.jobs)
    assert d.plan is not None
    d.apply_overwrite(); pump(app, lambda: not window.jobs, seconds=15)
    assert len(window.project.clips) == 3 and window.editor.revision == revision + 1, d.status.text()
    after = deepcopy(window.project)
    window.undo(); assert window.project == before
    window.redo(); assert window.project == after
    expected = original.copy(); expected[20:30] = original[10:20]
    renderer = RenderEngine(timeout=30)
    try:
        assert_delivery_frames(decode(renderer.preview(window.project), rgb16=False), expected)
    finally:
        renderer.close()
    # Audition never substitutes approximate milliseconds for the exact cursor.
    assert d._usable(), (window.selected_media_ids(), d.session.matches(window.project), d.session.media_id)
    assert d._observed == d._context(), ('before request', d._observed, d._context())
    d.seek(3); d.audition(); pump(app, lambda: not window.jobs, seconds=15)
    assert d.player is not None, d.status.text()
    assert not d.mark_in_button.isEnabled() and not d.mark_out_button.isEnabled()
    d.mark_in(); assert d.session.in_frame == 10
    assert d.session.cursor == 3
    d.cancel_work(); assert d.session.cursor == 3 and d.canvas.image.isNull()


@pytest.mark.parametrize('action', ['new', 'open'])
def test_successful_project_replacement_closes_source_session(window, app, tmp_path, monkeypatch, action):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    d = opened(window, app); old_editor = window.editor
    monkeypatch.setattr(window, 'confirm_discard', lambda: True)
    if action == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *args, **kwargs: ('1280 × 720 · 30 fps', True))
        window.new_project()
    else:
        path = tmp_path / 'empty.lumen'; Project().save(path)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args, **kwargs: (str(path), ''))
        window.open_project()
    pump(app, lambda: not window.jobs)
    assert window.editor is not old_editor and window.source_viewer_dialog is None
    assert d._closed_flag and d.backend.closed


def test_spinbox_enter_cannot_apply_or_start_implicit_source_job(window, app):
    d = planned(window, app); before = deepcopy(window.project)
    d.activateWindow(); d.cursor_spin.setFocus(); app.processEvents()
    QTest.keyClick(d.cursor_spin, Qt.Key.Key_Return)
    app.processEvents()
    assert not window.jobs and window.project == before and d.plan is not None


def test_cancel_dirty_window_close_preserves_source_marks_identity_and_plan(window, app, monkeypatch):
    d = planned(window, app); state = (d.session.selection(), d.identity, d.plan)
    window.dirty = True
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Cancel)
    assert not window.close()
    app.processEvents()
    assert window.isVisible() and window.source_viewer_dialog is d and not d._closed_flag
    assert (d.session.selection(), d.identity, d.plan) == state
    assert not d.backend.closed and not window.jobs and not window.closing


def test_declining_window_close_keeps_active_source_work_and_marks(window, app, monkeypatch):
    d = opened(window, app); d.seek(8); d.mark_in()
    gate = threading.Event(); d.backend.gate = gate; d.backend.started.clear()
    d.inspect_frame(); pump(app, d.backend.started.is_set)
    job = d.job; state = d.session.selection()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    assert not window.close()
    app.processEvents()
    assert not job.cancel.is_set() and not d._closed_flag and not window.closing
    assert window.source_viewer_dialog is d and d.session.selection() == state
    gate.set(); pump(app, lambda: not window.jobs)
    assert d.identity is not None and not d.canvas.image.isNull()


def test_dialog_fits_available_screen_and_small_logical_viewport(window, app):
    from PySide6.QtCore import QRect
    main_geometry = window.geometry()
    d = opened(window, app)
    assert d.screen().availableGeometry().contains(d.frameGeometry())
    small = QRect(0, 0, 360, 420)
    d.fit_available_screen(small); app.processEvents()
    assert small.contains(d.frameGeometry())
    assert d.width() <= 328 and d.height() <= 356
    assert d.scroll.verticalScrollBar().maximum() > 0
    for button in (d.apply_button, d.close_button):
        d.scroll.ensureWidgetVisible(button); app.processEvents()
        point = button.mapTo(d.scroll.viewport(), button.rect().center())
        assert d.scroll.viewport().rect().contains(point)
    assert window.geometry() == main_geometry


def test_source_catalog_english_chinese_key_parity():
    from lumen.i18n_catalog_source_overwrite import ZH_CN, EN
    assert EN.keys() == ZH_CN.keys()
    assert all(source == translated for source, translated in EN.items())


def test_failure_status_retranslates_raw_backend_error_live(window, app):
    d = planned(window, app)
    d.backend.changed = True; d.apply_overwrite(); pump(app, lambda: not window.jobs)
    assert 'Source changed; refresh' in d.status.text()
    set_locale('zh-CN'); app.processEvents()
    assert '源文件已改变' in d.status.text() and 'Source changed' not in d.status.text()
    set_locale('en'); app.processEvents()
    assert 'Source changed; refresh' in d.status.text()
    d._set_error('Source media is incompatible with the target track')
    set_locale('zh-CN'); app.processEvents()
    assert '源素材与目标轨道不兼容' in d.status.text()
    set_locale('en'); app.processEvents()
    assert 'incompatible' in d.status.text()
    for raw, translated in (
        ('Source viewer requires a self-contained media container; manifests and external-reference formats are unsupported.', '自包含媒体容器'),
        ('Source audio seek origin cannot be proven exactly.', '定位原点'),
        ('Source audio sample phase cannot be represented exactly.', '采样相位'),
        ('Exact compressed-audio preview is limited to the first 30 seconds; late-window codec priming cannot be proven within the source budget.', '音频原点后的前 30 秒'),
        ('Source overwrite requires every selected and affected compressed-audio range to end within the first 30 seconds of its audio stream; still inspection remains available.', '各自音频流原点后的前 30 秒'),
        ('Cannot inspect source: diagnostic α 123', '无法查看源素材'),
        ('Cannot audition source: diagnostic α 123', '无法试听源素材'),
        ('Cannot prove overwrite source timing: diagnostic α 123', '无法验证覆盖源时序'),
    ):
        d._set_error(raw, wrapped=True)
        assert raw in d.status.text()
        set_locale('zh-CN'); app.processEvents()
        assert translated in d.status.text() and raw not in d.status.text()
        if 'diagnostic' in raw:
            assert 'diagnostic α 123' in d.status.text()
        set_locale('en'); app.processEvents()
        assert raw in d.status.text()
    d.seek(1)
    set_locale('zh-CN'); app.processEvents()
    assert '源游标已移动' in d.status.text() and 'incompatible' not in d.status.text()


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('font_size', [10, 15])
def test_main_bin_entry_label_fits_narrow_window_with_large_fonts(window, app, locale, font_size):
    from PySide6.QtGui import QFont, QFontDatabase
    font = QFont('Noto Sans CJK SC' if 'Noto Sans CJK SC' in QFontDatabase.families() else app.font().family(), font_size)
    window.setFont(font); set_locale(locale)
    window.resize(1000, 768); app.processEvents()
    button = window.source_viewer_button
    window.media_scroll.ensureWidgetVisible(button); app.processEvents()
    assert window.width() == 1000
    assert '\n' in button.text()
    required_width = max(button.fontMetrics().horizontalAdvance(line) for line in button.text().splitlines())
    assert required_width <= button.width() - 24, (button.text(), required_width, button.width())
    assert button.height() >= button.fontMetrics().height() * len(button.text().splitlines()) + 12
    expected = 'Open source viewer…' if locale == 'en' else '打开源监看器…'
    assert button.accessibleName() == expected and button.toolTip() == button.text()
    point = button.mapTo(window.media_scroll.viewport(), button.rect().center())
    assert window.media_scroll.viewport().rect().contains(point)


def test_compressed_audio_prefix_limit_is_explicit_and_live_localized(window, app):
    d = opened(window, app)
    assert all(word in d.codec_note.text() for word in ('AAC/MP3/FLAC', 'including audio inside video', 'after the audio origin', 'PCM', 'still inspection'))
    from lumen.ui_help import help_text
    assert 'after that origin' in help_text() and 'phase-aligned PCM' in help_text()
    set_locale('zh-CN'); app.processEvents()
    assert '原点后的 30 秒' in d.codec_note.text() and '视频内音频' in d.codec_note.text() and 'PCM' in d.codec_note.text()
    assert '相位对齐' in help_text()


def test_compressed_audio_proof_rejection_cannot_mutate_video_overwrite(window, app, monkeypatch):
    window.project.media[0].has_audio = True
    window.refresh()
    d = planned(window, app)
    before = deepcopy((window.project, window.editor._undo, window.editor._redo,
                       window.editor.revision, window.revision, window.dirty))
    def reject_prefix(*args, **kwargs):
        raise ValueError('Source overwrite requires every selected and affected compressed-audio range to end within the first 30 seconds of its audio stream; still inspection remains available.')
    monkeypatch.setattr(d.backend, 'verify_overwrite_sources', reject_prefix)
    d.apply_overwrite(); pump(app, lambda: not window.jobs)
    assert (window.project, window.editor._undo, window.editor._redo,
            window.editor.revision, window.revision, window.dirty) == before
    assert d.plan is None and not d.apply_button.isEnabled()
    assert 'first 30 seconds' in d.status.text()
    set_locale('zh-CN'); app.processEvents()
    assert '各自音频流原点后的前 30 秒' in d.status.text()
