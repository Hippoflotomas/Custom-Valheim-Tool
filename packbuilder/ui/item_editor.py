"""The form for one item."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import gamedata as gd
from .. import imaging
from ..guides import GuideStore
from ..model import GENERIC_ID, Item, apply_recipe, id_from_name, id_problem, matches_recipe
from .art_editor import ArtEditor
from .requirements import RequirementsEditor


class AdvancedLayers(QGroupBox):
    """Optional texture layers, copied into the item folder unchanged."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__("Advanced texture layers (optional, copied as-is)", parent)
        self.setCheckable(True)
        self.setChecked(False)
        self.item: Item | None = None
        self.kind = gd.SHIELD
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        outer = QVBoxLayout(self)
        note = QLabel("These follow the model's UV layout, not the face, and are never cropped or "
                      "resized. See the mod's _Templates\\<Prefab> - UV layout.png.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        outer.addWidget(note)
        outer.addWidget(self.body)
        self.toggled.connect(self.body.setVisible)
        self.body.setVisible(False)
        self.last_dir = ""

    def set_item(self, kind: str, item: Item | None) -> None:
        self.kind, self.item = kind, item
        while self.form.rowCount():
            self.form.removeRow(0)
        if item is None:
            return
        for name in gd.ADVANCED_LAYERS[kind]:
            row = QHBoxLayout()
            status = QLabel(self._status(name))
            pick = QPushButton("Choose…")
            clear = QPushButton("Clear")
            pick.clicked.connect(lambda _=False, n=name, s=status: self._pick(n, s))
            clear.clicked.connect(lambda _=False, n=name, s=status: self._clear(n, s))
            row.addWidget(status, 1)
            row.addWidget(pick)
            row.addWidget(clear)
            w = QWidget()
            w.setLayout(row)
            self.form.addRow(name, w)
        if item.advanced and not self.isChecked():
            self.setChecked(True)

    def _status(self, name: str) -> str:
        data = self.item.advanced.get(name) if self.item else None
        if not data:
            return "—"
        try:
            w, h = imaging.open_rgba(data).size
            return f"{w}×{h}, {len(data) // 1024} KB"
        except Exception:
            return "unreadable image"

    def _pick(self, name: str, status: QLabel):
        path, _ = QFileDialog.getOpenFileName(self, f"Choose {name}", self.last_dir, "PNG (*.png)")
        if not path:
            return
        try:
            data = open(path, "rb").read()
            imaging.open_rgba(data)
        except Exception as ex:
            QMessageBox.warning(self, "Can't use file", f"{path}\n{ex}")
            return
        self.item.advanced[name] = data
        status.setText(self._status(name))
        self.changed.emit()

    def _clear(self, name: str, status: QLabel):
        if self.item and self.item.advanced.pop(name, None) is not None:
            status.setText(self._status(name))
            self.changed.emit()


class ItemEditor(QWidget):
    changed = Signal()           # anything edited
    labelChanged = Signal()      # ID or display name edited (item list text)

    def __init__(self, guides: GuideStore, parent=None):
        super().__init__(parent)
        self.guides = guides
        self.kind = gd.SHIELD
        self.item: Item | None = None
        self._loading = False
        self._dupes: set[str] = set()

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Shown in game, e.g. Black Dog Shield")
        form.addRow("Display name", self.name)

        id_box = QVBoxLayout()
        self.id = QLineEdit()
        self.id.setPlaceholderText("Folder name, e.g. HB_BlackDog")
        self.id_msg = QLabel()
        self.id_msg.setWordWrap(True)
        id_warn = QLabel("Don't change the ID once the pack is in use: placed banners and crafted shields "
                         "remember it, and a renamed item turns into a “missing” placeholder. "
                         "IDs must be unique across every pack a player installs, so a prefix like "
                         "‘YourName_’ helps.")
        id_warn.setWordWrap(True)
        id_warn.setStyleSheet("color: gray;")
        id_box.addWidget(self.id)
        id_box.addWidget(self.id_msg)
        id_box.addWidget(id_warn)
        form.addRow("ID", id_box)

        self.desc = QPlainTextEdit()
        self.desc.setFixedHeight(64)
        form.addRow("Description", self.desc)
        self.desc_note = QLabel("Blank: the display name is shown instead.")
        self.desc_note.setStyleSheet("color: gray;")
        form.addRow("", self.desc_note)

        self.prefab = QComboBox()
        form.addRow("Base prefab", self.prefab)
        self.station = QComboBox()
        form.addRow("Crafting station", self.station)
        self.min_level = QSpinBox()
        self.min_level.setRange(1, 10)
        self.min_level_label = QLabel("Min station level")
        form.addRow(self.min_level_label, self.min_level)
        self.hidden = QCheckBox("Hidden (can't be crafted or built)")
        form.addRow("", self.hidden)
        root.addLayout(form)

        req_group = QGroupBox("Requirements")
        rl = QVBoxLayout(req_group)
        self.reqs = RequirementsEditor()
        rl.addWidget(self.reqs)
        self.recipe_btn = QPushButton("Reset to base prefab's recipe")
        self.recipe_btn.setToolTip("Station, min level and requirements from the vanilla recipe of the chosen base prefab")
        self.recipe_btn.clicked.connect(self._reset_recipe)
        self.reqs.bottom_row.insertWidget(1, self.recipe_btn)
        root.addWidget(req_group)

        self.art_group = QGroupBox("Styles")
        al = QVBoxLayout(self.art_group)
        self.art = ArtEditor(guides)
        al.addWidget(self.art)
        self.art_group.setMinimumHeight(560)   # preview + controls; the form scrolls instead of squashing
        root.addWidget(self.art_group, 1)

        self.advanced = AdvancedLayers()
        root.addWidget(self.advanced)

        self.name.textEdited.connect(self._name_edited)
        self.id.textEdited.connect(self._id_edited)
        self.desc.textChanged.connect(self._desc_changed)
        self.prefab.currentIndexChanged.connect(self._prefab_changed)
        self.station.currentIndexChanged.connect(self._station_changed)
        self.min_level.valueChanged.connect(self._min_level_changed)
        self.hidden.toggled.connect(self._hidden_changed)
        self.reqs.changed.connect(self._emit)
        self.art.changed.connect(self._emit)
        self.advanced.changed.connect(self._emit)

    # ------------------------------------------------------------ binding
    def set_pack_kind(self, kind: str) -> None:
        self.kind = kind
        self._loading = True
        self._fill_combos()
        shield = kind == gd.SHIELD
        self.min_level.setVisible(shield)
        self.min_level_label.setVisible(shield)
        self.desc_note.setVisible(not shield)
        self.art_group.setTitle("Styles (1–16, list order = style order)" if shield else "Banner image")
        self._loading = False

    def _fill_combos(self) -> None:
        """(Re)build the dropdowns, dropping any "(custom)" entries from the previous item."""
        self.prefab.clear()
        for p in gd.prefabs(self.kind):
            self.prefab.addItem(p.name, p.id)
            self.prefab.setItemData(self.prefab.count() - 1, p.id, Qt.ItemDataRole.ToolTipRole)
        self.station.clear()
        for s in gd.stations(self.kind):
            self.station.addItem(s.name, s.value)
            self.station.setItemData(self.station.count() - 1, s.value or "(empty)", Qt.ItemDataRole.ToolTipRole)

    def set_item(self, item: Item | None) -> None:
        self.item = item
        self.setEnabled(item is not None)
        self._loading = True
        self._fill_combos()
        if item is not None:
            self.name.setText(item.display_name)
            self.id.setText(item.id)
            self.desc.setPlainText(item.description)
            self._select_data(self.prefab, item.base_prefab)
            self._select_data(self.station, item.station)
            self.min_level.setValue(max(1, item.min_station_level))
            self.hidden.setChecked(item.hidden)
            self.reqs.set_requirements(item.requirements, per_level=self.kind == gd.SHIELD)
        self.art.set_item(self.kind, item)
        self.advanced.set_item(self.kind, item)
        self._loading = False
        if item is not None:
            self._update_recipe_btn()
        self._check_id()

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        for i in range(combo.count()):
            if str(combo.itemData(i)).lower() == value.lower():
                combo.setCurrentIndex(i)
                return
        # value from a hand-made pack that isn't in the bundled list: keep it selectable
        combo.addItem(f"{value} (custom)", value)
        combo.setCurrentIndex(combo.count() - 1)

    def refresh_art(self) -> None:
        """Guide folder changed."""
        self.art.refresh()

    # ------------------------------------------------------------ edits
    def _emit(self):
        if not self._loading:
            if self.item:
                self._update_recipe_btn()
            self.changed.emit()

    def _name_edited(self, text: str):
        if not self.item:
            return
        self.item.display_name = text
        if not self.item.id_touched:
            self.item.id = id_from_name(text)
            self.id.setText(self.item.id)
        self._check_id()
        self.labelChanged.emit()
        self._emit()

    def _id_edited(self, text: str):
        if not self.item:
            return
        self.item.id = text.strip()
        self.item.id_touched = bool(text.strip())  # clearing the box resumes auto-fill
        self._check_id()
        self.labelChanged.emit()
        self._emit()

    def _check_id(self):
        if not self.item:
            self.id_msg.clear()
            return
        prob = id_problem(self.item.id)
        dupes = self._dupes
        if prob:
            self._set_id_msg(prob, True)
        elif dupes and self.item.id.lower() in dupes:
            self._set_id_msg("Another item in this pack already uses this ID.", True)
        elif GENERIC_ID.match(self.item.id):
            self._set_id_msg("Generic IDs clash easily with other people's packs. Add a prefix.", False)
        else:
            self._set_id_msg("", False)

    def _set_id_msg(self, text: str, error: bool):
        self.id_msg.setText(text)
        self.id_msg.setVisible(bool(text))
        self.id_msg.setStyleSheet("color: #bf616a;" if error else "color: #d08770;")

    def recheck_id(self, duplicates: set[str]) -> None:
        self._dupes = set(duplicates)
        self._check_id()

    def _desc_changed(self):
        if self.item and not self._loading:
            self.item.description = self.desc.toPlainText()
            self._emit()

    def _prefab_changed(self, _):
        if self._loading or not self.item:
            return
        new = self.prefab.currentData()
        old = self.item.base_prefab
        if new == old:
            return
        # Follow the new prefab's recipe if the cost is still the old prefab's default (or empty);
        # if the user has edited it, ask first rather than silently throwing their edits away.
        untouched = not self.item.requirements or matches_recipe(self.kind, self.item, old)
        self.item.base_prefab = new
        if gd.prefab(self.kind, new) and gd.prefab(self.kind, new).recipe:
            if untouched:
                self._apply_recipe()
            else:
                name = self.prefab.currentText()
                r = QMessageBox.question(self, "Use the new recipe?",
                                         f"You've edited this item's cost. Replace it with the {name} recipe?\n\n"
                                         "(Station, min station level and requirements.)")
                if r == QMessageBox.StandardButton.Yes:
                    self._apply_recipe()
        for a in self.item.art:
            a.placement = None     # different frame: auto-fit again
        self.art.refresh()
        self._update_recipe_btn()
        self._emit()

    def _apply_recipe(self) -> None:
        if not apply_recipe(self.kind, self.item, self.item.base_prefab):
            return
        self._loading = True
        self._select_data(self.station, self.item.station)
        self.min_level.setValue(self.item.min_station_level)
        self.reqs.set_requirements(self.item.requirements, per_level=self.kind == gd.SHIELD)
        self._loading = False

    def _reset_recipe(self) -> None:
        if self.item:
            self._apply_recipe()
            self._update_recipe_btn()
            self._emit()

    def _update_recipe_btn(self) -> None:
        p = gd.prefab(self.kind, self.item.base_prefab) if self.item else None
        self.recipe_btn.setVisible(bool(p and p.recipe))
        self.recipe_btn.setEnabled(bool(p and p.recipe) and not matches_recipe(self.kind, self.item, p.id))

    def _station_changed(self, _):
        if self.item and not self._loading:
            self.item.station = self.station.currentData()
            self._emit()

    def _min_level_changed(self, v: int):
        if self.item and not self._loading:
            self.item.min_station_level = v
            self._emit()

    def _hidden_changed(self, v: bool):
        if self.item and not self._loading:
            self.item.hidden = v
            self._emit()
