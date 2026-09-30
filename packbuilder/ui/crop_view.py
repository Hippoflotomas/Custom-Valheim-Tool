"""Crop/placement preview: drag to pan, wheel to zoom, Shift+wheel to rotate."""
from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import imaging
from ..model import Placement

WOOD = QColor(128, 96, 62)       # stands in for "plain wood" where the art is transparent
PREVIEW_MAX = 2048               # sources larger than this are drawn from a smaller copy


def pil_to_qimage(img: Image.Image) -> QImage:
    img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    return QImage(data, img.width, img.height, 4 * img.width, QImage.Format.Format_RGBA8888).copy()


class CropView(QWidget):
    """Shows the output frame with the art placed in it.

    Coordinates: *output* pixels are the exported image's pixels; the view maps the
    frame into the widget with ``_view_scale`` and ``_view_origin``.
    """
    placementChanged = Signal()
    rotateRequested = Signal(float)   # degrees to add (Shift+wheel); the editor applies it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.frame = (512, 512)
        self.overlay: QImage | None = None
        self.transparent_is_wood = True
        self.message = ""
        self._src: QImage | None = None
        self._src_factor = 1.0          # preview-copy pixels per real source pixel
        self._src_size = (1, 1)         # real source size, for the rotated bounding box
        self._rotation = 0.0
        self._placement: Placement | None = None
        self._drag_from: QPointF | None = None

    # ------------------------------------------------------------ setup
    def set_frame(self, size: tuple[int, int], overlay: Image.Image | None, transparent_is_wood: bool) -> None:
        self.frame = size
        self.overlay = pil_to_qimage(overlay) if overlay is not None else None
        self.transparent_is_wood = transparent_is_wood
        self.update()

    def set_source(self, img: Image.Image | None, placement: Placement | None, rotation: float = 0.0) -> None:
        self._rotation = rotation
        if img is None:
            self._src = None
        else:
            f = min(1.0, PREVIEW_MAX / max(img.width, img.height))
            small = img if f >= 1.0 else img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))),
                                                     Image.LANCZOS)
            self._src_factor = small.width / img.width
            self._src_size = img.size
            self._src = pil_to_qimage(small)
        self._placement = placement
        self.update()

    def set_rotation(self, deg: float, placement: Placement) -> None:
        self._rotation = deg
        self._placement = placement
        self.update()

    def set_message(self, text: str) -> None:
        self.message = text
        self.update()

    # ------------------------------------------------------------ geometry
    def _view(self) -> tuple[float, QPointF]:
        W, H = self.frame
        margin = 12
        s = min((self.width() - 2 * margin) / W, (self.height() - 2 * margin) / H)
        s = max(s, 0.01)
        origin = QPointF((self.width() - W * s) / 2, (self.height() - H * s) / 2)
        return s, origin

    def zoom_by(self, factor: float, anchor_out: QPointF | None = None) -> None:
        """Zoom the art by ``factor`` around a point in output pixels (default: frame centre)."""
        p = self._placement
        if p is None:
            return
        if anchor_out is None:
            anchor_out = QPointF(self.frame[0] / 2, self.frame[1] / 2)
        new_scale = min(max(p.scale * factor, 0.005), 50.0)
        factor = new_scale / p.scale
        p.ox = anchor_out.x() - (anchor_out.x() - p.ox) * factor
        p.oy = anchor_out.y() - (anchor_out.y() - p.oy) * factor
        p.scale = new_scale
        self.update()
        self.placementChanged.emit()

    # ------------------------------------------------------------ painting
    def paintEvent(self, _):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), QColor(40, 40, 44))
        s, o = self._view()
        W, H = self.frame
        frame_rect = QRectF(o.x(), o.y(), W * s, H * s)

        if self.transparent_is_wood:
            qp.fillRect(frame_rect, WOOD)
        else:
            self._checker(qp, frame_rect)

        qp.save()
        qp.setClipRect(frame_rect)
        if self._src is not None and self._placement is not None:
            p = self._placement
            k = s * p.scale / self._src_factor
            # Draw around the centre of the rotated bounding box, as imaging.render_placed does.
            rw, rh = imaging.rotated_size(*self._src_size, self._rotation)
            qp.save()
            qp.translate(o.x() + (p.ox + rw * p.scale / 2) * s, o.y() + (p.oy + rh * p.scale / 2) * s)
            qp.rotate(self._rotation)            # clockwise on screen, like the export
            w, h = self._src.width() * k, self._src.height() * k
            qp.drawImage(QRectF(-w / 2, -h / 2, w, h), self._src)
            qp.restore()
        if self.overlay is not None:
            qp.drawImage(frame_rect, self.overlay)
        qp.restore()

        qp.setPen(QPen(QColor(230, 230, 230, 120), 1, Qt.PenStyle.DashLine))
        qp.drawRect(frame_rect)

        if self.message:
            qp.setPen(QColor(255, 220, 140))
            qp.drawText(self.rect().adjusted(16, 16, -16, -16),
                        Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)

    @staticmethod
    def _checker(qp: QPainter, r: QRectF, cell: int = 12) -> None:
        qp.save()
        qp.setClipRect(r)
        qp.fillRect(r, QColor(200, 200, 200))
        y = r.top()
        row = 0
        while y < r.bottom():
            x = r.left() + (cell if row % 2 else 0)
            while x < r.right():
                qp.fillRect(QRectF(x, y, cell, cell), QColor(160, 160, 160))
                x += 2 * cell
            y += cell
            row += 1
        qp.restore()

    # ------------------------------------------------------------ interaction
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._placement is not None:
            self._drag_from = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag_from is None or self._placement is None:
            return
        s, _ = self._view()
        d = e.position() - self._drag_from
        self._drag_from = e.position()
        self._placement.ox += d.x() / s
        self._placement.oy += d.y() / s
        self.update()
        self.placementChanged.emit()

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, e):
        if self._placement is None:
            return
        steps = e.angleDelta().y() / 120 or e.angleDelta().x() / 120  # Shift can turn wheel into x
        if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.rotateRequested.emit(steps)    # 1 degree per notch
            return
        s, o = self._view()
        pos = e.position()
        anchor = QPointF((pos.x() - o.x()) / s, (pos.y() - o.y()) / s)
        self.zoom_by(1.1 ** steps, anchor)
