"""Image pipeline: face masks from ShieldShare's pattern guides, fitting, cropping,
edge bleed and icon generation."""
from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from . import gamedata as gd
from .model import Placement

# Pattern guide colours (ShieldShare.WritePatternGuide): face 205 grey, metal stripes 70/95,
# outline 40, crosshair 160. Anything opaque and at least this light counts as face.
FACE_LUMA_THRESHOLD = 180
MASK_CLOSE_SIZE = 5      # 5x5 close = 2 px: bridges the crosshair and anti-aliasing seams
EDGE_BLEED_PX = 8        # colour extended past the face outline so no wood shows at the rim
BANNER_ICON_SIZE = 128


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


def default_templates_dir() -> Path:
    return documents_dir() / gd.DROP_FOLDER_NAME[gd.SHIELD] / "_Templates"


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


# ---------------------------------------------------------------- placement

def auto_fit(src_size: tuple[int, int], box: tuple[int, int, int, int], mode: str = "fill") -> Placement:
    """Centre the source on ``box`` (l, t, r, b). ``fill`` covers the box, ``fit`` fits inside it."""
    sw, sh = src_size
    l, t, r, b = box
    bw, bh = r - l, b - t
    sx, sy = bw / sw, bh / sh
    s = max(sx, sy) if mode == "fill" else min(sx, sy)
    return Placement(s, l + (bw - sw * s) / 2, t + (bh - sh * s) / 2)


def render_placed(src: Image.Image, p: Placement, size: tuple[int, int]) -> Image.Image:
    """Draw ``src`` at placement ``p`` onto a transparent canvas of ``size``.

    Crops the source to the visible part before resizing, so large zooms of large
    images don't allocate huge intermediate images.
    """
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
    filtering at the rim samples the design rather than whatever lies outside the face."""
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    region = np.asarray(mask) > 0
    H, W = region.shape
    for _ in range(px):
        pad_a = np.pad(a, ((1, 1), (1, 1), (0, 0)))
        pad_r = np.pad(region, 1)
        acc = np.zeros_like(a)
        cnt = np.zeros((H, W), np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                r = pad_r[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
                acc += pad_a[1 + dy:1 + dy + H, 1 + dx:1 + dx + W] * r[..., None]
                cnt += r
        grow = (~region) & (cnt > 0)
        if not grow.any():
            break
        a[grow] = acc[grow] / cnt[grow][:, None]
        region = region | grow
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "RGBA")


def render_shield_pattern(src: Image.Image, p: Placement, guide: FaceGuide) -> Image.Image:
    """PatternN.png: the art placed in the guide's frame, transparency kept, edge colours bled."""
    placed = render_placed(src, p, guide.size)
    return edge_bleed(placed, guide.mask)


def render_banner(src: Image.Image, p: Placement, size: tuple[int, int]) -> Image.Image:
    return render_placed(src, p, size)


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
