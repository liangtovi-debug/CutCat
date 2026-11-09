"""Annotation-only timeline lane; existing track/transition hit geometry stays put.

The band is deliberately below the source-waveform legend. Markers never widen
this widget or its scroll range: out-of-extent annotations remain in the list.
"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPen

from .i18n import tr
from .ui_timeline_markers import validated_marker_in_bounds

MARKER_BAND_TOP = 374
MARKER_BAND_HEIGHT = 34
MARKER_BAND_BOTTOM = MARKER_BAND_TOP + MARKER_BAND_HEIGHT
MARKER_COLORS = {
    'red': '#f08c8c', 'orange': '#f4b17a', 'yellow': '#e6d479',
    'green': '#8fd69c', 'cyan': '#83d4d9', 'blue': '#8bb7ed',
    'purple': '#b9a2e6', 'pink': '#e6a0cb',
}


def marker_band_rect(timeline):
    return QRectF(0, MARKER_BAND_TOP, timeline.width(), MARKER_BAND_HEIGHT)


def marker_rect(timeline, marker, *, extent=None):
    """Exact start with bounded display, never a clamped navigation target."""
    if extent is None:
        extent = timeline.project.duration_frames
    if not 0 <= marker.start_frame < extent:
        return QRectF()
    x = 88 + marker.start_frame * timeline.scale
    right = 88 + extent * timeline.scale
    if marker.end_frame is None:
        width = min(150, max(28, timeline.fontMetrics().horizontalAdvance(marker.name) + 24))
    else:
        width = max(3, (min(marker.end_frame, extent) - marker.start_frame) * timeline.scale)
    return QRectF(x, MARKER_BAND_TOP + 4, min(width, max(3, right - x)), MARKER_BAND_HEIGHT - 8)


def marker_at(timeline, position):
    if not marker_band_rect(timeline).contains(position):
        return None
    extent = timeline.project.duration_frames
    for marker in reversed(sorted(timeline.project.timeline_markers, key=lambda m: (m.start_frame, m.id))):
        if marker_rect(timeline, marker, extent=extent).contains(position):
            return marker
    return None


def marker_tooltip(timeline, marker):
    kind = tr('Point marker') if marker.end_frame is None else tr('Range marker')
    where = str(marker.start_frame) if marker.end_frame is None else f'[{marker.start_frame}, {marker.end_frame})'
    status = tr('Within project duration') if validated_marker_in_bounds(marker, timeline.project.duration_frames) else tr('Outside project duration · navigation unavailable')
    text = f'{marker.name}\n{kind} · {where}\n{tr(marker.color.title())}\n{status}'
    if marker.comment:
        text += '\n' + marker.comment
    # QToolTip auto-detects rich text; escape user-authored names/comments.
    return '<qt>' + escape(text).replace('\n', '<br/>') + '</qt>'


def paint_marker_band(timeline, painter):
    painter.save()
    painter.fillRect(marker_band_rect(timeline), QColor('#202733'))
    painter.setPen(QColor('#c5cfda'))
    painter.drawText(QRectF(5, MARKER_BAND_TOP, 78, MARKER_BAND_HEIGHT),
                     Qt.AlignmentFlag.AlignVCenter,
                     painter.fontMetrics().elidedText(tr('Markers'), Qt.TextElideMode.ElideRight, 78))
    painter.setClipRect(QRectF(88, MARKER_BAND_TOP, max(0, timeline.width() - 88), MARKER_BAND_HEIGHT))
    extent = timeline.project.duration_frames
    for marker in sorted(timeline.project.timeline_markers, key=lambda m: (m.start_frame, m.id)):
        rect = marker_rect(timeline, marker, extent=extent)
        if rect.isEmpty():
            continue
        valid = validated_marker_in_bounds(marker, extent)
        color = QColor(MARKER_COLORS[marker.color])
        painter.setBrush(QColor('#283342'))
        painter.setPen(QPen(color, 2 if marker.id == timeline.marker_selection else 1,
                            Qt.PenStyle.SolidLine if valid else Qt.PenStyle.DashLine))
        painter.drawRoundedRect(rect, 3, 3)
        painter.save()
        painter.setClipRect(rect.adjusted(2, 1, -2, -1))
        painter.setPen(color)
        label = ('◆ ' if marker.end_frame is None else '[ ] ') + marker.name
        if not valid:
            label = '! ' + label
        painter.drawText(rect.adjusted(4, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                                         max(0, int(rect.width() - 7))))
        painter.restore()
    painter.restore()
