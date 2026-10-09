"""Style list (shields, tabards) / main image (banners) with the crop preview."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import gamedata as gd
from .. import imaging, packio
from ..guides import GuideStore
from ..model import BACK_COLOUR, BACK_OWN, BACK_SAME, Art, Item, TabardStyle
from .crop_view import CropView, pil_to_qimage

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp);;PNG (*.png)"
ART_ROLE = Qt.ItemDataRole.UserRole
BACK_MODE_LABELS = ((BACK_SAME, "Same as front"), (BACK_OWN, "Own image"), (BACK_COLOUR, "Plain colour"))


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
        self.panel = "front"          # tabards: the panel being edited
        self._loading = False

        # Tabard previews are re-rendered a moment after the last edit, not on every mouse move.
        self._underlay_timer = QTimer(self)
        self._underlay_timer.setSingleShot(True)
        self._underlay_timer.setInterval(250)
        self._underlay_timer.timeout.connect(self._refresh_tabard_preview)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(400)
        self._thumb_timer.timeout.connect(self._refresh_thumb_and_icon)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # left: list + buttons
        left = QVBoxLayout()
        self.list = StyleList()
        self.list.setIconSize(QSize(56, 56))
        self.list.setFixedWidth(190)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.orderChanged.connect(self._rows_moved)
        self.list.currentRowChanged.connect(self._show_current)
        left.addWidget(self.list, 1)
        self.add_btn = QPushButton("Add style…")
        self.sheet_btn = QPushButton("Import ready-made…")
        self.sheet_btn.setToolTip("Add a style from a pattern already painted over the tabard's UV layout "
                                  "(square; front on the left half, back on the right). Used as it is.")
        self.replace_btn = QPushButton("Replace image…")
        self.remove_btn = QPushButton("Remove style")
        self.add_btn.clicked.connect(self._add)
        self.sheet_btn.clicked.connect(self._add_sheet)
        self.replace_btn.clicked.connect(self._replace)
        self.remove_btn.clicked.connect(self._remove)
        for b in (self.add_btn, self.sheet_btn, self.replace_btn, self.remove_btn):
            left.addWidget(b)
        self.count_label = QLabel()
        self.count_label.setWordWrap(True)
        self.count_label.setStyleSheet("color: gray;")
        left.addWidget(self.count_label)
        root.addLayout(left)

        # right: (tabard controls) + preview + controls
        right = QVBoxLayout()
        right.addWidget(self._build_tabard_controls())
        self.view = CropView()
        self.view.setMinimumHeight(380)
        self.view.placementChanged.connect(self._placement_changed)
        self.view.rotateRequested.connect(lambda d: self._rotate_to(self._rotation() + d))
        right.addWidget(self.view, 1)
        ctl = QHBoxLayout()
        self.zoom_out = QPushButton("−")
        self.zoom_in = QPushButton("+")
        for b in (self.zoom_out, self.zoom_in):
            b.setFixedWidth(32)
        self.zoom_out.clicked.connect(lambda: self.view.zoom_by(1 / 1.1, self._zoom_anchor()))
        self.zoom_in.clicked.connect(lambda: self.view.zoom_by(1.1, self._zoom_anchor()))
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

        rot = QHBoxLayout()
        self.rot_left = QPushButton("↺ 90°")
        self.rot_right = QPushButton("↻ 90°")
        self.rot_left.setToolTip("Rotate a quarter turn anticlockwise")
        self.rot_right.setToolTip("Rotate a quarter turn clockwise")
        self.rot_left.clicked.connect(lambda: self._rotate_to(self._rotation() - 90))
        self.rot_right.clicked.connect(lambda: self._rotate_to(self._rotation() + 90))
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-180.0, 180.0)
        self.angle.setDecimals(1)
        self.angle.setSingleStep(1.0)
        self.angle.setWrapping(True)
        self.angle.setSuffix("°")
        self.angle.setKeyboardTracking(False)     # apply when Enter is pressed, not on every keystroke
        self.angle.setToolTip("Angle, clockwise. Shift+scroll over the preview turns it 1° per notch.")
        self.angle.valueChanged.connect(self._rotate_to)
        self.rot_reset = QPushButton("Straighten")
        self.rot_reset.clicked.connect(lambda: self._rotate_to(0.0))
        rot.addWidget(QLabel("Rotate"))
        rot.addWidget(self.rot_left)
        rot.addWidget(self.rot_right)
        rot.addWidget(self.angle)
        rot.addWidget(self.rot_reset)
        rot.addStretch(1)
        right.addLayout(rot)

        self.hint = QLabel("Drag to move, scroll to zoom, Shift+scroll to rotate.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: gray;")
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #d08770;")
        right.addWidget(self.hint)
        right.addWidget(self.warn)
        root.addLayout(right, 1)

    def _build_tabard_controls(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)

        row = QHBoxLayout()
        row.addWidget(QLabel("Editing"))
        self.front_btn = QPushButton("Front")
        self.back_btn = QPushButton("Back")
        self.panel_group = QButtonGroup(self)
        for b in (self.front_btn, self.back_btn):
            b.setCheckable(True)
            b.setMinimumWidth(70)
            self.panel_group.addButton(b)
            row.addWidget(b)
        self.front_btn.setChecked(True)
        self.front_btn.setToolTip("The front panel: the left half of the pattern")
        self.back_btn.setToolTip("The back panel: the right half of the pattern")
        self.front_btn.clicked.connect(lambda: self._set_panel("front"))
        self.back_btn.clicked.connect(lambda: self._set_panel("back"))
        row.addSpacing(16)
        row.addWidget(QLabel("Back"))
        self.back_mode = QComboBox()
        for value, label in BACK_MODE_LABELS:
            self.back_mode.addItem(label, value)
        self.back_mode.currentIndexChanged.connect(self._back_mode_changed)
        row.addWidget(self.back_mode)
        self.back_img_btn = QPushButton("Choose back image…")
        self.back_img_btn.clicked.connect(self._choose_back)
        row.addWidget(self.back_img_btn)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Background"))
        self.bg_btn = QPushButton()
        self.bg_btn.setFixedWidth(64)
        self.bg_btn.setToolTip("Fills the tabard wherever the art is transparent")
        self.bg_btn.clicked.connect(self._pick_background)
        row.addWidget(self.bg_btn)
        self.holes = QCheckBox("Keep transparency (cut holes)")
        self.holes.setToolTip("In game, anything under 50% opacity is a hole in the cloth. Useful for ragged "
                              "hems; leave it off for logos on a transparent background.")
        self.holes.toggled.connect(self._holes_toggled)
        row.addWidget(self.holes)
        self.grid = QCheckBox("Show UV grid")
        self.grid.setToolTip("Draws the model's mesh lines over the preview, so seams and the neck line show")
        self.grid.toggled.connect(lambda _: self._update_frame())
        row.addWidget(self.grid)
        row.addStretch(1)
        lay.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("Icon"))
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(40, 40)
        self.icon_preview.setStyleSheet("background: #2e3440; border: 1px solid #4c566a;")
        row.addWidget(self.icon_preview)
        self.icon_state = QLabel()
        self.icon_state.setStyleSheet("color: gray;")
        row.addWidget(self.icon_state)
        self.icon_btn = QPushButton("Custom icon…")
        self.icon_btn.clicked.connect(self._pick_icon)
        self.icon_clear = QPushButton("Use generated")
        self.icon_clear.setToolTip("Let TabardShare make the icon from the front panel")
        self.icon_clear.clicked.connect(self._clear_icon)
        row.addWidget(self.icon_btn)
        row.addWidget(self.icon_clear)
        row.addStretch(1)
        lay.addLayout(row)
        self.tabard_box = box
        box.setVisible(False)
        return box

    # ------------------------------------------------------------ binding
    def set_item(self, kind: str, item: Item | None) -> None:
        self.kind = kind
        self.item = item
        styles = gd.has_styles(kind)
        tabard = kind == gd.TABARD
        self.list.setVisible(styles)
        self.remove_btn.setVisible(styles)
        self.replace_btn.setVisible(styles)
        self.count_label.setVisible(styles)
        self.sheet_btn.setVisible(tabard)
        self.tabard_box.setVisible(tabard)
        self.add_btn.setText("Add style…" if styles else "Choose image…")
        self.fill_btn.setText({gd.SHIELD: "Fill face", gd.TABARD: "Fill panel"}.get(kind, "Fill banner"))
        self.list.setFixedWidth(190 if styles else 0)
        self.list.setIconSize(QSize(56, 56))
        self.panel = "front"
        self.front_btn.setChecked(True)
        self.refresh()

    def refresh(self, keep_row: int | None = None) -> None:
        """Rebuild after the item, its base prefab, texture size or the guide folder changed."""
        row = self.list.currentRow() if keep_row is None else keep_row
        self.list.blockSignals(True)
        self.list.clear()
        if self.item:
            for n, art in enumerate(self.item.art, 1):
                li = QListWidgetItem(self._thumb(art), self._list_text(n, art))
                li.setData(ART_ROLE, id(art))
                self.list.addItem(li)
        self.list.blockSignals(False)
        if self.item and self.item.art:
            self.list.setCurrentRow(min(max(row, 0), len(self.item.art) - 1))
            self._show_current(self.list.currentRow())
        else:
            self._show_current(-1)
        self._update_buttons()

    def _list_text(self, n: int, art: Art) -> str:
        if isinstance(art, TabardStyle) and art.sheet:
            return f"Style {n} (ready-made)\n{art.label}"
        return f"Style {n}\n{art.label}"

    def _thumb(self, art: Art) -> QIcon:
        if self.kind == gd.TABARD and self.item is not None:
            img = packio.tabard_icon(self.item, packio.as_style(art), self.guides)
            return QIcon(QPixmap.fromImage(pil_to_qimage(img)))
        img = art.source.copy()
        img.thumbnail((112, 112))
        if art.rotation:
            img = imaging.rotate_image(img, art.rotation)
        return QIcon(QPixmap.fromImage(pil_to_qimage(img)))

    def _guide(self):
        if self.kind == gd.SHIELD and self.item:
            return self.guides.get(self.item.base_prefab)
        return None

    def _tabard_guide(self) -> imaging.TabardGuide | None:
        if self.kind == gd.TABARD and self.item:
            return packio.tabard_guide(self.item, self.guides)
        return None

    def _update_frame(self) -> None:
        if not self.item:
            self.view.set_frame((512, 512), None, True)
            return
        size = packio.frame_size(self.kind, self.item, self.guides)
        if self.kind == gd.TABARD:
            style = self._current_style()
            active = None if style is None or style.sheet else self.panel
            overlay = self._tabard_guide().preview_overlay(active, self.grid.isChecked())
            self.view.set_frame(size, overlay, transparent_is_wood=False)   # transparent = a hole
            return
        guide = self._guide()
        overlay = guide.preview_overlay() if guide else None
        self.view.set_frame(size, overlay, transparent_is_wood=self.kind == gd.SHIELD)

    def _current_style(self) -> TabardStyle | None:
        """Tabards: the selected style (plain Art from a script is upgraded in place)."""
        if self.kind != gd.TABARD or not self.item or not self.item.art:
            return None
        row = self.list.currentRow()
        if not 0 <= row < len(self.item.art):
            return None
        art = self.item.art[row]
        if not isinstance(art, TabardStyle):
            art = self.item.art[row] = packio.as_style(art)
        return art

    def _current_art(self) -> Art | None:
        """The image the preview is placing right now (None if nothing is placeable)."""
        if not self.item or not self.item.art:
            return None
        if self.kind == gd.TABARD:
            st = self._current_style()
            if st is None or st.sheet:
                return None
            if self.panel == "front":
                return st
            return st.back if st.back_mode == BACK_OWN else None
        row = self.list.currentRow() if self.kind == gd.SHIELD else 0
        if 0 <= row < len(self.item.art):
            return self.item.art[row]
        return None

    def _show_current(self, _row: int) -> None:
        self._update_frame()
        if self.kind == gd.TABARD:
            self._show_tabard()
            return
        art = self._current_art()
        blocked = self.kind == gd.SHIELD and self.item is not None and self._guide() is None
        self.view.set_underlay(None)
        self.view.set_clip(None)
        if blocked:
            self.view.set_source(None, None)
            self.view.set_message(self.guides.missing_message(self.item.base_prefab) +
                                  "\n\nUse File ▸ Shield pattern guide folder… if it's somewhere else.")
        elif art is None:
            self.view.set_source(None, None)
            self.view.set_message("Add a style to start." if self.kind == gd.SHIELD else "Choose an image to start.")
        else:
            packio.ensure_placement(self.kind, self.item, art, self.guides)
            self.view.set_message("")
            self.view.set_source(art.source, art.placement, art.rotation)
        self._set_placing_enabled(art is not None and not blocked)
        self.hint.setText("Drag to move, scroll to zoom, Shift+scroll to rotate.")
        self._update_status()

    def _set_placing_enabled(self, on: bool) -> None:
        for w in (self.zoom_in, self.zoom_out, self.fill_btn, self.fit_btn,
                  self.rot_left, self.rot_right, self.angle, self.rot_reset):
            w.setEnabled(on)

    # ------------------------------------------------------------ tabards
    def _show_tabard(self) -> None:
        st = self._current_style()
        self._loading = True
        has = st is not None
        sheet = has and st.sheet
        for w in (self.front_btn, self.back_btn, self.back_mode):
            w.setEnabled(has and not sheet)
        for w in (self.bg_btn, self.holes, self.icon_btn):
            w.setEnabled(has)
        if has:
            self.back_mode.setCurrentIndex([v for v, _ in BACK_MODE_LABELS].index(st.back_mode))
            self.holes.setChecked(st.keep_holes)
            self._paint_bg_button(st.background)
        self.back_img_btn.setVisible(has and not sheet and st.back_mode == BACK_OWN)
        self.back_img_btn.setText("Replace back image…" if has and st.back is not None else "Choose back image…")
        self._loading = False

        art = self._current_art()
        guide = self._tabard_guide()
        if not has:
            self.view.set_underlay(None)
            self.view.set_clip(None)
            self.view.set_source(None, None)
            self.view.set_message("Add a style to start, or import a ready-made pattern.")
            hint = "Front = left half of the pattern, back = right half, neck at the top."
        else:
            if art is not None:
                packio.ensure_placement(self.kind, self.item, art, self.guides, panel=self.panel)
                self.view.set_clip(guide.islands[self.panel])
                self.view.set_source(art.source, art.placement, art.rotation)
                self.view.set_message("")
                hint = "Drag to move, scroll to zoom, Shift+scroll to rotate. The other panel is dimmed."
            else:
                self.view.set_clip(None)
                self.view.set_source(None, None)
                self.view.set_message("")
                if sheet:
                    hint = "Ready-made pattern: exported as it is (resized to the texture size)."
                elif st.back_mode == BACK_SAME:
                    hint = "The back shows the front image, placed the same way. Pick “Own image” to place it separately."
                elif st.back_mode == BACK_COLOUR:
                    hint = "The back is plain background colour."
                else:
                    hint = "Choose a back image. Until then the back is plain background colour."
            self._refresh_tabard_preview()
        self.hint.setText(hint)
        self._set_placing_enabled(art is not None)
        self._update_icon_row()
        self._update_status()

    def _refresh_tabard_preview(self) -> None:
        """Re-render what's under the live art: the other panel, and the background colour."""
        st = self._current_style()
        if st is None or not self.item:
            self.view.set_underlay(None)
            return
        omit = None if self._current_art() is None else self.panel
        self.view.set_underlay(packio.tabard_preview(self.item, st, self.guides, omit=omit))

    def _set_panel(self, panel: str) -> None:
        self.panel = panel
        (self.front_btn if panel == "front" else self.back_btn).setChecked(True)
        self._show_current(self.list.currentRow())

    def _back_mode_changed(self, _):
        st = self._current_style()
        if self._loading or st is None:
            return
        st.back_mode = self.back_mode.currentData()
        if st.back_mode == BACK_OWN and st.back is None:
            self._choose_back()      # picks a file: shows the back and redraws; cancelled: falls through
            if st.back is not None:
                return
        self.panel = "back"
        self.back_btn.setChecked(True)
        self._style_edited()

    def _choose_back(self):
        st = self._current_style()
        if st is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose back image", self.last_dir, IMAGE_FILTER)
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        art = self._load(path)
        if art:
            st.back = art
            st.back_mode = BACK_OWN
            self.panel = "back"
            self.back_btn.setChecked(True)
            self._style_edited()

    def _paint_bg_button(self, rgb: tuple[int, int, int]) -> None:
        self.bg_btn.setStyleSheet(f"background: rgb{tuple(rgb)}; border: 1px solid #4c566a;")
        self.bg_btn.setText("")
        self.bg_btn.setToolTip("Background colour #%02X%02X%02X — fills the tabard wherever the art is transparent"
                               % tuple(rgb))

    def _pick_background(self):
        st = self._current_style()
        if st is None:
            return
        c = QColorDialog.getColor(QColor(*st.background), self, "Background colour")
        if not c.isValid():
            return
        st.background = (c.red(), c.green(), c.blue())
        self._paint_bg_button(st.background)
        self._style_edited()

    def _holes_toggled(self, on: bool):
        st = self._current_style()
        if self._loading or st is None:
            return
        st.keep_holes = on
        self._style_edited()

    def _update_icon_row(self) -> None:
        st = self._current_style()
        if st is None or not self.item:
            self.icon_preview.clear()
            self.icon_state.setText("")
            self.icon_clear.setEnabled(False)
            return
        img = packio.tabard_icon(self.item, st, self.guides)
        self.icon_preview.setPixmap(QPixmap.fromImage(pil_to_qimage(img)).scaled(
            38, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.icon_state.setText("custom" if st.icon is not None else "made from the front by TabardShare")
        self.icon_clear.setEnabled(st.icon is not None)

    def _pick_icon(self):
        st = self._current_style()
        if st is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose icon", self.last_dir, IMAGE_FILTER)
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        try:
            img = imaging.trim_transparent(imaging.open_rgba(path))
        except Exception as ex:
            QMessageBox.warning(self, "Can't open image", f"{Path(path).name}:\n{ex}")
            return
        st.icon = imaging.png_bytes(imaging.make_icon(img, imaging.TABARD_ICON_SIZE))   # 128x128, centred
        self._style_edited()

    def _clear_icon(self):
        st = self._current_style()
        if st is not None and st.icon is not None:
            st.icon = None
            self._style_edited()

    def _style_edited(self) -> None:
        """A tabard style setting changed: redraw the preview, its icon and the list thumbnail."""
        self._show_current(self.list.currentRow())
        self._update_thumb()
        self.changed.emit()

    def _update_thumb(self) -> None:
        row = self.list.currentRow()
        li = self.list.currentItem()
        if li is not None and self.item and 0 <= row < len(self.item.art):
            li.setIcon(self._thumb(self.item.art[row]))
            li.setText(self._list_text(row + 1, self.item.art[row]))

    def _refresh_thumb_and_icon(self) -> None:
        self._update_thumb()
        self._update_icon_row()

    def _zoom_anchor(self):
        """Zoom buttons zoom about the centre of the panel being edited (tabards) or the frame."""
        if self.kind == gd.TABARD and self._current_art() is not None:
            from PySide6.QtCore import QPointF
            l, t, r, b = self._tabard_guide().islands[self.panel]
            return QPointF((l + r) / 2, (t + b) / 2)
        return None

    # ------------------------------------------------------------ status
    def _update_status(self) -> None:
        art = self._current_art()
        warn = ""
        if art is not None and art.placement is not None:
            s = art.placement.scale
            self.scale_label.setText(f"{s * 100:.0f}%")
            self.angle.blockSignals(True)
            self.angle.setValue(art.rotation)
            self.angle.blockSignals(False)
            if imaging.is_upscaled(art.placement):
                w, h = packio.frame_size(self.kind, self.item, self.guides)
                warn = (f"This image is enlarged {s:.1f}× ({art.source.width}×{art.source.height} source "
                        f"for a {w}×{h} output), so it may look soft in game.")
        else:
            self.scale_label.setText("")
        st = self._current_style()
        if st is not None and st.sheet and st.source.width < self.item.texture_size:
            warn = (f"This pattern is {st.source.width}px and will be enlarged to {self.item.texture_size}px, "
                    "so it may look soft in game.")
        if st is not None and st.keep_holes:
            warn = (warn + " " if warn else "") + ("Transparent parts (under 50%) will be holes in the cloth; "
                                                   "the preview shows them as a checkerboard.")
        self.warn.setText(warn)
        if self.item and gd.has_styles(self.kind):
            self.count_label.setText(f"{len(self.item.art)} of {gd.max_styles(self.kind)} styles. "
                                     "Drag to reorder.")

    def _update_buttons(self) -> None:
        n = len(self.item.art) if self.item else 0
        if gd.has_styles(self.kind):
            room = self.item is not None and n < gd.max_styles(self.kind)
            self.add_btn.setEnabled(room)
            self.sheet_btn.setEnabled(room)
            self.replace_btn.setEnabled(n > 0)
            self.remove_btn.setEnabled(n > 0)
        else:
            self.add_btn.setEnabled(self.item is not None)
            self.add_btn.setText("Replace image…" if n else "Choose image…")

    # ------------------------------------------------------------ actions
    def _load(self, path: str, trim: bool = True) -> Art | None:
        try:
            img = imaging.open_rgba(path)
            if trim:
                img = imaging.trim_transparent(img)
        except Exception as ex:
            QMessageBox.warning(self, "Can't open image", f"{Path(path).name}:\n{ex}")
            return None
        return Art(img, Path(path).name)

    def _new_style(self, art: Art) -> Art:
        if self.kind == gd.TABARD:
            return TabardStyle(art.source, art.label)
        return art

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
        cap = gd.max_styles(self.kind)
        room = cap - len(self.item.art)
        paths, _ = QFileDialog.getOpenFileNames(self, "Add styles", self.last_dir, IMAGE_FILTER)
        if not paths:
            return
        self.last_dir = str(Path(paths[0]).parent)
        if len(paths) > room:
            what = "A shield" if self.kind == gd.SHIELD else "A tabard"
            QMessageBox.information(self, "Too many styles",
                                    f"{what} holds {cap} styles. Only the first {room} were added.")
            paths = paths[:room]
        added = [self._new_style(a) for a in (self._load(p) for p in paths) if a]
        if added:
            self.item.art.extend(added)
            self.panel = "front"
            self.front_btn.setChecked(True)
            self.refresh(len(self.item.art) - 1)
            self.changed.emit()

    def _load_sheet(self, path: str) -> TabardStyle | None:
        art = self._load(path, trim=False)     # the position on the sheet matters: don't trim
        if art is None:
            return None
        w, h = art.source.size
        if w != h:
            QMessageBox.warning(self, "Not a tabard pattern",
                                f"{art.label} is {w}×{h}. A ready-made tabard pattern must be square, painted over "
                                "TabardShare's UV layout (front on the left half, back on the right).")
            return None
        size = (self.item.texture_size, self.item.texture_size)
        return TabardStyle(art.source, art.label, imaging.full_frame_placement(art.source.size, size), sheet=True)

    def _add_sheet(self):
        if not self.item or self.kind != gd.TABARD:
            return
        room = gd.MAX_STYLES - len(self.item.art)
        paths, _ = QFileDialog.getOpenFileNames(self, "Import ready-made patterns", self.last_dir, IMAGE_FILTER)
        if not paths:
            return
        self.last_dir = str(Path(paths[0]).parent)
        added = [s for s in (self._load_sheet(p) for p in paths[:room]) if s]
        if added:
            self.item.art.extend(added)
            self.refresh(len(self.item.art) - 1)
            self.changed.emit()

    def _replace(self):
        row = self.list.currentRow()
        if not self.item or not 0 <= row < len(self.item.art):
            return
        st = self._current_style()
        if st is not None and st.sheet:
            path, _ = QFileDialog.getOpenFileName(self, "Replace ready-made pattern", self.last_dir, IMAGE_FILTER)
            if not path:
                return
            self.last_dir = str(Path(path).parent)
            new = self._load_sheet(path)
            if new:
                new.background, new.keep_holes, new.icon = st.background, st.keep_holes, st.icon
                self.item.art[row] = new
                self.refresh(row)
                self.changed.emit()
            return
        title = "Replace back image" if st is not None and self.panel == "back" and st.back_mode == BACK_OWN \
            else "Replace image"
        path, _ = QFileDialog.getOpenFileName(self, title, self.last_dir, IMAGE_FILTER)
        if not path:
            return
        self.last_dir = str(Path(path).parent)
        art = self._load(path)
        if not art:
            return
        if st is not None:
            if title == "Replace back image":
                st.back = art
            else:   # keep the style's back, colour and icon settings; place the new front afresh
                st.source, st.label, st.placement, st.rotation = art.source, art.label, None, 0.0
        else:
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
        packio.ensure_placement(self.kind, self.item, art, self.guides, mode, panel=self.panel)
        self.view.set_source(art.source, art.placement, art.rotation)
        self._placement_changed()

    def _rotation(self) -> float:
        art = self._current_art()
        return art.rotation if art is not None else 0.0

    def _rotate_to(self, deg: float) -> None:
        """Set the current image's angle, turning it about the centre of where it sits now."""
        art = self._current_art()
        if art is None or art.placement is None:
            return
        new = imaging.normalise_angle(deg)
        if abs(new - art.rotation) < 1e-9:
            return
        art.placement = imaging.with_rotation(art.placement, art.source.size, art.rotation, new)
        art.rotation = new
        self.view.set_rotation(new, art.placement)
        if self.kind == gd.SHIELD:
            li = self.list.currentItem()
            if li is not None:
                li.setIcon(self._thumb(art))
        self._placement_changed()

    def _placement_changed(self):
        self._update_status()
        if self.kind == gd.TABARD:
            # The back follows the front ("same as front"), and the thumbnail and icon follow both.
            self._underlay_timer.start()
            self._thumb_timer.start()
        self.changed.emit()
