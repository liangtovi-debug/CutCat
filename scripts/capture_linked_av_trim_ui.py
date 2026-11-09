"""Fresh-process linked edge-trim EN/ZH geometry at real DPRs (not native QA)."""
from pathlib import Path
from copy import deepcopy
from fractions import Fraction
import argparse
import hashlib
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
parser = argparse.ArgumentParser()
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--scale', type=float, default=1)
parser.add_argument('--affine', action='store_true', help='Use unequal v2 edges and rational tails; strict v1 remains the default')
args = parser.parse_args()
os.environ['QT_SCALE_FACTOR'] = str(args.scale)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QFont, QFontInfo, QMouseEvent, QTextLayout
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from lumen.i18n import set_locale, tr
from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap
from lumen.ui import Timeline

args.output.mkdir(parents=True, exist_ok=True)
app = QApplication([]); font = QFont('Noto Sans CJK SC', 10); app.setFont(font)
files = ('lumen/ui.py', 'lumen/ui_linked_av.py', 'lumen/linked_av_trim.py', 'lumen/model.py',
         'lumen/i18n_catalog_linked_av.py', 'scripts/capture_linked_av_trim_ui.py')
identity = lambda: {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
source_identity = identity(); records = []
for text in ('V 27f A 31f Linked V+A Trim left J/L offsets preserved Fades stay edge-anchored audio output may change', 'V 27帧 A 31帧 联合音画修剪保留偏移淡化跟随边缘音频输出可能改变取消'):
    layout = QTextLayout(text, font); layout.beginLayout(); layout.createLine().setLineWidth(2000); layout.endLayout()
    assert all(i != 0 for run in layout.glyphRuns() for i in run.glyphIndexes())


def mouse(widget, kind, point, button=Qt.MouseButton.LeftButton, buttons=Qt.MouseButton.LeftButton):
    QApplication.sendEvent(widget, QMouseEvent(kind, QPointF(point), QPointF(point), button, buttons, Qt.KeyboardModifier.NoModifier))


def press(widget, member, edge):
    rect = widget.rect_for(next(c for c in widget.project.clips if c.id == member))
    point = QPointF(rect.left() if edge == 'left' else rect.right(), rect.center().y())
    mouse(widget, QEvent.Type.MouseButtonPress, point); return point


def drag(widget, point, delta):
    target = QPointF(point.x()+delta*widget.scale, point.y())
    mouse(widget, QEvent.Type.MouseMove, target, Qt.MouseButton.NoButton); return target


def release(widget, point):
    mouse(widget, QEvent.Type.MouseButtonRelease, point, buttons=Qt.MouseButton.NoButton)


with tempfile.TemporaryDirectory(prefix='linked-trim-ui-') as raw:
    source = Path(raw)/'identity.mov'; source.write_bytes(b'Identity only; no media decoding')
    source_stat = source.stat(); digest = hashlib.sha256(source.read_bytes()).hexdigest()
    for locale in ('en', 'zh-CN'):
        set_locale(locale)
        for geometry, width, zoom in (('normal', 780, 12), ('narrow', 390, 6)):
            tag = f'{locale}-{geometry}-{args.scale:g}'
            editor = Editor(Project(media=[Media('m',str(source),'Synthetic A/V',4,64,48,True,True)], clips=[
                Clip('video','m',0,5,12,30,source_audio_enabled=False),
                Clip('audio','m',2,5,12,30,fade_in=2,fade_out=3,audio_mix_anchor=AudioMixAnchor(0,5,'video'))]))
            if args.affine:
                video, audio = editor._clip('video'), editor._clip('audio')
                video.time_map = ClipTimeMap(Fraction(12), Fraction(167, 4))
                audio.start, audio.source_in, audio.duration = 3, Fraction(10), 34
                audio.fade_out = 6
                audio.time_map = ClipTimeMap(Fraction(10), Fraction(87, 2))
                editor.project.av_links = [AVLink('affine-pair', 'video', 'audio', 'm', AV_LINK_AFFINE_ALIGNMENT)]
                editor.project.validate()
            else:
                editor.link_av_clips('video','audio')
            widget = Timeline(); widget.setFont(font)
            widget.waveforms.request = lambda *_: None
            widget.edit_context = lambda: (editor, 0)
            widget.set_project(editor.project,'video',('video','audio'))
            widget.setMinimumWidth(0); widget.resize(width,390); widget.scale=zoom
            widget.show(); widget.activateWindow(); widget.setFocus(); app.processEvents()
            assert widget.devicePixelRatioF()==args.scale and widget.width()==width
            def commit(cid,edge,delta):
                editor.trim_linked_av(cid,edge,delta)
                widget.set_project(editor.project,cid,(cid,))
            widget.trimmed.connect(commit)
            before=deepcopy(editor.project); depth=len(editor._undo)
            p=press(widget,'video','left'); target=drag(widget,p,3)
            assert editor.project==before and len(widget.trim_preview_rects())==2
            assert all(widget.rect().contains(rect.toRect()) for rect in widget.trim_preview_rects())
            impact=widget.trim_preview_text(); assert widget.toolTip()==widget.accessibleDescription()==impact
            label=widget.trim_preview_label_rect(impact, widget.rect_for(widget.trim_drag.candidate))
            assert label.left()>=88 and label.right()<=widget.width()
            assert all(not label.intersects(button.geometry().toRectF()) for button in widget.track_lock_buttons)
            compact = widget.fontMetrics().elidedText(impact, Qt.TextElideMode.ElideRight, int(label.width()-16))
            durations = [27, 31 if args.affine else 27]
            assert ('V 27f' if locale == 'en' else 'V 27帧') in compact
            assert (f'A {durations[1]}f' if locale == 'en' else f'A {durations[1]}帧') in compact
            assert '+3' in compact
            assert ('J/L offsets preserved' if locale == 'en' else '保留 J/L 偏移') in impact
            assert ('audio output may change' if locale == 'en' else '音频输出可能改变') in impact
            success=f'{tag}-joint-left.png'; assert widget.grab().save(str(args.output/success))
            role_paths = []
            if args.affine:
                # Same canonical summary with A active; both actual screenshots retained.
                widget.cancel_gestures(); release(widget,target)
                widget.set_project(editor.project,'audio',('audio',)); widget.setMinimumWidth(0); widget.resize(width,390)
                assert widget.width()==width
                p=press(widget,'audio','left'); target=drag(widget,p,3)
                assert widget.trim_preview_text()==impact
                audio_success=f'{tag}-audio-joint-left.png'
                assert widget.grab().save(str(args.output/audio_success)); role_paths.append(audio_success)
            release(widget,target); assert len(editor._undo)==depth+1
            after=deepcopy(editor.project)
            if args.affine:
                v, a = editor._clip('video'), editor._clip('audio')
                assert (v.duration,a.duration,a.start-v.start,a.end-v.end)==(27,31,-2,2)
                assert v.source_out==Fraction(167,4) and a.source_out==Fraction(87,2)
                assert after.av_links==before.av_links
            editor.undo(); assert editor.project==before
            editor.redo(); assert editor.project==after
            widget.set_project(editor.project,'audio',('audio',)); widget.setMinimumWidth(0); widget.resize(width,390)
            p=press(widget,'audio','right'); target=drag(widget,p,-26 if args.affine else -27)
            assert widget.trim_drag.error and len(widget.trim_preview_rects())==2
            reason=widget.trim_preview_text(); assert reason==widget.toolTip()==widget.accessibleDescription()
            failure=f'{tag}-rejected-right.png'; assert widget.grab().save(str(args.output/failure))
            expected=deepcopy((editor.project,editor._undo,editor._redo,editor.revision))
            QTest.keyClick(widget,Qt.Key.Key_Escape); release(widget,target)
            assert (editor.project,editor._undo,editor._redo,editor.revision)==expected
            paths = [success, *role_paths, failure]
            rejection_reasons = {'fade': reason}
            for scenario in ('partner_collision', 'historical_order', 'partner_lock'):
                editor = Editor(deepcopy(before))
                if scenario == 'partner_collision':
                    editor.project.clips.append(Clip('blocker','m',2,editor._clip('audio').end+1,60,10))
                elif scenario == 'historical_order':
                    editor.project.clips.append(Clip('peer','m',3,editor._clip('audio').start+1,30,20,
                        audio_mix_anchor=AudioMixAnchor(0,5,'video')))
                else:
                    editor.set_track_edit_lock(2, True)
                widget.set_project(editor.project,'video',('video',)); widget.setMinimumWidth(0); widget.resize(width,390)
                expected = deepcopy((editor.project,editor._undo,editor._redo,editor.revision))
                edge = 'left' if scenario == 'historical_order' else 'right'
                p = press(widget,'video',edge); target = drag(widget,p,3)
                if scenario == 'partner_lock':
                    assert widget.trim_drag is None and widget.track_lock_buttons[2].isChecked()
                    rejection_reasons[scenario] = widget.toolTip()
                else:
                    assert widget.trim_drag is not None and widget.trim_drag.error
                    assert len(widget.trim_preview_rects()) == 2
                    rejection_reasons[scenario] = widget.trim_preview_text()
                    label=widget.trim_preview_label_rect(widget.trim_preview_text(),widget.trim_preview_rects()[0])
                    assert label.left()>=88 and label.right()<=widget.width()
                    assert all(not label.intersects(button.geometry().toRectF()) for button in widget.track_lock_buttons)
                filename = f'{tag}-{scenario}.png'; assert widget.grab().save(str(args.output/filename))
                paths.append(filename); release(widget,target)
                assert (editor.project,editor._undo,editor._redo,editor.revision)==expected
            assert digest==hashlib.sha256(source.read_bytes()).hexdigest() and source.stat().st_mtime_ns==source_stat.st_mtime_ns
            records.append({'locale':locale,'geometry':geometry,'requested_logical_size':[width,390],
                'logical_size':[widget.width(),widget.height()],
                'device_pixel_ratio':widget.devicePixelRatioF(),'both_outlines_visible':True,
                'full_impact_accessible':True,'full_rejection_accessible':True,'label_clear_of_track_controls':True,'one_commit_undo_redo':True,
                'escape_reject_no_commit':True,'source_unchanged':True,'impact':impact,'rejection':reason,
                'partner_collision_rejected':True,'partner_lock_rejected':True,'historical_order_rejected':True,
                'canonical_durations':durations,'common_delta':3,'compact_text':compact,
                'canonical_roles_visible':True,'offsets_preserved':True,'both_active_roles_same_summary':args.affine,
                'rejection_reasons':rejection_reasons,'paths':paths})
            widget.close(); widget.deleteLater(); app.processEvents()
assert identity()==source_identity
report={'status':'passed','affine_fixture':args.affine,'identity_only_fixture':True,'native_windows_macos_validation':False,
        'font_family':QFontInfo(font).family(),'source_identity':source_identity,'records':records,
        'screenshot_bytes':sum(p.stat().st_size for p in args.output.glob('*.png'))}
(args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','records':len(records),'bytes':report['screenshot_bytes']}))
