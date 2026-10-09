"""Image pipeline: face masks from ShieldShare's pattern guides, TabardShare's UV layout,
fitting, cropping, edge bleed and icon generation."""
from __future__ import annotations

import io
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import gamedata as gd
from .model import BACK_COLOUR, BACK_OWN, BACK_SAME, Art, Placement, TabardStyle

# Pattern guide colours (ShieldShare.WritePatternGuide): face 205 grey, metal stripes 70/95,
# outline 40, crosshair 160. Anything opaque and at least this light counts as face.
FACE_LUMA_THRESHOLD = 180
MASK_CLOSE_SIZE = 5      # 5x5 close = 2 px: bridges the crosshair and anti-aliasing seams
EDGE_BLEED_PX = 8        # colour extended past the face outline so no wood shows at the rim
BANNER_ICON_SIZE = 128
FILL_MARGIN_PX = 3       # soft-edge allowance for rotated fills, in both source and output px


# ---------------------------------------------------------------- locations

def documents_dir() -> Path:
    """The folder .NET calls MyDocuments (so OneDrive-redirected Documents work on Windows)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
                return Path(buf.value)
        except Exception:
            pass
    return Path.home() / "Documents"


def default_templates_dir(kind: str = gd.SHIELD) -> Path:
    """Where the mod writes its _Templates folder (pattern guides, UV layouts)."""
    return documents_dir() / gd.DROP_FOLDER_NAME[kind] / "_Templates"


def guide_path(templates_dir: Path, prefab_id: str) -> Path:
    return Path(templates_dir) / f"{prefab_id} - pattern guide.png"


# ---------------------------------------------------------------- basics

def open_rgba(data: bytes | str | Path) -> Image.Image:
    if isinstance(data, (bytes, bytearray)):
        img = Image.open(io.BytesIO(data))
    else:
        img = Image.open(data)
    img.load()
    return img.convert("RGBA")


def trim_transparent(img: Image.Image) -> Image.Image:
    """Crop away fully transparent margins; return the image unchanged if there are none."""
    bbox = img.getchannel("A").getbbox()
    if bbox is None or bbox == (0, 0, img.width, img.height):
        return img
    return img.crop(bbox)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- shield guides

@dataclass
class FaceGuide:
    """A ShieldShare pattern guide and the paintable-face mask taken from it."""
    prefab_id: str
    image: Image.Image       # the guide itself, RGBA
    mask: Image.Image        # "L", 255 = paintable face
    face_box: tuple[int, int, int, int]  # bounding box of the face mask

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @classmethod
    def load(cls, path: str | Path, prefab_id: str) -> "FaceGuide":
        img = open_rgba(path)
        mask = face_mask(img)
        box = mask.getbbox()
        if box is None:
            raise ValueError(f"No paintable face found in '{Path(path).name}'.")
        return cls(prefab_id, img, mask, box)

    def preview_overlay(self) -> Image.Image:
        """Everything that ISN'T face, for drawing over the art in the preview:
        metal and outline as the guide shows them, outside the shield darkened."""
        g = np.asarray(self.image).copy()
        m = np.asarray(self.mask) > 0
        a = g[..., 3] > 0
        out = np.zeros_like(g)
        metal = a & ~m
        out[metal] = g[metal]
        out[metal, 3] = 235
        outside = ~a
        out[outside] = (24, 24, 28, 215)
        return Image.fromarray(out, "RGBA")


def face_mask(guide: Image.Image) -> Image.Image:
    """Opaque AND light, then a small morphological close. Keeps small face islands."""
    rgba = guide.convert("RGBA")
    alpha = np.asarray(rgba.getchannel("A"))
    luma = np.asarray(rgba.convert("L"))
    m = ((alpha > 127) & (luma >= FACE_LUMA_THRESHOLD)).astype(np.uint8) * 255
    mask = Image.fromarray(m, "L")
    mask = mask.filter(ImageFilter.MaxFilter(MASK_CLOSE_SIZE)).filter(ImageFilter.MinFilter(MASK_CLOSE_SIZE))
    # The close must not grow the mask past the shield's own silhouette.
    sil = Image.fromarray(((alpha > 127).astype(np.uint8) * 255), "L")
    return Image.fromarray(np.minimum(np.asarray(mask), np.asarray(sil)), "L")


# ---------------------------------------------------------------- rotation

def normalise_angle(deg: float) -> float:
    """Any angle as the equivalent in (-180, 180]."""
    deg = math.fmod(deg, 360.0)
    if deg <= -180.0:
        deg += 360.0
    elif deg > 180.0:
        deg -= 360.0
    return 0.0 if abs(deg) < 1e-9 else deg


def _quarter_turns(deg: float) -> int | None:
    """0-3 clockwise quarter turns if ``deg`` is a multiple of 90, else None."""
    q = deg / 90.0
    if abs(q - round(q)) < 1e-9:
        return int(round(q)) % 4
    return None


def rotated_size(w: float, h: float, deg: float) -> tuple[float, float]:
    """Bounding box of a w x h rectangle rotated by ``deg``. Exact for quarter turns."""
    q = _quarter_turns(deg)
    if q is not None:
        return (h, w) if q % 2 else (w, h)
    c, s = abs(math.cos(math.radians(deg))), abs(math.sin(math.radians(deg)))
    return (w * c + h * s, w * s + h * c)


def rotate_image(img: Image.Image, deg: float) -> Image.Image:
    """Rotate clockwise by ``deg``, expanding the canvas. Quarter turns are lossless; other
    angles are resampled with premultiplied alpha so transparent edges don't go dark."""
    q = _quarter_turns(deg)
    if q is not None:
        return img if q == 0 else img.transpose(
            {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}[q])
    return img.convert("RGBa").rotate(-deg, resample=Image.BICUBIC, expand=True).convert("RGBA")


