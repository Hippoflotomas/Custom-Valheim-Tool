# Valheim Pack Builder (v1)

Builds BannerShare and ShieldShare pack zips: a form for the JSON, and a cropper that fits
your PNG to the shield face or banner shape.

## Run

```
pip install -r requirements.txt
python main.py
```

Python 3.10 or newer.

## Standalone .exe

Double-click `build_exe.bat`. It installs PyInstaller and writes `dist\ValheimPackBuilder.exe`,
a single file that runs without Python. Edit the `PY=` line if your Python isn't 3.13.
The bundled `game_data.json` / `items.json` are baked into the exe, so after editing them, rebuild.

## Use

1. **New shield pack** or **New banner pack**. The type is fixed for that pack.
2. Fill in the form. The ID auto-fills from the display name until you edit it by hand.
   Picking a base prefab fills in its vanilla cost (station, min level, requirements). If you've
   already edited the cost, you're asked before it's replaced. **Reset to base prefab's recipe** restores it.
3. Add images. Drag in the preview to move, scroll (or − / +) to zoom, **Fill face** / **Fit inside** to re-fit.
   On shields, the brown shows plain wood: transparent parts of your art stay transparent.
4. **Export zip** and drop it into `Documents\Valheim Custom Shields` or `Documents\Valheim Custom Banners`.

**Open pack…** reads an exported (or hand-made) zip back in for editing. The zip is the save file.

### Shield pattern guides

Shield cropping reads the mod's own guides from
`Documents\Valheim Custom Shields\_Templates\<Prefab> - pattern guide.png`.
ShieldShare writes `ShieldWood` on every launch; other bases only appear once a pack using
that base has been loaded. If yours are elsewhere, use **File ▸ Pattern guide folder…**.

## Files

| Path | What |
|---|---|
| `packbuilder/data/game_data.json` | Prefabs, output sizes, vanilla recipes, crafting stations. Edit when the game changes. |
| `packbuilder/data/items.json` | Requirement picker list (friendly name → internal name). |
| `packbuilder/imaging.py` | Face mask, auto-fit, crop, edge bleed, banner icon. |
| `packbuilder/packio.py` | Validation, zip export, zip open. |
| `packbuilder/ui/` | PySide6 GUI. |
| `tests/test_core.py` | `python -m pytest tests/test_core.py` |

## What gets written

- Shields: `<ID>/shield.json`, `Pattern1.png`… (512 wide, the guide's height), optional advanced layers.
  "None" station is written as `"none"`; `amountPerLevel` only when above 0; no `styleCount`.
- Banners: `<ID>/Banner.json`, `MainTex.png`, generated 128×128 `Icon.png`, optional advanced layers.
  "None" station is written as `""`; amounts are always explicit.
