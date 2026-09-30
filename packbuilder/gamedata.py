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
KINDS = (SHIELD, BANNER)

def _data_dir() -> Path:
    """Bundled data files. In a PyInstaller build they're unpacked under sys._MEIPASS."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "packbuilder" / "data"
    return Path(__file__).resolve().parent / "data"


DATA_DIR = _data_dir()

JSON_FILE_NAME = {SHIELD: "shield.json", BANNER: "Banner.json"}
DROP_FOLDER_NAME = {SHIELD: "Valheim Custom Shields", BANNER: "Valheim Custom Banners"}

MAX_SHIELD_STYLES = 16  # ShieldShare's style atlas is a fixed 4x4 grid

# Optional texture layers copied into the item folder unchanged (section 7).
# StyleTex.png is not in spec v1 but ShieldShare reads it (a 4x4 style sheet).
ADVANCED_LAYERS = {
    SHIELD: ["MainTex.png", "BumpMap.png", "MetallicGlossMap.png", "EmissionMap.png", "StyleTex.png"],
    BANNER: ["BumpMap.png", "EmissiveTex.png", "MetalTex.png", "MossTex.png"],
}

# ShieldShare also accepts these short station names (ShieldPack.StationAliases).
# Only used when reading packs made by hand; the app always writes internal names.
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
                tuple(RecipePart(q["item"], int(q["amount"]), int(q.get("per_level", 0)) if kind == SHIELD else 0)
                      for q in r.get("requirements", [])),
            )
        out.append(Prefab(p["id"], p["name"], (int(p["size"][0]), int(p["size"][1])), recipe))
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
    if kind == SHIELD:
        if not raw:
            return "piece_workbench"  # ShieldShare's default for an empty value
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
