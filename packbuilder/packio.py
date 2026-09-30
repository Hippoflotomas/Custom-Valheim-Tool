"""Reading and writing pack zips, and the checks run before export."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import gamedata as gd
from . import imaging
from .guides import GuideStore
from .model import GENERIC_ID, Art, Item, Pack, Placement, Requirement, id_problem

PATTERN_RE = re.compile(r"^pattern(\d+)\.png$", re.IGNORECASE)
ICON_RE = re.compile(r"^icon(\d+)\.png$", re.IGNORECASE)


# ---------------------------------------------------------------- frames & placement

def frame_size(kind: str, item: Item, guides: GuideStore | None) -> tuple[int, int]:
    """Output size for this item's images."""
    if kind == gd.SHIELD and guides is not None:
        g = guides.get(item.base_prefab)
        if g is not None:
            return g.size
    p = gd.prefab(kind, item.base_prefab)
    if p is not None:
        return p.size
    if item.art:
        return item.art[0].source.size
    return (512, 512)


def fit_box(kind: str, item: Item, guides: GuideStore | None) -> tuple[int, int, int, int]:
    """Area the auto-fit targets: the face mask's bounding box for shields, the whole frame for banners."""
    if kind == gd.SHIELD and guides is not None:
        g = guides.get(item.base_prefab)
        if g is not None:
            return g.face_box
    w, h = frame_size(kind, item, guides)
    return (0, 0, w, h)


def ensure_placement(kind: str, item: Item, art: Art, guides: GuideStore | None, mode: str = "fill") -> Placement:
    if art.placement is None:
        art.placement = imaging.auto_fit(art.source.size, fit_box(kind, item, guides), mode)
    return art.placement


# ---------------------------------------------------------------- validation

@dataclass
class Issue:
    error: bool       # True blocks export; False is a warning
    item: str         # item label, or "" for the pack
    message: str

    def __str__(self) -> str:
        where = f"{self.item}: " if self.item else ""
        return f"{'Error' if self.error else 'Warning'} - {where}{self.message}"


def validate(pack: Pack, guides: GuideStore | None) -> list[Issue]:
    issues: list[Issue] = []
    kind = pack.kind
    if not pack.items:
        issues.append(Issue(True, "", "The pack has no items."))
    dupes = pack.duplicate_ids()

    for n, it in enumerate(pack.items, 1):
        label = it.id or it.display_name or f"Item {n}"
        err = lambda m: issues.append(Issue(True, label, m))
        warn = lambda m: issues.append(Issue(False, label, m))

        prob = id_problem(it.id)
        if prob:
            err(prob)
        elif it.id.lower() in dupes:
            err(f"ID '{it.id}' is used by more than one item (IDs ignore capitals).")
        elif GENERIC_ID.match(it.id):
            warn(f"ID '{it.id}' looks generic and may clash with other people's packs. "
                 "A prefix such as 'YourName_' helps.")

        if not it.display_name.strip():
            err("Display name is empty.")
        if not it.base_prefab.strip():
            err("No base prefab chosen.")

        reqs = it.requirements
        if not reqs:
            err("Needs at least one requirement.")
        for r in reqs:
            if not r.item.strip():
                err("A requirement has no item.")
            if r.amount < 1:
                err(f"Requirement '{r.item}' needs an amount of 1 or more.")
            if kind == gd.SHIELD and r.amount_per_level < 0:
                err(f"Requirement '{r.item}' has a negative amount per level.")

        if kind == gd.SHIELD:
            if it.min_station_level < 1:
                err("Min station level must be 1 or more.")
            if not 1 <= len(it.art) <= gd.MAX_SHIELD_STYLES:
                err(f"Shields need 1 to {gd.MAX_SHIELD_STYLES} styles (has {len(it.art)}).")
            if guides is not None and it.base_prefab and guides.get(it.base_prefab) is None:
                err(guides.missing_message(it.base_prefab).split("\n")[0])
        else:
            if len(it.art) != 1:
                err("Banners need exactly one image.")

        for i, art in enumerate(it.art, 1):
            if kind == gd.SHIELD and guides is not None and guides.get(it.base_prefab) is None:
                continue
            p = ensure_placement(kind, it, art, guides)
            if imaging.is_upscaled(p):
                what = f"Style {i}" if kind == gd.SHIELD else "The image"
                warn(f"{what} ({art.label or 'image'}) is enlarged {p.scale:.1f}x and may look soft.")

        if kind == gd.BANNER and "BumpMap.png" in it.advanced and it.art:
            try:
                bw, bh = imaging.open_rgba(it.advanced["BumpMap.png"]).size
                fw, fh = frame_size(kind, it, guides)
                if (bw, bh) != (fw, fh):
                    warn(f"BumpMap.png is {bw}x{bh} but MainTex.png will be {fw}x{fh}; they should match.")
            except Exception:
                err("BumpMap.png can't be read as an image.")
    return issues


