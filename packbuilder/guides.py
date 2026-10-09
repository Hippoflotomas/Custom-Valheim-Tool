"""Finds and caches ShieldShare pattern guides and TabardShare's UV layout."""
from __future__ import annotations

from pathlib import Path

from . import gamedata as gd
from .imaging import TABARD_LAYOUT_NAME, FaceGuide, TabardGuide, default_templates_dir, guide_path

BUNDLED_TABARD_LAYOUT = gd.DATA_DIR / TABARD_LAYOUT_NAME


class TabardLayouts:
    """TabardShare's UV layout, scaled to each texture size on demand.

    Prefers the copy the mod writes to ``Documents\\Valheim Custom Tabards\\_Templates`` on every
    launch (so it follows the installed mod version), else the copy bundled with the app.
    Unlike shield guides, there's always a layout, so tabards are never blocked.
    """

    def __init__(self, templates_dir: str | Path | None = None):
        self.templates_dir = Path(templates_dir) if templates_dir else default_templates_dir(gd.TABARD)
        self._base: tuple[object, TabardGuide] | None = None
        self._scaled: dict[int, TabardGuide] = {}
        self.note = ""     # why the installed copy wasn't used, or that it differs from the bundled one

    @property
    def installed_path(self) -> Path:
        return self.templates_dir / TABARD_LAYOUT_NAME

    def _load_base(self) -> TabardGuide:
        p = self.installed_path
        try:
            key = (str(p), p.stat().st_mtime)
        except OSError:
            key = ("bundled",)
        if self._base and self._base[0] == key:
            return self._base[1]
        self._scaled.clear()
        self.note = ""
        guide = None
        if key[0] != "bundled":
            try:
                guide = TabardGuide.load(p, "installed")
                bundled = bundled_tabard_layout()
                if guide.scaled(bundled.size[0]).islands != bundled.islands:
                    self.note = ("The UV layout from your installed TabardShare differs from the one "
                                 "this app was built with; using the installed one.")
            except Exception as ex:
                self.note = f"Your installed TabardShare's UV layout can't be used ({ex}); using the bundled copy."
                guide = None
        if guide is None:
            guide = bundled_tabard_layout()
        self._base = (key, guide)
        return guide

    def get(self, size: int) -> TabardGuide:
        base = self._load_base()
        if size not in self._scaled:
            self._scaled[size] = base.scaled(size)
        return self._scaled[size]


_bundled: TabardGuide | None = None


def bundled_tabard_layout() -> TabardGuide:
    global _bundled
    if _bundled is None:
        _bundled = TabardGuide.load(BUNDLED_TABARD_LAYOUT, "bundled")
    return _bundled


_default_layouts: TabardLayouts | None = None


def default_tabard_layouts() -> TabardLayouts:
    """For callers without a GuideStore (tests, scripts)."""
    global _default_layouts
    if _default_layouts is None:
        _default_layouts = TabardLayouts()
    return _default_layouts


class GuideStore:
    """Reads ``<templates>/<Prefab> - pattern guide.png`` on demand and caches the mask."""

    def __init__(self, templates_dir: str | Path | None = None, tabard_templates_dir: str | Path | None = None):
        self.templates_dir = Path(templates_dir) if templates_dir else default_templates_dir(gd.SHIELD)
        self._cache: dict[str, tuple[float, FaceGuide]] = {}
        self.errors: dict[str, str] = {}
        self.tabard = TabardLayouts(tabard_templates_dir)

    def set_dir(self, templates_dir: str | Path) -> None:
        self.templates_dir = Path(templates_dir)
        self._cache.clear()
        self.errors.clear()

    def path(self, prefab_id: str) -> Path:
        return guide_path(self.templates_dir, prefab_id)

    def get(self, prefab_id: str) -> FaceGuide | None:
        p = self.path(prefab_id)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            self._cache.pop(prefab_id, None)
            self.errors[prefab_id] = "missing"
            return None
        hit = self._cache.get(prefab_id)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            guide = FaceGuide.load(p, prefab_id)
        except Exception as ex:  # unreadable or no face in it
            self.errors[prefab_id] = str(ex)
            return None
        self.errors.pop(prefab_id, None)
        self._cache[prefab_id] = (mtime, guide)
        return guide

    def missing_message(self, prefab_id: str) -> str:
        err = self.errors.get(prefab_id)
        if err and err != "missing":
            return f"The pattern guide for {prefab_id} can't be used: {err}"
        msg = (f"No pattern guide for {prefab_id}. Expected:\n{self.path(prefab_id)}\n"
               "ShieldShare writes it when the game starts.")
        if prefab_id != "ShieldWood":
            msg += (" Guides for bases other than ShieldWood only appear after a pack using that "
                    "base has been loaded once. ShieldWood is the only base the mod has tested.")
        return msg
