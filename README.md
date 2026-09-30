# Valheim Pack Builder (v1)

Builds BannerShare and ShieldShare pack zips: a form for the JSON, and a cropper that fits
your PNG to the shield face or banner shape.

## Download (no Python needed)

Windows: **[ValheimPackBuilder-windows.zip](https://github.com/Hippoflotomas/Custom-Valheim-Tool/releases/latest/download/ValheimPackBuilder-windows.zip)**
(always the newest release). Unzip the folder and run `ValheimPackBuilder.exe`. The zip has its own
`README.txt` for players.

## Run from source

```
pip install -r requirements.txt
python main.py
```

Python 3.10 or newer.

## Building a release

- **On GitHub (the normal way):** set `__version__` in `packbuilder/__init__.py`, commit, then
  `git tag v1.2.3` and `git push origin v1.2.3`. The **Release** workflow builds on Windows, runs the
  tests and the built exe's self-test, and publishes `ValheimPackBuilder-windows.zip` on the Releases
  page. To test a build without releasing: Actions ▸ Release ▸ Run workflow (the zip is under the run's Artifacts).
- **Locally:** double-click `build_exe.bat` (edit its `PY=` line if your Python isn't 3.13). It writes
  `dist\ValheimPackBuilder\` and `dist\ValheimPackBuilder-<version>-windows.zip`.

Both run `packaging/build.py`: tests, PyInstaller with `ValheimPackBuilder.spec` (a folder build, not a
single exe: faster start, fewer antivirus false alarms, and the LGPL Qt libraries stay replaceable),
licence files, then `ValheimPackBuilder.exe --self-test` on the result, then the zip.
The bundled `game_data.json` / `items.json` are baked in, so after editing them, rebuild.

## Use

1. **New shield pack** or **New banner pack**. The type is fixed for that pack.
2. Fill in the form. The ID auto-fills from the display name until you edit it by hand.
   Picking a base prefab fills in its vanilla cost (station, min level, requirements). If you've
   already edited the cost, you're asked before it's replaced. **Reset to base prefab's recipe** restores it.
3. Add images. Drag in the preview to move, scroll (or − / +) to zoom, **Fill face** / **Fit inside** to re-fit.
   Rotate with **↺ 90°** / **↻ 90°**, the angle box, or Shift+scroll (1° per notch); **Straighten** resets it.
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
| `packbuilder/selftest.py` | `python main.py --self-test`: headless end-to-end check, run on the built exe |
| `packaging/` | Release build script, player README, third-party notices, Qt/PySide6 licence texts |
| `.github/workflows/release.yml` | Builds and publishes the Windows zip when a `v*` tag is pushed |

If the app hits an unexpected error it shows a message and appends the details to
`%LOCALAPPDATA%\ValheimPackBuilder\error.log`.

## What gets written

- Shields: `<ID>/shield.json`, `Pattern1.png`… (512 wide, the guide's height), optional advanced layers.
  "None" station is written as `"none"`; `amountPerLevel` only when above 0; no `styleCount`.
- Banners: `<ID>/Banner.json`, `MainTex.png`, generated 128×128 `Icon.png`, optional advanced layers.
  "None" station is written as `""`; amounts are always explicit.