# ---------------------------------------------------------------- export

def item_json(kind: str, it: Item) -> dict:
    reqs = []
    for r in it.requirements:
        d = {"item": r.item.strip(), "amount": int(r.amount)}  # always explicit: BannerShare defaults to 0
        if kind == gd.SHIELD and r.amount_per_level > 0:
            d["amountPerLevel"] = int(r.amount_per_level)
        reqs.append(d)
    d = {
        "displayName": it.display_name.strip(),
        "description": it.description.strip(),
        "basePrefab": it.base_prefab,
        "craftingStation": it.station,
    }
    if kind == gd.SHIELD:
        d["minStationLevel"] = int(it.min_station_level)
    d["hidden"] = bool(it.hidden)
    d["requirements"] = reqs
    return d


def item_files(kind: str, it: Item, guides: GuideStore | None) -> dict[str, bytes]:
    """All files for one item folder, by file name."""
    files = {gd.JSON_FILE_NAME[kind]: (json.dumps(item_json(kind, it), indent=2) + "\n").encode("utf-8")}
    if kind == gd.SHIELD:
        guide = guides.get(it.base_prefab) if guides else None
        if guide is None:
            raise ValueError(f"{it.id}: no pattern guide for {it.base_prefab}.")
        for n, art in enumerate(it.art, 1):
            p = ensure_placement(kind, it, art, guides)
            files[f"Pattern{n}.png"] = imaging.png_bytes(imaging.render_shield_pattern(art.source, p, guide))
    else:
        art = it.art[0]
        p = ensure_placement(kind, it, art, guides)
        main = imaging.render_banner(art.source, p, frame_size(kind, it, guides))
        files["MainTex.png"] = imaging.png_bytes(main)
        files["Icon.png"] = imaging.png_bytes(imaging.make_icon(main))
    for name in gd.ADVANCED_LAYERS[kind]:
        if name in it.advanced:
            files[name] = it.advanced[name]
    return files


