"""Reading and writing pack zips, and the checks run before export."""
from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from PIL import Image

from . import gamedata as gd
from . import imaging
from .guides import GuideStore, TabardLayouts, default_tabard_layouts
from .model import (BACK_OWN, GENERIC_ID, Art, Item, Pack, Placement, Requirement, TabardStyle, id_problem,
                    recipe_requirements)

PATTERN_RE = re.compile(r"^pattern(\d+)\.png$", re.IGNORECASE)
ICON_RE = re.compile(r"^icon(\d+)\.png$", re.IGNORECASE)


# ---------------------------------------------------------------- frames & placement

def tabard_layouts(guides: GuideStore | None) -> TabardLayouts:
    return guides.tabard if guides is not None else default_tabard_layouts()


def tabard_guide(item: Item, guides: GuideStore | None) -> imaging.TabardGuide:
    return tabard_layouts(guides).get(item.texture_size)


def as_style(art: Art) -> TabardStyle:
    """Tabard items hold TabardStyle; a plain Art (e.g. from a script) gets the defaults."""
    if isinstance(art, TabardStyle):
        return art
    return TabardStyle(art.source, art.label, art.placement, art.rotation)


def frame_size(kind: str, item: Item, guides: GuideStore | None) -> tuple[int, int]:
    """Output size for this item's images."""
    if kind == gd.TABARD:
        return (item.texture_size, item.texture_size)
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


def fit_box(kind: str, item: Item, guides: GuideStore | None, panel: str = "front") -> tuple[int, int, int, int]:
    """Area the auto-fit targets: the face mask's bounding box for shields, the panel's box for
    tabards, the whole frame for banners."""
    if kind == gd.TABARD:
        return tabard_guide(item, guides).islands[panel]
    if kind == gd.SHIELD and guides is not None:
        g = guides.get(item.base_prefab)
        if g is not None:
            return g.face_box
    w, h = frame_size(kind, item, guides)
    return (0, 0, w, h)


def ensure_placement(kind: str, item: Item, art: Art, guides: GuideStore | None, mode: str = "fill",
                     panel: str = "front") -> Placement:
    if art.placement is None:
        if isinstance(art, TabardStyle) and art.sheet:
            art.placement = imaging.full_frame_placement(art.source.size, frame_size(kind, item, guides))
        else:
            art.placement = imaging.auto_fit(art.source.size, fit_box(kind, item, guides, panel), mode, art.rotation)
    return art.placement


def ensure_style_placements(item: Item, style: TabardStyle, guides: GuideStore | None) -> None:
    """Auto-fit whatever in a tabard style hasn't been placed yet (front, and its own back)."""
    ensure_placement(gd.TABARD, item, style, guides)
    if style.back is not None:
        ensure_placement(gd.TABARD, item, style.back, guides, panel="back")


def _peek_placement(kind: str, item: Item, art: Art, guides: GuideStore | None, panel: str = "front") -> Placement:
    """The placement export would use, without storing an auto-fit (validation mustn't change the pack)."""
    if art.placement is not None:
        return art.placement
    if isinstance(art, TabardStyle) and art.sheet:
        return imaging.full_frame_placement(art.source.size, frame_size(kind, item, guides))
    return imaging.auto_fit(art.source.size, fit_box(kind, item, guides, panel), "fill", art.rotation)


