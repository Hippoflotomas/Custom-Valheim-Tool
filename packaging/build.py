"""Build the downloadable release.

    python packaging/build.py              (or double-click build_exe.bat on Windows)

Steps:
  1. run the tests
  2. PyInstaller, using ValheimPackBuilder.spec  -> dist/ValheimPackBuilder/
  3. add the end-user README, third-party notices and licence texts to that folder
  4. run the BUILT program's --self-test, so a broken bundle is caught here, not by users
  5. zip it                                      -> dist/ValheimPackBuilder-<version>-windows.zip

Options: --skip-tests
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
APP = "ValheimPackBuilder"

# Bundled libraries whose wheels carry their own licence files (PySide6's don't; its
# LGPL/GPL texts are kept in packaging/licenses).
LICENSED_DISTS = {"Pillow": "pillow", "NumPy": "numpy"}


def version() -> str:
    text = (ROOT / "packbuilder" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        sys.exit("Can't find __version__ in packbuilder/__init__.py")
    return m.group(1)


def platform_tag() -> str:
    return {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)


def step(msg: str) -> None:
    print(f"\n=== {msg}", flush=True)


def run(*cmd: str) -> None:
    print("> " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


def write_version_info(ver: str) -> None:
    """Windows file properties (Details tab) for the exe."""
    nums = [int(n) for n in re.findall(r"\d+", ver)[:4]]
    nums += [0] * (4 - len(nums))
    t = tuple(nums)
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build" / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Hippoflotomas'),
      StringStruct('FileDescription', 'Valheim Pack Builder'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', '{APP}'),
      StringStruct('OriginalFilename', '{APP}.exe'),
      StringStruct('ProductName', 'Valheim Pack Builder'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def add_docs(app_dir: Path) -> None:
    shutil.copy(PACKAGING / "README.txt", app_dir / "README.txt")
    shutil.copy(PACKAGING / "THIRD-PARTY-NOTICES.txt", app_dir / "THIRD-PARTY-NOTICES.txt")
    lic = app_dir / "licenses"
    if lic.exists():
        shutil.rmtree(lic)
    (lic / "Qt-PySide6").mkdir(parents=True)
    for f in (PACKAGING / "licenses").glob("*.txt"):
        shutil.copy(f, lic / "Qt-PySide6" / f.name)
    for label, dist_name in LICENSED_DISTS.items():
        try:
            d = distribution(dist_name)
        except PackageNotFoundError:
            sys.exit(f"{dist_name} isn't installed, so its licence can't be bundled.")
        files = [f for f in (d.files or []) if ".dist-info/" in str(f)
                 and re.search(r"licen[cs]e|copying|notice", Path(str(f)).name + str(f), re.IGNORECASE)
                 and not str(f).endswith(("RECORD", "METADATA", "WHEEL"))]
        if not files:
            sys.exit(f"No licence files found in the {dist_name} package.")
        for f in files:
            src = Path(d.locate_file(f))
            # keep the path below "licenses/" (numpy has several), flattened to one folder level
            rel = str(f).split("licenses/", 1)[-1] if "licenses/" in str(f) else Path(str(f)).name
            dest = lic / label / rel.replace("/", "_")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    if py_license.exists():
        (lic / "Python").mkdir()
        shutil.copy(py_license, lic / "Python" / "LICENSE.txt")
    elif sys.platform == "win32":
        sys.exit(f"Python's LICENSE.txt wasn't found at {py_license}.")


def self_test(app_dir: Path) -> None:
    exe = app_dir / (f"{APP}.exe" if sys.platform == "win32" else APP)
    with tempfile.TemporaryDirectory() as tmp:
        result = Path(tmp) / "result.txt"
        proc = subprocess.run([str(exe), "--self-test", str(result)], timeout=300)
        text = result.read_text(encoding="utf-8") if result.exists() else "(no result file written)"
    print(text.strip())
    if proc.returncode != 0 or not text.startswith("OK"):
        sys.exit(f"Self-test of the built program failed (exit code {proc.returncode}).")


def make_zip(app_dir: Path, ver: str) -> Path:
    out = DIST / f"{APP}-{ver}-{platform_tag()}.zip"
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_file():      # (Linux builds contain .so symlinks, stored as copies; Windows has none)
                z.write(f, Path(APP) / f.relative_to(app_dir))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-tests", action="store_true")
    args = ap.parse_args()
    ver = version()
    print(f"Valheim Pack Builder {ver}, Python {sys.version.split()[0]} ({sys.executable})")

    if not args.skip_tests:
        step("Tests")
        run(sys.executable, "-m", "pytest", "-q", "tests")

    step("PyInstaller")
    if sys.platform == "win32":
        write_version_info(ver)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyinstaller"), f"{APP}.spec")
    app_dir = DIST / APP

    step("Licences and README")
    add_docs(app_dir)

    step("Self-test of the built program")
    self_test(app_dir)

    step("Zip")
    out = make_zip(app_dir, ver)
    size = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file() and not f.is_symlink())
    print(f"{out}\n  zip {out.stat().st_size / 1e6:.1f} MB, unpacked {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
