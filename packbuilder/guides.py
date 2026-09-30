"""Finds and caches ShieldShare pattern guides."""
from __future__ import annotations

from pathlib import Path

from .imaging import FaceGuide, default_templates_dir, guide_path


class GuideStore:
    """Reads ``<templates>/<Prefab> - pattern guide.png`` on demand and caches the mask."""

    def __init__(self, templates_dir: str | Path | None = None):
        self.templates_dir = Path(templates_dir) if templates_dir else default_templates_dir()
        self._cache: dict[str, tuple[float, FaceGuide]] = {}
        self.errors: dict[str, str] = {}

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
