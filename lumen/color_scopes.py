"""Bounded, display-referred scopes of the decoded 8-bit preview, not a meter.

The renderer's Rec.709 preview is already decoded to RGB by the preview converter. These scopes
neither expand video range a second time nor linearize the encoded R'G'B'.
Y', Cb and Cr use the BT.709 coefficients (not the BT.601 matrix). The RGB
parade displays those same decoded channel codes, not three luma waveforms.

At most 256 x 256 nearest-neighbour samples are examined. Sampling is explicit:
small details may be missed, and values clipped upstream cannot be recovered.
Counts stay integer and unnormalized; only trace brightness is logarithmic.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout

from .i18n import locale_events, tr
from .i18n_widgets import QComboBox, QLabel, QWidget


SAMPLE_SIDE = 256
CODE_BINS = 256
VECTOR_BINS = 257  # An odd grid has an exact neutral center.
KR, KG, KB = .2126, .7152, .0722
CHANNEL_COLORS = ('#ef7979', '#79d5a1', '#7aa9ed')
TARGET_COLORS = ('#ef7979', '#e7d37d', '#79d5a1', '#79d5d5', '#7aa9ed', '#d495dc')
TARGET_RGB = (
    ('R', (1., 0., 0.)), ('Y', (1., 1., 0.)), ('G', (0., 1., 0.)),
    ('C', (0., 1., 1.)), ('B', (0., 0., 1.)), ('M', (1., 0., 1.)),
)
SCOPE_LIMITS = (
    'Scopes use sampled 8-bit RGB from the decoded Rec.709 preview, not source '
    'or 10-bit export values. The preview converter has already decoded video range; no second '
    'range expansion is applied. Sampling can miss small details. Upstream '
    'clipping, HDR, and display calibration cannot be measured here. '
    'Supported YUV frames use explicit matrix/range conversion; already-RGB backend frames are passed through, not color-verified. '
    'These scopes do not guarantee colorimetric equivalence.'
)
VECTOR_EXPLANATION = (
    'Cb is horizontal and Cr is vertical, using encoded Rec.709 R′G′B′. '
    'Targets are ideal color bars at 75% and 100% encoded RGB amplitude '
    '(low channels are zero), not saturation percentages or legal-range limits.'
)


@dataclass(frozen=True)
class ScopeData:
    """Raw sample counts; all arrays are read-only, without intensity clipping."""

    histogram: np.ndarray  # [channel, code]
    waveform: np.ndarray  # [255 - rounded Y' code, sampled x]
    parade: np.ndarray  # [channel, 255 - code, sampled x]
    vectorscope: np.ndarray  # [Cr descending, Cb ascending]
    source_size: tuple[int, int]  # width, height before sampling
    sampled_size: tuple[int, int]

    @property
    def sample_count(self) -> int:
        return self.sampled_size[0] * self.sampled_size[1]


def _validate_rgb(rgb: np.ndarray) -> None:
    if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8
            or rgb.ndim != 3 or rgb.shape[2] != 3
            or rgb.shape[0] == 0 or rgb.shape[1] == 0):
        raise ValueError('Scopes require a nonempty H x W x 3 uint8 RGB image.')


def _sample_size(width: int, height: int) -> tuple[int, int]:
    longest = max(width, height)
    if longest <= SAMPLE_SIDE:
        return width, height
    return max(1, width * SAMPLE_SIDE // longest), max(1, height * SAMPLE_SIDE // longest)


def sample_rgb(rgb: np.ndarray) -> np.ndarray:
    """Bounded center-of-cell point samples; never interpolate new RGB codes."""
    _validate_rgb(rgb)
    height, width = rgb.shape[:2]
    sw, sh = _sample_size(width, height)
    xs = ((2 * np.arange(sw, dtype=np.int64) + 1) * width) // (2 * sw)
    ys = ((2 * np.arange(sh, dtype=np.int64) + 1) * height) // (2 * sh)
    return np.ascontiguousarray(rgb[ys[:, None], xs[None, :]])


def rgb_from_qimage(image: QImage) -> np.ndarray:
    """Copy bounded RGB samples, excluding padded scanline bytes.

    Resize before converting, so an unusually tall/wide decoded image cannot
    cause an unbounded RGB conversion or a scaledToWidth portrait allocation.
    FastTransformation performs point sampling, not smooth color averaging.
    """
    if image.isNull():
        raise ValueError('Cannot analyze an empty preview image.')
    width, height = _sample_size(image.width(), image.height())
    if (width, height) != (image.width(), image.height()):
        image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.FastTransformation)
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    raw = np.frombuffer(image.constBits(), dtype=np.uint8,
                        count=image.height() * image.bytesPerLine())
    rows = raw.reshape(image.height(), image.bytesPerLine())
    return rows[:, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def _encoded_components(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Normalized encoded RGB to Y', signed Cb and Cr, without clipping."""
    y = KR * rgb[..., 0] + KG * rgb[..., 1] + KB * rgb[..., 2]
    cb = (rgb[..., 2] - y) / (2 * (1 - KB))
    cr = (rgb[..., 0] - y) / (2 * (1 - KR))
    return y, cb, cr