def with_rotation(p: Placement, src_size: tuple[int, int], old_deg: float, new_deg: float) -> Placement:
    """The same placement after the art's rotation changes: scale kept, centre kept."""
    ow, oh = rotated_size(*src_size, old_deg)
    nw, nh = rotated_size(*src_size, new_deg)
    cx, cy = p.ox + ow * p.scale / 2, p.oy + oh * p.scale / 2
    return Placement(p.scale, cx - nw * p.scale / 2, cy - nh * p.scale / 2)


# ---------------------------------------------------------------- placement

def auto_fit(src_size: tuple[int, int], box: tuple[int, int, int, int], mode: str = "fill",
             rotation: float = 0.0) -> Placement:
    """Centre the (rotated) source on ``box`` (l, t, r, b).

    ``fill``: the rotated image covers the whole box, so no gaps even at an angle.
    ``fit``: the rotated image's bounding box fits inside the box.
    """
    sw, sh = src_size
    l, t, r, b = box
    bw, bh = r - l, b - t
    rw, rh = rotated_size(sw, sh, rotation)
    if mode == "fill" and _quarter_turns(rotation) is None:
        c, s_ = abs(math.cos(math.radians(rotation))), abs(math.sin(math.radians(rotation)))
        # The box, seen in the image's own (unrotated) axes, must fit inside the image. A rotated
        # edge is soft - a few source pixels from the rotation, and a few output pixels from
        # resizing - so leave both margins, or the box corners come out half-transparent.
        m = FILL_MARGIN_PX
        ew, eh = bw + 2 * m, bh + 2 * m
        uw, uh = max(sw - 2 * m, 1), max(sh - 2 * m, 1)
        s = max((ew * c + eh * s_) / uw, (ew * s_ + eh * c) / uh)
    elif mode == "fill":
        s = max(bw / rw, bh / rh)
    else:
        s = min(bw / rw, bh / rh)
    return Placement(s, l + (bw - rw * s) / 2, t + (bh - rh * s) / 2)


def render_placed(src: Image.Image, p: Placement, size: tuple[int, int], rotation: float = 0.0) -> Image.Image:
    """Draw ``src``, rotated by ``rotation``, at placement ``p`` onto a transparent canvas of ``size``.

    Crops the source to the visible part before resizing, so large zooms of large
    images don't allocate huge intermediate images.
    """
    if normalise_angle(rotation):
        # Rotate first, then place the result by its centre: Pillow's expanded canvas can be a
        # pixel off the exact bounding box, and the centre is what the preview draws around.
        rw, rh = rotated_size(*src.size, rotation)
        cx, cy = p.ox + rw * p.scale / 2, p.oy + rh * p.scale / 2
        src = rotate_image(src, rotation)
        p = Placement(p.scale, cx - src.width * p.scale / 2, cy - src.height * p.scale / 2)
    W, H = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    s = p.scale
    if s <= 0:
        return canvas
    # visible part of the source, in source pixels (1 px margin for resampling)
    x0 = max(0, int(np.floor(-p.ox / s)) - 1)
    y0 = max(0, int(np.floor(-p.oy / s)) - 1)
    x1 = min(src.width, int(np.ceil((W - p.ox) / s)) + 1)
    y1 = min(src.height, int(np.ceil((H - p.oy) / s)) + 1)
    if x1 <= x0 or y1 <= y0:
        return canvas
    crop = src.crop((x0, y0, x1, y1))
    # exact output rectangle of that crop
    fx0, fy0 = p.ox + x0 * s, p.oy + y0 * s
    tw = max(1, round((x1 - x0) * s))
    th = max(1, round((y1 - y0) * s))
    scaled = crop.resize((tw, th), Image.LANCZOS)
    canvas.paste(scaled, (round(fx0), round(fy0)))
    return canvas


