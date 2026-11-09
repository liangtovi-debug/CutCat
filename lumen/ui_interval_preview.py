"""Narrow presentation helpers for explicit interval edit previews.

No model planning, selection policy, mutation or stale-state ownership lives here.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSizePolicy
from .i18n import tr
from .i18n_widgets import QLabel, QPushButton


def plain_label(text):
    label = QLabel(text); label.setWordWrap(True); label.setMinimumWidth(0)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def action_button(source, callback, row):
    button = QPushButton(tr(source)); button.setAutoDefault(False)
    button.setAccessibleName(tr(source)); button.setToolTip(tr(source))
    button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    button.clicked.connect(callback); row.addWidget(button)
    return button


def fit_dialog(dialog, available=None):
    available = dialog.screen().availableGeometry() if available is None else available
    width, height = max(1, available.width() - 32), max(1, available.height() - 64)
    dialog.setMinimumSize(min(320, width), min(360, height))
    dialog.setMaximumSize(width, height)
    dialog.resize(min(dialog.width(), width), min(dialog.height(), height))
    dialog.scroll.widget().layout().activate(); dialog.scroll.widget().updateGeometry()
    frame = dialog.frameGeometry(); frame.moveCenter(available.center()); dialog.move(frame.topLeft())


def impact_text(items):
    headings = {'fixed': 'Fixed', 'move': 'Move', 'delete': 'Delete'}
    kinds = {'clip': 'clip', 'caption': 'caption', 'sticker': 'sticker'}
    lines = [tr('Exact impact · all existing items')]
    for item in items:
        track = tr(' · clip track {track}', track=item.track + 1) if item.track is not None else ''
        lines.append(tr('{action} {category}: {item_id}{track} · [{old_start}, {old_end}) → {position}',
            action=tr(headings[item.disposition]), category=tr(kinds[item.category]),
            item_id=item.item_id, track=track, old_start=item.previous_start,
            old_end=item.previous_start + item.duration,
            position=tr('removed') if item.disposition == 'delete'
            else f'[{item.start}, {item.start + item.duration})'))
    return '\n'.join(lines)
