"""Explicit noninteractive native-package smoke test. No downloads or model use."""
import json
import hashlib
import os
import platform
from pathlib import Path
import subprocess
import sys
import time


def _shortcut_runtime_checks(window, folder, screenshots):
    """Exercise the packaged event filter/dialog on this Qt host, not OS input.

    Synthetic Qt events do not establish physical keyboard delivery, a keyboard
    layout, OS-reserved shortcuts, or a real input-method candidate window.
    The existing editor and isolated self-test preferences are restored on exit.
    """
    from copy import deepcopy
    from PySide6.QtCore import QEvent, QSettings, Qt
    from PySide6.QtGui import QInputMethodEvent, QKeyEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from .shortcuts import SETTINGS_KEY, ShortcutController, sequence_from_binding
    from .ui_shortcuts import ShortcutDialog

    app = QApplication.instance()
    controller = window.shortcuts
    original_editor = window.editor
    original_clip_ids = window.selected_clip_ids
    original_profiles, original_profile = deepcopy(controller.profiles), controller.profile
    original_setting = controller.settings.value(SETTINGS_KEY, None)
    original_language = window.ui_language
    saved = (window.revision, window.dirty, window.selection,
             window.active_selection_type, window.timeline.playhead,
             window.side_tabs.currentIndex())
    original_ime = window.timeline.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    dialog = None

    def require(condition, message):
        if not condition:
            raise RuntimeError('Frozen shortcut acceptance: ' + message)

    def focus(widget, host=window):
        host.activateWindow()
        QApplication.setActiveWindow(host)
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        app.processEvents()
        focused = QApplication.focusWidget()
        require(QApplication.activeWindow() is host and
                (focused is widget or widget.isAncestorOf(focused)),
                'the intended native Qt widget did not receive focus')

    def press(widget, binding):
        # Conversion uses the actual running OS, including Qt's macOS
        # Control/Meta exchange. Never simulate a host by changing sys.platform.
        combination = sequence_from_binding(binding)[0]
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(
                kind, combination.key(), combination.keyboardModifiers()))
        app.processEvents()

    def snapshot():
        return deepcopy((
            window.project.to_dict(), window.revision, window.dirty,
            [project.to_dict() for project in window.editor._undo],
            [project.to_dict() for project in window.editor._redo],
            window.selection, window.active_selection_type, window.timeline.playhead,
            window.preview_revision, window.preview_paths, window.preview_request,
            window.preview_pending, window.loaded_preview_mode,
        )), set(window.jobs)

    def cache_files():
        return {str(path.relative_to(window.cache)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in Path(window.cache).rglob('*') if path.is_file()}

    try:
        # Tests may mutate only this copy; the render/screenshot baseline and
        # its real speed-edit history remain untouched after this helper.
        window.editor = deepcopy(original_editor)
        window.refresh()
        window.select_clip(window.project.clips[0].id)
        window.side_tabs.setCurrentIndex(0)
        window.inspector_tabs.setCurrentIndex(0)
        clip = window.selected_clip()
        window.seek_frame(clip.start + clip.duration // 2)
        split_binding = controller.bindings['edit.split'][0]
        undo_binding = controller.bindings['edit.undo'][0]
        require(split_binding == ('Cmd+K' if sys.platform == 'darwin' else 'Ctrl+K'),
                'fresh settings did not select the host default split binding')

        # Real editor fields retain ownership of text/numeric input. Deliberate
        # split and undo keys must not mutate the document or its history.
        for control in (window.speed_edit, window.start_spin):
            window.inspector.ensureWidgetVisible(control)
            focus(control)
            before = snapshot()
            press(QApplication.focusWidget(), split_binding)
            press(QApplication.focusWidget(), undo_binding)
            require(snapshot() == before, 'text/numeric focus leaked an editor command')

        focus(window.timeline)
        window.timeline.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        before = snapshot()
        QApplication.sendEvent(window.timeline, QInputMethodEvent('拼音', []))
        press(window.timeline, split_binding)
        require(snapshot() == before, 'Qt IME preedit leaked the split command')
        QApplication.sendEvent(window.timeline, QInputMethodEvent('', []))
        unsplit = deepcopy(window.project.to_dict())
        revision, undo_count = window.revision, len(window.editor._undo)
        count = len(window.project.clips)
        press(window.timeline, split_binding)
        require(len(window.project.clips) == count + 1 and window.revision == revision + 1
                and len(window.editor._undo) == undo_count + 1,
                'host split key did not dispatch exactly once after preedit ended')
        press(window.timeline, undo_binding)
        require(window.project.to_dict() == unsplit and window.revision == revision + 2
                and len(window.editor._undo) == undo_count,
                'host undo key did not restore the exact unsplit project')

        # A visible stale caption row cannot authorize deleting a timeline clip
        # or caption. The same Delete key must work once that list owns selection.
        window.select_clip(window.project.clips[0].id)
        focus(window.bin)
        before = snapshot()
        press(window.bin, 'Delete')
        require(snapshot() == before, 'media-bin Delete used a stale timeline selection')
        window.side_tabs.setCurrentIndex(1)
        window.caption_list.blockSignals(True)
        window.caption_list.setCurrentRow(0)
        window.caption_list.blockSignals(False)
        focus(window.caption_list)
        before = snapshot()
        press(window.caption_list, 'Delete')
        require(snapshot() == before, 'stale caption-list Delete changed the editor')
        caption_id = window.project.captions[0].id
        window.caption_list.setCurrentRow(-1)
        window.caption_list.setCurrentRow(0)
        require(window.active_selection_type == 'caption', 'caption list did not own its selection')
        before_delete = deepcopy(window.project.to_dict())
        press(window.caption_list, 'Delete')
        require(not any(caption.id == caption_id for caption in window.project.captions)
                and window.project.to_dict()['clips'] == before_delete['clips'],
                'caption-list Delete did not delete only its own caption')
        focus(window.timeline)
        press(window.timeline, undo_binding)
        require(window.project.to_dict() == before_delete, 'caption Delete could not be undone')
        window.select_clip(window.project.clips[0].id)

        before_preferences, before_cache = snapshot(), cache_files()
        dialog = ShortcutDialog(controller, window)
        dialog.resize(1000, 760)
        dialog.select_command('edit.split')
        dialog.show()
        for locale in ('zh-CN', 'en'):
            window.set_ui_language(locale, persist=False)
            focus(dialog.search_edit, dialog)
            for _ in range(5): app.processEvents()
            require(dialog.windowTitle() == ('键盘快捷键' if locale == 'zh-CN' else 'Keyboard shortcuts'),
                    'shortcut dialog did not use the requested language')
            filename = f'frozen-ui-{locale}-shortcuts.png'
            require(dialog.grab().save(str(folder / filename)), 'shortcut dialog screenshot failed')
            screenshots.append(filename)

        # Record a genuine Qt key event into QKeySequenceEdit, apply through the
        # real button signal, then reopen settings using the packaged controller.
        replacement = ('Cmd' if sys.platform == 'darwin' else 'Ctrl') + '+Alt+F12'
        focus(dialog.primary_edit, dialog)
        press(dialog.primary_edit, replacement)
        require(dialog.apply_button.isEnabled(), 'recorded shortcut was not staged')
        dialog.apply_button.click()
        app.processEvents()
        require(controller.bindings['edit.split'] == (replacement,), 'Apply did not persist the recorded key')
        require(snapshot() == before_preferences and cache_files() == before_cache,
                'shortcut preferences changed project, undo history, preview cache or jobs')
        require(Path(controller.settings.fileName()).is_file(), 'shortcut settings were not written to disk')
        host = QWidget()
        restored = ShortcutController(host, QSettings(controller.settings.fileName(), QSettings.Format.IniFormat))
        restored.detach()
        try:
            require(not restored.load_warning and restored.profile == controller.profile
                    and restored.profiles == controller.profiles,
                    'shortcut settings did not round-trip through a new controller')
        finally:
            host.deleteLater()
        return {
            'event_delivery': 'QApplication.sendEvent with QKeyEvent / QInputMethodEvent',
            'host_profile': original_profile, 'host_split_binding': split_binding,
            'split_and_undo_once': True, 'text_and_numeric_focus_guards': True,
            'qt_ime_preedit_guard': True, 'stale_list_delete_ownership': True,
            'dialog_locales': ['zh-CN', 'en'], 'dialog_record_apply_reload': True,
            'preferences_preserve_project_undo_preview_cache': True,
            'physical_keyboard_tested': False, 'os_ime_integration_tested': False,
        }
    finally:
        if dialog is not None:
            dialog.reject(); dialog.deleteLater()
        QApplication.sendEvent(window.timeline, QInputMethodEvent('', []))
        window.timeline.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, original_ime)
        controller.apply_configuration(original_profiles, original_profile)
        if original_setting is None:
            controller.settings.remove(SETTINGS_KEY)
        else:
            controller.settings.setValue(SETTINGS_KEY, original_setting)
        controller.settings.sync()
        window.editor = original_editor
        window._selection_editor_token = (id(original_editor), original_editor.session_id)
        (window.revision, window.dirty, window.selection, window.active_selection_type,
         window.timeline.playhead, side_index) = saved
        window.set_selected_clip_ids(original_clip_ids, anchor=window.selection, keep_anchor=True)
        window.refresh()
        window.side_tabs.setCurrentIndex(side_index)
        window.set_ui_language(original_language, persist=False)
        app.processEvents()


def _self_test_caption_style(font_directory):
    """Use installed fixture fonts only; never download or install a face."""
    from .caption_render import CaptionRenderError, font_identity
    from .model import CaptionStyle
    families = ['Noto Sans CJK SC'] if font_directory else ['Arial', 'Helvetica', 'DejaVu Sans']
    for family in families:
        style = CaptionStyle(font_family=family, font_size=28, margin_bottom=12,
                             font_directory=font_directory)
        try:
            font_identity(style)
        except CaptionRenderError:
            if font_directory:
                raise  # A configured fixture must work, not silently fall back.
        else:
            return style
    raise RuntimeError('Native smoke requires a local Arial, Helvetica or DejaVu Sans caption font, '
                       'or the explicitly provisioned CJK test-font fixture')


def run(directory):
    """Preserve failed windowed-package diagnostics without masking failures.

    Own a new directory before importing Qt or testing assets. An existing
    directory is never reused or modified, including on failure. Diagnostics
    contain bounded synthetic-test error text, not locals/environment dumps.
    """
    folder = Path(directory).expanduser().absolute()
    folder.mkdir(parents=True, exist_ok=False)
    try:
        return _run(folder)
    except Exception as error:
        report = {'passed': False, 'platform': sys.platform,
                  'frozen': bool(getattr(sys, 'frozen', False)),
                  'exception_type': type(error).__name__,
                  'error_message': str(error)[:2048]}
        if isinstance(error, subprocess.CalledProcessError):
            report['subprocess_returncode'] = error.returncode
        try:
            # Exclusive creation preserves a diagnostic already emitted by a
            # failing stage. An unwritable disk must not replace the real error.
            with (folder / 'failure.json').open('x', encoding='utf-8') as stream:
                json.dump(report, stream, indent=2)
        except OSError:
            pass
        raise


def _run(folder):
    from .caption_runtime import require_capability
    caption_runtime = require_capability()
    (folder / "caption-runtime.json").write_text(json.dumps(caption_runtime, indent=2), encoding="utf-8")
    from .build_provenance import built_source
    source_provenance = built_source()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QUrl, qVersion
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    from .media import ffmpeg_binary, probe
    from .model import Project, Clip, Grade, Overlay, OverlayKeyframe, Caption, CaptionStyle, ClipEffect, Editor
    from .sticker_assets import builtin_stickers, load_sticker
    from .sound_library import builtin_sounds, verify_sound
    from .render import RenderEngine
    sounds = builtin_sounds()
    if (len(sounds) != 77 or sum(entry.origin == 'procedural' for entry in sounds) != 24
            or sum(entry.origin == 'external-cc0' for entry in sounds) != 53):
        raise RuntimeError('Packaged sound library must contain 24 original and 53 external CC0 sounds')
    for entry in sounds:
        verify_sound(entry)
    app = QApplication.instance() or QApplication([])
    # Windows' offscreen backend may expose no OS fonts. The explicit CI-only
    # fixture is external to the package and must also be registered with Qt,
    # independently of FFmpeg's caption font directory.
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QIcon, QTextLayout
    icon = QIcon(str(Path(__file__).resolve().parent / 'assets' / 'app' / 'cutcat.png'))
    if icon.isNull() or icon.pixmap(64, 64).isNull():
        raise RuntimeError('Frozen application icon asset could not be loaded')
    app.setWindowIcon(icon)
    font_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR', '')
    ui_fixture_registered = False
    if font_directory:
        font_file = Path(font_directory) / 'NotoSansCJKsc-Regular.otf'
        expected = '2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b'
        if hashlib.sha256(font_file.read_bytes()).hexdigest() != expected:
            raise RuntimeError('Unexpected native UI test-font checksum')
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if font_id < 0 or not families:
            raise RuntimeError('Qt could not register external native UI test font')
        font = QFont(app.font())
        info = QFontInfo(font)
        if not info.family() or info.pixelSize() <= 0:
            font.setFamily(families[0])
        font.setFamilies(list(dict.fromkeys([*font.families(), *families])))
        app.setFont(font)
        ui_fixture_registered = True
    layout = QTextLayout('CutCatABCxyz0123456789' + ('剪辑猫' if font_directory else ''), app.font())
    layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    runs = layout.glyphRuns()
    if not runs or any(index == 0 for run in runs for index in run.glyphIndexes()):
        raise RuntimeError('Frozen UI font renders missing glyphs')
    for run in runs:
        for index in run.glyphIndexes():
            mask = run.rawFont().alphaMapForGlyph(index)
            if mask.isNull() or not any(bytes(mask.constBits())):
                raise RuntimeError('Frozen UI glyph has no visible raster pixels')
    # Original procedural landscape, generated locally for readable UI evidence.
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QImage, QPainter, QColor, QLinearGradient, QPolygonF
    background = QImage(640, 360, QImage.Format.Format_RGB32)
    painter = QPainter(background)
    gradient = QLinearGradient(0, 0, 0, 360)
    gradient.setColorAt(0, QColor('#203b5f')); gradient.setColorAt(1, QColor('#d9c3a3'))
    painter.fillRect(background.rect(), gradient)
    painter.setPen(QColor('#efc986')); painter.setBrush(QColor('#efc986'))
    painter.drawEllipse(QPointF(470, 88), 35, 35)
    for color, points in [('#506b7d', [(0,230),(120,105),(260,245),(400,150),(640,260),(640,360),(0,360)]),
                          ('#274b65', [(0,300),(170,210),(320,295),(490,175),(640,255),(640,360),(0,360)])]:
        painter.setPen(QColor(color)); painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(*point) for point in points]))
    painter.end(); background.save(str(folder / 'original-scene.png'))
    scene = folder / 'source.mp4'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-n', '-loop', '1', '-framerate', '24', '-i', str(folder / 'original-scene.png'), '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                    '-colorspace', 'bt709', '-c:a', 'aac', '-t', '1', str(scene)], check=True, timeout=30)
    media = probe(scene)
    asset_path = builtin_stickers()[0][1]
    asset = load_sticker(asset_path)
    # Original tiny 3D look fixture: exercise LUT parsing and mixed application
    # in the actual packaged preview/export graph, not a UI-only path.
    lut_path = folder / 'original-warm-look.cube'
    lut_rows = ['TITLE "Original native smoke look"', 'LUT_3D_SIZE 2']
    for blue in (0, 1):
        for green in (0, 1):
            for red in (0, 1):
                lut_rows.append(f'{red * .95 + .03} {green * .98} {blue * .9}')
    lut_path.write_text('\n'.join(lut_rows) + '\n', encoding='utf-8')
    project = Project(name='Native runtime self-test', width=640, height=360, fps_num=24,
                      media=[media], clips=[Clip('clip', media.id, 0, 0, 0, 24,
                                                grade=Grade(linear_exposure=.1, lift=[.015, -.005, .002], lut_path=str(lut_path), lut_strength=.4), effects=[ClipEffect('edge', 'vignette', .1)])],
                      overlays=[Overlay('star', str(asset_path), asset.digest, 2, 20,
                                        keyframes=[OverlayKeyframe(0, 10, 8, .15, .8)])])
    font_directory = os.environ.get('LUMEN_TEST_CJK_FONT_DIR', '')
    project.captions = [Caption('caption', 3, 17, '中文测试' if font_directory else 'Test', 'zh' if font_directory else 'en')]
    project.caption_style = _self_test_caption_style(font_directory)
    project.save(folder / 'project.lumen')
    project = Project.load(folder / 'project.lumen')
    engine = RenderEngine(timeout=60)
    try:
        output = engine.render(project, folder / 'output.mp4')
        # A real frozen retime render, separate from the screenshot baseline.
        from fractions import Fraction
        retimed = Project.from_dict(project.to_dict())
        retimed.overlays = []; retimed.captions = []
        retime_editor = Editor(retimed)
        retime_editor.set_clip_speed('clip', Fraction(3, 2))
        retimed = retime_editor.project
        if retimed.clips[0].duration != 16:
            raise RuntimeError('Frozen retime did not preserve the selected source range')
        retimed.save(folder / 'retimed-project.lumen')
        retimed = Project.load(folder / 'retimed-project.lumen')
        retime_output = engine.render(retimed, folder / 'retimed-output.mp4')
        retime_media = probe(retime_output)
        if abs(retime_media.duration - 16 / 24) > 1 / 24:
            raise RuntimeError('Frozen retime output duration is incorrect')
        from .stabilization_smoke import run_stabilization_smoke, capture_stabilization_ui
        stabilized_project, stabilized_preview, stabilization_report = run_stabilization_smoke(engine, folder)
        from .restoration_smoke import run_restoration_smoke, capture_restoration_ui
        restored_project, restored_preview, restoration_report = run_restoration_smoke(engine, folder)
        from .color_pipeline_smoke import run_color_pipeline_smoke, capture_color_pipeline_ui
        color_project, color_report = run_color_pipeline_smoke(engine, folder, project.caption_style)
        preview = engine.preview(project)
        png = engine.frame(project, 8, width=640)
        if not png.startswith(b'\x89PNG'):
            raise RuntimeError('Native preview extraction did not produce PNG')
        (folder / 'preview.png').write_bytes(png)
        sink = QVideoSink(); player = QMediaPlayer(); player.setVideoOutput(sink)
        frames = []
        def received(frame):
            image = frame.toImage()
            if not image.isNull(): frames.append((image.width(), image.height()))
        sink.videoFrameChanged.connect(received)
        player.setSource(QUrl.fromLocalFile(str(preview))); player.play()
        deadline = time.monotonic() + 20
        while not frames and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.01)
        error = player.errorString(); player.stop(); player.setSource(QUrl()); app.processEvents()
        if not frames:
            raise RuntimeError('Native Qt video decoding failed: ' + error)
        # Exercise the actual frozen MainWindow, not only media imports. These
        # images contain a real rendered frame from the shared evaluator.
        from PySide6.QtCore import QSettings
        from .model import Editor
        from .ui import MainWindow
        window = MainWindow(workspace_settings=QSettings(str(folder / 'ui-settings.ini'), QSettings.Format.IniFormat))
        screenshots = []
        try:
            window.editor = Editor(project); window.refresh(); window.select_clip('clip')
            window.resize(1920, 1080); window.show()
            window.vertical_split.setSizes([720, 180])
            window.preview_paths[(window.revision, 'graded')] = preview
            window.activate_preview(preview, window.revision, 'graded')
            window.player.play()
            ui_deadline = time.monotonic() + 10
            while (window.video.image.isNull() or window.player.position() < 333) and time.monotonic() < ui_deadline:
                app.processEvents(); time.sleep(.01)
            window.player.pause()
            if window.video.image.isNull():
                raise RuntimeError('Frozen MainWindow viewer did not decode a real preview frame')
            window.scopes.set_image(window.video.image)
            from .i18n import qt_translation_loaded
            from .branding import display_name
            invariant = (project.to_dict(), window.revision, window.dirty, window.selection)
            for locale in ('zh-CN', 'en'):
                window.set_ui_language(locale, persist=False)
                for _ in range(5): app.processEvents()
                if locale == 'zh-CN' and not qt_translation_loaded():
                    raise RuntimeError('Frozen standard-dialog Chinese translation was not loaded')
                if not window.windowTitle().endswith(display_name(locale)):
                    raise RuntimeError('Frozen UI brand does not match actual UI language')
                if invariant != (project.to_dict(), window.revision, window.dirty, window.selection):
                    raise RuntimeError('Language switch changed editing state')
                for mode in ('simple', 'professional'):
                    window.set_workspace_mode(mode, persist=False)
                    if mode == 'professional':
                        window.inspector_tabs.setCurrentIndex(1); window.color_tabs.setCurrentIndex(1)
                    for _ in range(10): app.processEvents()
                    if mode == 'professional':
                        # Scroll only after the native font/layout has settled.
                        window.inspector.ensureWidgetVisible(window.wheel_controls['lift'].spins[-1])
                        # Keep the inspector's tab labels fully visible in this overview.
                        window.inspector.verticalScrollBar().setValue(0)
                        for _ in range(10): app.processEvents()
                    if window.width() != 1920 or window.height() != 1080:
                        raise RuntimeError('Frozen UI does not fit the requested 1920x1080 client area')
                    filename = f'frozen-ui-{locale}-{mode}.png'
                    if not window.grab().save(str(folder / filename)):
                        raise RuntimeError('Frozen UI screenshot failed')
                    screenshots.append(filename)
                    if mode == 'professional':
                        window.inspector.ensureWidgetVisible(window.remove_lut_button)
                        for _ in range(10): app.processEvents()
                        if window.lut_strength.value() != .4:
                            raise RuntimeError('Frozen LUT strength control did not retain project value')
                        lut_filename = f'frozen-ui-{locale}-lut.png'
                        if not window.grab().save(str(folder / lut_filename)):
                            raise RuntimeError('Frozen LUT UI screenshot failed')
                        screenshots.append(lut_filename)
                        window.select_clip_effect('edge')
                        for _ in range(10): app.processEvents()
                        if window.side_tabs.currentWidget() is not window.clip_effects_panel:
                            raise RuntimeError('Effects did not open its independent panel')
                        effect_filename = f'frozen-ui-{locale}-effects.png'
                        if not window.grab().save(str(folder / effect_filename)):
                            raise RuntimeError('Frozen Effects UI screenshot failed')
                        screenshots.append(effect_filename)
                        window.select_overlay('star')
                        for _ in range(10): app.processEvents()
                        if window.side_tabs.currentWidget() is not window.stickers_panel:
                            raise RuntimeError('Sticker did not open its independent panel')
                        sticker_filename = f'frozen-ui-{locale}-stickers.png'
                        if not window.grab().save(str(folder / sticker_filename)):
                            raise RuntimeError('Frozen Stickers UI screenshot failed')
                        screenshots.append(sticker_filename)
                        window.side_tabs.setCurrentWidget(window.sounds_panel)
                        for _ in range(10): app.processEvents()
                        if window.sound_list.count() < 24 or not window.sound_insert_button.isEnabled():
                            raise RuntimeError('Packaged sound browser is unavailable')
                        sound_filename = f'frozen-ui-{locale}-sounds.png'
                        if not window.grab().save(str(folder / sound_filename)):
                            raise RuntimeError('Frozen Sounds UI screenshot failed')
                        screenshots.append(sound_filename)
                        window.select_clip('clip')
                        window.side_tabs.setCurrentIndex(0)
            window.select_clip('clip')
            window.side_tabs.setCurrentIndex(0); window.inspector_tabs.setCurrentIndex(0)
            anchors = ([c.__dict__.copy() for c in window.project.captions],
                       [o.__dict__.copy() for o in window.project.overlays])
            window.speed_edit.setText('1.5'); window.apply_speed()
            if window.selected_clip().speed != Fraction(3, 2):
                raise RuntimeError('Frozen speed control did not apply exact timing')
            if anchors != ([c.__dict__.copy() for c in window.project.captions],
                           [o.__dict__.copy() for o in window.project.overlays]):
                raise RuntimeError('Speed change moved independent caption/sticker anchors')
            speed_preview = engine.preview(window.project)
            window.activate_preview(speed_preview, window.revision, 'graded')
            window.player.play()
            speed_deadline = time.monotonic() + 10
            while (window.video.image.isNull() or window.player.position() < 333) and time.monotonic() < speed_deadline:
                app.processEvents(); time.sleep(.01)
            window.player.pause()
            if window.video.image.isNull():
                raise RuntimeError('Frozen speed preview was not decoded')
            window.scopes.set_image(window.video.image)
            for locale in ('zh-CN', 'en'):
                window.set_ui_language(locale, persist=False)
                window.inspector.verticalScrollBar().setValue(0)
                for _ in range(10): app.processEvents()
                filename = f'frozen-ui-{locale}-speed.png'
                if not window.grab().save(str(folder / filename)):
                    raise RuntimeError('Frozen speed UI screenshot failed')
                screenshots.append(filename)
            shortcut_report = _shortcut_runtime_checks(window, folder, screenshots)
            color_report['ui'] = capture_color_pipeline_ui(window, color_project, folder, screenshots)
            stabilization_report.update(capture_stabilization_ui(
                window, stabilized_project, stabilized_preview, folder, screenshots))
            restoration_report.update(capture_restoration_ui(
                window, restored_project, restored_preview, folder, screenshots))
        finally:
            window.dirty = False; window.close(); app.processEvents()
        from .audio_timing import ensure_retime_audio_capability
        audio_retime_report = ensure_retime_audio_capability(ffmpeg_binary(), 30)
        result = {'passed': True, 'caption_shaping': caption_runtime, 'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
                  'built_source': source_provenance,
                  'machine': platform.machine(), 'python': platform.python_version(), 'qt_version': qVersion(),
                  'video_frames_received': len(frames), 'native_ui_screenshots': screenshots, 'native_ui_preview_decoded': True, 'ui_glyphs_verified': True,
                  'ui_locales_tested': ['zh-CN', 'en'], 'independent_effects_stickers_panels': True, 'locale_state_invariant': True, 'qt_chinese_dialog_translation': True,
                  'external_ui_test_font_registered': ui_fixture_registered,
                  'application_icon_asset_loaded': True, 'shortcut_runtime_checks': shortcut_report,
                  'resolved_ui_font': QFontInfo(app.font()).family(),
                  'resolved_caption_font': project.caption_style.font_family, 'frame_size': frames[0],
                  'bundled_sticker_count': len(builtin_stickers()), 'bundled_sound_count': len(sounds),
                  'caption_render_exercised': True, 'cube_lut_render_exercised': True, 'cube_lut_strength': .4, 'external_cjk_test_font_used': bool(font_directory), 'rendered_file': output.name,
                  'constant_retime_render_exercised': True, 'retime_rate': '3/2',
                  'audio_retime_capability': audio_retime_report,
                  'retime_source_frames': 24, 'retime_output_frames': 16,
                  'stabilization_runtime_checks': stabilization_report,
                  'restoration_runtime_checks': restoration_report,
                  'color_pipeline_runtime_checks': color_report,
                  'physical_audio_output_tested': False, 'model_inference_tested': False}
        (folder / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        return 0
    finally:
        engine.close()
