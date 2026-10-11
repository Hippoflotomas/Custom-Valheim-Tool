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


def test_rotation_geometry():
    # quarter turns are exact and clockwise: the top-left pixel ends up top-right
    img = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 0, 0, 255))
    r = imaging.rotate_image(img, 90)
    assert r.size == (50, 100) and r.getpixel((49, 0)) == (255, 0, 0, 255)
    assert imaging.rotated_size(100, 50, 90) == (50, 100)
    w, h = imaging.rotated_size(100, 100, 45)
    assert abs(w - 100 * 2 ** 0.5) < 1e-9 and abs(h - w) < 1e-9
    assert imaging.normalise_angle(270) == -90 and imaging.normalise_angle(-180) == 180
    # changing the angle keeps the centre and the scale
    p = Placement(2.0, 10.0, 20.0)
    q = imaging.with_rotation(p, (100, 50), 0, 90)
    assert q.scale == 2.0
    assert (p.ox + 100, p.oy + 50) == (q.ox + 50, q.oy + 100)


def test_rotated_fill_leaves_no_gaps():
    box = (50, 60, 450, 400)
    for size, deg in [((300, 200), a) for a in (0, 17.5, 45, 90, -120)] + [((40, 40), 30), ((2000, 1500), 30)]:
        src = Image.new("RGBA", size, (10, 200, 10, 255))
        p = imaging.auto_fit(src.size, box, "fill", deg)
        out = np.asarray(imaging.render_placed(src, p, (512, 512), deg))
        inner = out[box[1]:box[3], box[0]:box[2], 3]        # every pixel of the box, corners included
        assert inner.min() == 255, deg
        # "fit" keeps the whole rotated image inside the box
        p = imaging.auto_fit(src.size, box, "fit", deg)
        rw, rh = imaging.rotated_size(*src.size, deg)
        assert p.ox >= box[0] - 1e-6 and p.ox + rw * p.scale <= box[2] + 1e-6
        assert p.oy >= box[1] - 1e-6 and p.oy + rh * p.scale <= box[3] + 1e-6


