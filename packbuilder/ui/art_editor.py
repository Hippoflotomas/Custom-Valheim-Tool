"""Style list (shields) / main image (banners) with the crop preview."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import gamedata as gd
from .. import imaging, packio
from ..guides import GuideStore
from ..model import Art, Item
from .crop_view import CropView, pil_to_qimage

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp);;PNG (*.png)"
ART_ROLE = Qt.ItemDataRole.UserRole


class StyleList(QListWidget):
    """List whose drag-and-drop reorder is reported once, after the drop."""
    orderChanged = Signal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.orderChanged.emit()


class ArtEditor(QWidget):
    changed = Signal()

    def __init__(self, guides: GuideStore, parent=None):
        super().__init__(parent)
        self.guides = guides
        self.kind = gd.SHIELD
        self.item: Item | None = None
        self.last_dir = str(Path.home())

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # left: list + buttons
        left = QVBoxLayout()
        self.list = StyleList()
        self.list.setIconSize(QSize(56, 56))
        self.list.setFixedWidth(190)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.orderChanged.connect(self._rows_moved)
        self.list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.list, 1)
        self.add_btn = QPushButton("Add style…")
        self.replace_btn = QPushButton("Replace image…")
        self.remove_btn = QPushButton("Remove style")
        self.add_btn.clicked.connect(self._add)
        self.replace_btn.clicked.connect(self._replace)
        self.remove_btn.clicked.connect(self._remove)
        for b in (self.add_btn, self.replace_btn, self.remove_btn):
            left.addWidget(b)
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: gray;")
        left.addWidget(self.count_label)
        root.addLayout(left)

        # right: preview + controls
        right = QVBoxLayout()
        self.view = CropView()
        self.view.setMinimumHeight(380)
        self.view.placementChanged.connect(self._placement_changed)
        right.addWidget(self.view, 1)
        ctl = QHBoxLayout()
        self.zoom_out = QPushButton("−")
        self.zoom_in = QPushButton("+")
        for b in (self.zoom_out, self.zoom_in):
            b.setFixedWidth(32)
        self.zoom_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.1))
        self.zoom_in.clicked.connect(lambda: self.view.zoom_by(1.1))
        self.fill_btn = QPushButton("Fill face")
        self.fit_btn = QPushButton("Fit inside")
        self.fill_btn.clicked.connect(lambda: self._refit("fill"))
        self.fit_btn.clicked.connect(lambda: self._refit("fit"))
        self.scale_label = QLabel()
        ctl.addWidget(QLabel("Zoom"))
        ctl.addWidget(self.zoom_out)
        ctl.addWidget(self.zoom_in)
        ctl.addWidget(self.fill_btn)
        ctl.addWidget(self.fit_btn)
        ctl.addStretch(1)
        ctl.addWidget(self.scale_label)
        right.addLayout(ctl)
        self.hint = QLabel("Drag to move, scroll to zoom.")
        self.hint.setStyleSheet("color: gray;")
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #d08770;")
        right.addWidget(self.hint)
        right.addWidget(self.warn)
        root.addLayout(right, 1)

    # ------------------------------------------------------------ binding
    def set_item(self, kind: str, item: Item | None) -> None:
        self.kind = kind
        self.item = item
        shield = kind == gd.SHIELD
        self.list.setVisible(shield)
        self.remove_btn.setVisible(shield)
        self.replace_btn.setVisible(shield)
        self.count_label.setVisible(shield)
        self.add_btn.setText("Add style…" if shield else "Choose image…")
        self.fill_btn.setText("Fill face" if shield else "Fill banner")
        self.list.setFixedWidth(190 if shield else 0)
        self.refresh()

    def refresh(self, keep_row: int | None = None) -> None:
        """Rebuild after the item, its base prefab or the guide folder changed."""
        row = self.list.currentRow() if keep_row is None else keep_row
        self.list.blockSignals(True)
        self.list.clear()
        if self.item:
            for n, art in enumerate(self.item.art, 1):
                li = QListWidgetItem(self._thumb(art), f"Style {n}\n{art.label}")
                li.setData(ART_ROLE, id(art))
                self.list.addItem(li)
        self.list.blockSignals(False)
        self._update_frame()
        if self.item and self.item.art:
            self.list.setCurrentRow(min(max(row, 0), len(self.item.art) - 1))
            self._show_current(self.list.currentRow())
        else:
            self._show_current(-1)
        self._update_buttons()

    def _thumb(self, art: Art) -> QIcon:
        img = art.source.copy()
        img.thumbnail((112, 112))
        return QIcon(QPixmap.fromImage(pil_to_qimage(img)))

    def _guide(self):
        if self.kind == gd.SHIELD and self.item:
            return self.guides.get(self.item.base_prefab)
        return None

    def _update_frame(self) -> None:
        if not self.item:
            self.view.set_frame((512, 512), None, True)
            return
        size = packio.frame_size(self.kind, self.item, self.guides)
        guide = self._guide()
        overlay = guide.preview_overlay() if guide else None
        self.view.set_frame(size, overlay, transparent_is_wood=self.kind == gd.SHIELD)

    def _current_art(self) -> Art | None:
        if not self.item or not self.item.art:
            return None
        row = self.list.currentRow() if self.kind == gd.SHIELD else 0
        if 0 <= row < len(self.item.art):
            return self.item.art[row]
        return None

    def _show_current(self, _row: int) -> None:
        art = self._current_art()
        blocked = self.kind == gd.SHIELD and self.item is not None and self._guide() is None
        if blocked:
            self.view.set_source(None, None)
            self.view.set_message(self.guides.missing_message(self.item.base_prefab) +
                                  "\n\nUse File ▸ Pattern guide folder… if it's somewhere else.")
        elif art is None:
            self.view.set_source(None, None)
            self.view.set_message("Add a style to start." if self.kind == gd.SHIELD else "Choose an image to start.")
        else:
            packio.ensure_placement(self.kind, self.item, art, self.guides)
            self.view.set_message("")
            self.view.set_source(art.source, art.placement)
        for w in (self.zoom_in, self.zoom_out, self.fill_btn, self.fit_btn):
            w.setEnabled(art is not None and not blocked)
        self._update_status()

    def _update_status(self) -> None:
        art = self._current_art()
        warn = ""
        if art is not None and art.placement is not None:
            s = art.placement.scale
            self.scale_label.setText(f"{s * 100:.0f}%")
            if imaging.is_upscaled(art.placement):
                w, h = packio.frame_size(self.kind, self.item, self.guides)
                warn = (f"This image is enlarged {s:.1f}× ({art.source.width}×{art.source.height} source "
                        f"for a {w}×{h} output), so it may look soft in game.")
        else:
            self.scale_label.setText("")
        self.warn.setText(warn)
        if self.item and self.kind == gd.SHIELD:
            self.count_label.setText(f"{len(self.item.art)} of {gd.MAX_SHIELD_STYLES} styles. "
                                     "Drag to reorder.")

    def _update_buttons(self) -> None:
        n = len(self.item.art) if self.item else 0
        if self.kind == gd.SHIELD:
            self.add_btn.setEnabled(self.item is not None and n < gd.MAX_SHIELD_STYLES)
            self.replace_btn.setEnabled(n > 0)
            self.remove_btn.setEnabled(n > 0)
        else:
            self.add_btn.setEnabled(self.item is not None)
            self.add_btn.setText("Replace image…" if n else "Choose image…")

    # ------------------------------------------------------------ actions
    def _load(self, path: str) -> Art | None:
        try:
            img = imaging.trim_transparent(imaging.open_rgba(path))
        except Exception as ex:
            QMessageBox.warning(self, "Can't open image", f"{Path(path).name}:\n{ex}")
            return None
        return Art(img, Path(path).name)

    def _add(self):
        if not self.item:
            return
        if self.kind == gd.BANNER:
            path, _ = QFileDialog.getOpenFileName(self, "Choose banner image", self.last_dir, IMAGE_FILTER)
            if not path:
                return
            self.last_dir = str(Path(path).parent)
            art = self._load(path)
            if art:
                self.item.art = [art]
                self.refresh(0)
                self.changed.emit()
            return
        room = gd.MAX_SHIELD_STYLES - len(self.item.art)
        paths, _ = QFileDialog.getOpenFileNames(self, "Add styles", self.last_dir, IMAGE_FILTER)
        if not paths:
            return
        self.last_dir = str(Path(paths[0]).parent)
        if len(paths) > room:
            QMessageBox.information(self, "Too many styles",
                                    f"A shield holds {gd.MAX_SHIELD_STYLES} styles. Only the first {room} were added.")
            paths = paths[:room]
        added = [a for a in (self._load(p) for p in paths) if a]
        if added:
            self.item.art.extend(added)
            self.refresh(len(self.item.art) - 1)
            self.changed.emit()

    def _replace(self):
        row = self.list.currentRow()
        if not self.item or not 0 <= row < len(self.item.art):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Replace image", self.last_dir, IMAGE_FILTER)
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        art = self._load(path)
        if art:
            self.item.art[row] = art
            self.refresh(row)
            self.changed.emit()

    def _remove(self):
        row = self.list.currentRow()
        if not self.item or not 0 <= row < len(self.item.art):
            return
        del self.item.art[row]
        self.refresh(row)
        self.changed.emit()

    def _rows_moved(self, *_):
        if not self.item:
            return
        by_id = {id(a): a for a in self.item.art}
        order = [self.list.item(i).data(ART_ROLE) for i in range(self.list.count())]
        self.item.art[:] = [by_id[k] for k in order if k in by_id]
        self.refresh(self.list.currentRow())
        self.changed.emit()

    def _refit(self, mode: str):
        art = self._current_art()
        if art is None:
            return
        art.placement = None
        packio.ensure_placement(self.kind, self.item, art, self.guides, mode)
        self.view.set_source(art.source, art.placement)
        self._placement_changed()

    def _placement_changed(self):
        self._update_status()
        self.changed.emit()
