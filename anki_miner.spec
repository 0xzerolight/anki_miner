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

# Fall back to SVG on Linux; skip icon on Windows/macOS if native format not found
if not os.path.exists(icon_file):
    if platform.system() == "Linux":
        icon_file = os.path.join(
            project_root, "anki_miner", "gui", "resources", "icons", "anki_miner.svg"
        )
    else:
        icon_file = None

# Bundle vendored ffmpeg/ffprobe binaries. CI populates vendor/ffmpeg/ with static
# builds before invoking PyInstaller; local dev builds leave it absent (empty list →
# unchanged behavior). The "bin" dest matches the runtime resolver's lookup at
# sys._MEIPASS/bin/ (see anki_miner/utils/ffmpeg_resolver.py).
ffmpeg_binaries = []
vendor_ffmpeg = os.path.join(project_root, "vendor", "ffmpeg")
if os.path.isdir(vendor_ffmpeg):
    for _fn in sorted(os.listdir(vendor_ffmpeg)):
        _full = os.path.join(vendor_ffmpeg, _fn)
        if os.path.isfile(_full):
            ffmpeg_binaries.append((_full, "bin"))

# Bundle the ffmpeg GPL license text if present (populated by a sibling CI task).
# Conditional so local builds don't hard-fail before the license dir exists. Lands at
# sys._MEIPASS/licenses/ffmpeg/ in the bundle.
ffmpeg_license_dir = os.path.join(project_root, "licenses", "ffmpeg")
ffmpeg_license_datas = []
if os.path.isdir(ffmpeg_license_dir):
    ffmpeg_license_datas.append((ffmpeg_license_dir, os.path.join("licenses", "ffmpeg")))

a = Analysis(
    [os.path.join(project_root, "anki_miner", "gui", "app.py")],
    pathex=[project_root],
    binaries=ffmpeg_binaries,
    datas=[
        # GUI resources (stylesheets and icons)
        (
            os.path.join(project_root, "anki_miner", "gui", "resources"),
            os.path.join("anki_miner", "gui", "resources"),
        ),
        # Bundled dictionary card stylesheet (Issue #44) — loaded at runtime via
        # importlib.resources, so it must land at the same package path.
        (
            os.path.join(project_root, "anki_miner", "services", "dictionary", "resources"),
            os.path.join("anki_miner", "services", "dictionary", "resources"),
        ),
        # Bundled name wordsets (Issue #59) — loaded at runtime via
        # importlib.resources, so they must land at the same package path.
        (
            os.path.join(project_root, "anki_miner", "resources"),
            os.path.join("anki_miner", "resources"),
        ),
        # unidic-lite dictionary data (required by fugashi/MeCab)
        (unidic_data, "unidic_lite"),
    ]
    + ffmpeg_license_datas,
    hiddenimports=[
        "unidic_lite",
        "fugashi",
        "PyQt6.sip",
    ],
    hookspath=[os.path.join(project_root, "PyInstaller-Hooks")],
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