def render_tabard_style(item: Item, style: TabardStyle, guides: GuideStore | None, bleed: bool = True):
    """Render one style without changing the item (placements auto-fitted on a copy if needed)."""
    st = style.clone()
    if st.placement is None:
        st.placement = _peek_placement(gd.TABARD, item, st, guides)
    if st.back is not None and st.back.placement is None:
        st.back.placement = _peek_placement(gd.TABARD, item, st.back, guides, "back")
    return imaging.render_tabard_pattern(st, tabard_guide(item, guides), bleed=bleed)


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

        prob = id_problem(it.id, kind)
        if prob:
            err(prob)
        elif it.id.lower() in dupes:
            err(f"ID '{it.id}' is used by more than one item (IDs ignore capitals).")
        elif GENERIC_ID.match(it.id):
            warn(f"ID '{it.id}' looks generic and may clash with other people's packs. "
                 "A prefix such as 'YourName_' helps.")

        if not it.display_name.strip():
            err("Display name is empty.")
        if gd.has_base_prefab(kind) and not it.base_prefab.strip():
            err("No base prefab chosen.")

        reqs = it.requirements
        if not reqs:
            err("Needs at least one requirement.")
        for r in reqs:
            if not r.item.strip():
                err("A requirement has no item.")
            if r.amount < 1:
                err(f"Requirement '{r.item}' needs an amount of 1 or more.")
            if gd.has_per_level(kind) and r.amount_per_level < 0:
                err(f"Requirement '{r.item}' has a negative amount per level.")

        if kind == gd.SHIELD:
            if it.min_station_level < 1:
                err("Min station level must be 1 or more.")
            if not 1 <= len(it.art) <= gd.MAX_SHIELD_STYLES:
                err(f"Shields need 1 to {gd.MAX_SHIELD_STYLES} styles (has {len(it.art)}).")
            if guides is not None and it.base_prefab and guides.get(it.base_prefab) is None:
                err(guides.missing_message(it.base_prefab).split("\n")[0])
        elif kind == gd.TABARD:
            if it.min_station_level < 1:
                err("Min station level must be 1 or more.")
            if not 1 <= len(it.art) <= gd.MAX_STYLES:
                err(f"Tabards need 1 to {gd.MAX_STYLES} styles (has {len(it.art)}).")
            if it.texture_size not in gd.TABARD_SIZES:
                err(f"Texture size must be one of {', '.join(map(str, gd.TABARD_SIZES))}.")
            else:
                _validate_tabard_styles(it, guides, err, warn)
        else:
            if len(it.art) != 1:
                err("Banners need exactly one image.")

        for i, art in enumerate(it.art, 1):
            if kind == gd.TABARD:
                continue    # checked per panel in _validate_tabard_styles
            if kind == gd.SHIELD and guides is not None and guides.get(it.base_prefab) is None:
                continue
            # Don't store an auto-fit here: validation must not change the pack.
            p = art.placement or imaging.auto_fit(art.source.size, fit_box(kind, it, guides), "fill", art.rotation)
            if imaging.is_upscaled(p):
                what = f"Style {i}" if kind == gd.SHIELD else "The image"
                warn(f"{what} ({art.label or 'image'}) is enlarged {p.scale:.1f}x and may look soft.")

        sized_layers = {gd.BANNER: ("BumpMap.png",), gd.TABARD: ("BumpMap.png", "MetallicGlossMap.png")}
        main_name = "MainTex.png" if kind == gd.BANNER else "the patterns"
        for layer in sized_layers.get(kind, ()):
            if layer not in it.advanced or not it.art:
                continue
            try:
                bw, bh = imaging.open_rgba(it.advanced[layer]).size
                fw, fh = frame_size(kind, it, guides)
                if (bw, bh) != (fw, fh):
                    warn(f"{layer} is {bw}x{bh} but {main_name} will be {fw}x{fh}; they should match.")
            except Exception:
                err(f"{layer} can't be read as an image.")
    return issues


def _validate_tabard_styles(it: Item, guides: GuideStore | None, err, warn) -> None:
    for i, raw in enumerate(it.art, 1):
        st = as_style(raw)
        name = f"Style {i}"
        if st.sheet:
            if st.source.width != st.source.height:
                err(f"{name} ({st.label or 'pattern'}) is {st.source.width}x{st.source.height}; "
                    "ready-made patterns must be square.")
            elif st.source.width < it.texture_size:
                warn(f"{name} ({st.label or 'pattern'}) is {st.source.width}px and is enlarged to "
                     f"{it.texture_size}px, so it may look soft.")
        else:
            p = _peek_placement(gd.TABARD, it, st, guides)
            if imaging.is_upscaled(p):
                warn(f"{name} front ({st.label or 'image'}) is enlarged {p.scale:.1f}x and may look soft.")
            if st.back_mode == BACK_OWN:
                if st.back is None:
                    warn(f"{name} is set to its own back image but has none; the back will be plain.")
                else:
                    bp = _peek_placement(gd.TABARD, it, st.back, guides, "back")
                    if imaging.is_upscaled(bp):
                        warn(f"{name} back ({st.back.label or 'image'}) is enlarged {bp.scale:.1f}x "
                             "and may look soft.")
        if st.keep_holes:
            frac = imaging.hole_fraction(render_tabard_style(it, st, guides, bleed=False), tabard_guide(it, guides))
            if frac > 0.01:
                warn(f"{name}: {frac * 100:.0f}% of the tabard is transparent and will show as holes in game.")
        if st.icon is not None:
            try:
                iw, ih = imaging.open_rgba(st.icon).size
                if iw != ih:
                    warn(f"{name}: the custom icon is {iw}x{ih}; icons should be square (128x128).")
            except Exception:
                err(f"{name}: the custom icon can't be read as an image.")


# ---------------------------------------------------------------- export

