# anki_miner.spec — PyInstaller spec file for Anki Miner GUI
import os
import platform

import unidic_lite

block_cipher = None

project_root = os.path.abspath(".")
unidic_data = os.path.dirname(unidic_lite.__file__)

# Platform-specific icon
if platform.system() == "Windows":
    icon_file = os.path.join(
        project_root, "anki_miner", "gui", "resources", "icons", "anki_miner.ico"
    )
elif platform.system() == "Darwin":
    icon_file = os.path.join(
        project_root, "anki_miner", "gui", "resources", "icons", "anki_miner.icns"
    )
else:
    icon_file = os.path.join(
        project_root, "anki_miner", "gui", "resources", "icons", "anki_miner.svg"
    )

# Fall back to SVG if platform-specific icon doesn't exist
if not os.path.exists(icon_file):
    icon_file = os.path.join(
        project_root, "anki_miner", "gui", "resources", "icons", "anki_miner.svg"
    )

a = Analysis(
    [os.path.join(project_root, "anki_miner", "gui", "app.py")],
    pathex=[project_root],
    binaries=[],
    datas=[
        # GUI resources (stylesheets and icons)
        (
            os.path.join(project_root, "anki_miner", "gui", "resources"),
            os.path.join("anki_miner", "gui", "resources"),
        ),
        # unidic-lite dictionary data (required by fugashi/MeCab)
        (unidic_data, "unidic_lite"),
    ],
    hiddenimports=[
        "unidic_lite",
        "fugashi",
        "PyQt6.sip",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Dev/test dependencies
        "pytest",
        "black",
        "mypy",
        "ruff",
        "pre_commit",
        # Other Qt bindings (avoid conflicts)
        "PySide2",
        "PySide6",
        "PyQt5",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AnkiMiner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AnkiMiner",
)
