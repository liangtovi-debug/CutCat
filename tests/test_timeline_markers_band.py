"""Independent annotation lane: no clip hit-layout changes or hidden seeks."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtWidgets import QApplication

from lumen.i18n import set_locale
from lumen.model import Clip, Editor, Media, Project, TimelineMarker
from lumen.ui import Timeline
from lumen.ui_timeline_marker_band import (MARKER_BAND_TOP, MARKER_BAND_BOTTOM,
    marker_at, marker_rect, marker_tooltip)
from test_speed_ui import app
from test_clip_multiselection_timeline import mouse


@pytest.fixture
def band(app, monkeypatch):
    set_locale('en')
    w = Timeline()
    p = Project(media=[Media('m', '/not-decoded.mp4', 'Media', 10, 640, 360, True, False)],
        clips=[Clip('c', 'm', 0, 0, 0, 60)], timeline_markers=[
            TimelineMarker('p', '<b>Point</b>', '<img src="file:///private">', 'yellow', 10),
            TimelineMarker('r', 'Range', 'Some notes', 'blue', 30, 50),
            TimelineMarker('o', 'Outside', '', 'red', 1000),
            TimelineMarker('partial', 'Long range', '', 'green', 55, 1000),
        ])
    monkeypatch.setattr(w.waveforms, 'request', lambda *_: None)
    w.set_project(p); w.resize(800, 420); w.show(); app.processEvents()
    yield w
    w.close(); w.deleteLater(); app.processEvents()


def test_existing_tracks_locks_captions_stickers_geometry_unchanged(band):
    assert band.rect_for(band.project.clips[0]).y() == 34
    assert [(b.x(), b.y(), b.width(), b.height()) for b in band.track_lock_buttons] == [
        (4, 54 + track * 53, 79, 22) for track in range(4)]
    from lumen.model import Caption, Overlay
    assert band.caption_rect(Caption('caption', 0, 10, 'Caption')).y() == 246
    assert band.overlay_rect(Overlay('overlay', '/unused.png', 'sticker', 0, 10)).y() == 299
    assert MARKER_BAND_TOP > 365 and band.minimumHeight() > MARKER_BAND_BOTTOM
    assert '4' in band.accessibleDescription() and 'Timeline markers' in band.accessibleDescription()


@pytest.mark.parametrize('scale', [1, 4, 20])
def test_marker_geometry_scale_and_no_duration_or_scroll_extension(band, scale):
    band.scale = scale; band.set_project(band.project)
    assert band.project.duration_frames == 60
    assert band.minimumWidth() == max(700, int(150 + 120 * scale))
    for marker in band.project.timeline_markers:
        rect = marker_rect(band, marker)
        if marker.id == 'o':
            assert rect.isEmpty(); continue
        assert rect.left() == 88 + marker.start_frame * scale
        assert rect.top() > 365
        assert rect.right() <= 88 + band.project.duration_frames * scale + 2
        assert not rect.intersects(band.rect_for(band.project.clips[0]))


def test_marker_click_and_doubleclick_emit_only_marker_ids(band):
    clicks, edits, seeks, clips, transitions = [], [], [], [], []
    band.marker_selected.connect(clicks.append); band.marker_edit_requested.connect(edits.append)
    band.seeked.connect(seeks.append); band.selected.connect(clips.append)
    band.transition_selected.connect(transitions.append)
    before = deepcopy(band.project.to_dict())
    p = marker_rect(band, band.project.timeline_markers[0]).center()
    mouse(band, QEvent.Type.MouseButtonPress, p)
    mouse(band, QEvent.Type.MouseButtonRelease, p, buttons=Qt.MouseButton.NoButton)
    mouse(band, QEvent.Type.MouseButtonDblClick, p)
    assert clicks == ['p'] and edits == ['p']
    assert not seeks and not clips and not transitions and band.drag is None and band.trim_drag is None
    assert band.project.to_dict() == before


def test_empty_band_click_never_falls_through_to_seek(band):
    seeks = []; band.seeked.connect(seeks.append)
    mouse(band, QEvent.Type.MouseButtonPress, QPointF(700, MARKER_BAND_TOP + 10))
    mouse(band, QEvent.Type.MouseButtonPress, QPointF(20, MARKER_BAND_TOP + 10))
    assert seeks == []


def test_same_frame_records_remain_individual_and_tooltips_escape_markup(band):
    band.project.timeline_markers.append(TimelineMarker('same', 'Twin', 'literal <script>', 'pink', 10))
    point = marker_rect(band, band.project.timeline_markers[0]).topLeft() + QPointF(2, 2)
    assert marker_at(band, point).id == 'same'  # deterministic topmost record
    assert len(band.project.timeline_markers) == 5
    tooltip = marker_tooltip(band, band.project.timeline_markers[0])
    assert '&lt;b&gt;Point&lt;/b&gt;' in tooltip and '&lt;img' in tooltip
    assert '<img' not in tooltip and 'file:///private' in tooltip
    assert marker_at(band, QPointF(point.x(), 34)) is None


def test_unknown_old_selection_reconciles_on_project_switch(band):
    band.marker_selection = 'p'
    band.set_project(Project())
    assert band.marker_selection == ''


def test_paint_and_hit_do_not_revalidate_annotation_text(band, app, monkeypatch):
    import lumen.timeline_markers as model_markers
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Validated UI metadata must not rescan text per painted record')
    monkeypatch.setattr(model_markers, 'validate_timeline_marker', forbidden)
    rect = marker_rect(band, band.project.timeline_markers[0])
    assert marker_at(band, rect.center()) is not None
    assert not band.grab().isNull()
