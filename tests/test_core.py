"""Core tests (no GUI). Run:  python -m pytest tests   or   python tests/test_core.py"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packbuilder import gamedata as gd, imaging, packio  # noqa: E402
from packbuilder.guides import GuideStore  # noqa: E402
from packbuilder.model import Art, Pack, Placement, Requirement, id_from_name, id_problem  # noqa: E402


def make_guide(w: int, h: int, metal_band: int = 0) -> Image.Image:
    """Imitates ShieldShare's pattern guide: grey face, optional striped metal rim,
    dark 2px outline and a grey crosshair."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, w - 5, h - 5), fill=(205, 205, 205, 255))
    px = np.asarray(img).copy()
    a = px[..., 3] > 0
    if metal_band:
        inner = Image.new("L", (w, h), 0)
        ImageDraw.Draw(inner).ellipse((4 + metal_band, 4 + metal_band, w - 5 - metal_band, h - 5 - metal_band), fill=255)
        inner = np.asarray(inner) > 0
        yy, xx = np.mgrid[0:h, 0:w]
        stripes = np.where(((xx + yy) // 8) % 2 == 0, 70, 95)
        metal = a & ~inner
        for c in range(3):
            px[..., c][metal] = stripes[metal]
    # outline: pixels within 2px of transparency
    from PIL import ImageFilter
    alpha = Image.fromarray((a * 255).astype(np.uint8))
    eroded = np.asarray(alpha.filter(ImageFilter.MinFilter(5))) > 0
    edge = a & ~eroded
    px[edge] = (40, 40, 40, 255)
    body = a & ~edge
    px[:, w // 2][body[:, w // 2]] = (160, 160, 160, 255)
    px[h // 2, :][body[h // 2, :]] = (160, 160, 160, 255)
    return Image.fromarray(px, "RGBA")


def templates(tmp: Path) -> Path:
    t = tmp / "_Templates"
    t.mkdir()
    make_guide(512, 512).save(t / "ShieldWood - pattern guide.png")
    make_guide(512, 488, metal_band=60).save(t / "ShieldBlackmetal - pattern guide.png")
    return t


def art(w=300, h=300, color=(200, 30, 30, 255)) -> Image.Image:
    img = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((20, 20, 20 + w - 1, 20 + h - 1), fill=color)
    return img


def test_ids():
    assert id_from_name("Black Dog!") == "BlackDog"
    assert id_from_name("  _x_ ") == "x"
    assert id_problem("BlackDog") is None
    assert id_problem("Missing")
    assert id_problem("missing_ShieldWood")
    assert id_problem("_x")
    assert id_problem("a b")
    assert id_problem("")


def test_mask_bridges_crosshair_and_keeps_metal_out():
    g = make_guide(512, 512)
    m = np.asarray(imaging.face_mask(g)) > 0
    assert m[256, 256] and m[256, 100] and m[100, 256]   # crosshair bridged
    assert not m[0, 0]
    bm = make_guide(512, 488, metal_band=60)
    mm = np.asarray(imaging.face_mask(bm)) > 0
    assert not mm[244, 30]    # metal band
    assert mm[244, 256]       # face centre
    box = Image.fromarray(mm.astype(np.uint8) * 255).getbbox()
    assert box[0] > 50 and box[2] < 460   # fit box is the face, not the whole template


def test_trim_and_fit():
    src = imaging.trim_transparent(art(300, 200))
    assert src.size == (300, 200)
    p = imaging.auto_fit(src.size, (10, 10, 110, 110), "fill")
    assert abs(p.scale - 0.5) < 1e-9 and abs(p.ox - (10 + (100 - 150) / 2)) < 1e-9
    p = imaging.auto_fit(src.size, (10, 10, 110, 110), "fit")
    assert abs(p.scale - 1 / 3) < 1e-9


def test_render_negative_offset_and_bleed():
    src = Image.new("RGBA", (100, 100), (10, 200, 10, 255))
    out = imaging.render_placed(src, Placement(2.0, -50, -50), (120, 120))
    a = np.asarray(out)
    assert a[0, 0, 3] == 255 and a[119, 119, 3] == 255 and tuple(a[60, 60, :3]) == (10, 200, 10)
    # bleed: mask is a centre square; colour must spread outward, transparency kept inside
    img = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 10, 29, 29), fill=(255, 0, 0, 255))
    img.putpixel((15, 15), (0, 0, 0, 0))   # user's own transparency inside the face
    mask = Image.new("L", (40, 40), 0)
    ImageDraw.Draw(mask).rectangle((10, 10, 29, 29), fill=255)
    b = np.asarray(imaging.edge_bleed(img, mask, 4))
    assert tuple(b[10, 6]) == (255, 0, 0, 255)       # bled 4 px out
    assert b[10, 5, 3] == 0                          # but no further
    assert b[15, 15, 3] == 0                         # inner transparency untouched


def build_pack(tmp: Path, kind: str) -> tuple[Pack, GuideStore]:
    guides = GuideStore(templates(tmp))
    pack = Pack(kind)
    it = pack.new_item()
    it.display_name, it.id = "Black Dog", "HB_BlackDog"
    it.requirements = [Requirement("LeatherScraps", 4, 2), Requirement("Wood", 10, 0)]
    if kind == gd.SHIELD:
        it.art = [Art(imaging.trim_transparent(art(700, 700)), "a.png"), Art(art(600, 900), "b.png")]
        it.station = "none"
        it.advanced["BumpMap.png"] = imaging.png_bytes(Image.new("RGBA", (64, 64)))
    else:
        it.art = [Art(art(800, 1600), "banner.png")]
        it.station = ""
    return pack, guides


def test_export_and_reopen_shield(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_pack(tmp, gd.SHIELD)
    out = tmp / "MyShields.zip"
    issues = packio.export_pack(pack, str(out), guides)
    assert not any(i.error for i in issues), issues
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
        assert names == ["HB_BlackDog/BumpMap.png", "HB_BlackDog/Pattern1.png", "HB_BlackDog/Pattern2.png",
                         "HB_BlackDog/shield.json"], names
        j = json.loads(z.read("HB_BlackDog/shield.json"))
        assert j["craftingStation"] == "none"
        assert j["requirements"] == [{"item": "LeatherScraps", "amount": 4, "amountPerLevel": 2},
                                     {"item": "Wood", "amount": 10}]
        assert "styleCount" not in j
        p1 = Image.open(io.BytesIO(z.read("HB_BlackDog/Pattern1.png")))
        assert p1.size == (512, 512) and p1.mode == "RGBA"
    pack2, notes = packio.open_pack(str(out))
    assert pack2.kind == gd.SHIELD and not notes
    it = pack2.items[0]
    assert it.id == "HB_BlackDog" and it.station == "none" and len(it.art) == 2
    assert it.requirements[0].amount_per_level == 2 and "BumpMap.png" in it.advanced
    # round trip re-export is stable
    out2 = tmp / "again.zip"
    assert not any(i.error for i in packio.export_pack(pack2, str(out2), guides))


def test_export_banner(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_pack(tmp, gd.BANNER)
    out = tmp / "MyBanners.zip"
    issues = packio.export_pack(pack, str(out), guides)
    assert not any(i.error for i in issues), issues
    with zipfile.ZipFile(out) as z:
        j = json.loads(z.read("HB_BlackDog/Banner.json"))
        assert j["craftingStation"] == "" and "minStationLevel" not in j
        assert j["requirements"][0] == {"item": "LeatherScraps", "amount": 4}  # no amountPerLevel for banners
        assert Image.open(io.BytesIO(z.read("HB_BlackDog/MainTex.png"))).size == (400, 1000)
        assert Image.open(io.BytesIO(z.read("HB_BlackDog/Icon.png"))).size == (128, 128)


def test_validation_blocks(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_pack(tmp, gd.SHIELD)
    dup = pack.new_item()
    dup.id, dup.display_name = "hb_blackdog", ""
    dup.requirements = []
    dup.base_prefab = "ShieldIronTower"     # no guide in the test templates
    msgs = [str(i) for i in packio.validate(pack, guides) if i.error]
    joined = "\n".join(msgs)
    assert "more than one item" in joined and "Display name" in joined
    assert "at least one requirement" in joined and "No pattern guide for ShieldIronTower" in joined
    assert "1 to 16 styles" in joined


def test_open_rejects_mixed(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    p = tmp / "mixed.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("A/shield.json", "{}")
        z.writestr("B/Banner.json", "{}")
    try:
        packio.open_pack(str(p))
    except packio.PackReadError as ex:
        assert "mixes" in str(ex)
    else:
        raise AssertionError("mixed zip accepted")


def test_open_hand_made_shield(tmp_path: Path | None = None):
    """A pack written by hand with aliases, odd capitals and a wrong-aspect pattern."""
    tmp = Path(tmp_path or tempfile.mkdtemp())
    p = tmp / "hand.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Hawk/SHIELD.JSON", json.dumps({"DisplayName": "Hawk", "craftingstation": "Galdr Table",
                                                   "requirements": [{"Item": "Wood"}]}))
        z.writestr("Hawk/pattern2.png", imaging.png_bytes(Image.new("RGBA", (256, 128), (1, 2, 3, 255))))
        z.writestr("Hawk/Icon2.png", imaging.png_bytes(Image.new("RGBA", (8, 8))))
    pack, notes = packio.open_pack(str(p))
    it = pack.items[0]
    assert it.station == "piece_magetable" and it.base_prefab == "ShieldWood"
    assert it.requirements[0].amount == 1            # ShieldShare's default
    assert it.art[0].source.size == (512, 512)       # stretched to the frame like the mod does
    assert any("IconN" in n for n in notes)



def test_validate_does_not_change_pack(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_pack(tmp, gd.SHIELD)
    assert all(a.placement is None for a in pack.items[0].art)
    packio.validate(pack, guides)
    assert all(a.placement is None for a in pack.items[0].art)


def test_open_duplicate_style_numbers(tmp_path: Path | None = None):
    """Pattern2.png and Pattern02.png are the same style: keep one, like ShieldShare."""
    tmp = Path(tmp_path or tempfile.mkdtemp())
    p = tmp / "dupes.zip"
    png = imaging.png_bytes(Image.new("RGBA", (512, 512), (1, 2, 3, 255)))
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Hawk/shield.json", json.dumps({"displayName": "Hawk", "requirements": [{"item": "Wood", "amount": 1}]}))
        z.writestr("Hawk/Pattern1.png", png)
        z.writestr("Hawk/Pattern02.png", png)
        z.writestr("Hawk/Pattern2.png", png)
    pack, notes = packio.open_pack(str(p))
    assert len(pack.items[0].art) == 2
    assert any("two pattern files for style 2" in n for n in notes)

def test_prefab_recipes():
    from packbuilder.model import apply_recipe, matches_recipe
    # every prefab has a recipe, and every recipe item is in the picker list
    ids = {i.id for i in gd.items()}
    for kind in gd.KINDS:
        for p in gd.prefabs(kind):
            assert p.recipe and p.recipe.requirements, p.id
            assert all(r.item in ids for r in p.recipe.requirements), p.id
    pack = Pack(gd.SHIELD)
    it = pack.new_item()
    assert matches_recipe(gd.SHIELD, it, "ShieldWood")
    assert apply_recipe(gd.SHIELD, it, "ShieldFlametal")
    assert it.station == "blackforge" and it.min_station_level == 3
    assert [(r.item, r.amount, r.amount_per_level) for r in it.requirements] == \
        [("Blackwood", 10, 10), ("FlametalNew", 8, 4), ("AskHide", 2, 2)]
    it.requirements[0].amount = 11
    assert not matches_recipe(gd.SHIELD, it, "ShieldFlametal")
    b = Pack(gd.BANNER).new_item()
    assert b.station == "piece_workbench" and all(r.amount_per_level == 0 for r in b.requirements)
    assert "Flametal" != gd.items()[0].id and any(i.id == "FlametalNew" and i.name == "Flametal" for i in gd.items())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
