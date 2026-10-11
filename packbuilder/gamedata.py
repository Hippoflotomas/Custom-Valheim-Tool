"""Bundled game data: prefabs, crafting stations and the item list.

Everything lives in JSON files under ``data/`` so it can be updated without
touching code when the game or the mods change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
import sys
from pathlib import Path

SHIELD = "shield"
BANNER = "banner"
TABARD = "tabard"
KINDS = (SHIELD, BANNER, TABARD)

def _data_dir() -> Path:
    """Bundled data files. In a PyInstaller build they're unpacked under sys._MEIPASS."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "packbuilder" / "data"
    return Path(__file__).resolve().parent / "data"


DATA_DIR = _data_dir()

JSON_FILE_NAME = {SHIELD: "shield.json", BANNER: "Banner.json", TABARD: "Tabard.json"}
DROP_FOLDER_NAME = {SHIELD: "Valheim Custom Shields", BANNER: "Valheim Custom Banners",
                    TABARD: "Valheim Custom Tabards"}
MOD_NAME = {SHIELD: "ShieldShare", BANNER: "BannerShare", TABARD: "TabardShare"}

MAX_STYLES = 16  # ShieldShare's style atlas is a fixed 4x4 grid; TabardShare also caps at 16
MAX_SHIELD_STYLES = MAX_STYLES  # old name

TABARD_SIZES = (1024, 2048)  # pattern texture sizes offered for tabards (square)


# ---------------------------------------------------------------- what each pack type has

def has_styles(kind: str) -> bool:
    """1-16 PatternN.png styles (shields, tabards) rather than one main image (banners)."""
    return kind in (SHIELD, TABARD)


def has_min_level(kind: str) -> bool:
    return kind in (SHIELD, TABARD)


def has_per_level(kind: str) -> bool:
    """amountPerLevel is only used by shields: TabardShare items have m_maxQuality = 1."""
    return kind == SHIELD


def has_base_prefab(kind: str) -> bool:
    """Tabards have one model, so no basePrefab is written."""
    return kind in (SHIELD, BANNER)


def max_styles(kind: str) -> int:
    return MAX_STYLES if has_styles(kind) else 1


def default_requirement_amount(kind: str) -> int:
    """Amount a requirement gets when the JSON leaves it out (BannerShare: 0, the others: 1)."""
    return 0 if kind == BANNER else 1

# Optional texture layers copied into the item folder unchanged (section 7).
# StyleTex.png is not in spec v1 but ShieldShare reads it (a 4x4 style sheet).
ADVANCED_LAYERS = {
    SHIELD: ["MainTex.png", "BumpMap.png", "MetallicGlossMap.png", "EmissionMap.png", "StyleTex.png"],
    BANNER: ["BumpMap.png", "EmissiveTex.png", "MetalTex.png", "MossTex.png"],
    TABARD: ["BumpMap.png", "MetallicGlossMap.png"],
}

# ShieldShare and TabardShare also accept these short station names (ShieldPack.StationAliases,
# TabardPack.StationAliases). Only used when reading packs made by hand; the app always writes
# internal names. Both mods treat "none" as "no station" and an EMPTY value as the workbench.
SHIELD_STATION_ALIASES = {
    "workbench": "piece_workbench",
    "forge": "forge",
    "stonecutter": "piece_stonecutter",
    "artisan": "piece_artisanstation",
    "artisantable": "piece_artisanstation",
    "artisanstation": "piece_artisanstation",
    "blackforge": "blackforge",
    "blacksmith": "blackforge",
    "galdr": "piece_magetable",
    "galdrtable": "piece_magetable",
    "magetable": "piece_magetable",
    "cauldron": "piece_cauldron",
    "none": "none",
}


@dataclass(frozen=True)
class RecipePart:
    item: str
    amount: int
    per_level: int = 0


@dataclass(frozen=True)
class Recipe:
    """The vanilla cost of a base prefab, used as the form's default."""
    station: str                      # value written for this pack type
    min_station_level: int
    requirements: tuple[RecipePart, ...]


@dataclass(frozen=True)
class Prefab:
    id: str
    name: str
    size: tuple[int, int]  # output (width, height) in pixels
    recipe: Recipe | None = None
    note: str = ""         # shown under the base prefab dropdown (e.g. which mod version it needs)


@dataclass(frozen=True)
class Station:
    name: str       # friendly name shown in the dropdown
    value: str      # what gets written to the JSON for this pack type


@dataclass(frozen=True)
class GameItem:
    name: str
    id: str


def _load(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=None)
def prefabs(kind: str) -> tuple[Prefab, ...]:
    raw = _load("game_data.json")[f"{kind}_prefabs"]
    out = []
    for p in raw:
        r = p.get("recipe")
        recipe = None
        if r:
            recipe = Recipe(
                r.get("station", default_station(kind)),
                int(r.get("min_station_level", 1)),
                tuple(RecipePart(q["item"], int(q["amount"]), int(q.get("per_level", 0)) if has_per_level(kind) else 0)
                      for q in r.get("requirements", [])),
            )
        out.append(Prefab(p["id"], p["name"], (int(p["size"][0]), int(p["size"][1])), recipe, p.get("note", "")))
    return tuple(out)


def prefab(kind: str, prefab_id: str) -> Prefab | None:
    for p in prefabs(kind):
        if p.id.lower() == prefab_id.lower():
            return p
    return None


@lru_cache(maxsize=None)
def stations(kind: str) -> tuple[Station, ...]:
    raw = _load("game_data.json")["stations"]
    return tuple(Station(s["name"], s[kind]) for s in raw)


def station_name(kind: str, value: str) -> str | None:
    for s in stations(kind):
        if s.value.lower() == value.lower():
            return s.name
    return None


def default_station(kind: str) -> str:
    return stations(kind)[0].value  # Workbench


def normalise_station(kind: str, raw: str | None) -> str:
    """Turn a craftingStation value read from a pack into what this app writes."""
    raw = (raw or "").strip()
    if kind in (SHIELD, TABARD):
        if not raw:
            return "piece_workbench"  # ShieldShare's and TabardShare's default for an empty value
        key = raw.replace(" ", "").replace("_", "").lower()
        if key in SHIELD_STATION_ALIASES:
            return SHIELD_STATION_ALIASES[key]
        return raw
    # BannerShare passes the value straight to Jotunn; empty means no station.
    return raw


@lru_cache(maxsize=None)
def items() -> tuple[GameItem, ...]:
    return tuple(GameItem(i["name"], i["id"]) for i in _load("items.json")["items"])


def item_name(item_id: str) -> str | None:
    for i in items():
        if i.id.lower() == item_id.lower():
            return i.name
    return None
