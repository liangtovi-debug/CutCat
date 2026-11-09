"""Real Qt widgets and interrupted audition requests, without audible-device claims."""
from copy import deepcopy
from pathlib import Path
import time
from PySide6.QtCore import QSettings, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
import pytest
from lumen.i18n import set_locale
from lumen.ui import MainWindow
from lumen.ui_sounds import SoundAudition
from lumen.sound_library import builtin_sounds


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app,tmp_path,monkeypatch):
    messages=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args:messages.append(args[2]))
    w=MainWindow(workspace_settings=QSettings(str(tmp_path/'w.ini'),QSettings.Format.IniFormat),ui_language='en',sound_library_root=tmp_path/'local-sounds')
    w.test_messages=messages; w.show(); app.processEvents()
    yield w
    w.cancel_jobs()
    for job in list(w.jobs): assert job.wait(10000)
    w.closing=True; w.dirty=False; w.close(); app.processEvents()


def test_separate_panel_search_categories_and_live_locale(window,app):
    w=window; assert w.sound_list.count()==len(builtin_sounds())
    w.side_tabs.setCurrentWidget(w.sounds_panel); app.processEvents()
    assert w.sounds_panel is not w.clip_effects_panel and w.sounds_panel is not w.stickers_panel
    before=deepcopy(w.project)
    w.sound_category.setCurrentIndex(w.sound_category.findData('atmosphere'))
    assert w.sound_list.count()==sum(entry.category=='atmosphere' for entry in builtin_sounds())
    w.sound_search.setText('unlikely string'); assert w.sound_list.count()==0 and not w.sound_insert_button.isEnabled()
    w.sound_search.clear(); w.sound_category.setCurrentIndex(0)
    entry=w.sound_entries[0]; w.sound_search.setText(entry.name_zh); assert w.sound_list.count()>=1
    chosen=w.selected_sound().id; w.sound_start.setValue(127)
    set_locale('zh-CN'); assert w.sound_insert_button.text()=='插入音效'
    assert w.selected_sound().id==chosen and w.sound_search.text()==entry.name_zh and w.sound_start.value()==127
    assert w.side_tabs.tabText(w.side_tabs.indexOf(w.sounds_panel))=='音效库'
    for mode in ('professional','simple'):
        w.set_workspace_mode(mode,persist=False)
        assert w.side_tabs.isTabVisible(w.side_tabs.indexOf(w.sounds_panel))
    assert w.project==before


def test_insert_position_collision_append_and_undo(window):
    w=window; w.sound_position.setCurrentIndex(w.sound_position.findData('frame')); w.sound_start.setValue(53)
    before=deepcopy(w.project); QTest.mouseClick(w.sound_insert_button,Qt.MouseButton.LeftButton)
    assert len(w.project.clips)==1 and w.project.clips[0].start==53
    after=deepcopy(w.project); w.insert_sound()
    assert w.project==after and w.test_messages
    w.undo(); assert w.project==before
    w.redo(); assert w.project==after
    w.sound_position.setCurrentIndex(w.sound_position.findData('append')); w.insert_sound()
    assert w.project.clips[-1].start==after.clips[0].end
    w.sound_track.setCurrentIndex(1); w.sound_position.setCurrentIndex(w.sound_position.findData('playhead'))
    w.timeline.playhead=17; w.insert_sound(); assert w.project.clips[-1].start==17 and w.project.clips[-1].track==3


def test_no_audio_device_is_honest_and_nonmutating(window,monkeypatch):
    from lumen import ui_sounds
    monkeypatch.setattr(ui_sounds.QMediaDevices,'audioOutputs',lambda:[])
    before=deepcopy(window.project); window.preview_sound()
    assert 'No audio output' in window.sound_status.text()
    assert window.sound_audition.player is None and window.project==before


