"""Requirement rows: filterable item picker, amount, amount per level, remove."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QCompleter, QHBoxLayout, QLabel, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import gamedata as gd
from ..model import Requirement


class ItemPicker(QComboBox):
    """Editable combo with type-to-filter. Shows friendly names, returns internal names.
    Free text is kept as-is (items from other mods)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumWidth(180)
        names = [i.name for i in gd.items()]
        for i in gd.items():
            self.addItem(i.name, i.id)
            self.setItemData(self.count() - 1, i.id, Qt.ItemDataRole.ToolTipRole)
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(completer)
        self.lineEdit().setPlaceholderText("Item (type to search)")

    def item_id(self) -> str:
        text = self.currentText().strip()
        if not text:
            return ""
        for i in gd.items():
            if text.lower() in (i.name.lower(), i.id.lower()):
                return i.id
        return text

    def set_item_id(self, item_id: str) -> None:
        name = gd.item_name(item_id)
        self.setEditText(name if name else item_id)


class RequirementRow(QWidget):
    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, req: Requirement, show_per_level: bool, parent=None):
        super().__init__(parent)
        self.req = req
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.picker = ItemPicker()
        self.picker.set_item_id(req.item)
        self.amount = QSpinBox()
        self.amount.setRange(1, 9999)
        self.amount.setValue(max(1, req.amount))
        self.amount.setPrefix("× ")
        self.amount.setToolTip("Amount")
        self.per_level = QSpinBox()
        self.per_level.setRange(0, 9999)
        self.per_level.setValue(max(0, req.amount_per_level))
        self.per_level.setPrefix("+")
        self.per_level.setSuffix(" / level")
        self.per_level.setToolTip("Extra amount for each upgrade level (0 = none)")
        self.per_level.setVisible(show_per_level)
        self.remove = QPushButton("✕")
        self.remove.setFixedWidth(30)
        self.remove.setToolTip("Remove this requirement")
        lay.addWidget(self.picker, 1)
        lay.addWidget(self.amount)
        lay.addWidget(self.per_level)
        lay.addWidget(self.remove)
        self.req.amount = self.amount.value()

        self.picker.editTextChanged.connect(self._sync)
        self.picker.currentIndexChanged.connect(self._sync)
        self.amount.valueChanged.connect(self._sync)
        self.per_level.valueChanged.connect(self._sync)
        self.remove.clicked.connect(lambda: self.removeRequested.emit(self))

    def _sync(self, *_):
        self.req.item = self.picker.item_id()
        self.req.amount = self.amount.value()
        self.req.amount_per_level = self.per_level.value() if self.per_level.isVisible() else 0
        self.changed.emit()


class RequirementsEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reqs: list[Requirement] = []
        self._per_level = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.rows_box = QVBoxLayout()
        outer.addLayout(self.rows_box)
        bottom = QHBoxLayout()
        self.bottom_row = bottom
        self.add_btn = QPushButton("Add requirement")
        self.add_btn.clicked.connect(self._add)
        self.empty_note = QLabel("At least one requirement is needed.")
        self.empty_note.setStyleSheet("color: #d08770;")
        bottom.addWidget(self.add_btn)
        bottom.addWidget(self.empty_note)
        bottom.addStretch(1)
        outer.addLayout(bottom)

    def set_requirements(self, reqs: list[Requirement], per_level: bool) -> None:
        self._reqs = reqs
        self._per_level = per_level
        while self.rows_box.count():
            w = self.rows_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in reqs:
            self._add_row(r)
        self._update_note()

    def _add_row(self, r: Requirement) -> RequirementRow:
        row = RequirementRow(r, self._per_level)
        row.changed.connect(self.changed)
        row.removeRequested.connect(self._remove)
        self.rows_box.addWidget(row)
        return row

    def _add(self):
        r = Requirement("", 1, 0)
        self._reqs.append(r)
        row = self._add_row(r)
        row.picker.setFocus()
        self._update_note()
        self.changed.emit()

    def _remove(self, row: RequirementRow):
        for i, r in enumerate(self._reqs):
            if r is row.req:   # by identity: two rows can hold equal requirements
                del self._reqs[i]
                break
        self.rows_box.removeWidget(row)
        row.deleteLater()
        self._update_note()
        self.changed.emit()

    def _update_note(self):
        self.empty_note.setVisible(not self._reqs)
