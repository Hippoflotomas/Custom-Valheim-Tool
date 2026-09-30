# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the release build. Don't run this directly; use packaging/build.py
# (or build_exe.bat), which also adds the licence files, runs the self-test and makes the zip.
#
# A folder build ("onedir"), not a single exe: it starts faster (a single exe unpacks itself
# to a temp folder on every launch), antivirus flags it less often, and the Qt/PySide6
# libraries stay as separate files that can be swapped, which is how the LGPL expects them
# to be shipped.
import os
import re

# Parts of Qt the app never uses. It only uses QtWidgets: images are loaded and converted by
# Pillow, so Qt's image-format plugins aren't needed, and there's no networking, QML or touch input.
# If one of these turns out to be needed, the self-test in packaging/build.py fails the build.
_DROP = re.compile(
    r"(^|[\\/])opengl32sw\.dll$"           # software OpenGL fallback (~20 MB)
    r"|(^|[\\/])translations[\\/]"         # Qt's own UI translations; the app doesn't load them
    # plugins: the virtual keyboard input context alone pulls in Quick, QML, OpenGL and Network
    r"|[\\/]plugins[\\/](imageformats|iconengines|networkinformation|tls|generic|platforminputcontexts"
    r"|qmltooling|egldeviceintegrations|wayland-[a-z-]+)[\\/]"
    # the Qt libraries only those plugins need
    r"|(^|[\\/])(lib)?Qt6(Quick|Qml|Pdf|VirtualKeyboard|Svg|Network|OpenGL|EglFS|WlShell|WaylandClient)[A-Za-z]*\.(dll|so)",
    re.IGNORECASE,
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('packbuilder/data', 'packbuilder/data')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'pydoc', 'pytest', 'yaml'],
    noarchive=False,
    optimize=0,        # keep asserts: the --self-test relies on them
)
a.binaries = [b for b in a.binaries if not _DROP.search(b[0])]
a.datas = [d for d in a.datas if not _DROP.search(d[0])]

pyz = PYZ(a.pure)

_version_file = os.path.join(SPECPATH, 'build', 'version_info.txt')   # written by packaging/build.py

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ValheimPackBuilder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # UPX-packed DLLs are a common cause of antivirus false positives
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_file if os.path.exists(_version_file) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ValheimPackBuilder',
)
