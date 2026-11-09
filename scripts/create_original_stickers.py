"""Regenerate the bundled original stickers using only geometric drawing.

Run with the project's pinned PySide6 environment. No fonts, external artwork,
models, downloads, or other image libraries are used. Output uses plain PNG.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

ROOT = Path(__file__).resolve().parents[1]
SIZE = 256


def _canvas():
    image = QImage(SIZE * 4, SIZE * 4, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(4, 4)
    painter.setPen(Qt.PenStyle.NoPen)
    return image, painter


def _star(cx, cy, outer, inner, points=5, phase=-math.pi / 2):
    return QPolygonF([QPointF(cx + math.cos(phase + n * math.pi / points) * (inner if n % 2 else outer),
                             cy + math.sin(phase + n * math.pi / points) * (inner if n % 2 else outer))
                      for n in range(points * 2)])


def _finish(image, painter, output):
    painter.end()
    result = image.scaled(SIZE, SIZE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # Stable PNG resolution metadata: QApplication/screen DPI must not change
    # original asset identity when regenerating identical pixel artwork.
    result.setDotsPerMeterX(3937); result.setDotsPerMeterY(3937)
    if not result.save(str(output), 'PNG'):
        raise RuntimeError(f'Could not save {output}.')


def spark_star(output):
    image, p = _canvas()
    p.setBrush(QColor('#a66a1b'))
    p.drawPolygon(_star(128, 136, 91, 44))
    p.setBrush(QColor('#ffd45c'))
    p.drawPolygon(_star(128, 127, 91, 44))
    highlight = QPainterPath(QPointF(128, 50))
    highlight.lineTo(145, 108)
    highlight.lineTo(128, 98)
    highlight.lineTo(111, 108)
    highlight.closeSubpath()
    p.setBrush(QColor('#fff1ac'))
    p.drawPath(highlight)
    p.setBrush(QColor('#fff1ac'))
    p.drawPolygon(_star(35, 49, 17, 5, points=4))
    p.setBrush(QColor('#ffd45c'))
    p.drawPolygon(_star(218, 77, 13, 4, points=4))
    p.drawPolygon(_star(222, 204, 10, 3, points=4))
    _finish(image, p, output)


def check_bubble(output):
    image, p = _canvas()
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(36, 36, 184, 158), 44, 44)
    tail = QPainterPath(QPointF(66, 181))
    tail.lineTo(65, 221)
    tail.lineTo(113, 189)
    tail.closeSubpath()
    bubble = bubble.united(tail)
    p.translate(0, 8)
    p.setBrush(QColor('#0b726c'))
    p.drawPath(bubble)
    p.translate(0, -8)
    p.setBrush(QColor('#31d6b0'))
    p.drawPath(bubble)
    p.setPen(QPen(QColor('#effff9'), 20, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    check = QPainterPath(QPointF(82, 112))
    check.lineTo(113, 142)
    check.lineTo(174, 83)
    p.drawPath(check)
    _finish(image, p, output)


def pulse_frame(output, index):
    image, p = _canvas()
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Two offset waves keep the loop visually continuous at the wrap boundary.
    for offset in (0.0, 0.5):
        phase = (index / 8 + offset) % 1
        radius = 36 + phase * 76
        alpha = round(205 * math.sin(math.pi * phase))
        p.setPen(QPen(QColor(112, 172, 255, alpha), 11 - phase * 5))
        p.drawEllipse(QPointF(128, 128), radius, radius)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor('#508af5'))
    p.drawEllipse(QPointF(128, 128), 34, 34)
    p.setBrush(QColor('#d6eaff'))
    p.drawPolygon(_star(128, 128, 24, 9, points=4, phase=-math.pi / 2 + index * math.pi / 16))
    _finish(image, p, output)


def generate(destination: Path) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    pulse = destination / 'pulse_ring'
    pulse.mkdir(exist_ok=True)
    spark_star(destination / 'spark_star.png')
    check_bubble(destination / 'check_bubble.png')
    names = [f'frame_{index:02d}.png' for index in range(8)]
    for index, name in enumerate(names):
        pulse_frame(pulse / name, index)
    manifest = {'version': 1, 'frames': names, 'frame_duration': 3, 'loop': True}
    (pulse / 'sticker.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return destination / 'spark_star.png', destination / 'check_bubble.png', pulse / 'sticker.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'lumen' / 'assets' / 'stickers')
    args = parser.parse_args()
    for path in generate(args.output):
        print(path)


if __name__ == '__main__':
    main()
