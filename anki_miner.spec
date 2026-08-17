# anki_miner.spec — PyInstaller spec file for Anki Miner GUI
import os
import platform
import re
import sys

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

# Bundle vendored alass binary. CI populates vendor/alass/ with a static build
# before invoking PyInstaller; local dev builds leave it absent (empty list →
# unchanged behavior). The "bin" dest matches the runtime resolver's lookup at
# sys._MEIPASS/bin/ (see anki_miner/utils/alass_resolver.py).
alass_binaries = []
vendor_alass = os.path.join(project_root, "vendor", "alass")
if os.path.isdir(vendor_alass):
    for _fn in sorted(os.listdir(vendor_alass)):
        _full = os.path.join(vendor_alass, _fn)
        if os.path.isfile(_full):
            alass_binaries.append((_full, "bin"))

# Bundle the vendored yt-dlp standalone binary. CI and scripts/release_preflight.sh
# populate vendor/yt-dlp/ from the pin in .github/ytdlp-pin.json before invoking
# PyInstaller; local dev builds leave it absent (empty list → unchanged behavior).
# The "bin" dest matches sys._MEIPASS/bin/, the tier anki_miner/utils/ytdlp_resolver.py
# checks after PATH. Without this the resolver fell through to the bare literal
# "yt-dlp" and a fresh packaged install could not mine YouTube at all.
#
# Must be a standalone build, never the bare "yt-dlp" zipapp asset: that one shebangs
# the system python3 (which a packaged app does not ship) and carries no curl_cffi, so
# --list-impersonate-targets would come back empty.
#
# NOT collected on macOS. yt-dlp_macos is itself a PyInstaller onefile: its
# payload is a PKG archive appended after the Mach-O image. Anything that
# rewrites the Mach-O -- which is exactly what PyInstaller does to every entry
# in `binaries` on macOS (arch thinning, install_name_tool, ad-hoc re-signing)
# -- drops that trailing payload, and the result runs only far enough to say
#     [PYI-12119:ERROR] Could not load PyInstaller's embedded PKG archive
# The macOS legs therefore copy the binary into _internal/bin AFTER the build,
# byte for byte; see the "Vendor yt-dlp into the bundle (macOS)" step in
# release.yml. Caught by the Intel bundle smoke, which is why the release
# dry-run runs `all` and not just linux-windows.
ytdlp_binaries = []
vendor_ytdlp = os.path.join(project_root, "vendor", "yt-dlp")
if os.path.isdir(vendor_ytdlp) and sys.platform != "darwin":
    for _fn in sorted(os.listdir(vendor_ytdlp)):
        _full = os.path.join(vendor_ytdlp, _fn)
        if os.path.isfile(_full):
            ytdlp_binaries.append((_full, "bin"))

# Bundle the vendored libmpv shared library. CI populates vendor/libmpv/ from the
# repo-owned vendor-libmpv-* release before invoking PyInstaller; local dev builds
# leave it absent (empty list → unchanged behavior). Dest is "." (the _MEIPASS
# root), NOT "bin": python-mpv's Windows fallback searches dirname(mpv.__file__),
# the macOS closure resolves @loader_path siblings, and the Linux onedir loader
# path covers _internal/ — mpv_loader.bundled_libmpv_path() globs the root.
libmpv_binaries = []
vendor_libmpv = os.path.join(project_root, "vendor", "libmpv")
if os.path.isdir(vendor_libmpv):
    for _fn in sorted(os.listdir(vendor_libmpv)):
        _full = os.path.join(vendor_libmpv, _fn)
        if os.path.isfile(_full):
            libmpv_binaries.append((_full, "."))

# Bundle the ffmpeg GPL license text if present (populated by a sibling CI task).
# Conditional so local builds don't hard-fail before the license dir exists. Lands at
# sys._MEIPASS/licenses/ffmpeg/ in the bundle.
ffmpeg_license_dir = os.path.join(project_root, "licenses", "ffmpeg")
ffmpeg_license_datas = []
if os.path.isdir(ffmpeg_license_dir):
    ffmpeg_license_datas.append((ffmpeg_license_dir, os.path.join("licenses", "ffmpeg")))

# Bundle the alass GPL-3.0 license text if present (populated by a sibling CI task).
# Conditional so local builds don't hard-fail before the license dir exists. Lands at
# sys._MEIPASS/licenses/alass/ in the bundle.
alass_license_dir = os.path.join(project_root, "licenses", "alass")
alass_license_datas = []
if os.path.isdir(alass_license_dir):
    alass_license_datas.append((alass_license_dir, os.path.join("licenses", "alass")))