def test_audition_repeat_stop_late_events_and_volume(app):
    controller=SoundAudition(app); entries=builtin_sounds()
    try:
        controller.play(entries[0]); first=controller.player
        assert controller.sound_id==entries[0].id and Path(first.source().toLocalFile()).samefile(entries[0].path)
        controller.set_volume(.12); assert controller.audio.volume()==pytest.approx(.12)
        controller.play(entries[1]); current=controller.player
        first.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        first.errorOccurred.emit(QMediaPlayer.Error.ResourceError,'stale error')
        assert controller.player is current and controller.sound_id==entries[1].id
        controller.stop(); current.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        assert controller.player is None and controller.sound_id==''
        controller.stop(); app.processEvents()
    finally:
        controller.stop(); controller.deleteLater(); app.processEvents()


def test_navigation_and_close_stop_audition(window,app):
    w=window; w.side_tabs.setCurrentWidget(w.sounds_panel)
    w.sound_audition.play(w.selected_sound()); assert w.sound_audition.player
    w.side_tabs.setCurrentWidget(w.inspector); assert w.sound_audition.player is None
    w.sound_audition.play(w.selected_sound()); w.closing=True; w.close(); app.processEvents()
    assert w.sound_audition.player is None


def test_real_local_import_is_not_inserted_until_clicked(window,app):
    w=window; source=builtin_sounds()[0].path; original=source.read_bytes()
    w.import_sound_path(source); job=w.sound_job
    w.import_sound_path(source); assert w.sound_job is job
    deadline=time.monotonic()+20
    while w.sound_job is not None and time.monotonic()<deadline:
        app.processEvents(); QTest.qWait(20)
    assert w.sound_job is None and not w.test_messages
    assert len(w.sound_entries)==len(builtin_sounds())+1 and w.sound_list.count()==1
    assert not w.project.media and not w.project.clips and source.read_bytes()==original
    w.insert_sound(); assert len(w.project.clips)==1 and w.project.media[0].sound_provenance.origin=='user-imported'
    w.load_sound_catalog(); assert len(w.sound_entries)==len(builtin_sounds())+1


def test_small_window_keeps_actions_and_target_visible(window,app):
    w=window; w.resize(1366,768); w.side_tabs.setCurrentWidget(w.sounds_panel)
    w.sound_position.setCurrentIndex(w.sound_position.findData('frame'))
    for _ in range(6):app.processEvents()
    for widget in (w.sound_track,w.sound_position,w.sound_start,w.sound_insert_button,w.sound_preview_button,w.sound_stop_button,w.sound_import_button):
        assert widget.isVisible() and widget.visibleRegion().boundingRect()==widget.rect()


def test_invalid_user_store_does_not_hide_builtin_sounds(window,tmp_path):
    blocked=tmp_path/'not-a-folder';blocked.write_text('not a directory')
    window.sound_library_root=blocked;window.load_sound_catalog()
    assert len(window.sound_entries)==len(builtin_sounds()) and window.sound_list.count()==len(builtin_sounds())
    assert 'unavailable' in window.sound_status.text()


def test_external_attribution_locale_and_insertion_keep_source(window,app):
    w=window; external=next(e for e in w.sound_entries if e.origin=='external-cc0')
    w.sound_search.setText(external.id); assert w.sound_list.count()==1
    assert external.author in w.sound_details.text() and external.source_name in w.sound_details.text()
    assert 'CC0-1.0' in w.sound_details.text() and 'Original procedural' not in w.sound_details.text()
    assert external.source_url in w.sound_details.toolTip() and external.license_url in w.sound_details.toolTip()
    for locale in ('zh-CN','en'):
        set_locale(locale); app.processEvents()
        assert external.author in w.sound_details.text() and external.source_name in w.sound_details.text()
        assert w.selected_sound().id==external.id
    w.insert_sound(); provenance=w.project.media[0].sound_provenance
    assert provenance.origin=='external-cc0' and provenance.author==external.author
    assert provenance.source_url==external.source_url and provenance.source_sha256==external.source_sha256
    w.sound_search.clear(); w.sound_list.setCurrentRow(0)
    assert w.sound_details.toolTip()==''
