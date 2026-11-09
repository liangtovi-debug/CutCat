"""Capture and measure the actual editor, without rendering or downloading models.

Examples:
  python scripts/capture_ui_alignment.py --output artifacts/ui-alignment
  python scripts/capture_ui_alignment.py --output build/ui-150 --scale 1.5

The default driver starts a separate Qt process for each device scale. All
sizes are logical client-area pixels. PNG dimensions are physical pixels.
A passing JSON report means the named geometry checks passed; screenshots still
need human visual review. It is not a claim of native-platform testing unless
this script actually ran on that platform. The fixture contains synthetic media
and bilingual text; it does not claim to show a decoded or rendered video.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SIZES = ('1366x768', '1440x900', '1920x1080')
STATES = ('clip', 'clip-edit', 'clip-restoration', 'clip-audio', 'clip-wheels', 'clip-curves', 'captions', 'caption-appearance',
          'tts', 'tts-audition', 'local-ai', 'local-ai-search', 'stickers', 'clip-effects')
MIXED_TEXT = ('你好，世界。 Hello, world.\n'
              '中英文字幕排版测试。 Bilingual caption alignment, punctuation, and readable wrapping.')
LIMITS = {
    'coordinate_tolerance_logical_px': 2,
    'baseline_tolerance_font_height_fraction': .20,
    'minimum_edit_width_em': 2,
    'physical_pixel_rounding_tolerance': 1,
    'scope': 'Visible real Qt layouts and their vertically scrollable content; no mock widgets.',
}


def parse_sizes(value):
    sizes = []
    for token in value.split(','):
        width, height = map(int, token.lower().split('x'))
        if width < 1 or height < 1:
            raise ValueError('Window sizes must be positive.')
        sizes.append((width, height))
    return sizes


def run_worker(args):
    # All scale/platform configuration must precede the first Qt import.
    os.environ['QT_SCALE_FACTOR'] = str(args.scale)
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runtime = out / '_runtime'
    runtime.mkdir(exist_ok=True)
    os.environ['XDG_CONFIG_HOME'] = str(runtime / 'config')
    os.environ['XDG_CACHE_HOME'] = str(runtime / 'cache')
    os.environ['XDG_DATA_HOME'] = str(runtime / 'data')
    tempfile.tempdir = str(runtime)
    sys.path.insert(0, str(ROOT))

    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QPoint, QRect, QSettings, Qt, qVersion
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QAccessible
    from PySide6.QtWidgets import (
        QApplication, QAbstractSpinBox, QComboBox, QFormLayout, QHBoxLayout, QLabel,
        QLayout, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
        QStyle, QStyleOptionComboBox, QStyleOptionFrame, QStyleOptionButton, QWidget,
    )
    from lumen.model import Caption, CaptionStyle, Clip, ClipEffect, Editor, Media, Overlay, Project
    from lumen.ui import MainWindow
    from lumen.i18n import set_locale
    from lumen.sticker_assets import load_sticker

    app = QApplication.instance() or QApplication([])
    app.setApplicationName('LumenEdit UI geometry acceptance')
    for fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(fmt, QSettings.Scope.UserScope, str(runtime / 'settings'))
    # Use an explicit fixture when provisioned, otherwise an installed CJK font.
    # Nothing is downloaded, installed system-wide, or silently substituted with tofu.
    font_dir = args.font_dir or os.environ.get('LUMEN_TEST_CJK_FONT_DIR')
    candidates = ([Path(font_dir) / 'NotoSansCJKsc-Regular.otf'] if font_dir else [])
    candidates += [Path('/tmp/lumen-test-fonts/Sans2.004/NotoSansCJKsc-Regular.otf')]
    font_info = {'source': 'system', 'family': None}
    for path in candidates:
        if path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                font_info = {'source': str(path), 'family': families[0],
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                break
    if not font_info['family']:
        families = QFontDatabase.families()
        font_info['family'] = next((name for name in (
            'Noto Sans CJK SC', 'Noto Sans SC', 'Microsoft YaHei', 'PingFang SC',
            'Heiti SC', 'WenQuanYi Zen Hei') if name in families), app.font().family())
    font = QFont(app.font())
    native_family = font.family()
    if args.ui_font == 'cjk':
        font.setFamily(font_info['family'])
    else:
        font.setFamilies([native_family, font_info['family']])
    app.setFont(font)
    font_info['native_ui_family'] = native_family
    font_info['ui_font_mode'] = args.ui_font
    settings = QSettings(str(runtime / 'workspace.ini'), QSettings.Format.IniFormat)
    settings.clear()
    settings.setValue('language', args.locale)
    set_locale(args.locale)
    window = MainWindow(workspace_settings=settings)
    source = out / 'synthetic-source.png'
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(QColor('#37655f'))
    if not image.save(str(source)):
        raise RuntimeError('Cannot save the local synthetic fixture.')
    project = Project(name='排版验收 · UI alignment',
                      media=[Media('media', str(source), '海边散步 · Coastal walk',
                                   10, 1280, 720, color_space='Rec.709')],
                      clips=[Clip('clip', 'media', 0, 0, 0, 300,
                                  effects=[ClipEffect('blur', 'blur', 2.)])],
                      captions=[Caption('caption', 0, 150, MIXED_TEXT, 'zh')],
                      caption_style=CaptionStyle(font_family=font_info['family']))
    sticker = ROOT / 'lumen/assets/stickers/spark_star.png'
    project.overlays.append(Overlay('sticker', str(sticker), load_sticker(sticker).digest, 0, 150))
    window.editor = Editor(project)
    if 'clip-roll' in args.states.split(','):
        # A real adjacent pair lets this opt-in state measure the full staged
        # range preview, not only the no-neighbor diagnostic.
        window.editor.split_clip('clip', 150)
    window.selection = 'clip'
    window.refresh()
    window.show()

    def pump():
        # Geometry can require more than one queued layout pass after tab switches.
        for _ in range(4):
            app.processEvents()

    pump()
    aliases = {id(value): name for name, value in vars(window).items() if isinstance(value, QWidget)}
    for collection in ('grade_spins', 'overlay_transform', 'overlay_keyframe_values'):
        aliases.update({id(value): f'{collection}.{name}' for name, value in getattr(window, collection).items()})
    for model, fields in window.ai_fields.items():
        aliases.update({id(value): f'ai_fields.{model}.{name}' for name, value in fields.items()})

    def name(widget):
        if id(widget) in aliases:
            return aliases[id(widget)]
        text = widget.text() if isinstance(widget, (QLabel, QPushButton, QLineEdit)) else ''
        return widget.objectName() or f'{type(widget).__name__}:{text[:90]}'

    def rect_values(rect):
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    def global_rect(widget, rect=None):
        rect = rect or widget.rect()
        return QRect(widget.mapTo(window, rect.topLeft()), rect.size())

    def accessible_name(widget):
        interface = QAccessible.queryAccessibleInterface(widget)
        return interface.text(QAccessible.Text.Name) if interface else widget.accessibleName()

    def label_content(label):
        rect = label.contentsRect()
        margin = label.margin()
        rect.adjust(margin, margin, -margin, -margin)
        indent = label.indent()
        if indent < 0:
            indent = label.fontMetrics().horizontalAdvance('x') // 2 if label.frameWidth() else 0
        if label.alignment() & Qt.AlignmentFlag.AlignLeft:
            rect.adjust(indent, 0, 0, 0)
        return rect

    def baseline(widget):
        target = widget
        if isinstance(widget, QAbstractSpinBox):
            target = widget.findChild(QLineEdit)
        if isinstance(target, QLineEdit):
            opt = QStyleOptionFrame()
            target.initStyleOption(opt)
            rect = target.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt, target)
            margins = target.textMargins()
            rect.adjust(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        elif isinstance(target, QComboBox):
            opt = QStyleOptionComboBox()
            target.initStyleOption(opt)
            rect = target.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                                 QStyle.SubControl.SC_ComboBoxEditField, target)
        elif isinstance(target, QLabel):
            rect = label_content(target)
        else:
            return None
        fm = target.fontMetrics()
        if isinstance(target, QLabel) and target.alignment() & Qt.AlignmentFlag.AlignTop:
            y = rect.y() + fm.ascent()
        elif isinstance(target, QLabel) and target.alignment() & Qt.AlignmentFlag.AlignBottom:
            y = rect.bottom() + 1 - fm.height() + fm.ascent()
        else:
            y = rect.y() + (rect.height() - fm.height()) / 2 + fm.ascent()
        return target.mapTo(window, QPoint(0, 0)).y() + y

    def text_rect(widget):
        if isinstance(widget, QAbstractSpinBox):
            widget = widget.findChild(QLineEdit)
        if isinstance(widget, QComboBox):
            opt = QStyleOptionComboBox(); widget.initStyleOption(opt)
            return widget.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt,
                                                 QStyle.SubControl.SC_ComboBoxEditField, widget)
        opt = QStyleOptionFrame(); widget.initStyleOption(opt)
        rect = widget.style().subElementRect(QStyle.SubElement.SE_LineEditContents, opt, widget)
        m = widget.textMargins(); rect.adjust(m.left(), m.top(), -m.right(), -m.bottom())
        return rect

    def activate(state):
        window.select_clip('clip')
        window.side_tabs.setCurrentIndex(0)
        window.inspector_tabs.setCurrentIndex({'clip-transform-apply': 0, 'clip-transform': 0, 'clip-ripple': 0, 'clip-edit': 0, 'clip-roll': 0, 'clip-restoration': 0, 'clip-audio': 2}.get(state, 1))
        if state == 'clip-roll':
            window.roll_cut_spin.setValue(160)
        target = {'clip-transform-apply': window.apply_transform_button, 'clip-transform': window.transform_group, 'clip-ripple': window.ripple_note, 'clip-roll': window.apply_roll_button, 'clip-restoration': window.restoration_group, 'clip': window.grade_spins['contrast'], 'clip-wheels': window.wheel_controls['lift'],
                  'clip-curves': window.curve_editor}.get(state)
        window.color_tabs.setCurrentIndex({'clip-wheels': 1, 'clip-curves': 2}.get(state, 0))
        if state in ('captions', 'caption-appearance', 'tts', 'tts-audition'):
            window.select_caption('caption')
            target = {'captions': window.caption_text, 'caption-appearance': window.caption_font,
                      'tts': window.tts_model, 'tts-audition': window.tts_preview_text}[state]
        elif state in ('local-ai', 'local-ai-search'):
            window.side_tabs.setCurrentWidget(window.ai_panel)
            target = window.ai_query if state.endswith('search') else None
        elif state in ('stickers', 'clip-effects'):
            window.side_tabs.setCurrentWidget(window.clip_effects_panel if state == 'clip-effects' else window.stickers_panel)
            if state == 'stickers':
                window.select_overlay('sticker')
                target = window.overlay_start
        pump()
        for scroll in window.side_tabs.currentWidget().findChildren(QScrollArea):
            scroll.verticalScrollBar().setValue(0)
        current = window.side_tabs.currentWidget()
        if isinstance(current, QScrollArea):
            current.verticalScrollBar().setValue(0)
        if target:
            parent = target.parentWidget()
            while parent and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent:
                y = target.mapTo(parent.widget(), QPoint()).y()
                parent.verticalScrollBar().setValue(y - max(0, (parent.viewport().height() - target.height()) // 2))
        pump()

    records = []
    failures = []
    tol = LIMITS['coordinate_tolerance_logical_px']

    def measure(mode, size, state):
        problems = []
        checks = {'layout_items': 0, 'labels': 0, 'edit_rects': 0, 'form_rows': 0,
                  'scroll_reachability': 0, 'baseline_pairs': 0, 'accessible_fields': 0,
                  'button_text_rects': 0, 'elided_combo_full_text': 0}
        rows = []
        edits = []
        wraps = []
        def issue(code, widget, **details):
            problems.append({'code': code, 'widget': name(widget), **details})
        if (window.width(), window.height()) != size:
            issue('requested_window_size_changed', window, requested=list(size),
                  actual=[window.width(), window.height()])
        if abs(window.devicePixelRatioF() - args.scale) > .01:
            issue('device_scale_mismatch', window, requested=args.scale,
                  actual=window.devicePixelRatioF())
        # Geometric sibling intersections detect the real baseline failure:
        # minimum-height viewer/timeline painted underneath other layout items.
        for layout in window.findChildren(QLayout):
            parent = layout.parentWidget()
            if not parent or not parent.isVisible():
                continue
            items = []
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.isEmpty() or (item.widget() and not item.widget().isVisible()):
                    continue
                if not item.widget() and not item.layout():
                    continue
                rect = item.geometry()
                checks['layout_items'] += 1
                if not parent.rect().adjusted(-tol, -tol, tol, tol).contains(rect):
                    issue('layout_item_outside_parent', item.widget() or parent,
                          item=rect_values(rect), parent=rect_values(parent.rect()))
                for other, other_rect in items:
                    overlap = rect.intersected(other_rect)
                    if overlap.width() > tol and overlap.height() > tol:
                        issue('layout_siblings_overlap', item.widget() or parent,
                              other=name(other), overlap=rect_values(overlap))
                items.append((item.widget() or parent, rect))
        for label in window.findChildren(QLabel):
            if not label.isVisible() or not label.text() or label.pixmap() and not label.pixmap().isNull():
                continue
            checks['labels'] += 1
            fm = label.fontMetrics(); rect = label_content(label)
            flags = int(label.alignment()) | int(Qt.TextFlag.TextExpandTabs)
            if label.wordWrap():
                flags |= int(Qt.TextFlag.TextWordWrap)
            needed = fm.boundingRect(QRect(0, 0, max(1, rect.width()), 100000), flags, label.text())
            wraps.append({'widget': name(label), 'wrapped': label.wordWrap(),
                          'available': rect_values(rect), 'text_bounds': rect_values(needed)})
            if needed.height() > rect.height() + tol:
                issue('label_text_height_clipped', label, required=needed.height(), available=rect.height())
            if needed.width() > rect.width() + tol:
                issue('label_text_width_clipped', label, required=needed.width(), available=rect.width())
        for widget in window.findChildren(QWidget):
            if not widget.isVisible() or not isinstance(widget, (QAbstractSpinBox, QComboBox, QLineEdit)):
                continue
            # The spinbox's internal line edit is measured through the spinbox.
            if isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QAbstractSpinBox):
                continue
            checks['edit_rects'] += 1
            rect = text_rect(widget)
            fm = widget.fontMetrics()
            info = {'widget': name(widget), 'outer': rect_values(global_rect(widget)),
                    'edit_rect': rect_values(rect), 'font_height': fm.height(),
                    'baseline': baseline(widget), 'accessible_name': accessible_name(widget)}
            edits.append(info)
            if rect.height() + tol < fm.height():
                issue('edit_text_height_clipped', widget, required=fm.height(), available=rect.height())
            if rect.width() + tol < 2 * fm.horizontalAdvance('0'):
                issue('edit_text_width_unusable', widget, available=rect.width())
            if isinstance(widget, QComboBox) and any(char in widget.currentText() for char in ('\n', '\r')):
                issue('combo_multiline_display_text', widget, text=widget.currentText(),
                      reason='Single-line combo labels must flatten caption line breaks; original text remains in the model.')
            if isinstance(widget, QComboBox) and fm.horizontalAdvance(widget.currentText()) > rect.width() + tol:
                checks['elided_combo_full_text'] += 1
                if widget.currentText() not in widget.toolTip():
                    issue('elided_combo_missing_full_text', widget, text=widget.currentText())
            if isinstance(widget, QAbstractSpinBox) and fm.horizontalAdvance(widget.text()) > rect.width() + tol:
                issue('numeric_value_clipped', widget, text=widget.text(), available=rect.width())
        for button in window.findChildren(QPushButton):
            if not button.isVisible() or not button.text():
                continue
            opt = QStyleOptionButton(); button.initStyleOption(opt)
            rect = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, button)
            text = button.text().replace('&&', '\0').replace('&', '').replace('\0', '&')
            fm = button.fontMetrics()
            checks['button_text_rects'] += 1
            if fm.horizontalAdvance(text) > rect.width() + tol or fm.height() > rect.height() + tol:
                issue('button_text_clipped', button, text=text, available=rect_values(rect),
                      required=[fm.horizontalAdvance(text), fm.height()])
        for form in window.findChildren(QFormLayout):
            parent = form.parentWidget()
            if not parent or not parent.isVisible():
                continue
            field_edges = []
            for row in range(form.rowCount()):
                li = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
                fi = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
                label = li.widget() if li else None
                if not isinstance(label, QLabel) or not label.isVisible() or not fi:
                    continue
                field = fi.widget()
                if field is None and fi.layout():
                    field = next((fi.layout().itemAt(i).widget() for i in range(fi.layout().count())
                                  if fi.layout().itemAt(i).widget()), None)
                if field is None or not field.isVisible():
                    continue
                checks['form_rows'] += 1
                same_line = abs(label.geometry().center().y() - fi.geometry().center().y()) < max(label.height(), fi.geometry().height()) / 2
                record = {'label': label.text(), 'field': name(field),
                          'label_rect': rect_values(global_rect(label)),
                          'field_rect': rect_values(global_rect(field)), 'wrapped_row': not same_line}
                if same_line:
                    field_edges.append(fi.geometry().left())
                single = isinstance(field, (QAbstractSpinBox, QComboBox, QLineEdit))
                label_text_bounds = label.fontMetrics().boundingRect(QRect(0, 0, max(1, label_content(label).width()), 100000), int(Qt.TextFlag.TextWordWrap), label.text())
                if single and same_line and (label.alignment() & Qt.AlignmentFlag.AlignTop or label_text_bounds.height() <= label.fontMetrics().height() + tol):
                    a, b = baseline(label), baseline(field)
                    delta = abs(a - b)
                    allowed = max(tol, label.fontMetrics().height() * LIMITS['baseline_tolerance_font_height_fraction'])
                    checks['baseline_pairs'] += 1
                    record.update(label_baseline=a, field_baseline=b, baseline_delta=delta, allowed=allowed)
                    if delta > allowed:
                        issue('form_text_baseline_misaligned', field, label=label.text(), delta=delta, allowed=allowed)
                if single:
                    checks['accessible_fields'] += 1
                    if not accessible_name(field):
                        issue('form_field_missing_accessible_name', field, label=label.text())
                rows.append(record)
            if field_edges and max(field_edges) - min(field_edges) > tol:
                issue('form_field_left_edges_misaligned', parent, edges=field_edges)
        # Color-wheel RGB rows are QHBoxLayouts, not QFormLayouts. Check their
        # actual label/edit baselines too; otherwise custom controls escape QA.
        for layout in window.findChildren(QHBoxLayout):
            widgets = [layout.itemAt(i).widget() for i in range(layout.count())]
            widgets = [w for w in widgets if w is not None and w.isVisible()]
            if len(widgets) != 2 or not isinstance(widgets[0], QLabel) or not isinstance(widgets[1], (QAbstractSpinBox, QLineEdit, QComboBox)):
                continue
            label, field = widgets
            if '\n' in label.text():
                continue
            a, b = baseline(label), baseline(field)
            allowed = max(tol, label.fontMetrics().height() * LIMITS['baseline_tolerance_font_height_fraction'])
            checks['baseline_pairs'] += 1
            rows.append({'label': label.text(), 'field': name(field), 'layout': 'QHBoxLayout',
                         'label_baseline': a, 'field_baseline': b, 'baseline_delta': abs(a - b), 'allowed': allowed})
            if abs(a - b) > allowed:
                issue('inline_text_baseline_misaligned', field, label=label.text(), delta=abs(a - b), allowed=allowed)
        # Every visible content control must be reachable without horizontal
        # scrolling, even if its normal vertical position is below the viewport.
        for scroll in window.findChildren(QScrollArea):
            if not scroll.isVisible() or not scroll.widget():
                continue
            if scroll.widget() is window.timeline:
                # Timeline is intentionally two-dimensionally scrollable.
                content = scroll.widget()
                if content.height() > scroll.viewport().height() + tol and scroll.verticalScrollBar().maximum() == 0:
                    issue('timeline_bottom_unreachable', scroll)
                continue
            checks['scroll_reachability'] += 1
            if scroll.horizontalScrollBar().maximum() > 0:
                issue('inspector_horizontal_overflow', scroll, scroll_max=scroll.horizontalScrollBar().maximum(),
                      content_width=scroll.widget().width(), viewport_width=scroll.viewport().width())
            old = scroll.verticalScrollBar().value()
            old_x = scroll.horizontalScrollBar().value()
            scroll.horizontalScrollBar().setValue(0)
            for widget in scroll.widget().findChildren(QWidget):
                if not widget.isVisible() or not isinstance(widget, (QAbstractSpinBox, QLineEdit, QComboBox, QPushButton, QPlainTextEdit)):
                    continue
                # ensureWidgetVisible prioritizes an editor's cursor rectangle;
                # here we test whether the whole control can actually be viewed.
                y = widget.mapTo(scroll.widget(), QPoint()).y()
                scroll.verticalScrollBar().setValue(y - max(0, (scroll.viewport().height() - widget.height()) // 2))
                pump()
                rect = QRect(widget.mapTo(scroll.viewport(), QPoint()), widget.size())
                vp = scroll.viewport().rect().adjusted(-tol, -tol, tol, tol)
                if rect.height() <= vp.height() and not vp.contains(rect):
                    issue('scroll_control_unreachable', widget, control=rect_values(rect), viewport=rect_values(vp))
            scroll.verticalScrollBar().setValue(old)
            scroll.horizontalScrollBar().setValue(old_x); pump()
        return {'checks': checks, 'violations': problems, 'forms': rows, 'edit_controls': edits,
                'labels': wraps}

    try:
        missing = [char for char in set('中文测试你好世界') if not window.fontMetrics().inFontUcs4(ord(char))]
        if missing:
            failures.append({'code': 'cjk_font_missing_glyphs', 'characters': missing})
        for mode in args.modes.split(','):
            window.set_workspace_mode(mode, persist=False)
            for size in parse_sizes(args.sizes):
                window.resize(*size); pump()
                for state in args.states.split(','):
                    if state in ('clip-effects', 'clip-wheels', 'clip-curves') and mode != 'professional':
                        continue
                    activate(state)
                    result = measure(mode, size, state)
                    screenshot = f'{mode}-{size[0]}x{size[1]}-{state}.png'
                    pixmap = window.grab()
                    if not args.no_screenshots and not pixmap.save(str(out / screenshot)):
                        raise RuntimeError(f'Cannot save {screenshot}')
                    actual_pixels = [pixmap.width(), pixmap.height()]
                    expected_pixels = [round(n * args.scale) for n in size]
                    if any(abs(a - e) > 1 for a, e in zip(actual_pixels, expected_pixels)):
                        result['violations'].append({'code': 'screenshot_physical_dimensions_mismatch',
                                                     'actual': actual_pixels, 'expected': expected_pixels})
                    result.update(mode=mode, logical_size=list(size), actual_size=[window.width(), window.height()],
                                  device_pixel_ratio=window.devicePixelRatioF(), physical_pixels=actual_pixels,
                                  state=state, screenshot=None if args.no_screenshots else screenshot,
                                  splitter_widths=window.main_split.sizes())
                    records.append(result)
                    failures.extend({'mode': mode, 'size': list(size), 'state': state, **v} for v in result['violations'])
    finally:
        window.dirty = False; window.closing = True; window.close(); pump()
    report = {'status': 'passed' if not failures else 'failed', 'platform': platform.platform(),
              'python': platform.python_version(), 'qt': qVersion(), 'pyside': pyside_version,
              'qt_platform': app.platformName(), 'scale': args.scale, 'locale': args.locale, 'font': font_info,
              'limits': LIMITS, 'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                               for path in sorted((ROOT / 'lumen').glob('ui*.py'))}, 'fixture': {'synthetic_media': True, 'rendered_preview': False,
                                           'model_downloads': False, 'text': MIXED_TEXT},
              'human_visual_review': 'required separately', 'record_count': len(records),
              'failures': failures, 'records': records}
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{report["status"]}: scale {args.scale:g}; {len(records)} states; {len(failures)} geometry violations; {out / "report.json"}')
    return int(bool(failures))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', '--output-dir', type=Path, default=ROOT / 'artifacts/ui-alignment')
    parser.add_argument('--locale', choices=('en', 'zh-CN'), default='en')
    parser.add_argument('--locales', default='en,zh-CN', help='Driver languages; each uses fresh Qt processes.')
    parser.add_argument('--scale', type=float, help='Run this scale in one fresh process.')
    parser.add_argument('--scales', default='1,1.5,2', help='Driver matrix, each scale in its own process.')
    parser.add_argument('--sizes', default=','.join(SIZES))
    parser.add_argument('--modes', default='simple,professional')
    parser.add_argument('--states', default=','.join(STATES))
    parser.add_argument('--font-dir', type=str)
    parser.add_argument('--ui-font', choices=('native', 'cjk'), default='native',
                        help='Preserve the platform UI family with local CJK fallback; cjk stress-tests the full UI family.')
    parser.add_argument('--no-screenshots', action='store_true', help='Measure only; JSON explicitly records no PNG evidence.')
    args = parser.parse_args(argv)
    parse_sizes(args.sizes)
    if args.scale is not None:
        if args.scale <= 0:
            parser.error('--scale must be positive')
        return run_worker(args)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for locale in args.locales.split(','):
        if locale not in ('en', 'zh-CN'):
            parser.error('Unsupported UI locale')
        for scale in map(float, args.scales.split(',')):
            output = args.output / locale / f'scale-{scale:g}'
            cmd = [sys.executable, str(Path(__file__).resolve()), '--output', str(output),
                   '--scale', str(scale), '--locale', locale, '--sizes', args.sizes, '--modes', args.modes, '--states', args.states, '--ui-font', args.ui_font]
            if args.font_dir:
                cmd += ['--font-dir', args.font_dir]
            if args.no_screenshots:
                cmd += ['--no-screenshots']
            completed = subprocess.run(cmd, check=False)
            report_file = output / 'report.json'
            child = json.loads(report_file.read_text(encoding='utf-8')) if report_file.is_file() else {}
            results.append({'scale': scale, 'locale': locale, 'exit_code': completed.returncode, 'report': str(report_file),
                            'status': child.get('status', 'crashed'), 'record_count': child.get('record_count', 0),
                            'violations': len(child.get('failures', []))})
    passed = all(item['exit_code'] == 0 and item['status'] == 'passed' for item in results)
    summary = {'status': 'passed' if passed else 'failed', 'limits': LIMITS, 'runs': results,
               'human_visual_review': 'required separately'}
    (args.output / 'report.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
