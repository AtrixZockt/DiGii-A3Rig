# PyInstaller build for the standalone Windows executable shipped via winget.
#
# One-*dir* rather than one-file, matching how HEMTT ships: a one-file build unpacks
# itself to %TEMP% on every run, which costs startup time and trips antivirus heuristics
# far more often. The directory is zipped, and winget's `zip` + nested `portable`
# installer type handles it natively.
#
#   pyinstaller packaging/a3rig.spec --noconfirm
#
# Produces dist/a3rig/a3rig.exe

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH).parent / "src"))

from a3rig import __version__  # noqa: E402

block_cipher = None

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(Path(SPECPATH).parent / "src")],
    binaries=[],
    datas=[],
    # Typer/Click and rich resolve some imports lazily; psutil's Windows backend is a C
    # extension. Naming them keeps the build stable across PyInstaller versions.
    hiddenimports=[
        "a3rig",
        "a3rig.cli",
        "psutil",
        "tomlkit",
        "typer",
        "click",
        "rich",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pulled in transitively but never used by a CLI; excluding them keeps the zip small.
    excludes=["tkinter", "unittest", "pydoc", "pdb", "email", "http", "xml"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="a3rig",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is a reliable way to get flagged by antivirus.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="a3rig",
)
