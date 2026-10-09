"""Pack data model and item-ID rules."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from PIL import Image

from . import gamedata as gd

_ID_STRIP = re.compile(r"[^A-Za-z0-9_-]")
_ID_VALID = re.compile(r"^[A-Za-z0-9_-]+$")

# "Missing" is BannerShare's placeholder folder and ShieldShare's reserved folder;
# ShieldShare's built-in placeholders are named "Missing_<base>".
RESERVED_IDS = {"missing"}
RESERVED_PREFIX = "missing_"
# Per pack type, lower-case: the mod's own built-in items. Folder names ignore case on Windows.
RESERVED_BY_KIND = {
    gd.TABARD: {"test": "'Test' is TabardShare's built-in test tabard."},
}
GENERIC_ID = re.compile(r"^(shield|banner|tabard|item|test|new)[-_]?\d*$", re.IGNORECASE)

LINEN = (232, 220, 196)   # default tabard background: unbleached linen, #E8DCC4


def id_from_name(display_name: str) -> str:
    """Auto-fill an ID from a display name: keep letters, digits, - and _ only."""
    return _ID_STRIP.sub("", display_name.strip()).strip("_")


def id_problem(item_id: str, kind: str | None = None) -> str | None:
    """Why this ID can't be used, or None if it's fine. Checks one ID on its own.
    ``kind`` adds that pack type's reserved IDs (e.g. TabardShare's built-in "Test")."""
    if not item_id:
        return "ID is empty."
    if not _ID_VALID.match(item_id):
        return "ID may only use letters, digits, - and _."
    if item_id.startswith("_") or item_id.endswith("_"):
        # ShieldShare trims leading/trailing '_' and skips folders starting with '_'.
        return "ID can't start or end with _."
    low = item_id.lower()
    if low in RESERVED_IDS or low.startswith(RESERVED_PREFIX):
        return f"'{item_id}' is reserved by the mods."
    reserved = RESERVED_BY_KIND.get(kind, {})
    if low in reserved:
        return reserved[low]
    return None


@dataclass
class Requirement:
    item: str = ""            # internal item name, e.g. LeatherScraps
    amount: int = 1
    amount_per_level: int = 0  # shields only (tabards parse it but never use it)


@dataclass
class Placement:
    """Where the source image sits in the output frame.

    ``scale`` is output pixels per source pixel; ``ox``/``oy`` is the position
    of the source's top-left corner in output pixels. If the art is rotated, they
    describe the rotated image's bounding box (see ``imaging.rotated_size``).
    """
    scale: float
    ox: float
    oy: float


@dataclass
class Art:
    """One imported image: a shield style or a banner's main image."""
    source: Image.Image           # RGBA, transparent margins already trimmed
    label: str = ""               # file name it came from, for the list
    placement: Placement | None = None  # None = auto-fit when first shown/exported
    rotation: float = 0.0         # degrees clockwise, applied before placement; kept across re-fits

    def clone(self) -> "Art":
        """An independent copy (the source image is shared: it's never edited in place)."""
        dup = copy.copy(self)
        dup.placement = copy.copy(self.placement)
        return dup


BACK_SAME = "same"      # the front image again, placed the same way on the back panel
BACK_OWN = "own"        # its own image (``TabardStyle.back``)
BACK_COLOUR = "colour"  # plain background colour
BACK_MODES = (BACK_SAME, BACK_OWN, BACK_COLOUR)


@dataclass
class TabardStyle(Art):
    """One tabard style (one PatternN.png).

    The inherited Art fields are the FRONT image; its placement is in pattern pixels and
    normally sits on the front (left) panel. ``sheet`` styles are ready-made patterns
    painted over the UV layout: ``source`` is the whole pattern and is used as-is.
    """
    back: Art | None = None
    back_mode: str = BACK_SAME
    sheet: bool = False
    background: tuple[int, int, int] = LINEN
    keep_holes: bool = False      # leave transparency alone: under 50% alpha cuts a hole in game
    icon: bytes | None = None     # custom IconN.png; None = TabardShare makes one from the front panel

    def clone(self) -> "TabardStyle":
        dup = copy.copy(self)
        dup.placement = copy.copy(self.placement)
        dup.back = self.back.clone() if self.back is not None else None
        return dup

    def placed_arts(self) -> list[Art]:
        """Every image this style places (for rescaling or invalidating placements)."""
        out: list[Art] = [self]
        if self.back is not None:
            out.append(self.back)
        return out

@dataclass
class Item:
    id: str = ""
    display_name: str = ""
    description: str = ""
    base_prefab: str = ""
    hidden: bool = False
    station: str = ""             # the value written to JSON for this pack type
    min_station_level: int = 1    # shields and tabards
    requirements: list[Requirement] = field(default_factory=list)
    art: list[Art] = field(default_factory=list)          # shields 1-16, banners exactly 1, tabards 1-16 TabardStyle
    texture_size: int = 1024      # tabards only: square pattern size (1024 or 2048)
    advanced: dict[str, bytes] = field(default_factory=dict)  # layer file name -> PNG bytes
    id_touched: bool = False      # user edited the ID by hand: stop auto-filling it


@dataclass
class Pack:
    kind: str                     # gd.SHIELD, gd.BANNER or gd.TABARD; fixed at creation
    items: list[Item] = field(default_factory=list)

    def new_item(self) -> Item:
        item = Item(base_prefab=gd.prefabs(self.kind)[0].id, station=gd.default_station(self.kind))
        apply_recipe(self.kind, item, item.base_prefab)
        self.items.append(item)
        return item

    def duplicate_ids(self) -> set[str]:
        """IDs (lower-cased) used by more than one item. Case-insensitive: Windows folders are."""
        seen: dict[str, int] = {}
        for it in self.items:
            seen[it.id.lower()] = seen.get(it.id.lower(), 0) + 1
        return {k for k, n in seen.items() if n > 1 and k}


# ---------------------------------------------------------------- prefab recipes

def recipe_requirements(recipe: gd.Recipe) -> list[Requirement]:
    return [Requirement(p.item, p.amount, p.per_level) for p in recipe.requirements]


def apply_recipe(kind: str, item: Item, prefab_id: str) -> bool:
    """Set the item's station, level and requirements to the prefab's vanilla recipe.
    Returns False (and changes nothing) if the prefab has no recipe."""
    p = gd.prefab(kind, prefab_id)
    if p is None or p.recipe is None:
        return False
    item.station = p.recipe.station
    item.min_station_level = p.recipe.min_station_level
    item.requirements[:] = recipe_requirements(p.recipe)   # in place: the form holds this list
    return True


def matches_recipe(kind: str, item: Item, prefab_id: str) -> bool:
    """True if the item's cost is still exactly the prefab's vanilla recipe (i.e. not edited)."""
    p = gd.prefab(kind, prefab_id)
    if p is None or p.recipe is None:
        return False
    if item.station != p.recipe.station:
        return False
    if gd.has_min_level(kind) and item.min_station_level != p.recipe.min_station_level:
        return False
    mine = [(r.item, r.amount, r.amount_per_level if gd.has_per_level(kind) else 0) for r in item.requirements]
    theirs = [(r.item, r.amount, r.per_level) for r in p.recipe.requirements]
    return mine == theirs


def rescale_placements(item: Item, factor: float) -> None:
    """Scale every placement by ``factor`` (a tabard's texture size changed): same look, new pixels."""
    for a in item.art:
        arts = a.placed_arts() if isinstance(a, TabardStyle) else [a]
        for x in arts:
            p = x.placement
            if p is not None:
                x.placement = Placement(p.scale * factor, p.ox * factor, p.oy * factor)
