"""A quick end-to-end check that runs without showing a window.

Used by the release build to prove the packaged exe works (bundled data files load,
imaging runs, a pack exports and reopens) before it's published:

    ValheimPackBuilder.exe --self-test result.txt

Writes "OK" or the error to the result file (a windowed exe has no console) and
returns the exit code.
"""
from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from PIL import Image, ImageDraw


def _guide(w: int, h: int) -> Image.Image:
    """A minimal stand-in for ShieldShare's pattern guide: a grey face on transparency."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, w - 5, h - 5), fill=(205, 205, 205, 255))
    return img


def run() -> None:
    """Raise on any failure."""
    from . import gamedata as gd, packio
    from .guides import GuideStore, bundled_tabard_layout
    from .model import Art, Pack, Requirement, TabardStyle

    assert all(gd.prefabs(k) and gd.stations(k) for k in gd.KINDS) and gd.items(), \
        "bundled game data didn't load"
    # the tabard UV layout ships with the app (proves the PyInstaller datas include it)
    layout = bundled_tabard_layout()
    assert layout.size == (1024, 1024) and set(layout.islands) == {"front", "back"}, "bundled UV layout is wrong"

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        templates = tmp / "_Templates"
        templates.mkdir()
        _guide(512, 512).save(templates / "ShieldWood - pattern guide.png")
        # an empty tabard templates folder: tabards must work from the bundled layout alone
        guides = GuideStore(templates, tabard_templates_dir=tmp / "no_tabard_templates")

        art = Image.new("RGBA", (700, 500), (200, 30, 30, 255))
        for kind in gd.KINDS:
            pack = Pack(kind)
            it = pack.new_item()
            it.display_name, it.id = "Self test", "SelfTest_Item"
            it.requirements = [Requirement("Wood", 1)]
            if kind == gd.TABARD:
                it.art = [TabardStyle(art, "test.png", rotation=30.0)]
            else:
                it.art = [Art(art, "test.png", rotation=30.0)]
            out = tmp / f"{kind}.zip"
            issues = packio.export_pack(pack, str(out), guides)
            errors = [str(i) for i in issues if i.error]
            assert not errors, errors
            reopened, _ = packio.open_pack(str(out))
            assert reopened.kind == kind and len(reopened.items[0].art) == 1, f"{kind} pack didn't reopen"

    # the GUI builds (without being shown)
    from .ui.main_window import MainWindow
    MainWindow().close()


def main(result_path: str | None) -> int:
    try:
        run()
        text, code = "OK", 0
    except BaseException:
        text, code = "FAILED\n" + traceback.format_exc(), 1
    if result_path:
        Path(result_path).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return code