def rec709_ycbcr(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return Y' in [0, 1], Cb/Cr in [-0.5, 0.5] for uint8 RGB samples."""
    _validate_rgb(rgb)
    return _encoded_components(rgb.astype(np.float64) / 255.)


def vector_targets(amplitude: float = 1.) -> dict[str, tuple[float, float]]:
    """Ideal zero-low Rec.709 color bars, with the specified encoded amplitude.

    0.75 is exactly 75% signal amplitude, not a claim about HSL saturation and
    not an 8-bit quantized 191/255 patch. Traces may differ by a quantization bin.
    """
    if (isinstance(amplitude, (bool, np.bool_)) or not isinstance(amplitude, (int, float))
            or not math.isfinite(amplitude) or not 0 <= amplitude <= 1):
        raise ValueError('Reference amplitude must be finite and between 0 and 1.')
    rgb = np.array([color for _name, color in TARGET_RGB], dtype=np.float64) * amplitude
    _y, cb, cr = _encoded_components(rgb)
    return {name: (float(cb[i]), float(cr[i])) for i, (name, _color) in enumerate(TARGET_RGB)}


def analyze_rgb(rgb: np.ndarray) -> ScopeData:
    """Compute exact integer counts from bounded point samples of RGB codes."""
    _validate_rgb(rgb)
    source_size = (rgb.shape[1], rgb.shape[0])
    data = sample_rgb(rgb)
    height, width = data.shape[:2]
    histogram = np.stack([np.bincount(data[..., ch].ravel(), minlength=CODE_BINS)
                          for ch in range(3)]).astype(np.uint32)
    x = np.broadcast_to(np.arange(width, dtype=np.int64), (height, width))
    parade = np.stack([
        np.bincount(((255 - data[..., ch].astype(np.int64)) * width + x).ravel(),
                    minlength=CODE_BINS * width).reshape(CODE_BINS, width)
        for ch in range(3)
    ]).astype(np.uint32)
    y, cb, cr = rec709_ycbcr(data)
    y_codes = np.rint(y * 255).astype(np.int64)
    waveform = np.bincount(((255 - y_codes) * width + x).ravel(),
                           minlength=CODE_BINS * width).reshape(CODE_BINS, width).astype(np.uint32)
    vx = np.rint((cb + .5) * (VECTOR_BINS - 1)).astype(np.int64)
    vy = np.rint((.5 - cr) * (VECTOR_BINS - 1)).astype(np.int64)
    # Valid uint8 input fits the full square. Never clamp hidden outliers into
    # an edge bin if the math/input contract is changed later.
    if (np.any(vx < 0) or np.any(vx >= VECTOR_BINS)
            or np.any(vy < 0) or np.any(vy >= VECTOR_BINS)):
        raise ValueError('Decoded RGB chroma exceeds the scope coordinate range.')
    vectorscope = np.bincount((vy * VECTOR_BINS + vx).ravel(),
                             minlength=VECTOR_BINS ** 2).reshape(VECTOR_BINS, VECTOR_BINS).astype(np.uint32)
    for array in (histogram, waveform, parade, vectorscope):
        array.setflags(write=False)
    return ScopeData(histogram, waveform, parade, vectorscope, source_size, (width, height))


def analyze_qimage(image: QImage) -> ScopeData:
    """Analyze the already decoded RGB preview, preserving original dimensions."""
    data = analyze_rgb(rgb_from_qimage(image))
    return ScopeData(data.histogram, data.waveform, data.parade, data.vectorscope,
                     (image.width(), image.height()), data.sampled_size)


def _density_image(counts: np.ndarray, color: str, maximum: int | None = None) -> QImage:
    maximum = max(1, int(counts.max()) if maximum is None else maximum)
    alpha = np.log1p(counts) / math.log1p(maximum)
    # Ensure a single occupied bin remains visible in a dense image. No sample
    # counts are discarded; this is only the explicitly logarithmic display.
    alpha = np.where(counts > 0, .2 + .8 * alpha, 0.)
    ink = np.array(QColor(color).getRgb()[:3])
    paper = np.array([16, 21, 27])
    rgb = np.ascontiguousarray(np.rint(paper + alpha[..., None] * (ink - paper)).astype(np.uint8))
    height, width = counts.shape
    return QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888).copy()