def edge_bleed(img: Image.Image, mask: Image.Image, px: int = EDGE_BLEED_PX) -> Image.Image:
    """Copy the colours (and alpha) at the face edge outward by ``px`` pixels, so texture
    filtering at the rim samples the design rather than whatever lies outside the face.

    Each pass gives every pixel just outside the region the average of its in-region
    8-neighbours, then adds it to the region. Only the pixels on the edge are touched,
    so the cost follows the outline's length, not the image's area.
    """
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    region = np.asarray(mask) > 0
    H, W = region.shape
    a_pad = np.zeros((H + 2, W + 2, 4), np.float32)
    a_pad[1:-1, 1:-1] = a
    r_pad = np.zeros((H + 2, W + 2), bool)
    r_pad[1:-1, 1:-1] = region
    offsets = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    for _ in range(px):
        # candidates: outside the region with at least one neighbour inside it
        near = np.zeros_like(r_pad)
        for dy, dx in offsets:
            near[1:-1, 1:-1] |= r_pad[1 + dy:H + 1 + dy, 1 + dx:W + 1 + dx]
        ys, xs = np.nonzero(near & ~r_pad)
        if ys.size == 0:
            break
        acc = np.zeros((ys.size, 4), np.float32)
        cnt = np.zeros(ys.size, np.float32)
        for dy, dx in offsets:
            r = r_pad[ys + dy, xs + dx]
            acc += a_pad[ys + dy, xs + dx] * r[:, None]
            cnt += r
        a_pad[ys, xs] = acc / cnt[:, None]
        r_pad[ys, xs] = True
    return Image.fromarray(np.clip(a_pad[1:-1, 1:-1] + 0.5, 0, 255).astype(np.uint8), "RGBA")


def render_shield_pattern(src: Image.Image, p: Placement, guide: FaceGuide, rotation: float = 0.0) -> Image.Image:
    """PatternN.png: the art placed in the guide's frame, transparency kept, edge colours bled."""
    placed = render_placed(src, p, guide.size, rotation)
    return edge_bleed(placed, guide.mask)


def render_banner(src: Image.Image, p: Placement, size: tuple[int, int], rotation: float = 0.0) -> Image.Image:
    return render_placed(src, p, size, rotation)