def export_pack(pack: Pack, path: str, guides: GuideStore | None) -> list[Issue]:
    """Validate, then write the zip. Returns the issues; nothing is written if any is an error."""
    issues = validate(pack, guides)
    if any(i.error for i in issues):
        return issues
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=folder)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for it in pack.items:
                for name, data in item_files(pack.kind, it, guides).items():
                    z.writestr(f"{it.id}/{name}", data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return issues


# ---------------------------------------------------------------- open

class PackReadError(Exception):
    pass


def _ci(d: dict, key: str, default=None):
    """Case-insensitive dict lookup (both mods match JSON keys case-insensitively)."""
    for k, v in d.items():
        if isinstance(k, str) and k.lower() == key.lower():
            return v
    return default


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def open_pack(path: str) -> tuple[Pack, list[str]]:
    """Read a pack zip back in. Returns the pack and a list of notes about anything dropped."""
    notes: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as ex:
        raise PackReadError(f"Not a zip file: {ex}") from ex

    folders: dict[str, dict[str, bytes]] = {}
    with z:
        for info in z.infolist():
            name = info.filename
            if info.is_dir() or name.startswith("__MACOSX/"):
                continue
            parts = PurePosixPath(name).parts
            if parts and parts[-1].startswith("."):
                continue
            if len(parts) != 2:
                notes.append(f"Ignored '{name}' (files must sit in an item folder at the top of the zip).")
                continue
            folders.setdefault(parts[0], {})[parts[1]] = z.read(info)

    kinds = set()
    for files in folders.values():
        lower = {n.lower() for n in files}
        for k in gd.KINDS:
            if gd.JSON_FILE_NAME[k].lower() in lower:
                kinds.add(k)
    if not kinds:
        raise PackReadError("No shield.json or Banner.json found - this isn't a shield or banner pack.")
    if len(kinds) > 1:
        raise PackReadError("This zip mixes shields and banners. Split it into one zip per type.")
    kind = kinds.pop()
    pack = Pack(kind)

    for folder, files in sorted(folders.items(), key=lambda kv: kv[0].lower()):
        by_lower = {n.lower(): n for n in files}
        json_name = by_lower.get(gd.JSON_FILE_NAME[kind].lower())
        if json_name is None:
            notes.append(f"Skipped folder '{folder}' - no {gd.JSON_FILE_NAME[kind]}.")
            continue
        try:
            raw = json.loads(files[json_name].decode("utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("top level isn't an object")
        except Exception as ex:
            notes.append(f"Skipped '{folder}' - {gd.JSON_FILE_NAME[kind]} can't be read: {ex}")
            continue

        it = Item(id=folder, id_touched=True)
        it.display_name = str(_ci(raw, "displayName") or "")
        it.description = str(_ci(raw, "description") or "")
        default_base = "ShieldWood" if kind == gd.SHIELD else "piece_banner01"
        base = str(_ci(raw, "basePrefab") or "").strip() or default_base
        known = gd.prefab(kind, base)
        it.base_prefab = known.id if known else base
        it.hidden = bool(_ci(raw, "hidden", False))
        it.station = gd.normalise_station(kind, _ci(raw, "craftingStation"))
        if kind == gd.SHIELD:
            it.min_station_level = max(1, _int(_ci(raw, "minStationLevel", 1), 1))
        for r in _ci(raw, "requirements") or []:
            if not isinstance(r, dict):
                continue
            item_name = str(_ci(r, "item") or "").strip()
            if not item_name:
                continue
            default_amount = 1 if kind == gd.SHIELD else 0
            it.requirements.append(Requirement(
                item_name,
                _int(_ci(r, "amount", default_amount), default_amount),
                _int(_ci(r, "amountPerLevel", 0), 0) if kind == gd.SHIELD else 0,
            ))

        frame = gd.prefab(kind, it.base_prefab).size if gd.prefab(kind, it.base_prefab) else None
        if kind == gd.SHIELD:
            numbered = []
            for n in files:
                m = PATTERN_RE.match(n)
                if m:
                    numbered.append((int(m.group(1)), n))
            numbered.sort()
            cap = _int(_ci(raw, "styleCount", 0), 0)
            limit = cap if 0 < cap < gd.MAX_SHIELD_STYLES else gd.MAX_SHIELD_STYLES
            if len(numbered) > limit:
                notes.append(f"{folder}: only the first {limit} patterns are used (the mod ignores the rest).")
                numbered = numbered[:limit]
            for _, n in numbered:
                it.art.append(_art_from_pack(files[n], n, frame))
            if any(ICON_RE.match(n) for n in files):
                notes.append(f"{folder}: custom IconN.png files are dropped on export; "
                             "ShieldShare generates icons from the patterns.")
        else:
            main = by_lower.get("maintex.png")
            if main:
                it.art.append(_art_from_pack(files[main], main, frame))

        for layer in gd.ADVANCED_LAYERS[kind]:
            real = by_lower.get(layer.lower())
            if real:
                it.advanced[layer] = files[real]

        handled = {json_name.lower()} | {l.lower() for l in gd.ADVANCED_LAYERS[kind]}
        for n in files:
            low = n.lower()
            if low in handled or (kind == gd.SHIELD and (PATTERN_RE.match(n) or ICON_RE.match(n))):
                continue
            if kind == gd.BANNER and low in ("maintex.png", "icon.png"):
                continue
            notes.append(f"{folder}: '{n}' is not used by the mod and will be dropped on export.")
        pack.items.append(it)

    if not pack.items:
        raise PackReadError("No readable items in this zip.")
    return pack, notes


def _art_from_pack(data: bytes, label: str, frame: tuple[int, int] | None) -> Art:
    img = imaging.open_rgba(data)
    if frame:
        img = imaging.conform_to_frame(img, frame)
        placement = imaging.full_frame_placement(img.size, frame)
    else:
        placement = None
    return Art(img, label, placement)