def test_rotation_exported_and_kept_on_refit(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_pack(tmp, gd.BANNER)
    it = pack.items[0]
    src = Image.new("RGBA", (1000, 400), (255, 0, 0, 255))
    src.paste((0, 0, 255, 255), (500, 0, 1000, 400))    # left red, right blue
    it.art = [Art(src, "wide.png", rotation=90)]       # a quarter turn clockwise: top red, bottom blue
    packio.ensure_placement(gd.BANNER, it, it.art[0], guides)
    assert it.art[0].placement.scale == 1.0             # 400x1000 after turning: fits exactly
    main = Image.open(io.BytesIO(packio.item_files(gd.BANNER, it, guides)["MainTex.png"]))
    assert main.size == (400, 1000)
    assert main.getpixel((200, 100))[:3] == (255, 0, 0) and main.getpixel((200, 900))[:3] == (0, 0, 255)
    it.art[0].placement = None                          # e.g. base prefab changed
    packio.ensure_placement(gd.BANNER, it, it.art[0], guides)
    assert it.art[0].rotation == 90 and it.art[0].placement.scale == 1.0

def test_prefab_recipes():
    from packbuilder.model import apply_recipe, matches_recipe
    # every prefab has a recipe, and every recipe item is in the picker list
    ids = {i.id for i in gd.items()}
    # BannerShare's own bases have no vanilla cost and are the only prefabs allowed none
    no_vanilla_cost = {"StandardLarge"}
    for kind in gd.KINDS:
        for p in gd.prefabs(kind):
            if p.id in no_vanilla_cost:
                assert p.recipe is None and p.note, p.id
                continue
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



# ---------------------------------------------------------------- tabards

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FRONT_BOX = (18, 21, 506, 1007)
BACK_BOX = (516, 18, 1006, 1006)


def solid(w, h, color):
    return Image.new("RGBA", (w, h), color)


def build_tabard_pack(tmp: Path) -> tuple[Pack, GuideStore]:
    from packbuilder.model import BACK_OWN, TabardStyle
    guides = GuideStore(templates(tmp), tabard_templates_dir=tmp / "no_tabard_templates")
    pack = Pack(gd.TABARD)
    it = pack.new_item()
    it.display_name, it.id = "Black Dog Tabard", "HB_BlackDog"
    it.station = "none"
    it.requirements = [Requirement("LeatherScraps", 4, 3)]   # per-level must never be written
    it.art = [TabardStyle(solid(600, 1200, (220, 0, 0, 255)), "red.png"),
              TabardStyle(solid(600, 1200, (220, 0, 0, 255)), "red.png", back_mode=BACK_OWN,
                          back=Art(solid(600, 1200, (0, 0, 220, 255)), "blue.png"))]
    return pack, guides


def test_tabard_gamedata_and_ids():
    from packbuilder.model import id_problem
    assert gd.normalise_station(gd.TABARD, "") == "piece_workbench"
    assert gd.normalise_station(gd.TABARD, "none") == "none"
    assert gd.normalise_station(gd.TABARD, "Black forge") == "blackforge"
    assert gd.station_name(gd.TABARD, "none") == "None"
    assert id_problem("Test", gd.TABARD) and id_problem("TEST", gd.TABARD)
    assert id_problem("Test", gd.SHIELD) is None
    assert gd.has_per_level(gd.SHIELD) and not gd.has_per_level(gd.TABARD)
    assert not gd.has_base_prefab(gd.TABARD) and gd.has_min_level(gd.TABARD)
    it = Pack(gd.TABARD).new_item()
    assert it.station == "piece_workbench" and [(r.item, r.amount) for r in it.requirements] == [("LeatherScraps", 4)]


def test_tabard_layout_islands():
    from packbuilder.guides import bundled_tabard_layout
    g = bundled_tabard_layout()
    assert g.size == (1024, 1024)
    assert g.islands == {"front": FRONT_BOX, "back": BACK_BOX}
    m = np.asarray(g.mask) > 0
    assert m[500, 250] and m[500, 760] and not m[500, 511] and not m[2, 2]
    g2 = g.scaled(2048)
    assert g2.islands["front"] == tuple(2 * v for v in FRONT_BOX)


def test_tabard_export_and_reopen(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_tabard_pack(tmp)
    out = tmp / "MyTabards.zip"
    issues = packio.export_pack(pack, str(out), guides)
    assert not any(i.error for i in issues), issues
    with zipfile.ZipFile(out) as z:
        names = sorted(z.namelist())
        assert names == ["HB_BlackDog/Pattern1.png", "HB_BlackDog/Pattern2.png", "HB_BlackDog/Tabard.json"], names
        j = json.loads(z.read("HB_BlackDog/Tabard.json"))
        assert j["craftingStation"] == "none"
        assert "basePrefab" not in j and "styleCount" not in j
        assert j["requirements"] == [{"item": "LeatherScraps", "amount": 4}]
        assert j["minStationLevel"] == 1
        p1 = np.asarray(Image.open(io.BytesIO(z.read("HB_BlackDog/Pattern1.png"))).convert("RGBA"))
        p2 = np.asarray(Image.open(io.BytesIO(z.read("HB_BlackDog/Pattern2.png"))).convert("RGBA"))
    assert p1.shape == (1024, 1024, 4)
    g = packio.tabard_guide(pack.items[0], guides)
    inside = np.asarray(g.mask) > 0
    assert (p1[..., 3][inside] == 255).all()                  # flattened: no holes
    assert (p1[500, 250, :3] == (220, 0, 0)).all() and (p1[500, 760, :3] == (220, 0, 0)).all()   # back = front
    assert (p2[500, 250, :3] == (220, 0, 0)).all() and (p2[500, 760, :3] == (0, 0, 220)).all()   # own back
    # bleed doesn't bridge the gutter: no purple anywhere
    r, b = p2[..., 0].astype(int), p2[..., 2].astype(int)
    assert not ((r > 20) & (b > 20) & (p2[..., 3] > 0)).any()
    assert p2[500, 510, 3] == 0 and p2[500, 511, 3] == 0      # gutter centre stays empty
    assert p2[500, 506, 3] > 0 and p2[500, 514, 3] > 0        # ...but each panel bled a little

    pack2, notes = packio.open_pack(str(out))
    assert pack2.kind == gd.TABARD and not notes, notes
    it = pack2.items[0]
    assert it.station == "none" and it.texture_size == 1024 and len(it.art) == 2
    assert all(s.sheet for s in it.art)
    out2 = tmp / "again.zip"
    assert not any(i.error for i in packio.export_pack(pack2, str(out2), guides))
    with zipfile.ZipFile(out2) as z:
        again = np.asarray(Image.open(io.BytesIO(z.read("HB_BlackDog/Pattern2.png"))).convert("RGBA"))
    assert np.abs(again.astype(int) - p2.astype(int)).max() <= 1


def test_tabard_front_art_stays_on_front():
    from packbuilder.model import BACK_COLOUR, TabardStyle
    pack = Pack(gd.TABARD)
    it = pack.new_item()
    st = TabardStyle(solid(100, 100, (0, 200, 0, 255)), "green.png", back_mode=BACK_COLOUR, background=(255, 255, 255))
    it.art = [st]
    packio.ensure_style_placements(it, st, None)
    st.placement = Placement(20.0, 0.0, 0.0)            # huge: covers the whole sheet
    img = np.asarray(packio.render_tabard_style(it, st, None))
    assert (img[500, 250, :3] == (0, 200, 0)).all()
    assert (img[500, 760, :3] == (255, 255, 255)).all()  # back panel is plain, not green


def test_tabard_holes_and_2048(tmp_path: Path | None = None):
    from packbuilder.model import TabardStyle, rescale_placements
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_tabard_pack(tmp)
    it = pack.items[0]
    st = it.art[0]
    st.source = art(300, 300)            # transparent margin round a red square
    st.placement = None
    st.keep_holes = True
    msgs = [str(i) for i in packio.validate(pack, guides)]
    assert any("holes" in m for m in msgs), msgs
    st.keep_holes = False
    assert not any("holes" in str(i) for i in packio.validate(pack, guides))
    img = np.asarray(packio.render_tabard_style(it, st, guides))
    g = packio.tabard_guide(it, guides)
    assert np.asarray(g.mask)[60, 100] and (img[60, 100] == (*model_linen(), 255)).all()   # linen where the art is clear
    assert (img[500, 250, :3] == (200, 30, 30)).all()
    packio.ensure_style_placements(it, st, guides)
    before = st.placement
    rescale_placements(it, 2.0)
    it.texture_size = 2048
    assert st.placement.scale == before.scale * 2
    out = tmp / "big.zip"
    assert not any(i.error for i in packio.export_pack(pack, str(out), guides))
    with zipfile.ZipFile(out) as z:
        assert Image.open(io.BytesIO(z.read("HB_BlackDog/Pattern1.png"))).size == (2048, 2048)


def model_linen():
    from packbuilder.model import LINEN
    return LINEN


def test_tabard_custom_icon_survives(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    pack, guides = build_tabard_pack(tmp)
    icon = imaging.png_bytes(solid(128, 128, (1, 2, 3, 255)))
    pack.items[0].art[1].icon = icon
    out = tmp / "icons.zip"
    assert not any(i.error for i in packio.export_pack(pack, str(out), guides))
    with zipfile.ZipFile(out) as z:
        assert "HB_BlackDog/Icon2.png" in z.namelist() and "HB_BlackDog/Icon1.png" not in z.namelist()
    pack2, _ = packio.open_pack(str(out))
    assert pack2.items[0].art[0].icon is None and pack2.items[0].art[1].icon == icon
    # reorder: the icon moves with its style
    pack2.items[0].art.reverse()
    out2 = tmp / "icons2.zip"
    assert not any(i.error for i in packio.export_pack(pack2, str(out2), guides))
    with zipfile.ZipFile(out2) as z:
        assert z.read("HB_BlackDog/Icon1.png") == icon and "HB_BlackDog/Icon2.png" not in z.namelist()


def test_tabard_icon_is_front_panel():
    pattern = Image.new("RGBA", (1024, 1024), (0, 0, 255, 255))
    pattern.paste((255, 0, 0, 255), (0, 0, 512, 1024))
    icon = np.asarray(imaging.make_tabard_icon(pattern))
    assert icon.shape == (128, 128, 4)
    assert (icon[64, 64, :3] == (255, 0, 0)).all() and icon[64, 0, 3] == 0


def test_open_tabard_example_pack():
    pack, notes = packio.open_pack(str(FIXTURES / "TabardShare_ExamplePack.zip"))
    assert pack.kind == gd.TABARD
    by_id = {it.id: it for it in pack.items}
    assert set(by_id) == {"Example_Heraldry", "Example_Plain"}
    h = by_id["Example_Heraldry"]
    assert h.station == "piece_workbench" and len(h.art) == 3
    assert [(r.item, r.amount) for r in h.requirements] == [("Wood", 1)]
    plain = by_id["Example_Plain"]
    assert plain.display_name == "Example Plain"
    assert [(r.item, r.amount) for r in plain.requirements] == [("LeatherScraps", 4)]
    assert plain.station == "piece_workbench"            # empty means the workbench in TabardShare
    assert any("Example_Plain" in n and "leather" in n for n in notes)
    assert not any(i.error for i in packio.validate(pack, None))


def test_tabard_open_rejects_mixed(tmp_path: Path | None = None):
    tmp = Path(tmp_path or tempfile.mkdtemp())
    p = tmp / "mixed.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("A/Tabard.json", "{}")
        z.writestr("B/shield.json", "{}")
    try:
        packio.open_pack(str(p))
    except packio.PackReadError as ex:
        assert "mixes" in str(ex)
    else:
        raise AssertionError("mixed zip accepted")


def test_tabard_validation():
    from packbuilder.model import BACK_OWN, TabardStyle
    pack = Pack(gd.TABARD)
    it = pack.new_item()
    it.id, it.display_name = "Test", "x"
    msgs = "\n".join(str(i) for i in packio.validate(pack, None) if i.error)
    assert "built-in test tabard" in msgs and "1 to 16 styles" in msgs and "base prefab" not in msgs
    it.id = "HB_ok"
    it.art = [TabardStyle(solid(800, 600, (0, 0, 0, 255)), "sheet.png", sheet=True)]
    msgs = "\n".join(str(i) for i in packio.validate(pack, None) if i.error)
    assert "must be square" in msgs
    it.art = [TabardStyle(solid(40, 40, (0, 0, 0, 255)), "tiny.png", back_mode=BACK_OWN)]
    warns = "\n".join(str(i) for i in packio.validate(pack, None) if not i.error)
    assert "front (tiny.png) is enlarged" in warns and "has none" in warns
    it.advanced["MetallicGlossMap.png"] = imaging.png_bytes(solid(512, 512, (0, 0, 0, 255)))
    warns = "\n".join(str(i) for i in packio.validate(pack, None) if not i.error)
    assert "MetallicGlossMap.png is 512x512" in warns



def test_banner_standing_large(tmp_path: Path | None = None):
    """BannerShare 1.2.0's own standing banner: basePrefab StandardLarge, image exactly 2:3."""
    from packbuilder.model import apply_recipe, matches_recipe
    tmp = Path(tmp_path or tempfile.mkdtemp())
    p = gd.prefab(gd.BANNER, "standardlarge")          # case-insensitive, like the mod
    assert p and p.id == "StandardLarge" and p.size == (1024, 1536) and "1.2.0" in p.note
    pack, guides = build_pack(tmp, gd.BANNER)
    it = pack.items[0]
    before = [(r.item, r.amount) for r in it.requirements]
    assert not apply_recipe(gd.BANNER, it, "StandardLarge")   # no vanilla cost: the item's cost is kept
    assert [(r.item, r.amount) for r in it.requirements] == before
    assert not matches_recipe(gd.BANNER, it, "StandardLarge")
    it.base_prefab = "StandardLarge"
    it.art[0].placement = None
    out = tmp / "Standing.zip"
    issues = packio.export_pack(pack, str(out), guides)
    assert not any(i.error for i in issues), issues
    with zipfile.ZipFile(out) as z:
        j = json.loads(z.read("HB_BlackDog/Banner.json"))
        assert j["basePrefab"] == "StandardLarge"
        assert Image.open(io.BytesIO(z.read("HB_BlackDog/MainTex.png"))).size == (1024, 1536)
        assert Image.open(io.BytesIO(z.read("HB_BlackDog/Icon.png"))).size == (128, 128)
    pack2, notes = packio.open_pack(str(out))
    assert not notes and pack2.items[0].base_prefab == "StandardLarge"
    assert pack2.items[0].art[0].source.size == (1024, 1536)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
