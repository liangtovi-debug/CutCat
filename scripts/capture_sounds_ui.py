"""Real Qt sound-library screenshots and small-window layout checks, no mocks."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--external',action='store_true'); args=parser.parse_args()
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QWidget,QPushButton,QLineEdit,QComboBox,QAbstractSpinBox
    from PySide6.QtCore import QSettings,Qt,QPoint,QCoreApplication,QEvent
    from PySide6.QtGui import QFont
    from PySide6.QtMultimedia import QMediaDevices
    from lumen.ui import MainWindow
    from lumen.i18n import set_locale
    from lumen.model import Project,Editor
    args.output.mkdir(parents=True,exist_ok=True)
    app=QApplication.instance() or QApplication([]); app.setFont(QFont('Noto Sans CJK SC',10))
    states=[]
    with tempfile.TemporaryDirectory(prefix='cutcat-sounds-ui-') as tmp:
        w=MainWindow(workspace_settings=QSettings(str(Path(tmp)/'settings.ini'),QSettings.Format.IniFormat),
                     ui_language='zh-CN',sound_library_root=Path(tmp)/'sounds')
        w.editor=Editor(Project(name='音效库示例 · Sound effects demo',width=320,height=180));w.refresh()
        # Real library inserts, not drawn placeholders.
        w.sound_category.setCurrentIndex(w.sound_category.findData('transitions'))
        w.sound_position.setCurrentIndex(w.sound_position.findData('frame'));w.sound_start.setValue(30);w.insert_sound()
        w.sound_track.setCurrentIndex(1);w.sound_category.setCurrentIndex(w.sound_category.findData('atmosphere'))
        w.sound_start.setValue(0);w.insert_sound()
        w.sound_track.setCurrentIndex(0);w.sound_category.setCurrentIndex(0)
        w.side_tabs.setCurrentWidget(w.sounds_panel)
        w.sound_list.setCurrentRow(0); w.sound_list.scrollToTop()
        if args.external:
            entry=next(e for e in w.sound_entries if e.origin=='external-cc0')
            for row in range(w.sound_list.count()):
                if w.sound_list.item(row).data(Qt.ItemDataRole.UserRole)==entry.id:
                    w.sound_list.setCurrentRow(row); w.sound_list.scrollToItem(w.sound_list.item(row)); break
        try:
            for locale in ('zh-CN','en'):
                set_locale(locale)
                for width,height in ((1366,768),(1600,1000)):
                    w.resize(width,height);w.main_split.setSizes([190,width-710,520]);w.show()
                    for _ in range(12):app.processEvents()
                    w.sounds_scroll.verticalScrollBar().setValue(0)
                    if args.external:
                        w.sounds_scroll.ensureWidgetVisible(w.sound_details,0,8)
                    app.processEvents()
                    violations=[]
                    content=w.sounds_scroll.widget()
                    for widget in content.findChildren(QWidget):
                        if not isinstance(widget,(QPushButton,QLineEdit,QComboBox,QAbstractSpinBox)):continue
                        if not widget.isVisibleTo(content):continue
                        position=widget.mapTo(content,QPoint(0,0))
                        if position.x() < -2 or position.x()+widget.width()>content.width()+2:
                            violations.append(widget.objectName() or widget.metaObject().className())
                    filename=f'sounds-{locale}-{width}x{height}.png'
                    assert w.grab().save(str(args.output/filename))
                    states.append({'locale':locale,'size':[width,height],'screenshot':filename,
                        'sounds':w.sound_list.count(),'inserted_clips':len(w.project.clips),
                        'horizontal_violations':violations,'vertical_scroll_max':w.sounds_scroll.verticalScrollBar().maximum()})
            report={'platform':sys.platform,'qt_platform':app.platformName(),'audio_outputs':len(QMediaDevices.audioOutputs()),
                    'states':states,'audible_listening_verified':False}
            (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            assert not any(state['horizontal_violations'] for state in states), report
        finally:
            w.closing=True;w.dirty=False;w.close();w.deleteLater();app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    return 0

if __name__=='__main__':raise SystemExit(main())
