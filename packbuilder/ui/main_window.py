"""Main window: start page, item list, item form, open/export."""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from .. import gamedata as gd
from .. import imaging, packio
from ..guides import GuideStore
from ..model import Pack
from .item_editor import ItemEditor

APP_NAME = "Valheim Pack Builder"
KIND_LABEL = {gd.SHIELD: "Shield pack", gd.BANNER: "Banner pack"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("HippoTools", "ValheimPackBuilder")
        self.guides = GuideStore(self.settings.value("templates_dir") or None)
        self.pack: Pack | None = None
        self.path: str | None = None
        self.dirty = False

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_start_page())
        self.stack.addWidget(self._build_editor_page())
        self._build_menu()
        self.guide_label = QLabel()
        self.statusBar().addPermanentWidget(self.guide_label)
        self._update_guide_label()
        self.resize(1200, 900)
        self._update_title()

    # ------------------------------------------------------------ layout
    def _build_start_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addStretch(1)
        title = QLabel(f"<h1>{APP_NAME}</h1><p>Build BannerShare and ShieldShare packs. "
                       "A pack holds only shields or only banners.</p>"
                       f"<p style='color: gray;'>Version {__version__}</p>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        row = QHBoxLayout()
        row.addStretch(1)
        for text, fn in (("New shield pack", lambda: self.new_pack(gd.SHIELD)),
                         ("New banner pack", lambda: self.new_pack(gd.BANNER)),
                         ("Open pack…", self.open_pack)):
            b = QPushButton(text)
            b.setMinimumSize(180, 56)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)
        return page

    def _build_editor_page(self) -> QWidget:
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        self.kind_label = QLabel()
        ll.addWidget(self.kind_label)
        self.item_list = QListWidget()
        self.item_list.currentRowChanged.connect(self._select_item)
        ll.addWidget(self.item_list, 1)
        for text, fn in (("Add item", self.add_item), ("Duplicate item", self.duplicate_item),
                         ("Remove item", self.remove_item)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            ll.addWidget(b)
        self.export_btn = QPushButton("Export zip…")
        self.export_btn.setMinimumHeight(40)
        self.export_btn.clicked.connect(lambda: self.export_pack(False))
        ll.addWidget(self.export_btn)
        split.addWidget(left)

        self.editor = ItemEditor(self.guides)
        self.editor.changed.connect(self._mark_dirty)
        self.editor.labelChanged.connect(self._refresh_labels)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 960])
        return split

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")

        def act(text, fn, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if shortcut:
                a.setShortcut(shortcut)
            m.addAction(a)
            return a

        act("New shield pack", lambda: self.new_pack(gd.SHIELD))
        act("New banner pack", lambda: self.new_pack(gd.BANNER))
        act("Open pack…", self.open_pack, QKeySequence.StandardKey.Open)
        m.addSeparator()
        self.export_act = act("Export zip", lambda: self.export_pack(False), QKeySequence.StandardKey.Save)
        self.export_as_act = act("Export zip as…", lambda: self.export_pack(True), QKeySequence.StandardKey.SaveAs)
        m.addSeparator()
        act("Pattern guide folder…", self.choose_templates_dir)
        m.addSeparator()
        act("Quit", self.close, QKeySequence.StandardKey.Quit)
        m = self.menuBar().addMenu("&Help")
        act("About…", self.show_about)
        self._enable_pack_actions(False)

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Builds pack zips for the BannerShare and ShieldShare Valheim mods.<br>"
            "Made by Hippoflotomas. Free to use.</p>"
            "<p>Built with Python, Qt and PySide6 (used under the GNU LGPL v3), Pillow and NumPy. "
            "Their licence texts are in the <i>licenses</i> folder next to the program, and "
            "Qt's source code is available from <a href='https://download.qt.io/'>download.qt.io</a>.</p>"
            "<p>If something goes wrong, details are saved to "
            "<i>%LOCALAPPDATA%\\ValheimPackBuilder\\error.log</i>.</p>"))

    def _enable_pack_actions(self, on: bool) -> None:
        self.export_act.setEnabled(on)
        self.export_as_act.setEnabled(on)

    # ------------------------------------------------------------ pack lifecycle
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_NAME, "This pack has changes that haven't been exported. Discard them?",
                                 QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        return r == QMessageBox.StandardButton.Discard

    def new_pack(self, kind: str) -> None:
        if not self._confirm_discard():
            return
        pack = Pack(kind)
        pack.new_item()
        self._load_pack(pack, None)
        self.dirty = True
        self._update_title()

    def open_pack(self) -> None:
        if not self._confirm_discard():
            return
        start = self.settings.value("last_zip_dir") or str(imaging.documents_dir())
        path, _ = QFileDialog.getOpenFileName(self, "Open pack", start, "Pack zip (*.zip)")
        if not path:
            return
        self.settings.setValue("last_zip_dir", str(Path(path).parent))
        try:
            pack, notes = packio.open_pack(path)
        except packio.PackReadError as ex:
            QMessageBox.warning(self, "Can't open pack", str(ex))
            return
        except Exception as ex:
            QMessageBox.critical(self, "Can't open pack", f"Unexpected error reading the zip:\n{ex}")
            return
        self._load_pack(pack, path)
        if notes:
            QMessageBox.information(self, "Opened with notes", "\n".join(f"• {n}" for n in notes))

    def _load_pack(self, pack: Pack, path: str | None) -> None:
        self.pack, self.path, self.dirty = pack, path, False
        self.editor.set_pack_kind(pack.kind)
        self.kind_label.setText(f"<b>{KIND_LABEL[pack.kind]}</b> — {len(pack.items)} item(s)")
        self.stack.setCurrentIndex(1)
        self._enable_pack_actions(True)
        self._rebuild_list(0)
        self._update_title()

    # ------------------------------------------------------------ items
    def _item_label(self, n: int) -> str:
        it = self.pack.items[n]
        return it.display_name or it.id or f"(item {n + 1})"

    def _rebuild_list(self, select: int) -> None:
        self.item_list.blockSignals(True)
        self.item_list.clear()
        for n in range(len(self.pack.items)):
            self.item_list.addItem(self._item_label(n))
        self.item_list.blockSignals(False)
        if self.pack.items:
            self.item_list.setCurrentRow(min(max(select, 0), len(self.pack.items) - 1))
        else:
            self.editor.set_item(None)
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        if not self.pack:
            return
        for n in range(self.item_list.count()):
            self.item_list.item(n).setText(self._item_label(n))
        self.kind_label.setText(f"<b>{KIND_LABEL[self.pack.kind]}</b> — {len(self.pack.items)} item(s)")
        self.editor.recheck_id(self.pack.duplicate_ids())

    def _select_item(self, row: int) -> None:
        if self.pack and 0 <= row < len(self.pack.items):
            self.editor.set_item(self.pack.items[row])
            self.editor.recheck_id(self.pack.duplicate_ids())

    def add_item(self) -> None:
        if not self.pack:
            return
        self.pack.new_item()
        self._rebuild_list(len(self.pack.items) - 1)
        self.editor.name.setFocus()
        self._mark_dirty()

    def duplicate_item(self) -> None:
        row = self.item_list.currentRow()
        if not self.pack or not 0 <= row < len(self.pack.items):
            return
        src = self.pack.items[row]
        dup = copy.copy(src)
        dup.requirements = [copy.copy(r) for r in src.requirements]
        dup.art = [copy.copy(a) for a in src.art]
        for a in dup.art:
            a.placement = copy.copy(a.placement)
        dup.advanced = dict(src.advanced)
        dup.id = f"{src.id}_copy" if src.id else ""
        dup.display_name = f"{src.display_name} (copy)" if src.display_name else ""
        dup.id_touched = True
        self.pack.items.insert(row + 1, dup)
        self._rebuild_list(row + 1)
        self._mark_dirty()

    def remove_item(self) -> None:
        row = self.item_list.currentRow()
        if not self.pack or not 0 <= row < len(self.pack.items):
            return
        label = self._item_label(row)
        if QMessageBox.question(self, "Remove item", f"Remove '{label}' from the pack?") \
                != QMessageBox.StandardButton.Yes:
            return
        del self.pack.items[row]
        self._rebuild_list(row)
        self._mark_dirty()

    # ------------------------------------------------------------ export
    def export_pack(self, ask_path: bool) -> None:
        if not self.pack:
            return
        issues = packio.validate(self.pack, self.guides)
        errors = [i for i in issues if i.error]
        warnings = [i for i in issues if not i.error]
        if errors:
            self._show_issues("Can't export yet", "Fix these first:", errors + warnings, QMessageBox.Icon.Warning)
            return
        if warnings:
            text = "Export anyway?\n\n" + "\n".join(f"• {w.item}: {w.message}" if w.item else f"• {w.message}"
                                                   for w in warnings)
            if QMessageBox.question(self, "Warnings", text) != QMessageBox.StandardButton.Yes:
                return

        path = self.path
        if ask_path or not path:
            start_dir = self.settings.value("last_zip_dir") or str(
                imaging.documents_dir() / gd.DROP_FOLDER_NAME[self.pack.kind])
            default = Path(start_dir) / (Path(path).name if path else
                                         ("MyShields.zip" if self.pack.kind == gd.SHIELD else "MyBanners.zip"))
            path, _ = QFileDialog.getSaveFileName(self, "Export pack", str(default), "Pack zip (*.zip)")
            if not path:
                return
            if not path.lower().endswith(".zip"):
                path += ".zip"
            self.settings.setValue("last_zip_dir", str(Path(path).parent))

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            issues = packio.export_pack(self.pack, path, self.guides)
        except Exception as ex:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Export failed", f"Nothing was written.\n\n{ex}")
            return
        QApplication.restoreOverrideCursor()
        if any(i.error for i in issues):   # something changed between the check and the write
            self._show_issues("Can't export yet", "Fix these first:", issues, QMessageBox.Icon.Warning)
            return
        self.path = path
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Exported {len(self.pack.items)} item(s) to {path}", 8000)

    def _show_issues(self, title: str, lead: str, issues, icon) -> None:
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(lead)
        box.setInformativeText("\n".join(f"• {i}" for i in issues))
        box.exec()

    # ------------------------------------------------------------ guides
    def choose_templates_dir(self) -> None:
        start = str(self.guides.templates_dir if self.guides.templates_dir.exists() else imaging.documents_dir())
        d = QFileDialog.getExistingDirectory(self, "Folder with the ShieldShare pattern guides (_Templates)", start)
        if not d:
            return
        self.guides.set_dir(d)
        self.settings.setValue("templates_dir", d)
        self._update_guide_label()
        if self.pack:
            self.editor.refresh_art()

    def _update_guide_label(self) -> None:
        d = self.guides.templates_dir
        state = "" if d.exists() else " (not found)"
        self.guide_label.setText(f"Pattern guides: {d}{state}")

    # ------------------------------------------------------------ misc
    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self) -> None:
        if not self.pack:
            self.setWindowTitle(APP_NAME)
            return
        name = Path(self.path).name if self.path else "Untitled"
        self.setWindowTitle(f"{name}{' *' if self.dirty else ''} — {KIND_LABEL[self.pack.kind]} — {APP_NAME}")

    def closeEvent(self, e):
        if self._confirm_discard():
            e.accept()
        else:
            e.ignore()