def _rebin_counts(counts: np.ndarray, width: int, height: int) -> np.ndarray:
    """Aggregate bins before display reduction, never drop single-bin traces.

    QPainter's image downscaling can otherwise skip a saturated endpoint or a
    neutral vector spot completely. Each source bin maps to its nearest screen
    bin with endpoints preserved. Counts, not rendered RGB colors, are summed.
    No upsampling/allocation beyond the original grid is needed.
    """
    sh, sw = counts.shape
    width, height = max(1, min(sw, width)), max(1, min(sh, height))
    if (sw, sh) == (width, height):
        return counts
    xs = np.rint(np.arange(sw) * (width - 1) / max(1, sw - 1)).astype(np.int64)
    ys = np.rint(np.arange(sh) * (height - 1) / max(1, sh - 1)).astype(np.int64)
    indices = ys[:, None] * width + xs[None, :]
    return np.bincount(indices.ravel(), weights=counts.ravel(),
                       minlength=height * width).reshape(height, width).astype(np.uint32)


class _ScopeCanvas(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        # The editor's viewer shares vertical space with playback and timeline
        # controls. Never force a 200px graph into its ~100px compact allocation.
        self.setMinimumSize(0, 50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(400, 180)

    def _text(self, painter, rect, text, color='#9aabba', align=Qt.AlignmentFlag.AlignCenter):
        painter.setPen(QColor(color))
        painter.drawText(rect, align, text)

    def _code_grid(self, painter, rect, *, labels=True):
        painter.setPen(QPen(QColor(53, 64, 77, 100), 1, Qt.PenStyle.DotLine))
        for code in ((0, 255) if self.height() < 100 else (0, 64, 128, 192, 255)):
            y = rect.bottom() - code / 255 * rect.height()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            if labels:
                self._text(painter, QRectF(0, y - 8, rect.left() - 5, 16), str(code),
                           align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                painter.setPen(QPen(QColor(53, 64, 77, 100), 1, Qt.PenStyle.DotLine))

    def _parade(self, painter):
        owner = self.owner
        left, gap = 30., 9.
        width = max(1., (self.width() - left - 8 - 2 * gap) / 3)
        for ch, name in enumerate(('R′', 'G′', 'B′')):
            compact = self.height() < 100
            top, bottom = (16, 8) if compact else (22, 11)
            rect = QRectF(left + ch * (width + gap), top, width, max(1, self.height() - top - bottom))
            images = owner._plot_images('parade', rect)
            self._draw_density(painter, rect, images[ch])
            self._code_grid(painter, rect, labels=ch == 0)
            self._text(painter, QRectF(rect.left(), 0, width, top - 1), name, CHANNEL_COLORS[ch])

    def _histogram_waveform(self, painter):
        owner = self.owner
        width = max(1., (self.width() - 58) / 2)
        compact = self.height() < 100
        top = 16 if compact else 23
        hist_rect = QRectF(10, top, width, max(1, self.height() - top - 17))
        wave_rect = QRectF(width + 48, top, width, hist_rect.height())
        self._text(painter, QRectF(0, 0, width + 20, top - 1), 'RGB' if compact else tr('RGB histogram'))
        self._text(painter, QRectF(wave_rect.left(), 0, width, top - 1), 'Y′' if compact else tr('Luma waveform'))
        self._draw_density(painter, wave_rect, owner._plot_images('waveform', wave_rect)[0])
        painter.setPen(QPen(QColor(53, 64, 77, 100), 1, Qt.PenStyle.DotLine))
        for code in ((0, 255) if compact else (0, 128, 255)):
            y = wave_rect.bottom() - code / 255 * wave_rect.height()
            painter.drawLine(QPointF(wave_rect.left(), y), QPointF(wave_rect.right(), y))
            self._text(painter, QRectF(wave_rect.left() - 28, y - 8, 23, 16), str(code),
                       align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            painter.setPen(QPen(QColor(53, 64, 77, 100), 1, Qt.PenStyle.DotLine))
        max_count = max(1., float(np.log1p(owner.data.histogram).max()))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for hist, color in zip(owner.data.histogram, CHANNEL_COLORS):
            values = np.log1p(hist) / max_count
            path = QPainterPath()
            for code, value in enumerate(values):
                point = QPointF(hist_rect.left() + code / 255 * hist_rect.width(),
                                hist_rect.bottom() - value * hist_rect.height())
                path.moveTo(point) if code == 0 else path.lineTo(point)
            painter.setPen(QPen(QColor(color), 1.1))
            painter.drawPath(path)
        self._text(painter, QRectF(hist_rect.left() - 5, hist_rect.bottom(), 26, 17), '0')
        self._text(painter, QRectF(hist_rect.right() - 26, hist_rect.bottom(), 31, 17), '255')

    def vector_rect(self) -> QRectF:
        """A square plane, never stretched to the shape of the source/widget."""
        compact = self.height() < 150
        side = max(1., min(self.width() - 100., self.height() - (12. if compact else 40.)))
        return QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

    def _vectorscope(self, painter):
        rect = self.vector_rect()
        compact = self.height() < 150
        self._draw_density(painter, rect, self.owner._plot_images('vectorscope', rect)[0])
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = rect.center()
        # Both axes intersect the neutral bin. Keep their combined opacity low
        # enough that a one-pixel neutral trace is still clearly visible.
        painter.setPen(QPen(QColor(53, 64, 77, 32), 1, Qt.PenStyle.DotLine))
        painter.drawLine(QPointF(rect.left(), center.y()), QPointF(rect.right(), center.y()))
        painter.drawLine(QPointF(center.x(), rect.top()), QPointF(center.x(), rect.bottom()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        def point(cb, cr):
            return QPointF(center.x() + cb * rect.width(), center.y() - cr * rect.height())
        for amplitude in (1., .75):
            targets = vector_targets(amplitude)
            for (name, (cb, cr)), color in zip(targets.items(), TARGET_COLORS):
                pos = point(cb, cr)
                painter.setPen(QPen(QColor(color), 1,
                                    Qt.PenStyle.SolidLine if amplitude == 1 else Qt.PenStyle.DotLine))
                radius = 2 if compact else 3
                painter.drawRect(QRectF(pos.x() - radius, pos.y() - radius, 2 * radius, 2 * radius))
                if amplitude == 1 and not compact:
                    # Labels sit outside targets, with enough plot padding.
                    offset = QPointF(-11 if cb < 0 else 11, -9 if cr > 0 else 9)
                    self._text(painter, QRectF(pos.x() + offset.x() - 7,
                                              pos.y() + offset.y() - 8, 14, 16), name, color)
        if compact:
            self._text(painter, QRectF(rect.right() + 8, center.y() - 18, 80, 18), 'Cr ↑ ±0.5')
            self._text(painter, QRectF(rect.right() + 8, center.y(), 80, 18), 'Cb → ±0.5')
        else:
            self._text(painter, QRectF(rect.right() + 22, center.y() - 9, 26, 18), 'Cb')
            self._text(painter, QRectF(center.x() + 8, rect.top() - 20, 28, 18), 'Cr')
            self._text(painter, QRectF(rect.left() - 19, rect.bottom() + 3, 38, 17), '−0.5')
            self._text(painter, QRectF(rect.right() - 19, rect.bottom() + 3, 38, 17), '+0.5')

    @staticmethod
    def _draw_density(painter, rect, image):
        # Coordinates refer to bin centers, not the outside edge of a pixel.
        dx = rect.width() / max(1, image.width() - 1) / 2
        dy = rect.height() / max(1, image.height() - 1) / 2
        # A single source x sample fills the panel instead of doubling it.
        if image.width() == 1:
            dx = 0
        if image.height() == 1:
            dy = 0
        painter.drawImage(rect.adjusted(-dx, -dy, dx, dy), image)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#10151b'))
        if self.owner.data is None:
            text = tr('Preview out of date · rebuild preview for scopes.') if self.owner.stale else tr('No preview frame · build or play preview.')
            self._text(painter, QRectF(self.rect()).adjusted(12, 6, -12, -6), text,
                       align=Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap)
            return
        if self.owner.stale:
            painter.setOpacity(.35)
        if self.owner.mode == 'vectorscope':
            self._vectorscope(painter)
        elif self.owner.mode == 'histogram_waveform':
            self._histogram_waveform(painter)
        else:
            self._parade(painter)
        if self.owner.stale:
            painter.setOpacity(1.)
            rect = QRectF(self.rect()).adjusted(6, 22, -6, -6)
            painter.fillRect(rect, QColor(16, 21, 27, 160))
            self._text(painter, rect, tr('Preview out of date · rebuild preview for scopes.'),
                       '#e9bc78', Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap)


class _ScopeSourceLabel(QLabel):
    """Reflow this short caption without Qt height-for-width minima.

    Automatic word-wrap makes a custom painted sibling's preferred height a
    minimum-height-for-width in outer QBoxLayouts. An explicit two-line caption
    is enough at narrow widths and leaves the graph normally compressible.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_text = ''
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_source_text(self, text):
        self._source_text = str(text)
        self._fit_text()

    def _fit_text(self):
        text = self._source_text
        if self.fontMetrics().horizontalAdvance(text) > self.width():
            text = text.replace(' Rec.709', '\nRec.709')
        self.setText(text)
        self.setMinimumHeight(self.fontMetrics().height() * (2 if '\n' in text else 1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, '_source_text'):
            self._fit_text()


class Scopes(QWidget):
    """Selectable preview scopes, with the existing set_image(QImage) interface.

    clear() is for project/source switches or an invalid frame. set_stale(True)
    explicitly marks retained old measurements. set_image does NOT clear that
    flag: only the owner, which knows the preview revision/mode, may do so.
    """

    MODES = (
        ('parade', 'RGB parade'),
        ('vectorscope', 'Vectorscope · Rec.709'),
        ('histogram_waveform', 'Histogram + luma'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: ScopeData | None = None
        self.stale = False
        self.histogram = self.waveform = self.parade = self.vectorscope = None
        self._images = {}
        self._compact = False
        # An explicit compact floor is necessary: QWidget's wrapped-label
        # height-for-width negotiation can otherwise collapse this whole panel
        # to its selector row while the sibling video consumes all extra space.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(2)
        row = QHBoxLayout()
        self._header = row
        row.setContentsMargins(0, 0, 0, 0)
        self.source_label = _ScopeSourceLabel()
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.source_label, 1)
        self.mode_choice = QComboBox()
        self.mode_choice.setObjectName('scopeMode')
        self.mode_choice.setAccessibleName(tr('Scope mode'))
        for key, label in self.MODES:
            self.mode_choice.addItem(tr(label), key)
        row.addWidget(self.mode_choice, 1)
        layout.addLayout(row)
        self.canvas = _ScopeCanvas(self)
        layout.addWidget(self.canvas, 1)
        self.detail_label = QLabel()
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.detail_label)
        self.setToolTip(tr(SCOPE_LIMITS))
        self.source_label.setToolTip(tr(SCOPE_LIMITS))
        self.mode_choice.currentIndexChanged.connect(self._mode_changed)
        locale_events.changed.connect(self._retranslate)
        self._retranslate()

    @property
    def mode(self) -> str:
        return self.mode_choice.currentData()

    def set_mode(self, mode: str) -> None:
        index = self.mode_choice.findData(mode)
        if index < 0:
            raise ValueError('Unknown scope mode.')
        self.mode_choice.setCurrentIndex(index)

    def sizeHint(self) -> QSize:
        return QSize(480, 260)

    def minimumSizeHint(self) -> QSize:
        # Two real rows in compact mode: native selector/source caption and a
        # reduced graph. Explanatory legends remain available in tooltips.
        margins = self.layout().contentsMargins()
        height = (self._header.minimumSize().height() + self.canvas.minimumHeight()
                  + margins.top() + margins.bottom() + self.layout().spacing())
        return QSize(240, height)

    def _reserve_canvas_height(self):
        # Native/style/font metrics can change after construction. Reserve the
        # actual header plus the graph floor; a fixed panel height steals space
        # from the custom-painted graph when the video sibling stretches.
        height = self.minimumSizeHint().height()
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, 'canvas'):
            self._reserve_canvas_height()
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._compact = self.height() < 210
        self._retranslate()

    def _plot_images(self, mode, rect):
        width, height = max(1, round(rect.width()) + 1), max(1, round(rect.height()) + 1)
        key = (mode, width, height)
        if key not in self._images:
            if mode == 'parade':
                arrays = [_rebin_counts(counts, width, height) for counts in self.parade]
                maximum = max(int(counts.max()) for counts in arrays)
                images = [_density_image(counts, color, maximum)
                          for counts, color in zip(arrays, CHANNEL_COLORS)]
            else:
                counts = self.waveform if mode == 'waveform' else self.vectorscope
                color = '#a6dec3' if mode == 'waveform' else '#d4e6dd'
                images = [_density_image(_rebin_counts(counts, width, height), color)]
            # Resize drags cannot accumulate unbounded cached screenshots.
            if len(self._images) >= 6:
                self._images.clear()
            self._images[key] = images
        return self._images[key]

    def _mode_changed(self, _index=None):
        self._retranslate()

    def _retranslate(self, _locale=None):
        self.source_label.set_source_text(tr('Current · 8-bit decoded Rec.709 preview'))
        source_tip = str(tr(SCOPE_LIMITS))
        if self.data is not None:
            source_tip = str(tr('8-bit decoded Rec.709 preview · {width}×{height} samples',
                                width=self.data.sampled_size[0], height=self.data.sampled_size[1])) + '\n\n' + source_tip
        self.source_label.setToolTip(source_tip)
        if self.mode == 'vectorscope':
            self.detail_label.setText(tr('Targets: 75% dotted / 100% solid RGB amplitude · not HDR'))
            self.detail_label.setToolTip(tr(VECTOR_EXPLANATION))
        else:
            self.detail_label.setText(tr('Encoded RGB / Y′ 0–255 · logarithmic density · not HDR'))
            self.detail_label.setToolTip(tr(SCOPE_LIMITS))
        legend_fits = self.detail_label.fontMetrics().horizontalAdvance(self.detail_label.text()) <= self.width() - 12
        self.detail_label.setVisible(not self._compact and legend_fits)
        # The preview boundary stays visible at every size. Compact mode only
        # removes secondary sample counts/legend, never the source description.
        self.canvas.setToolTip(tr(VECTOR_EXPLANATION) if self.mode == 'vectorscope' else tr(SCOPE_LIMITS))
        self.canvas.setAccessibleName(tr(dict(self.MODES)[self.mode]))
        self.canvas.setAccessibleDescription(tr('Preview out of date · rebuild preview for scopes.')
                                             if self.stale else self.source_label.text())
        self._reserve_canvas_height()
        self.canvas.update()

    def set_image(self, image: QImage) -> None:
        if image.isNull():
            self.clear()
            return
        self.data = analyze_qimage(image)
        self.histogram = self.data.histogram
        self.waveform = self.data.waveform
        self.parade = self.data.parade
        self.vectorscope = self.data.vectorscope
        self._images = {}
        self._retranslate()

    def clear(self) -> None:
        self.data = None
        self.histogram = self.waveform = self.parade = self.vectorscope = None
        self._images = {}
        self.stale = False
        self._retranslate()

    def set_stale(self, stale: bool = True) -> None:
        self.stale = bool(stale)
        self._retranslate()