# Bundle the yt-dlp license texts if present (populated by a sibling CI task, and by
# release_preflight.sh locally). Conditional so local builds don't hard-fail before
# the license dir exists. Lands at sys._MEIPASS/licenses/yt-dlp/ in the bundle.
#
# This is `datas`, not `binaries`, so unlike ytdlp_binaries above it is NOT skipped on
# macOS: PyInstaller copies data files verbatim, and only the Mach-O rewriting that
# corrupts the vendored executable forced the post-build copy there. Every OS that
# ships the binary therefore ships its license alongside, as ffmpeg/alass/libmpv do.
ytdlp_license_dir = os.path.join(project_root, "licenses", "yt-dlp")
ytdlp_license_datas = []
if os.path.isdir(ytdlp_license_dir):
    ytdlp_license_datas.append((ytdlp_license_dir, os.path.join("licenses", "yt-dlp")))

# Bundle the libmpv license/source-offer files (committed README/COPYING plus the
# per-artifact Copyright/SOURCES.txt the CI fetch step drops in). Lands at
# sys._MEIPASS/licenses/libmpv/ in the bundle.
libmpv_license_dir = os.path.join(project_root, "licenses", "libmpv")
libmpv_license_datas = []
if os.path.isdir(libmpv_license_dir):
    libmpv_license_datas.append((libmpv_license_dir, os.path.join("licenses", "libmpv")))

# Bundle the MIT notice for audio-pack parser code ported from
# local-audio-yomichan. It ships in the wheel via project.license-files and must
# also accompany frozen copies of formats.py.
local_audio_license_dir = os.path.join(project_root, "licenses", "local-audio-yomichan")
local_audio_license_datas = []
if os.path.isdir(local_audio_license_dir):
    local_audio_license_datas.append(
        (local_audio_license_dir, os.path.join("licenses", "local-audio-yomichan"))
    )

# Windows release builds add the Apache-licensed Vulkan loader next to libmpv.
# Keep its committed, version-matched notice in the frozen bundle.
vulkan_loader_license_dir = os.path.join(project_root, "licenses", "vulkan-loader")
vulkan_loader_license_datas = []
if os.path.isdir(vulkan_loader_license_dir):
    vulkan_loader_license_datas.append(
        (vulkan_loader_license_dir, os.path.join("licenses", "vulkan-loader"))
    )

# Embed a Windows PE VERSIONINFO resource (company/product/version/copyright). An
# unsigned, metadata-less PyInstaller exe is a textbook Defender false-positive: the
# ML model has no positive trust signals to weigh against "packed binary that runs
# code". These strings give it some. Windows-only (VERSIONINFO is a PE concept; the
# version= arg is ignored on other platforms). Version is parsed from the single
# source of truth in anki_miner/__init__.py so it never drifts.
def _read_app_version():
    init_path = os.path.join(project_root, "anki_miner", "__init__.py")
    with open(init_path, encoding="utf-8") as fh:
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    return match.group(1) if match else "0.0.0"


app_version = _read_app_version()
_version_nums = [int(n) for n in re.findall(r"\d+", app_version)[:4]]
version_tuple = tuple(_version_nums) + (0,) * (4 - len(_version_nums))

version_info = None
if platform.system() == "Windows":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=version_tuple,
            prodvers=version_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            # 040904B0 = US English (0x0409) + Unicode codepage (0x04B0 == 1200).
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Anki Miner Contributors"),
                            StringStruct(
                                "FileDescription",
                                "Anki Miner - Japanese vocabulary mining from "
                                "media",
                            ),
                            StringStruct("FileVersion", app_version),
                            StringStruct("InternalName", "AnkiMiner"),
                            StringStruct("LegalCopyright", "GPL-3.0-or-later"),
                            StringStruct("OriginalFilename", "AnkiMiner.exe"),
                            StringStruct("ProductName", "Anki Miner"),
                            StringStruct("ProductVersion", app_version),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