def item_json(kind: str, it: Item) -> dict:
    reqs = []
    for r in it.requirements:
        d = {"item": r.item.strip(), "amount": int(r.amount)}  # always explicit: BannerShare defaults to 0
        if gd.has_per_level(kind) and r.amount_per_level > 0:
            d["amountPerLevel"] = int(r.amount_per_level)
        reqs.append(d)
    d = {
        "displayName": it.display_name.strip(),
        "description": it.description.strip(),
    }
    if gd.has_base_prefab(kind):
        d["basePrefab"] = it.base_prefab
    # Station: for shields and tabards "none" means no station; "" would mean the workbench.
    d["craftingStation"] = it.station
    if gd.has_min_level(kind):
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
            files[f"Pattern{n}.png"] = imaging.png_bytes(imaging.render_shield_pattern(art.source, p, guide, art.rotation))
    elif kind == gd.TABARD:
        guide = tabard_guide(it, guides)
        for n, raw in enumerate(it.art, 1):
            st = as_style(raw)
            if st is raw:
                ensure_style_placements(it, st, guides)
            else:
                ensure_placement(kind, it, st, guides)
            files[f"Pattern{n}.png"] = imaging.png_bytes(imaging.render_tabard_pattern(st, guide))
            if st.icon is not None:
                # Only custom icons: TabardShare makes the same front-panel icon itself otherwise.
                files[f"Icon{n}.png"] = st.icon
    else:
        art = it.art[0]
        p = ensure_placement(kind, it, art, guides)
        main = imaging.render_banner(art.source, p, frame_size(kind, it, guides), art.rotation)
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
        raise PackReadError("No shield.json, Banner.json or Tabard.json found - "
                            "this isn't a shield, banner or tabard pack.")
    if len(kinds) > 1:
        raise PackReadError("This zip mixes pack types (shields, banners, tabards). "
                            "Split it into one zip per type.")
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
        if kind == gd.TABARD:
            if not it.display_name.strip():
                it.display_name = str(_ci(raw, "name") or "")
            if not it.display_name.strip():
                # TabardShare's own default, so the form isn't blank
                it.display_name = folder.replace("_", " ").replace("-", " ")
        it.description = str(_ci(raw, "description") or "")
        if kind == gd.TABARD:
            it.base_prefab = gd.prefabs(kind)[0].id
        else:
            default_base = "ShieldWood" if kind == gd.SHIELD else "piece_banner01"
            base = str(_ci(raw, "basePrefab") or "").strip() or default_base
            known = gd.prefab(kind, base)
            it.base_prefab = known.id if known else base
        it.hidden = bool(_ci(raw, "hidden", False))
        it.station = gd.normalise_station(kind, _ci(raw, "craftingStation"))
        if gd.has_min_level(kind):
            it.min_station_level = max(1, _int(_ci(raw, "minStationLevel", 1), 1))
        default_amount = gd.default_requirement_amount(kind)
        for r in _ci(raw, "requirements") or []:
            if not isinstance(r, dict):
                continue
            item_name = str(_ci(r, "item") or "").strip()
            if not item_name:
                continue
            amount = _int(_ci(r, "amount", default_amount), default_amount)
            if kind == gd.TABARD:
                amount = max(1, amount)     # TabardShare raises anything under 1 to 1
            it.requirements.append(Requirement(
                item_name,
                amount,
                _int(_ci(r, "amountPerLevel", 0), 0) if gd.has_per_level(kind) else 0,
            ))
        if kind == gd.TABARD and not it.requirements:
            p = gd.prefab(kind, it.base_prefab)
            it.requirements = recipe_requirements(p.recipe)
            notes.append(f"{folder}: no requirements in {gd.JSON_FILE_NAME[kind]}, so TabardShare's default "
                         "(4 leather scraps) was filled in.")

        frame = gd.prefab(kind, it.base_prefab).size if gd.prefab(kind, it.base_prefab) else None
        if gd.has_styles(kind):
            numbered = []
            for n in files:
                m = PATTERN_RE.match(n)
                if m:
                    numbered.append((int(m.group(1)), n))
            numbered.sort()
            # Like ShieldShare: one file per style number (e.g. Pattern2.png and Pattern02.png),
            # the first one wins and the rest are ignored.
            by_number: dict[int, str] = {}
            for num, n in numbered:
                if num in by_number:
                    notes.append(f"{folder}: two pattern files for style {num} "
                                 f"('{by_number[num]}' and '{n}') - using '{by_number[num]}'.")
                else:
                    by_number[num] = n
            numbered = sorted(by_number.items())
            cap = _int(_ci(raw, "styleCount", 0), 0)
            limit = cap if 0 < cap < gd.MAX_STYLES else gd.MAX_STYLES
            if len(numbered) > limit:
                notes.append(f"{folder}: only the first {limit} patterns are used (the mod ignores the rest).")
                numbered = numbered[:limit]
            if kind == gd.TABARD:
                _read_tabard_styles(it, folder, files, numbered, notes)
            else:
                for _, n in numbered:
                    it.art.append(_art_from_pack(files[n], n, frame))
            if kind == gd.SHIELD and any(ICON_RE.match(n) for n in files):
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
            if low in handled or (gd.has_styles(kind) and (PATTERN_RE.match(n) or ICON_RE.match(n))):
                continue
            if kind == gd.BANNER and low in ("maintex.png", "icon.png"):
                continue
            notes.append(f"{folder}: '{n}' is not used by the mod and will be dropped on export.")
        pack.items.append(it)

    if not pack.items:
        raise PackReadError("No readable items in this zip.")
    return pack, notes


