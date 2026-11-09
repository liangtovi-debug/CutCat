"""Real encoded-range Qt screenshots and geometry receipts, one DPI/process.

Uses only generated 64x48 picture/tone media and the pre-provisioned licensed
font fixture. Linux/offscreen decoding does not prove audible output, native
Windows/macOS behavior, HDR, color calibration, or packaged execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--selection', action='store_true')
parser.add_argument('--scale', type=float, default=1)
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings, QTimer, Qt, qVersion
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyleOptionComboBox
from lumen.media import ffmpeg_binary, probe
from lumen.model import Clip, Editor, Project
from lumen.ui import MainWindow
from scripts.provision_test_font import verify_fixture

out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
app = QApplication([])
font_record = verify_fixture(os.environ['LUMEN_TEST_CJK_FONT_DIR'])
font_id = QFontDatabase.addApplicationFont(font_record['font_file'])
assert font_id >= 0
families = QFontDatabase.applicationFontFamilies(font_id)
assert families
app.setFont(QFont(families[0], 10))
FILES = ('lumen/ui.py', 'lumen/ui_preview_selection.py', 'lumen/shortcuts.py', 'lumen/ui_range_preview.py', 'lumen/i18n_catalog_range_preview.py',
         'lumen/range_preview.py', 'lumen/range_audio_mix.py', 'lumen/render.py',
         'lumen/media.py', 'lumen/color_media.py', 'lumen/overlay_render.py', 'lumen/caption_render.py',
         'lumen/clip_composite.py', 'lumen/ui_source_viewer.py', 'scripts/capture_range_preview_ui.py')
def identity():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}
source_before = identity()
records = []


def events():
    for _ in range(4):
        app.processEvents()


def until(condition, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events()
        if condition():
            return
        time.sleep(.003)
    assert condition()


def button_record(button):
    option = QStyleOptionButton()
    option.initFrom(button)
    option.text = button.text()
    contents = button.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, button)
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    assert text_width <= contents.width(), (button.text(), text_width, contents.width())
    assert button.visibleRegion().boundingRect() == button.rect()
    return dict(text=button.text(), width=button.width(), text_width=text_width, contents_width=contents.width())


def verify_decoded_loop(dialog):
    """Observe native restart and newly delivered frames, not just UI intent."""
    player, sink = dialog.player, dialog.sink
    generation = dialog._play_generation
    evidence = dict(end_of_media=False, position_reset=False,
                    advanced_position_ms=0, new_valid_frame_timestamps_us=[])
    def status(value):
        if value == QMediaPlayer.MediaStatus.EndOfMedia:
            evidence['end_of_media'] = True
    def position(value):
        if not evidence['end_of_media']:
            return
        if value == 0:
            evidence['position_reset'] = True
        elif evidence['position_reset']:
            evidence['advanced_position_ms'] = max(evidence['advanced_position_ms'], value)
    def frame(value):
        # The one-second fixture's restarted leading frames distinguish fresh
        # delivery from any queued tail picture from the preceding iteration.
        if (evidence['position_reset'] and value.isValid()
                and 0 <= value.startTime() <= 300000):
            stamps = evidence['new_valid_frame_timestamps_us']
            if len(stamps) < 16:
                stamps.append(value.startTime())
    player.mediaStatusChanged.connect(status)
    player.positionChanged.connect(position)
    sink.videoFrameChanged.connect(frame)
    try:
        until(lambda: dialog.player is player and dialog._play_generation > generation
              and player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
              and evidence['position_reset'] and evidence['advanced_position_ms'] >= 100
              and bool(evidence['new_valid_frame_timestamps_us']) and not dialog.window.jobs,
              timeout=10)
        evidence['native_playback_state'] = player.playbackState().name
        evidence['observed_position_ms'] = player.position()
        return evidence
    except AssertionError:
        print(json.dumps(dict(loop_failure=evidence, ui_state=dialog.state,
                              native_state=player.playbackState().name,
                              position_ms=player.position())), flush=True)
        raise
    finally:
        player.mediaStatusChanged.disconnect(status)
        player.positionChanged.disconnect(position)
        sink.videoFrameChanged.disconnect(frame)


with tempfile.TemporaryDirectory(prefix='cutcat-range-ui-') as name:
    temp = Path(name)
    source = temp / 'synthetic-av.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
        '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=30:duration=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
        '-c:v', 'ffv1', '-threads', '1', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2',
        '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', str(source)],
        check=True, capture_output=True, timeout=20)
    media = probe(source)
    for locale in ('en', 'zh-CN'):
        w = MainWindow(workspace_settings=QSettings(str(temp / f'{locale}.ini'), QSettings.Format.IniFormat),
            recovery_root=temp / f'recovery-{locale}', ui_language=locale)
        w.editor = Editor(Project(width=64, height=48, media=[media], clips=[Clip('v', media.id, 0, 0, 0, 60)]))
        w.refresh(); w.seek_frame(30); w.resize(1000, 700); w.show(); events()
        original = (w.project.to_dict(), w.revision, len(w.editor._undo), dict(w.preview_paths))
        if args.selection:
            w.seek_frame(15); w.set_preview_mark('in')
            w.seek_frame(44); w.set_preview_mark('out'); w.seek_frame(30)
            from lumen.ui_range_preview import open_range_preview
            d = open_range_preview(w, selected=True)
        else:
            d = w.open_range_preview()
        d.timer.stop(); d.radius.setValue(.5)
        d.loop_checkbox.setChecked(args.selection)
        heartbeat = []
        timer = QTimer(); timer.setInterval(16)
        timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
        timer.start(); started = time.monotonic()
        d.build_range(autoplay=args.selection)
        until(lambda: d.current() and not w.jobs and (not args.selection or d.player is not None))
        built = time.monotonic(); timer.stop()
        assert d.snapshot.identity.start_frame == 15 and d.snapshot.identity.end_frame == 45
        if not args.selection:
            assert d.player is None  # explicit Build is never autoplay
            d.toggle_play()
        until(lambda: d.player is not None and not d.canvas.image.isNull() and not w.jobs)
        loop_observed = False
        loop_evidence = None
        if args.selection:
            loop_evidence = verify_decoded_loop(d)
            loop_observed = True
        d.toggle_play(); events()
        assert d.state == 'paused' and not d._play_intent
        assert d.canvas.image.width() == 64 and d.canvas.image.height() == 48
        d.loop_checkbox.setChecked(True)
        for mode, requested in (('professional', (760, 620)), ('simple', (360, 540))):
            w.set_workspace_mode(mode, persist=False)
            w.resize(1000, 700); w.side_tabs.setFixedWidth(320)
            w.main_split.setSizes([210, 450, 320])
            d.resize(*requested); d.fit_available_screen(); events()
            assert w.range_preview_button.isVisible()
            main = button_record(w.range_preview_button)
            assert [w.width(), w.height()] == [1000, 700]
            assert w.video.visibleRegion().boundingRect() == w.video.rect()
            scopes_visible = all(child.visibleRegion().boundingRect() == child.rect()
                for child in (w.scopes.source_label, w.scopes.mode_choice, w.scopes.canvas))
            if mode == 'professional':
                assert scopes_visible and w.scopes.canvas.height() >= 44
            assert w.range_preview_button.geometry().top() == w.preview_mode_choice.geometry().top()
            combo = w.preview_mode_choice
            combo_option = QStyleOptionComboBox(); combo.initStyleOption(combo_option)
            combo_contents = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,
                combo_option, QStyle.SubControl.SC_ComboBoxEditField, combo)
            comparison_width = max(combo.fontMetrics().horizontalAdvance(combo.itemText(i))
                                   for i in range(combo.count()))
            assert comparison_width <= combo_contents.width(), (comparison_width, combo_contents.width())
            assert d.scroll.horizontalScrollBar().maximum() == 0
            assert d.canvas.visibleRegion().boundingRect() == d.canvas.rect()
            assert d.stage.height() >= d.stage.heightForWidth(d.stage.width())
            for label in (d.status, d.metadata, d.position_label, d.limitations, d.freshness):
                assert label.wordWrap() and label.width() <= d.scroll.viewport().width()
                assert label.height() >= label.heightForWidth(label.width())
            buttons = [button_record(button) for button in
                (d.build_button, d.play_button, d.cancel_button, d.close_button, d.timeline_button)]
            for button in (d.build_button, d.play_button, d.cancel_button, d.close_button, d.timeline_button):
                assert d.rect().contains(button.mapTo(d, button.rect().bottomRight()))
            tag = f'{locale}-{mode}-dpi{args.scale:g}'
            d.scroll.verticalScrollBar().setValue(0); events()
            paths = [tag + '-top.png']
            assert d.grab().save(str(out / paths[0]))
            if d.scroll.verticalScrollBar().maximum():
                d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum()); events()
                paths.append(tag + '-bottom.png')
                assert d.grab().save(str(out / paths[-1]))
            main_path = tag + '-entry.png'
            assert w.range_preview_button.grab().save(str(out / main_path))
            main_window_path = tag + '-main-1000x700.png'
            assert w.grab().save(str(out / main_window_path))
            intervals = [b-a for a, b in zip([started, *heartbeat], [*heartbeat, built])]
            records.append(dict(locale=locale, workspace=mode, real_loop_observed=loop_observed, native_loop_evidence=loop_evidence, logical_size=[d.width(), d.height()],
                requested_size=list(requested), device_pixel_ratio=d.devicePixelRatioF(),
                screen_available=[d.screen().availableGeometry().width(), d.screen().availableGeometry().height()],
                canvas=[d.canvas.image.width(), d.canvas.image.height()], range_frames=[15, 45],
                build_seconds=built-started, heartbeat_ticks=len(heartbeat),
                heartbeat_max_gap_seconds=max(intervals), heartbeat_median_gap_seconds=statistics.median(intervals),
                horizontal_scroll=d.scroll.horizontalScrollBar().maximum(),
                vertical_scroll=d.scroll.verticalScrollBar().maximum(), buttons=buttons,
                entry=main, entry_path=main_path, main_window_path=main_window_path,
                main_window_size=[w.width(), w.height()], video_fully_visible=True,
                scopes_fully_visible=scopes_visible if mode == 'professional' else None,
                scope_canvas_height=w.scopes.canvas.height() if mode == 'professional' else None,
                comparison_max_text_width=comparison_width, comparison_content_width=combo_contents.width(),
                paths=paths))
            assert original == (w.project.to_dict(), w.revision, len(w.editor._undo), dict(w.preview_paths))
            assert w.timeline.playhead == 30 and w.video.image.isNull() and w.scopes.data is None
        QApplication.setActiveWindow(d); d.activateWindow(); d.close_button.setFocus(Qt.FocusReason.TabFocusReason)
        events(); QTest.keyClick(d.close_button, Qt.Key.Key_Space)
        until(lambda: d._closed_flag and not w.jobs)
        assert d.snapshot is None and d.player is None
        w.dirty = False; w.close(); until(lambda: not w.jobs and not w.isVisible())
        w.deleteLater(); events()

source_after = identity()
assert source_before == source_after
report = dict(passed=True, selection_mode=args.selection, platform=platform.platform(), qt_version=qVersion(), scale=args.scale,
    qt_platform=os.environ['QT_QPA_PLATFORM'], native_package_or_audible_output_claim=False,
    font_file=font_record['font_file'], source_before=source_before, source_after=source_after,
    source_unchanged=True, records=records)
(out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'passed': True, 'captures': len(records), 'scale': args.scale, 'output': str(out)}))