a = Analysis(
    [os.path.join(project_root, "anki_miner", "gui", "launch.py")],
    pathex=[project_root],
    binaries=ffmpeg_binaries + alass_binaries + libmpv_binaries + ytdlp_binaries,
    datas=[
        # GUI resources (stylesheets, icons, translations, and the bundled
        # Japanese fallback font under resources/fonts — the whole tree is
        # copied, so a new resource kind needs no change here).
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
    + ffmpeg_license_datas
    + alass_license_datas
    + ytdlp_license_datas
    + libmpv_license_datas
    + local_audio_license_datas
    + vulkan_loader_license_datas,
    hiddenimports=[
        "unidic_lite",
        "fugashi",
        "PyQt6.sip",
        # python-mpv: imported only inside utils/mpv_loader.py functions.
        # Bytecode analysis should find the literal `import mpv` there, but the
        # module is a single file (no package dir) and the import sits behind a
        # find_library monkeypatch — belt-and-braces it into the graph.
        "mpv",
        # ffsubsync (primary subtitle-sync engine): imported function-locally in
        # services/sync_engines/ffsubsync_engine.py. Bytecode analysis finds the
        # IMPORT opcodes, but the engine is load-bearing for the Retime tool —
        # belt-and-braces it into the graph like mpv. Pure-Python package, no
        # data files; its VAD/chardet deps are ordinary static imports it pulls
        # in itself.
        "ffsubsync",
    ],
    # PyInstaller-Hooks/ holds hook-faster_whisper.py (faster_whisper + ctranslate2
    # + av) and hook-pywhispercpp.py (the whisper.cpp/ggml Vulkan ASR backend).
    # Both target packages are imported function-locally in services/asr/_engine.py,
    # but PyInstaller's bytecode analysis finds those IMPORT opcodes and pulls the
    # packages into the graph, so the matching hooks auto-run from here — no
    # hiddenimports entry needed.  Each hook collects nothing when its package is
    # absent (collect_all returns empty lists; hook-pywhispercpp also short-circuits
    # its explicit ggml/whisper-lib collection on a missing find_spec), so the
    # Intel-mac / no-[asr] build (which installs neither faster_whisper nor
    # pywhispercpp) is unaffected: nothing is forced onto macOS.  The Linux/Windows
    # release jobs install the from-source Vulkan pywhispercpp wheel before this
    # spec runs (see release.yml).
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
        # onnxruntime (Whisper VAD backend) ships as an on-demand downloadable
        # pack (gui/workers/onnx_pack_download_worker.py), not in the bundle, to
        # keep it lean — availability is probed at runtime via find_spec.  av,
        # however, is a HARD import of faster_whisper (faster_whisper/audio.py
        # does `import av` at package load), so it MUST be bundled or the offline
        # ASR bundle smoke fails with ModuleNotFoundError: No module named 'av'.
        "onnxruntime",
        # yt-dlp ships as the vendored standalone EXECUTABLE (vendor/yt-dlp above),
        # which is the only form the app ever uses — every call site spawns it as a
        # subprocess. The Python package was collected wholesale for no runtime
        # benefit (~16 MB, 13 MB of it extractors) and is dropped here.
        #
        # Kept as a pip dependency on purpose: non-frozen installs (pip/pipx/source)
        # get its console script, which ytdlp_resolver's interpreter-sibling tier
        # finds. This exclude only affects the frozen bundle.
        "yt_dlp",
    ],
    noarchive=False,
    optimize=0,
)

# Drop host-audio client libraries bindepend may pull in through the vendored
# libmpv (Linux). These must come from the host at runtime: a bundled libasound/
# libpulse resolves its config/plugin paths relative to itself and breaks audio
# on foreign distros (same convention as the AppImage excludelist). PyInstaller's
# own exclude list already refuses GL/EGL/wayland/xcb/drm/nvidia. A NEEDED lib we
# drop that is absent on a host degrades to "libmpv fails to dlopen → preview
# notice", never a crash.
#
# PLAIN SONAMES ONLY: auditwheel-mangled wheel-vendored copies (e.g. PyAV's
# libasound-c7818c60.so.2.0.0) are a hard NEEDED of their wheel's extension and
# MUST stay bundled — filtering one broke the asr smoke (ImportError on av).
# The mangled names have a -<hash> before ".so", so anchoring "lib<name>.so"
# matches only the plain system sonames bindepend picked up via libmpv.
_HOST_ONLY_LIB_RE = re.compile(r"^lib(asound|pulse(-simple)?|pulsecommon-[0-9.]+|jack|pipewire-0\.3)\.so(\.|$)")
a.binaries = [entry for entry in a.binaries if not _HOST_ONLY_LIB_RE.match(os.path.basename(entry[0]))]

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
    # UPX packing is a classic AV heuristic trigger; disabled to reduce Defender
    # false positives (the unpack-at-runtime pattern reads as malware behavior).
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=icon_file,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # see EXE() above — UPX off to reduce AV false positives
    upx_exclude=[],
    name="AnkiMiner",
)