def make_icon(img: Image.Image, size: int = BANNER_ICON_SIZE) -> Image.Image:
    """Square icon: the whole image fitted inside, centred on transparency."""
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s = min(size / img.width, size / img.height)
    w, h = max(1, round(img.width * s)), max(1, round(img.height * s))
    icon.paste(img.resize((w, h), Image.LANCZOS), ((size - w) // 2, (size - h) // 2))
    return icon


def full_frame_placement(src_size: tuple[int, int], frame: tuple[int, int]) -> Placement:
    """Placement that maps the source exactly onto the frame (same aspect assumed)."""
    return Placement(frame[0] / src_size[0], 0.0, 0.0)


def conform_to_frame(img: Image.Image, frame: tuple[int, int], tolerance: float = 0.005) -> Image.Image:
    """For images read back from a pack: the mods stretch the image over the whole frame,
    so if the aspect ratio differs, stretch it the same way to keep what the player sees."""
    src_ar = img.width / img.height
    frame_ar = frame[0] / frame[1]
    if abs(src_ar / frame_ar - 1) <= tolerance:
        return img
    return img.resize(frame, Image.LANCZOS)


def is_upscaled(p: Placement) -> bool:
    return p.scale > 1.001


# ---------------------------------------------------------------- tabards

TABARD_LAYOUT_NAME = "Tabard - UV layout.png"   # TabardShare writes it to its _Templates folder
TABARD_REFERENCE_SIZE = 1024
TABARD_BLEED_PX = 4         # at 1024; the gutter between the panels is only 10 px
TABARD_ICON_SIZE = 128
HOLE_ALPHA = 128            # TabardShare's cut-out shader: alpha under 50% is a hole
# The guide says front = left half, back = right half, neither mirrored. If an in-game check
# shows the back panel's UVs ARE mirrored, set this and the back art is flipped on render.
BACK_MIRRORED = False
PANELS = ("front", "back")


@dataclass
class TabardGuide:
    """TabardShare's UV layout at one texture size: the panel mask and the two panel boxes."""
    image: Image.Image                      # the layout, RGBA, at ``size``
    mask: Image.Image                       # "L", 255 = inside a panel (layout alpha > 0)
    islands: dict[str, tuple[int, int, int, int]]   # "front"/"back" -> (l, t, r, b), r/b exclusive
    origin: str = "bundled"                 # "bundled" or "installed" (the mod's own copy)

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @classmethod
    def from_image(cls, img: Image.Image, origin: str = "bundled") -> "TabardGuide":
        img = img.convert("RGBA")
        if img.width != img.height:
            raise ValueError(f"the UV layout is {img.width}x{img.height}, not square")
        mask = Image.fromarray(((np.asarray(img.getchannel("A")) > 0).astype(np.uint8) * 255), "L")
        return cls(img, mask, find_islands(mask), origin)

    @classmethod
    def load(cls, path: str | Path, origin: str = "bundled") -> "TabardGuide":
        return cls.from_image(open_rgba(path), origin)

    def scaled(self, size: int) -> "TabardGuide":
        if self.size == (size, size):
            return self
        img = self.image.resize((size, size), Image.NEAREST)
        mask = self.mask.resize((size, size), Image.NEAREST)
        return TabardGuide(img, mask, find_islands(mask), self.origin)

    def panel_mask(self, panel: str) -> Image.Image:
        """The mask limited to one panel's box."""
        l, t, r, b = self.islands[panel]
        m = np.zeros(self.mask.size[::-1], np.uint8)
        m[t:b, l:r] = np.asarray(self.mask)[t:b, l:r]
        return Image.fromarray(m, "L")

    def wireframe(self) -> np.ndarray:
        """True where the layout draws its (opaque, dark) mesh lines."""
        a = np.asarray(self.image)
        return (a[..., 3] > 200) & (a[..., :3].max(axis=2) < 110)

    def preview_overlay(self, active: str | None = None, show_grid: bool = False) -> Image.Image:
        """Drawn over the preview: outside the panels darkened, the panel not being edited
        dimmed, and optionally the mesh lines so seams and the belt line show."""
        m = np.asarray(self.mask) > 0
        W, H = self.size
        out = np.zeros((H, W, 4), np.uint8)
        out[~m] = (24, 24, 28, 215)
        if active in self.islands:
            other = "back" if active == "front" else "front"
            l, t, r, b = self.islands[other]
            sub = np.zeros_like(m)
            sub[t:b, l:r] = m[t:b, l:r]
            out[sub] = (24, 24, 28, 120)
        if show_grid:
            out[self.wireframe()] = (20, 20, 20, 150)
        return Image.fromarray(out, "RGBA")


def find_islands(mask: Image.Image) -> dict[str, tuple[int, int, int, int]]:
    """The front (left) and back (right) panels: the two runs of non-empty columns."""
    m = np.asarray(mask) > 0
    cols = m.any(axis=0)
    runs, start = [], None
    for x, on in enumerate(list(cols) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append((start, x))
            start = None
    if len(runs) != 2:
        raise ValueError(f"expected two panels side by side in the UV layout, found {len(runs)}")
    out = {}
    for name, (l, r) in zip(PANELS, runs):
        rows = np.where(m[:, l:r].any(axis=1))[0]
        out[name] = (l, int(rows[0]), r, int(rows[-1]) + 1)
    return out


def tabard_bleed_px(size: int) -> int:
    return max(1, round(TABARD_BLEED_PX * size / TABARD_REFERENCE_SIZE))


def back_same_placement(front: Placement, guide: TabardGuide) -> Placement:
    """The front placement moved onto the back panel: same size, same spot relative to the panel."""
    fl, ft, fr, fb = guide.islands["front"]
    bl, bt, br, bb = guide.islands["back"]
    dx = (bl + br) / 2 - (fl + fr) / 2
    dy = (bt + bb) / 2 - (ft + fb) / 2
    return Placement(front.scale, front.ox + dx, front.oy + dy)


def _flip_box(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    part = img.crop(box).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    out = img.copy()
    out.paste(part, box[:2])
    return out


def _clip(img: Image.Image, mask: Image.Image) -> Image.Image:
    a = np.asarray(img).copy()
    a[np.asarray(mask) == 0] = 0
    return Image.fromarray(a, "RGBA")


def _flatten(layer: Image.Image, mask: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """The layer composited over a solid colour inside ``mask``: opaque there, empty elsewhere."""
    base = Image.new("RGBA", layer.size, (*colour, 255))
    flat = Image.alpha_composite(base, layer)
    return _clip(flat, mask)


def render_tabard_panel(style: TabardStyle, guide: TabardGuide, panel: str, omit_art: bool = False) -> Image.Image:
    """One panel of a style's pattern, clipped to that panel, before edge bleed."""
    size = guide.size
    pmask = guide.panel_mask(panel)
    art: Art | None = None
    placement: Placement | None = None
    if panel == "front":
        art, placement = style, style.placement
    elif style.back_mode == BACK_SAME:
        art = style
        placement = back_same_placement(style.placement, guide) if style.placement else None
    elif style.back_mode == BACK_OWN and style.back is not None:
        art, placement = style.back, style.back.placement

    if omit_art or art is None or placement is None:
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
    else:
        layer = render_placed(art.source, placement, size, art.rotation)
        if panel == "back" and BACK_MIRRORED:
            layer = _flip_box(layer, guide.islands["back"])
        layer = _clip(layer, pmask)

    plain_back = panel == "back" and (style.back_mode == BACK_COLOUR or
                                      (style.back_mode == BACK_OWN and style.back is None))
    if plain_back or not style.keep_holes:
        return _flatten(layer, pmask, style.background)
    return layer


def render_tabard_pattern(style: TabardStyle, guide: TabardGuide, bleed: bool = True,
                          omit: str | None = None) -> Image.Image:
    """PatternN.png. Front art on the left panel, back on the right, each clipped to its own
    panel and edge-bled separately (so the colours never meet in the narrow gutter).
    ``omit`` leaves one panel's art out (the preview draws that one live)."""
    size = guide.size
    if style.sheet:
        img = style.source if style.source.size == size else style.source.resize(size, Image.LANCZOS)
        if not style.keep_holes:
            # flatten inside the panels only; anything painted outside them is kept as it is
            flat = _flatten(img, guide.mask, style.background)
            inside = np.asarray(guide.mask) > 0
            a = np.asarray(img).copy()
            a[inside] = np.asarray(flat)[inside]
            img = Image.fromarray(a, "RGBA")
        return img
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    px = tabard_bleed_px(size[0])
    for panel in PANELS:
        layer = render_tabard_panel(style, guide, panel, omit_art=omit == panel)
        if bleed:
            layer = edge_bleed(layer, guide.panel_mask(panel), px)
        out = Image.alpha_composite(out, layer)
    return out


def holes_preview(img: Image.Image) -> Image.Image:
    """What the game draws: alpha under 50% is a hole, the rest is solid."""
    a = np.asarray(img.convert("RGBA")).copy()
    a[..., 3] = np.where(a[..., 3] >= HOLE_ALPHA, 255, 0)
    return Image.fromarray(a, "RGBA")


def hole_fraction(pattern: Image.Image, guide: TabardGuide) -> float:
    """Share of panel pixels that will be holes in game."""
    inside = np.asarray(guide.mask) > 0
    if not inside.any():
        return 0.0
    alpha = np.asarray(pattern.convert("RGBA").getchannel("A"))
    return float((alpha[inside] < HOLE_ALPHA).mean())


def make_tabard_icon(pattern: Image.Image, size: int = TABARD_ICON_SIZE) -> Image.Image:
    """The icon TabardShare makes when there's no IconN.png: the front panel (left half),
    fitted at 96%, centred, with the same 50% alpha cut as in game."""
    half = pattern.crop((0, 0, pattern.width // 2, pattern.height))
    s = min(size / half.width, size / half.height) * 0.96
    w, h = max(1, round(half.width * s)), max(1, round(half.height * s))
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(half.resize((w, h), Image.LANCZOS), ((size - w) // 2, (size - h) // 2))
    return holes_preview(icon)