def _read_tabard_styles(it: Item, folder: str, files: dict[str, bytes], numbered: list[tuple[int, str]],
                        notes: list[str]) -> None:
    """Each PatternN.png comes back as a ready-made sheet (it can't be split back into the front
    and back images it was made from). IconN.png is kept with the pattern of the same number."""
    icons: dict[int, str] = {}
    for n in sorted(files, key=str.lower):
        m = ICON_RE.match(n)
        if m and int(m.group(1)) not in icons:
            icons[int(m.group(1))] = n
    used_icons = set()
    for i, (num, n) in enumerate(numbered):
        img = imaging.open_rgba(files[n])
        if i == 0:
            it.texture_size = img.width if (img.width == img.height and img.width in gd.TABARD_SIZES) else gd.TABARD_SIZES[0]
            if img.size != (it.texture_size, it.texture_size):
                notes.append(f"{folder}: {n} is {img.width}x{img.height}; the tabard is set to "
                             f"{it.texture_size}x{it.texture_size} and the patterns are resized to fit.")
        size = (it.texture_size, it.texture_size)
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        # Keep transparency as it was authored if the pattern actually uses it inside the panels.
        guide = default_tabard_layouts().get(it.texture_size)
        holes = imaging.hole_fraction(img, guide) > 0
        st = TabardStyle(img, n, imaging.full_frame_placement(img.size, size), sheet=True, keep_holes=holes)
        if num in icons:
            st.icon = files[icons[num]]
            used_icons.add(num)
        it.art.append(st)
    for num, n in icons.items():
        if num not in used_icons:
            notes.append(f"{folder}: '{n}' has no Pattern{num}.png and will be dropped on export.")


def _art_from_pack(data: bytes, label: str, frame: tuple[int, int] | None) -> Art:
    img = imaging.open_rgba(data)
    if frame:
        img = imaging.conform_to_frame(img, frame)
        placement = imaging.full_frame_placement(img.size, frame)
    else:
        placement = None
    return Art(img, label, placement)


# ---------------------------------------------------------------- tabard previews

PREVIEW_MAX = 1024     # tabard previews are rendered at most this big; the screen shows less


def tabard_preview(item: Item, style: TabardStyle, guides: GuideStore | None, size: int | None = None,
                   omit: str | None = None):
    """A quick render of one style at ``size`` (default: the texture size, capped at PREVIEW_MAX),
    without edge bleed. Holes are shown as the game shows them. Doesn't change the item."""
    from .model import rescale_placements
    full = item.texture_size
    size = min(size or full, full, PREVIEW_MAX)
    st = style.clone()
    if st.placement is None:
        st.placement = _peek_placement(gd.TABARD, item, st, guides)
    if st.back is not None and st.back.placement is None:
        st.back.placement = _peek_placement(gd.TABARD, item, st.back, guides, "back")
    if size != full:
        tmp = Item(art=[st])
        rescale_placements(tmp, size / full)
    img = imaging.render_tabard_pattern(st, tabard_layouts(guides).get(size), bleed=False, omit=omit)
    return imaging.holes_preview(img) if st.keep_holes or st.sheet else img


def tabard_icon(item: Item, style: TabardStyle, guides: GuideStore | None):
    """The icon the crafting menu will show: the custom one, or the front panel like TabardShare makes."""
    if style.icon is not None:
        try:
            return imaging.make_icon(imaging.open_rgba(style.icon), imaging.TABARD_ICON_SIZE)
        except Exception:
            pass
    return imaging.make_tabard_icon(tabard_preview(item, style, guides, size=256))
