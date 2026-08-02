"""Main GUI application entry point.

Hidden ``ANKI_MINER_SMOKE`` modes:

* ``youtube``, ``asr``, and ``whispercpp`` validate frozen dependencies before
  Qt starts.
* ``installer`` runs full GUI composition while suppressing optional startup
  work, validates installed-runtime invariants, and writes an atomic result
  marker before exiting.
"""

import logging
import os
import platform
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtCore import QCoreApplication, QEvent, QLockFile, QObject, QProcess, Qt, QThread, QTimer, pyqtBoundSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui import restart
from anki_miner.gui.controllers import recovery_controller
from anki_miner.gui.controllers.recovery_controller import RecoveryController
from anki_miner.gui.i18n import install_translators
from anki_miner.gui.launch import get_effective_log_path as _get_effective_log_path
from anki_miner.gui.main_window import MainWindow, open_log_folder
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.focus_ring import install_keyboard_focus_ring
from anki_miner.gui.utils.fonts import initialize_application_fonts
from anki_miner.gui.utils.run_off_thread import join_all_off_thread_workers, run_off_thread, still_running
from anki_miner.gui.utils.service_factory import create_youtube_fetcher
from anki_miner.gui.utils.stall_watchdog import install_stall_watchdog
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.reading_tab import ReadingTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab
from anki_miner.gui.widgets.video_tab import VideoTab
from anki_miner.services.startup_store_recovery import run_startup_store_recovery
from anki_miner.services.stats_service import StatsService
from anki_miner.utils import alass_resolver
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.file_utils import ensure_directory
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ComposedApp:
    """Main-window composition needed by the application lifecycle."""

    window: MainWindow
    stats_service: StatsService
    analytics_tab: AnalyticsTab


def _scrub_pyinstaller_env() -> None:
    # PyInstaller's bootloader prepends _internal/ to LD_LIBRARY_PATH so
    # bundled libs load at startup. That value leaks into every subprocess
    # we spawn (yt-dlp, ffmpeg), where it shadows the host's newer OpenSSL
    # with our older bundled libcrypto and breaks system binaries linked
    # against OpenSSL >= 3.1. Restore the pre-launch value before anything
    # else runs.
    # https://pyinstaller.org/en/stable/runtime-information.html
    if not getattr(sys, "frozen", False):
        return
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = os.environ.pop(f"{var}_ORIG", None)
        if orig is not None:
            os.environ[var] = orig
        else:
            os.environ.pop(var, None)


def available_impersonate_targets(stdout: str) -> list[str]:
    """Usable target rows from yt-dlp's ``--list-impersonate-targets`` output.

    The output is a ``[info]`` banner, a ``Client  OS  Source`` header, a rule of
    dashes, then one row per target — every row ending in ``(unavailable)`` when
    curl_cffi is absent::

        [info] Available impersonate targets
        Client    OS   Source
        ------------------------------------
        Chrome    -    curl_cffi (unavailable)

    Module-level and free of subprocess plumbing so the filter itself is
    testable. It has to be: the original inline version dropped lines on
    ``"unavailable" not in line.lower()``, and "unavailable" is not a substring
    of "Available" — the banner always survived, ``available`` was never empty,
    and the assertion it feeds could not fail. A zipapp asset would have shipped
    with a green smoke.
    """
    rows: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("["):
            continue  # [info] / [debug] banner
        fields = line.split()
        if len(fields) < 3:
            continue  # blank line or the dashed rule
        if fields[0] == "Client":
            continue  # column header
        if "(unavailable)" in line:
            continue
        rows.append(line)
    return rows


def _run_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller bundle validation.

    Triggered by ANKI_MINER_SMOKE=youtube. Verifies the vendored yt-dlp EXECUTABLE
    landed in the bundle and runs, plus that the Pillow JPEG codec survived. No
    network, no YoutubeDL, no bot challenge. Not a CLI surface — the flag is hidden,
    env-var-only, and exits before any Qt init.

    This used to walk ``yt_dlp``'s extractor registry, which tested a Python package
    the app never imports at runtime: every call site spawns yt-dlp as a subprocess.
    The bundle now ships the standalone binary instead, so the smoke asserts the
    artifact production actually uses.

    The binary is checked at its absolute bundled path rather than through
    ``ytdlp_resolver``: the resolver deliberately prefers PATH over the bundle (a
    user's own yt-dlp is usually fresher than a build-time pin), and neither
    ``bundle_smoke.sh`` nor ``release_preflight.sh`` scrubs PATH — so a
    resolver-based assertion would fail on any machine that happens to have yt-dlp
    installed, including a developer with this project's own venv activated.
    Precedence is covered by unit tests, where PATH is patched deterministically.
    """
    try:
        import subprocess

        from anki_miner.utils.bundled_binary import bundled_name, frozen_state

        frozen, meipass = frozen_state()
        if not frozen or meipass is None:
            raise RuntimeError("not running from a PyInstaller bundle")

        ytdlp = Path(meipass) / "bin" / bundled_name("yt-dlp")
        if not ytdlp.is_file():
            raise RuntimeError(
                f"vendored yt-dlp missing from the bundle at {ytdlp}. CI must populate "
                "vendor/yt-dlp/ from .github/ytdlp-pin.json before PyInstaller runs."
            )

        version_proc = subprocess.run(
            [str(ytdlp), "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if version_proc.returncode != 0:
            raise RuntimeError(f"bundled yt-dlp --version exited {version_proc.returncode}: {version_proc.stderr}")
        lines = (version_proc.stdout or "").strip().splitlines()
        ytdlp_version = lines[0].strip() if lines else ""
        if not ytdlp_version:
            raise RuntimeError("bundled yt-dlp --version printed nothing")

        # Impersonation is why the standalone build is vendored rather than the 3 MB
        # zipapp: yt-dlp's own release builds embed curl_cffi, and YouTube
        # increasingly gates on it. Every target line ends in "(unavailable)" when
        # curl_cffi is absent, so an available target proves it came through.
        targets_proc = subprocess.run(
            [str(ytdlp), "--list-impersonate-targets"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if targets_proc.returncode != 0:
            raise RuntimeError(
                f"bundled yt-dlp --list-impersonate-targets exited " f"{targets_proc.returncode}: {targets_proc.stderr}"
            )
        available = available_impersonate_targets(targets_proc.stdout or "")
        if not available:
            raise RuntimeError(
                "bundled yt-dlp reports no available impersonate targets — curl_cffi is "
                "missing, which means a zipapp asset was vendored instead of a standalone build"
            )

        # Prove the Pillow JPEG codec survived bundling — reading-tab cards encode
        # manga pages/covers to JPEG (services/reading/images.py). A bare import is
        # not enough: Pillow lazy-loads codecs, so a real encode+decode round-trip
        # is the only way to catch a missing JPEG plugin in the frozen bundle.
        import io

        from PIL import Image

        _jpeg_buf = io.BytesIO()
        Image.new("RGB", (8, 8)).save(_jpeg_buf, "JPEG")
        Image.open(io.BytesIO(_jpeg_buf.getvalue())).convert("RGB").load()
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"BUNDLED_SMOKE_PASS: bundled yt-dlp {ytdlp_version}")
    return 0


def _run_asr_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller ASR bundle validation.

    Triggered by ANKI_MINER_SMOKE=asr. Verifies faster-whisper and ctranslate2
    survived PyInstaller's collection by calling available() and importing
    WhisperModel. No model download — HF_HUB_OFFLINE is honoured by the caller.
    Not a CLI surface — the flag is hidden, env-var-only, and exits before any
    Qt init.
    """
    from anki_miner.services.asr import _engine

    try:
        if not _engine.available():
            raise RuntimeError("faster-whisper or ctranslate2 not importable from bundle (available() returned False)")
        # Importing the class exercises ctranslate2 native lib resolution.
        _engine.get_whisper_model_cls()
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("BUNDLED_SMOKE_PASS: asr faster_whisper+ctranslate2 resolved")
    return 0


def _run_whispercpp_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller whisper.cpp (Vulkan) validation.

    Triggered by ANKI_MINER_SMOKE=whispercpp. It exercises the REAL runtime import
    chain the Vulkan engine takes: ``import pywhispercpp.model`` (via
    get_whisper_cpp_model_cls), which transitively imports pywhispercpp.constants
    (-> platformdirs) and pywhispercpp.utils (-> requests, tqdm) at module load.
    A missing transitive runtime dep (e.g. platformdirs absent from the bundle
    env) raises here, so this catches what the ctypes-only Vulkan probe and the
    filesystem ggml-vulkan find cannot.

    When ANKI_MINER_SMOKE_GGML_MODEL points at an existing ggml acoustic file this
    ALSO registers the ggml DL backends (ensure_ggml_backends_loaded — the DEFECT-1
    fix) and constructs a pywhispercpp Model + runs a minimal decode over a short
    silent buffer. The Model is built with GPU DISABLED (context_params
    use_gpu=False) so it never enumerates Vulkan devices — on the ICD-less CI
    runner enumeration C-aborts. With GPU off the decode runs on the libggml-cpu
    backend, which is exactly what catches DEFECT 1 (no ggml_backend_load_all ->
    SIGABRT on first Model) and DEFECT 2 (libggml-cpu not bundled -> no CPU
    backend). With no model path the decode is skipped (import/loadability only)
    so CI stays green when the release job ships no ggml model.

    Not a CLI surface — the flag is hidden, env-var-only, and exits before any
    Qt init.
    """
    from anki_miner.services.asr import _engine

    model_path = os.environ.get("ANKI_MINER_SMOKE_GGML_MODEL")
    will_decode = bool(model_path and os.path.isfile(model_path))
    try:
        if not _engine.whisper_cpp_available():
            raise RuntimeError(
                "pywhispercpp + ggml-vulkan not available from bundle (whisper_cpp_available() returned False)"
            )
        # DEFECT-1 fix: register the ggml DL backends (cpu + vulkan) BEFORE importing
        # pywhispercpp, so its extension binds THIS (populated) libggml instance rather
        # than loading a second copy via its RUNPATH (else whisper reads an empty
        # registry and GGML_ASSERT(device) aborts on Model()). Only needed when we go
        # on to construct a Model.
        if will_decode:
            _engine.ensure_ggml_backends_loaded()
        # The real runtime import path: pulls pywhispercpp.model and its
        # platformdirs/requests/tqdm transitive imports.
        model_cls = _engine.get_whisper_cpp_model_cls()
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not will_decode:
        # No acoustic model available: import/loadability only, as before.
        print("BUNDLED_SMOKE_PASS: whispercpp pywhispercpp.model import resolved (decode skipped — no ggml model)")
        return 0

    assert model_path is not None  # narrowed by will_decode (env set + file exists)
    try:
        import numpy as np  # noqa: PLC0415  (numpy ships in the [asr] frozen env)

        # GPU DISABLED: use_gpu=False keeps the Model on the CPU backend and skips
        # Vulkan device enumeration, which C-aborts on the ICD-less CI runner. This
        # forces the exact libggml-cpu path DEFECT 2 must bundle; the construct is
        # the call that SIGABRTs today when DEFECT 1 regresses.
        model = model_cls(model_path, context_params={"use_gpu": False})
        # 1 s of silence @ 16 kHz float32 mono; language ja + no_context mirror the
        # real cpp decode params. No VAD (no silero file needed in the smoke).
        audio = np.zeros(16000, dtype=np.float32)
        model.transcribe(audio, language="ja", no_context=True)
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("BUNDLED_SMOKE_PASS: whispercpp pywhispercpp.model construct+decode resolved (CPU backend)")
    return 0


class _OwnerOnlyRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        Path(self.baseFilename).touch(mode=0o600, exist_ok=True)
        if os.name == "posix":
            os.chmod(self.baseFilename, 0o600)
        return super()._open()


def _configure_logging(log_path: Path) -> None:
    """Attach (or re-point) a RotatingFileHandler on the root logger.

    Called from main() so all modules that already call
    ``logging.getLogger(__name__)`` have their records captured to disk.
    Five 2 MB backup files → at most ~12 MB on disk at any time.

    Idempotent: a handler attached by a previous call is removed and replaced,
    so calling this twice — bootstrap default-path → config-path re-point (F3),
    or a second ``main()``/in-process re-launch (test/E2E harness) — never stacks
    handlers writing each record N times (F5).
    """
    log_path = Path(log_path)  # tolerate a str caller; .parent below needs a Path
    root = logging.getLogger()
    old_sinks = [existing for existing in root.handlers if getattr(existing, "_anki_miner_sink", False)]
    handler: _OwnerOnlyRotatingFileHandler | None = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = _OwnerOnlyRotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
            delay=False,
        )
        handler.setLevel(logging.DEBUG)
        handler._anki_miner_sink = True  # type: ignore[attr-defined]  # sentinel for idempotent replacement
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        root.addHandler(handler)
    except Exception:
        if handler is not None:
            handler.close()
        logger.warning("Failed to configure log at %s; keeping existing log sink", log_path, exc_info=True)
        return

    # Root logger at WARNING so third-party libs (yt-dlp, fugashi, …) only
    # write WARNING+ to the file; the project namespace gets full DEBUG coverage.
    # A record must clear both its logger's effective level AND the handler's
    # level — setting the handler to DEBUG here means the handler itself never
    # silences anything; filtering happens at the logger level.
    root.setLevel(logging.WARNING)
    logging.getLogger("anki_miner").setLevel(logging.DEBUG)
    for existing in old_sinks:
        root.removeHandler(existing)
        try:
            existing.close()
        except Exception:
            logger.warning("Failed to close replaced log sink", exc_info=True)


def get_effective_log_path() -> Path:
    """Return the active sink path, including any retained early fallback."""
    return _get_effective_log_path(ANKI_MINER_HOME / "anki_miner.log")


def _log_session_boundary() -> None:
    """Write one searchable boundary after final log-sink selection."""
    sinks = [handler for handler in logging.getLogger().handlers if getattr(handler, "_anki_miner_sink", False)]
    if sinks:
        logging.getLogger("anki_miner").setLevel(logging.DEBUG)
        for handler in sinks:
            handler.setLevel(logging.DEBUG)
    logger.info(
        "Session start session_id=%s version=%s platform=%s frozen=%s pid=%s",
        uuid.uuid4().hex[:8],
        __version__,
        platform.platform(),
        bool(getattr(sys, "frozen", False)),
        os.getpid(),
    )


def _apply_ui_zoom(config: AnkiMinerConfig | None) -> None:
    """Inject the whole-UI zoom factor as ``QT_SCALE_FACTOR``.

    Qt reads ``QT_SCALE_FACTOR`` only once, when the first ``QApplication`` is
    constructed, which is why this must run before that and why the setting is
    restart-to-apply. An explicit user-set env override wins (we never clobber
    it), and the no-op 1.0 case is left unset so the env stays clean.
    """
    if config is None:
        # Config failed to load at startup — leave the env untouched so Qt uses
        # its default 1.0 scale rather than crashing the whole app over zoom.
        return
    if "QT_SCALE_FACTOR" in os.environ:
        return
    if config.ui_zoom != 1.0:
        os.environ["QT_SCALE_FACTOR"] = repr(float(config.ui_zoom))


def _configure_qt_application_policy() -> None:
    """Set the Qt-wide policies that only take effect before ``QApplication``.

    ``AA_UseStyleSheetPropagationInWidgetStyles`` is the load-bearing one.
    Measured in Qt 6.11: *any* non-empty application stylesheet freezes palette
    propagation completely — even a rule that matches nothing at all. A
    ``QApplication.setPalette()`` afterwards reaches no already-polished widget,
    so everything ``Theme.build_palette()`` assembles stays inert until the next
    full repolish. This attribute is the only thing that unfreezes it.

    It is verified pixel-identical on the real composed window across all 29
    shipped themes, so it changes nothing on screen today. What it buys is the
    precondition for a palette-only theme apply (decision D39-C): without it,
    dropping the stylesheet re-install would simply stop themes working.
    """
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseStyleSheetPropagationInWidgetStyles, True)


def _ensure_default_dicts_root(config: AnkiMinerConfig | None) -> None:
    """Create the default ``dicts_root`` so a clean install starts valid.

    Nothing else creates ``~/.anki_miner/dicts`` before the first dictionary
    import, so on a clean install the Settings → Dictionaries storage-folder
    selector renders a red "Folder not found" border until then (Issue #100).

    Deliberately limited to the DEFAULT location: a user-relocated
    ``dicts_root`` (e.g. an external drive) that is missing/unmounted must
    stay visibly invalid — eagerly creating it here would plant a phantom
    local directory at the mount point and mask the misconfiguration.
    Creation failure only warns; it must never block boot.
    """
    if config is None:
        return
    if config.dicts_root != ANKI_MINER_HOME / "dicts":
        return
    try:
        ensure_directory(config.dicts_root)
    except OSError:
        logger.warning("Could not create default dicts_root at %s", config.dicts_root, exc_info=True)


def _acquire_instance_lock(
    lock_path: Path,
    on_conflict: Callable[[], bool],
) -> tuple[QLockFile | None, bool]:
    """Try to take the single-instance lock; ask ``on_conflict`` on failure.

    Two concurrent app processes share ``known_words.db``/``stats.db`` (both
    default rollback-journal sqlite), so a second instance risks
    "database is locked" errors and lost writes — the reporter's log shows a
    double launch (Issue #100). The guard WARNS, never hard-blocks:
    ``on_conflict()`` (production: a QMessageBox) returns True to proceed
    anyway, so a stale-detection false positive can never lock users out.

    Returns ``(lock_or_None, proceed)``. The caller must keep the returned
    lock referenced for the process lifetime; it is released on exit.
    """
    lock = QLockFile(str(lock_path))
    # A crashed instance leaves a lock QLockFile auto-reclaims once its PID is
    # gone (built-in stale detection); 0 ms try = never block startup.
    if lock.tryLock(0):
        return lock, True
    return None, on_conflict()


def _relaunch_if_requested(app: QApplication) -> None:
    """Start the replacement process, if a restart was asked for (D39b-A).

    Called only after ``app.exec()`` has returned, so the parent has already
    completed its ordinary shutdown: settings flushed, workers cancelled and
    joined, dictionaries released, config saved. The single-instance lock is
    held for the process lifetime, so it is released here — before the child is
    spawned — and the child therefore never meets a second-instance prompt and
    never shares a live sqlite handle with us.

    Failure is silent-but-logged on purpose: the user asked to restart, the app
    is already closing, and there is no window left to report into.
    """
    if not restart.restart_requested():
        return
    restart.clear_restart_request()
    program = restart.resolve_relaunch_target()
    if program is None:
        logger.warning("Restart was requested but the executable could not be resolved")
        return
    lock = getattr(app, "_instance_lock", None)
    if lock is not None:
        lock.unlock()
    if not QProcess.startDetached(str(program), []):
        logger.warning("Restart was requested but launching %s failed", program)


def _confirm_second_instance(parent: QWidget | None = None) -> bool:
    """Production ``on_conflict``: warn and let the user decide."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(QCoreApplication.translate("App", "Anki Miner Is Already Running"))
    box.setText(
        QCoreApplication.translate(
            "App",
            "Another copy of Anki Miner appears to be running. Running two copies at once "
            "can corrupt the known-words and statistics databases.\n\n"
            "Continue anyway?",
        )
    )
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Close)
    if (yes_btn := box.button(QMessageBox.StandardButton.Yes)) is not None:
        yes_btn.setText(QCoreApplication.translate("App", "Continue anyway"))
    if (close_btn := box.button(QMessageBox.StandardButton.Close)) is not None:
        close_btn.setText(QCoreApplication.translate("App", "Quit"))
    box.setDefaultButton(QMessageBox.StandardButton.Close)
    return box.exec() == QMessageBox.StandardButton.Yes


def _run_store_recovery_if_locked(
    config: AnkiMinerConfig,
    instance_lock: QLockFile | None,
    *,
    allow_collection: bool,
) -> None:
    """Run destructive startup repair only while this process owns the lock."""
    if instance_lock is None:
        logger.warning("Skipping startup store recovery because the instance lock is not held")
        return
    try:
        run_startup_store_recovery(
            config,
            allow_collection=allow_collection,
        )
    except Exception:
        logger.exception("Startup store recovery failed; continuing startup")


def _seed_file_dialog_mode(config: AnkiMinerConfig | None) -> None:
    """Seed the app-wide file-dialog mode from config (Issue #100).

    Non-native Qt dialogs are the default; ``use_native_file_dialogs`` restores
    the OS pickers. A failed config load (``None``) keeps the safe default.
    """
    if config is None:
        return
    file_dialogs.set_use_native(config.use_native_file_dialogs)


@runtime_checkable
class _HasUpdateConfig(Protocol):
    """Structural type for tab widgets that accept config updates."""

    def update_config(self, config: AnkiMinerConfig) -> None: ...


def _wire_presenter(window: "MainWindow", presenter: "GUIPresenter") -> None:
    """Connect one presenter's five output signals to the window handlers."""
    presenter.info_signal.connect(window._on_info_message)
    presenter.success_signal.connect(window._on_success_message)
    presenter.warning_signal.connect(window._on_warning_message)
    presenter.error_signal.connect(window._on_error_message)
    presenter.processing_result_signal.connect(window._on_processing_result)
    presenter.run_details_signal.connect(window._on_run_details)


def register_mining_tab(
    window: "MainWindow",
    tab: "_HasUpdateConfig",
    presenter: "GUIPresenter",
    label: str,
    *,
    extra_presenters: Sequence["GUIPresenter"] = (),
) -> None:
    """Register a mining tab and wire its presenter(s) to the main window.

    One call replaces the hand-repeated boilerplate that used to appear at
    separate sites in ``main()``:

    1. ``window.tabs.addTab(tab, label)``
    2. Five presenter-signal → ``window._on_*`` handler connections, for
       ``presenter`` and every entry in ``extra_presenters``.
    3. ``window.config_refreshed`` → ``tab.update_config`` (non-settings refreshes,
       e.g. JMdict migration finishing in the background).

    ``extra_presenters`` exists for container tabs whose children each own a
    presenter (``VideoTab``): the container is added once, but every child
    presenter still needs the window connections.

    The ``settings_tab.config_changed`` → ``tab.update_config`` connection is NOT
    wired here because ``SettingsTab`` does not yet exist when mining tabs are
    registered.  That connection is handled at ``SettingsTab`` construction time
    in ``main()`` — it iterates over ``window.tabs`` (excluding the Settings tab
    itself) to avoid repeating every tab name.

    Args:
        window: The :class:`MainWindow` instance.
        tab: The tab widget to add; must expose ``update_config``.
        presenter: The :class:`GUIPresenter` for this tab.
        label: The text label for the tab.
        extra_presenters: Additional child presenters to wire (container tabs).
    """
    assert isinstance(tab, QWidget), "tab must be a QWidget"

    window.tabs.addTab(tab, label)

    for p in (presenter, *extra_presenters):
        _wire_presenter(window, p)

    window.config_refreshed.connect(tab.update_config)


def _connect_settings_validation(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Connect the Settings tab's validation requests to the window (T-53).

    ``SettingsTab.validation_requested`` is emitted by Test Connection. It was
    declared and forwarded but never connected, so the button did nothing and
    the connection badge stuck at "Checking connection...". (The deck/note-type
    refresh buttons used to feed this too; they now drive
    ``AnkiProbeController.refresh_name_lists``.) Wiring
    it to ``_run_validation`` runs a validation pass; the result flows back
    through ``_on_validation_result``, which now updates the badge.

    Extracted from ``main()`` so the connection is unit-testable without
    standing up the whole app.
    """
    settings_tab.validation_requested.connect(window._run_validation)


def _start_stats_load(window: QWidget, stats_service: StatsService, analytics_tab: AnalyticsTab) -> None:
    """Initialize stats off-thread and refresh Analytics when ready."""

    def on_done(result: object) -> None:
        if result is not True:
            logger.warning("Stats database initialization failed")
            return
        # closeEvent's worker sweep runs on this same GUI thread and hides the
        # window first; a refresh delivered after it would spawn a fresh
        # Analytics worker nothing joins (QThread destroyed-while-running
        # abort). Visibility is therefore the closing gate.
        try:
            if not window.isVisible():
                return
        except RuntimeError:
            return
        analytics_tab.refresh_data(force=True)

    def on_error(error_message: str) -> None:
        logger.warning("Stats database initialization failed: %s", error_message)

    run_off_thread(window, stats_service.load, on_done, on_error)


# --- Resource download-button wiring (ARC-010) --------------------------------
#
# The five in-app resource downloads (ASR model, alass, CUDA pack, VAD pack,
# Vulkan model) share one connect skeleton: on the request signal, build an
# ``_on_finished`` that sets the panel status line then runs a per-tool tail,
# and hand the worker off to ``background_tasks.start_*``. Only the tail (which
# panel notify + any post-success extra) and the ``start`` call differ per tool,
# so those two closures are the sole per-tool table entries; ``_connect_download``
# owns everything shared. The named ``_connect_*_download`` builders survive as
# the unit-test seam (``test_app_*_download_wiring``) and the main() loop entries.

# request arg: model name for ASR/Vulkan, None for the 0-arg pack buttons.
_DownloadStart = Callable[[object, Callable[[str], None], Callable[[bool, str], None]], None]
_DownloadTail = Callable[[object, bool, str], None]


def _connect_download(
    requested: pyqtBoundSignal,
    *,
    set_status: Callable[[str], None],
    start: _DownloadStart,
    on_finished_tail: _DownloadTail,
) -> None:
    """Wire one resource download button (see the ARC-010 note above).

    ``requested`` may be 0-arg (pack buttons) or 1-arg (ASR/Vulkan model name);
    the model name, when present, flows into both ``start`` and
    ``on_finished_tail``. ``set_status`` receives every status line and the
    final message; ``on_finished_tail`` runs after it on completion.
    """

    def _on_requested(*args: object) -> None:
        request_arg = args[0] if args else None

        def _on_finished(ok: bool, message: str) -> None:
            set_status(message)
            on_finished_tail(request_arg, ok, message)

        start(request_arg, set_status, _on_finished)

    requested.connect(_on_requested)


def _connect_asr_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download model" button to the ASR download worker.

    Status flows back to the panel's status label; on finish the panel's
    downloaded-state label is refreshed (clearing the in-flight guard) so it
    reflects the new on-disk state.
    """

    def _tail(request_arg: object, ok: bool, message: str) -> None:
        settings_tab.subtitles_panel.notify_asr_download_finished(str(request_arg), window.get_config().asr_models_root)

    def _start(request_arg: object, on_status: Callable[[str], None], on_finished: Callable[[bool, str], None]) -> None:
        window.background_tasks.start_asr_model_download(
            str(request_arg), window.get_config().asr_models_root, on_status, on_finished
        )

    _connect_download(
        settings_tab.asr_download_requested,
        set_status=settings_tab.set_asr_model_status,
        start=_start,
        on_finished_tail=_tail,
    )


def _connect_alass_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download alass" button to the install worker.

    Status flows back to the panel; on a successful install the resolver's
    cached PATH-miss is dropped and config is re-propagated via
    ``config_refreshed`` so the (non-Settings) Retime tab re-runs its
    availability guard and enables. Without that, the download→retime happy
    path stays disabled until a Settings save or app restart.
    """

    def _tail(request_arg: object, ok: bool, message: str) -> None:
        settings_tab.subtitles_panel.notify_alass_download_finished()
        if ok:
            alass_resolver._clear_cache()
            window.config_refreshed.emit(window.get_config())

    def _start(request_arg: object, on_status: Callable[[str], None], on_finished: Callable[[bool, str], None]) -> None:
        window.background_tasks.start_alass_download(window.get_config().bin_root, on_status, on_finished)

    _connect_download(
        settings_tab.alass_download_requested,
        set_status=settings_tab.set_alass_status,
        start=_start,
        on_finished_tail=_tail,
    )


def _connect_cuda_pack_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download GPU acceleration" button to the worker.

    Status flows back to the panel; on finish the panel's in-flight guard is
    cleared and its installed-state label refreshed via
    ``notify_cuda_pack_download_finished``.
    """

    def _tail(request_arg: object, ok: bool, message: str) -> None:
        settings_tab.subtitles_panel.notify_cuda_pack_download_finished(window.get_config().cuda_libs_root)

    def _start(request_arg: object, on_status: Callable[[str], None], on_finished: Callable[[bool, str], None]) -> None:
        window.background_tasks.start_cuda_pack_download(window.get_config().cuda_libs_root, on_status, on_finished)

    _connect_download(
        settings_tab.cuda_pack_download_requested,
        set_status=settings_tab.set_cuda_pack_status,
        start=_start,
        on_finished_tail=_tail,
    )


def _connect_vad_pack_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download silence removal" button to the worker.

    Status flows back to the panel; on finish the panel's in-flight guard is
    cleared and its installed-state label refreshed via
    ``notify_vad_pack_download_finished``.
    """

    def _tail(request_arg: object, ok: bool, message: str) -> None:
        settings_tab.subtitles_panel.notify_vad_pack_download_finished(window.get_config().onnx_pack_root)

    def _start(request_arg: object, on_status: Callable[[str], None], on_finished: Callable[[bool, str], None]) -> None:
        window.background_tasks.start_vad_pack_download(window.get_config().onnx_pack_root, on_status, on_finished)

    _connect_download(
        settings_tab.vad_pack_download_requested,
        set_status=settings_tab.set_vad_pack_status,
        start=_start,
        on_finished_tail=_tail,
    )


def _connect_vulkan_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download Vulkan model" button to the worker.

    One action fetches BOTH the ggml acoustic model and the Silero VAD. Status
    flows back to the panel; on finish the panel's in-flight guard is cleared and
    its installed-state label refreshed via ``notify_vulkan_download_finished``,
    which is passed the verbatim ``(ok, message)`` (not a root path).
    """

    def _tail(request_arg: object, ok: bool, message: str) -> None:
        settings_tab.subtitles_panel.notify_vulkan_download_finished(ok, message)

    def _start(request_arg: object, on_status: Callable[[str], None], on_finished: Callable[[bool, str], None]) -> None:
        window.background_tasks.start_vulkan_download(
            str(request_arg), window.get_config().asr_models_root, on_status, on_finished
        )

    _connect_download(
        settings_tab.vulkan_model_download_requested,
        set_status=settings_tab.set_vulkan_status,
        start=_start,
        on_finished_tail=_tail,
    )


_in_excepthook = False


def _install_excepthook(app: QApplication, *, fail_fast: bool = False) -> None:
    """Route unhandled GUI-thread exceptions to the log + a dialog instead of abort().

    Since PyQt 5.5 an exception that escapes a Qt slot calls ``qFatal``/``abort``
    (the "Aborted (core dumped)" the trailing-space batch bug produced). Installing
    our own ``sys.excepthook`` intercepts it, logs to ``anki_miner.log``, shows a
    dialog, and keeps the event loop alive. This trades a hard crash for
    log-and-continue — the widget tree may be in an inconsistent state afterward,
    an acceptable tradeoff for a desktop GUI that would otherwise vanish silently.

    The CRITICAL log is unconditional; the dialog is guarded three ways because an
    unreliable safety net is worse than none:

    * reentrancy — ``QMessageBox.exec`` spins a nested event loop; an exception it
      dispatches would re-enter the hook and stack dialogs forever;
    * thread affinity — a ``QMessageBox`` built off the GUI thread is undefined
      behavior; worker ``QThread.run`` bodies already wrap themselves, so any
      exception reaching here off-thread is logged only;
    * live app — after ``QApplication`` teardown, constructing a dialog faults.
    """

    def _hook(exc_type, exc_value, exc_tb):
        global _in_excepthook
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
        if fail_fast:
            app.exit(1)
            return
        instance = QApplication.instance()
        if _in_excepthook or instance is None or QThread.currentThread() != instance.thread():
            return
        _in_excepthook = True
        try:
            log_path = get_effective_log_path()
            box = QMessageBox(app.activeWindow())
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle(QCoreApplication.translate("app", "Anki Miner — Unexpected Error"))
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(
                tr_format(
                    QCoreApplication.translate(
                        "app",
                        "%1: %2\n\nVersion: %3\nPlatform: %4\nLog file: %5",
                    ),
                    exc_type.__name__,
                    exc_value,
                    __version__,
                    platform.platform(),
                    log_path,
                )
            )
            open_button = box.addButton(
                QCoreApplication.translate("MainWindow", "Open Log Folder"),
                QMessageBox.ButtonRole.ActionRole,
            )
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if box.clickedButton() is open_button:
                open_log_folder(log_path)
        except Exception:
            logger.exception("Failed to display error dialog for unhandled exception")
        finally:
            _in_excepthook = False

    sys.excepthook = _hook


def _rollback_workers_on_startup_fault(fn: Callable[[], None]) -> Callable[[], None]:
    """Cancel and join constructor-started workers before startup unwinds."""

    @wraps(fn)
    def wrapped() -> None:
        try:
            fn()
        except Exception:
            laggards = join_all_off_thread_workers()
            for worker in laggards:
                try:
                    if still_running(worker):
                        worker.wait()
                except RuntimeError:
                    pass
            if os.environ.get("ANKI_MINER_SMOKE") == "installer":
                logger.critical("Installer smoke failed during startup", exc_info=True)
                raise SystemExit(1) from None
            raise

    return wrapped


def compose_main_window(
    config: AnkiMinerConfig,
    *,
    suppress_optional_startup: bool = False,
) -> ComposedApp:
    """Build the main window and its production tab/service graph."""
    # Create main window
    window = MainWindow(config)

    # Initialize stats service for analytics. ``.load()`` opens the SQLite
    # file; defer to after window.show() so the empty shell paints first
    # and the user sees feedback while disk I/O finishes.
    stats_service = StatsService(window.get_config().stats_db_path)

    # Create per-child presenters and progress callbacks to avoid cross-tab signal
    # pollution (Single/Batch wire presenter signals into their own log widgets).
    # register_mining_tab() handles: addTab + six presenter-signal connections per
    # presenter + window.config_refreshed → tab.update_config; the container fans
    # config out to its children. The YouTube child keeps the lazy-processor
    # startup optimization (processor=None inside VideoTab) so the dictionary
    # chain — which opens every installed dict's sqlite — does not block the
    # initial window paint.
    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    video_tab = VideoTab(
        window.get_config(),
        episode_presenter=episode_presenter,
        episode_progress=episode_progress,
        batch_presenter=batch_presenter,
        batch_progress=batch_progress,
        youtube_presenter=youtube_presenter,
        youtube_fetcher=youtube_fetcher,
        stats_service=stats_service,
    )
    register_mining_tab(
        window,
        video_tab,
        episode_presenter,
        QCoreApplication.translate("MainWindow", "Video"),
        extra_presenters=(batch_presenter, youtube_presenter),
    )

    deck_builder_presenter = GUIPresenter(window)
    deck_builder_progress = GUIProgressCallback(window)
    deck_builder_tab = DeckBuilderTab(
        window.get_config(),
        deck_builder_presenter,
        deck_builder_progress,
        stats_service=stats_service,
    )
    register_mining_tab(
        window, deck_builder_tab, deck_builder_presenter, QCoreApplication.translate("MainWindow", "Deck Builder")
    )

    # Audiobook tab (Issue #71). Same lazy-processor pattern as YouTube:
    # processor=None defers the dictionary-chain build to the first Mine
    # click; stats_service is threaded through so sessions land in analytics.
    audiobook_presenter = GUIPresenter(window)
    audiobook_tab = AudiobookTab(
        config=window.get_config(),
        processor=None,
        presenter=audiobook_presenter,
        stats_service=stats_service,
    )
    register_mining_tab(
        window, audiobook_tab, audiobook_presenter, QCoreApplication.translate("MainWindow", "Audiobooks")
    )

    # The two list queues publish their runs to the window's task registry, so
    # each one's current-job strip has a snapshot to render and the status bar
    # can name a queue run the user has navigated away from. Worker lifetime is
    # unaffected: it stays with the tab. Every other screen that runs work is
    # bound in one place further down, once its tab exists.
    video_tab.youtube_tab.bind_task_registry(window.task_registry)
    audiobook_tab.bind_task_registry(window.task_registry)

    # Reading tab: a container nesting the Manga and Novels sub-tabs. Each child
    # owns its own worker/processor lifecycle and defers its dictionary-chain
    # build to the first Mine click (no prebuilt processor is shared across the
    # two concurrently-runnable sub-tabs, so the container takes no processor).
    # ONE presenter is shared by both children — safe because the reading
    # sub-tabs never wire presenter signals into their log widgets (presenter
    # output goes to the window status bar / dialogs only). stats_service is
    # threaded through so sessions land in analytics; like Audiobook, the
    # children build their own progress callback per run, so no
    # GUIProgressCallback is wired here.
    reading_presenter = GUIPresenter(window)
    reading_tab = ReadingTab(
        config=window.get_config(),
        presenter=reading_presenter,
        stats_service=stats_service,
    )
    register_mining_tab(window, reading_tab, reading_presenter, QCoreApplication.translate("MainWindow", "Reading"))

    # Analytics tab (non-mining: no presenter, no update_config wiring)
    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, QCoreApplication.translate("MainWindow", "Analytics"))

    # Utilities tab (non-mining: no presenter). Nests Generate (SubtitleCreationTab)
    # and Retime (SubtitleRetimeTab) as inner tabs; will host further tools over
    # time. It DOES need config updates so an ASR model switch in Settings reaches
    # the model-downloaded guard and the worker: config_changed is auto-wired by
    # the loop below (it has update_config); config_refreshed is wired explicitly
    # near the SettingsTab refresh connection.
    subtitles_tab = SubtitlesTab(
        window.get_config(),
        suppress_optional_startup=suppress_optional_startup,
    )
    window.tabs.addTab(subtitles_tab, QCoreApplication.translate("MainWindow", "Utilities"))

    settings_tab = SettingsTab(
        window.get_config(),
        commit_config=window.update_config,
        suppress_optional_startup=suppress_optional_startup,
    )
    window.background_tasks.set_dictionary_mutation_panel(settings_tab.dictionary_panel)
    settings_tab.set_dictionary_mutation_preflight(window.prepare_dictionary_mutation)
    # MainWindow stamps + saves the config, then config_refreshed fans the
    # POST-SAVE committed object out to every tab. This prevents a scan worker's
    # stale pre-save config snapshot from regaining authority after save.
    settings_tab.config_changed.connect(lambda cfg: window.update_config(cfg))
    # Make Test Connection live: it emits SettingsTab.validation_requested,
    # which was previously connected to nothing (T-53). Routing it to
    # _run_validation also drives the Anki connection badge via
    # _on_validation_result.
    _connect_settings_validation(window, settings_tab)
    # yt-dlp manual update: the YouTube panel's "Update yt-dlp now" button →
    # forced background update. Results flow back to MainWindow (status bar /
    # error dialog) and to the panel's status line.
    settings_tab.ytdlp_update_requested.connect(
        lambda: window.background_tasks.start_ytdlp_update(window.get_config(), force=True)
    )
    window.background_tasks.ytdlp_update_result.connect(settings_tab.set_ytdlp_status_from_result)

    # Resource download buttons (ASR model, alass, CUDA pack, VAD pack, Vulkan
    # model): each Subtitles-panel "Download …" button hands off to a background
    # worker and refreshes the panel on finish. All five share the connect
    # skeleton in _connect_download; the per-tool builders carry the differences.
    for _connect in (
        _connect_asr_download,
        _connect_alass_download,
        _connect_cuda_pack_download,
        _connect_vad_pack_download,
        _connect_vulkan_download,
    ):
        _connect(window, settings_tab)
    # Wire indexed-resource mutation hooks so replacing or deleting a store
    # releases cached readers across every tab first.
    settings_tab.dictionary_panel.set_release_callback(window.release_dictionary_resources)
    settings_tab.frequency_panel.set_release_callback(window.release_dictionary_resources)
    settings_tab.audio_panel.set_release_callback(window.release_dictionary_resources)
    settings_tab.pitch_panel.set_release_callback(window.release_dictionary_resources)
    # Favorites-list edits in the UI panel must repopulate the top-right combo
    # immediately; the panel doesn't know about the header so the wiring lives
    # here. Active-theme changes from the panel must update the selected entry
    # in the combo without re-emitting `theme_changed` (the theme is already
    # applied — re-emitting would loop back through `_on_theme_changed`).
    settings_tab.ui_panel.favorites_changed.connect(window.header.refresh_favorites)
    settings_tab.ui_panel.state_changed.connect(lambda *_: window.header.update_theme_selector())
    # "Manage Profiles…" is re-emitted by the tab rather than handled there: the
    # window owns the dialog, because a switch reloads every panel in this tab
    # from the incoming config. Same handler as the header's combo sentinel.
    settings_tab.manage_profiles_requested.connect(window._open_profile_manager)
    window.tabs.addTab(settings_tab, QCoreApplication.translate("MainWindow", "Settings"))

    # Non-Settings config refreshes (e.g. JMdict migration finishing in the
    # background) must propagate to SettingsTab so its panels don't go stale.
    # Mining tabs are already wired via register_mining_tab's config_refreshed
    # connection.
    window.config_refreshed.connect(settings_tab.update_config)
    # The Subtitles tab is non-mining (not registered via register_mining_tab),
    # so its config_refreshed connection is wired here too. SubtitlesTab.update_config
    # fans out to both Generate and Retime children.
    window.config_refreshed.connect(subtitles_tab.update_config)
    # The Condense tab persists its inline run options (padding/offset/format/
    # write-subs) by emitting config_changed; route it through window.update_config
    # so condenser_* land in gui_config.json and survive restart.
    subtitles_tab.condense_tab.config_changed.connect(window.update_config)

    # --- task-registry publication (W5) -----------------------------------
    # Until now only the two list queues published, so only they had the
    # ticking wait clock and the "Finishing <phase>" explanation behind Cancel,
    # and only their pinned bars showed a stage. Every remaining screen that
    # runs work is bound here, in one place, once its tab exists. Binding is
    # inert on a screen that declares no TASK_ID, and it never touches worker
    # ownership -- that stays on the screen that started the run.
    for screen in (
        video_tab.single_tab,
        video_tab.batch_tab,
        reading_tab.manga_tab,
        reading_tab.novels_tab,
        reading_tab.subtitles_tab,
        reading_tab.text_tab,
        subtitles_tab.generate_tab,
        subtitles_tab.retime_tab,
        subtitles_tab.condense_tab,
        subtitles_tab.backfill_tab,
    ):
        screen.bind_task_registry(window.task_registry)
    # --- end task-registry publication ------------------------------------

    # All tabs are now registered — create the count-driven Ctrl+N shortcuts.
    # This must come AFTER all addTab calls so self.tabs.count() is final.
    window.setup_tab_shortcuts()

    # Reopen where the last session ended (D7). Also AFTER every addTab: the
    # saved route is addressed by stable key, so the tab it names has to be
    # registered before it can be resolved. Still before show(), so the window
    # is never painted at one size and then jumped to another.
    window.restore_session_state()

    return ComposedApp(window=window, stats_service=stats_service, analytics_tab=analytics_tab)


def offer_recovery(window: MainWindow) -> bool:
    """Ask once whether to pick up what the last session left (D16-C).

    Called after translators are installed and every tab is registered: the
    question is translated, and "Restore" has to have somewhere to put the rows.
    Restore refills the queues and leaves the partial downloads on disk for the
    next transfer to continue — that transfer still has to prove to itself that
    the artifact is unchanged, and silently starts over if it cannot. Discard
    removes both, under the runtime-state roots only.

    Returns:
        True when the user chose Restore.
    """
    inventory = recovery_controller.take_inventory()
    if not inventory:
        return False
    if not RecoveryController(window).offer(inventory):
        recovery_controller.discard_all()
        return False
    window.restore_queue_snapshots()
    return True


class _DeferredDeleteWatcher(QObject):
    """A global event filter that counts ``DeferredDelete`` deliveries.

    Installed on the application itself (not any one widget), so it sees
    every ``DeferredDelete`` sent to every object in the app during a
    ``sendPostedEvents`` pass -- Qt delivers app-installed filters every event
    for every object, ahead of the object's own handler. Never blocks
    delivery (always returns ``False``).
    """

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def eventFilter(self, obj: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 - Qt override
        if event is not None and event.type() == QEvent.Type.DeferredDelete:
            self.count += 1
        return False


def _drain_deferred_deletes(app: QApplication, *, max_passes: int = 8) -> None:
    """Flush pending ``DeferredDelete`` events over ``max_passes`` fixed passes.

    Deleting a widget can post fresh ``DeferredDelete`` events for its own
    children, so a single ``sendPostedEvents`` pass does not always catch the
    tail -- see the installer-smoke exit path this backs.

    ``max_passes`` is an unfalsifiable guess by construction (there is no Qt
    API to ask "is the deferred-delete queue empty"), so an early return the
    moment a pass delivers nothing looks like the obvious tightening -- it was
    tried here and is WRONG: on the installer-smoke failure path (``fail()``
    calls ``app.exit()`` on the very first tick, before ``window.close()``
    ever runs) an early return reliably reintroduces the SIGSEGV this whole
    drain exists to prevent, reproduced across six consecutive runs. Loop the
    full fixed count unconditionally, as before; ``_DeferredDeleteWatcher``
    only makes the cap's outcome observable -- a DEBUG log if deletes were
    still landing on the very last pass -- it does not shorten the loop.

    ``app.processEvents()`` is likewise load-bearing here, not a redundant
    belt-and-suspenders call: dropping it (even with the full fixed-pass count
    kept) reproduces the same SIGSEGV, six-for-six. ``sendPostedEvents(None,
    DeferredDelete)`` alone is not sufficient on this path.
    """
    watcher = _DeferredDeleteWatcher()
    app.installEventFilter(watcher)
    try:
        for _ in range(max_passes):
            watcher.count = 0
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            app.processEvents()
        if watcher.count:
            logger.debug(
                "Deferred-delete drain hit its %d-pass cap with %d delete(s) still pending at exit",
                max_passes,
                watcher.count,
            )
    finally:
        app.removeEventFilter(watcher)


def _schedule_installer_smoke(app: QApplication, window: MainWindow) -> None:
    """Run installed-artifact assertions over two event-loop ticks."""

    def fail(stage: str) -> None:
        logger.critical("Installer smoke failed during %s", stage, exc_info=True)
        # Close the window here too, exactly as the success path does before
        # finish(). Without it the whole MainWindow widget tree is still ALIVE at
        # interpreter exit, and PyQt/sip's exit-time cleanup walks it and
        # segfaults — the process dies with SIGSEGV (139) instead of the exit 1
        # the Windows installer smoke asserts, and the CRITICAL log above is the
        # last thing anyone sees. _drain_deferred_deletes below does not cover
        # this: the fault is a live window, not a pending delete, so the drain
        # completes with an empty queue and the crash still happens.
        #
        # Latent and allocation-sensitive: it reproduces only once the process
        # crosses some threshold, so an unrelated module-level addition anywhere
        # in the import graph can surface it (bisected to a no-op dataclass, 4/4).
        try:
            window.close()
        except Exception:
            logger.debug("installer smoke: window.close() on the failure path raised", exc_info=True)
        app.exit(1)

    def finish() -> None:
        try:
            required_files = (
                ANKI_MINER_HOME / "gui_config.json",
                ANKI_MINER_HOME / "anki_miner.log",
            )
            missing = [str(path) for path in required_files if not path.is_file()]
            dicts_root = ANKI_MINER_HOME / "dicts"
            if not dicts_root.is_dir():
                missing.append(str(dicts_root))
            if missing:
                raise RuntimeError(f"installed smoke outputs missing: {', '.join(missing)}")

            result_value = os.environ.get("ANKI_MINER_SMOKE_RESULT")
            if not result_value:
                raise RuntimeError("ANKI_MINER_SMOKE_RESULT is required")
            result_path = Path(result_value)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_write_path(result_path) as staged:
                staged.write_bytes(f"ANKI_MINER_INSTALLER_READY {__version__}\n".encode())
        except Exception:
            fail("post-close checks")
            return
        app.exit(0)

    def validate_and_close() -> None:
        try:
            platform_name = app.platformName()
            frozen_windows = bool(getattr(sys, "frozen", False) and sys.platform == "win32")
            expected_platform = "windows" if frozen_windows else os.environ.get("QT_QPA_PLATFORM", platform_name)
            if platform_name != expected_platform:
                raise RuntimeError(f"Qt platform mismatch: expected {expected_platform!r}, got {platform_name!r}")

            expected_titles = [
                QCoreApplication.translate("MainWindow", "Video"),
                QCoreApplication.translate("MainWindow", "Deck Builder"),
                QCoreApplication.translate("MainWindow", "Audiobooks"),
                QCoreApplication.translate("MainWindow", "Reading"),
                QCoreApplication.translate("MainWindow", "Analytics"),
                QCoreApplication.translate("MainWindow", "Utilities"),
                QCoreApplication.translate("MainWindow", "Settings"),
            ]
            actual_titles = [window.tabs.tabText(index) for index in range(window.tabs.count())]
            if actual_titles != expected_titles:
                raise RuntimeError(f"main tab mismatch: expected {expected_titles!r}, got {actual_titles!r}")

            expected_version = os.environ.get("ANKI_MINER_SMOKE_EXPECTED_VERSION")
            if expected_version and __version__ != expected_version:
                raise RuntimeError(f"version mismatch: expected {expected_version!r}, got {__version__!r}")

            from anki_miner.services.tagger import get_shared_tagger

            source = "日本語"
            reconstructed = "".join(str(token.surface) for token in get_shared_tagger()(source))
            if reconstructed != source:
                raise RuntimeError(f"tagger surface mismatch: expected {source!r}, got {reconstructed!r}")

            if frozen_windows:
                from anki_miner.gui import launch

                if not launch.TRUSTSTORE_INJECTED:
                    raise RuntimeError("Windows trust store was not injected")

            if not window.close():
                raise RuntimeError("main window refused installer-smoke close")
            QTimer.singleShot(0, finish)
        except Exception:
            fail("GUI checks")

    QTimer.singleShot(0, validate_and_close)


@_rollback_workers_on_startup_fault
def main():
    """Launch the Anki Miner GUI application.

    This is the one place that decides what happens in what order at startup.
    Several independent features each contribute a small hook here rather than a
    boot state machine of their own, and the sequence below is the composition
    of all of them. ``tests/unit/test_boot_order.py`` pins it.

    1. Pre-Qt: scrub the bootloader env, attach the log sink, decode the config,
       and seed the three settings Qt only reads once — dialog mode, default
       ``dicts_root``, and ``QT_SCALE_FACTOR`` (whole-UI zoom is therefore
       restart-to-apply by nature).
    2. ``QApplication``, then the crash net, then ``initialize_application_fonts``
       — before the first widget, so every widget is measured against the face it
       will be drawn with (D44-B).
    3. Translators, also before the first widget: widgets capture their ``tr()``
       strings at construction and language is restart-to-apply. The theme is
       seeded next; a broken local theme must not block an unstyled GUI.
    4. The single-instance lock, and the destructive store repair that is only
       safe while we hold it. It is taken *after* the application and the
       translators because its conflict prompt is a translated modal — the one
       place the ideal "lock first" order is not available — and *before* any
       window is composed, which is what the guard is actually for.
    5. ``compose_main_window``: build the seven tabs, bind them to the task
       registry, then restore the saved geometry and route (D7). Restoration is
       last inside that call because the route is addressed by stable key, and
       still ahead of ``show()`` so the window is never painted at one size and
       then jumped to another. W1-T7's queue/download **Restore or Discard**
       offer belongs at the end of this step, for the same two reasons.
    6. ``commit_boot``: reconcile settings profiles, stamp the version, then
       either offer first-run setup or release the optional startup work behind
       it (D26). Boot used to start the JMdict migration and let the wizard
       cancel it two lines later. Every optional job — validation, the update
       checks, the migration, the stale-dictionary scan and the prewarm — is
       started from that one gate, never from here, or the wizard could not be
       made to precede it.
    7. ``show()``, then the two things that need a painted window: the stall
       watchdog and the stats load.
    8. ``app.exec()``. Its result is captured rather than passed straight to
       ``sys.exit`` so a requested restart (D39b) can release the instance lock
       and start the replacement only after the loop has returned and this
       process is finished with its stores.
    """
    _scrub_pyinstaller_env()
    installer_smoke = os.environ.get("ANKI_MINER_SMOKE") == "installer"

    # Env-var-gated smoke path (PyInstaller bundled-binary validation).
    # Runs before Qt init so headless CI can verify yt-dlp extractor
    # bundling without spinning up a display.
    if os.environ.get("ANKI_MINER_SMOKE") == "youtube":
        sys.exit(_run_bundled_smoke())

    if os.environ.get("ANKI_MINER_SMOKE") == "asr":
        sys.exit(_run_asr_bundled_smoke())

    if os.environ.get("ANKI_MINER_SMOKE") == "whispercpp":
        sys.exit(_run_whispercpp_bundled_smoke())

    # Env-var-gated ASR Vulkan device probe. The parent process
    # (_engine.vulkan_device_count) spawns a frozen bundle with this flag set so
    # the cold ctypes call into ggml-vulkan runs in a throwaway child — a broken
    # Vulkan driver can C-abort uncatchably, and isolating it here means the abort
    # kills only this child. Must run before any Qt init. Hidden, env-var-only.
    if os.environ.get("ANKI_MINER_ASR_VULKAN_PROBE"):
        from anki_miner.services.asr import _vulkan_probe

        raise SystemExit(_vulkan_probe.main())

    # Env-var-gated libmpv bundle probe (bundle_smoke.sh). Loads the bundled
    # libmpv through mpv_loader's resolution order and constructs a display-free
    # core (vo=null/ao=null) — proves the shared library + its dependency
    # closure actually dlopen inside the frozen bundle. Must run before Qt init.
    if os.environ.get("ANKI_MINER_MPV_PROBE"):
        from anki_miner.utils import mpv_loader

        raise SystemExit(mpv_loader.mpv_probe_main())

    # Attach the rotating file handler to the DEFAULT path before loading config
    # so config-load diagnostics — including the OVH-001 .bak-recovery warnings
    # emitted inside load_config — are captured: those warnings fire as soon as a
    # handler exists, so attaching here (before the load) is what makes them land
    # in the file rather than going nowhere (F3).
    # GUIConfigManager has no Qt dependency, so it can run before QApplication.
    _default_log_path = ANKI_MINER_HOME / "anki_miner.log"
    try:
        _configure_logging(_default_log_path)
    except Exception:
        logger.exception("Failed to configure startup log; continuing with stderr logging")
    try:
        _early_config, _allow_store_collection = GUIConfigManager.load_config_with_provenance()
        _log_path = _early_config.log_path
    except Exception:
        # Never leave _early_config unbound (would NameError at the zoom call
        # and every later read) — fall back to defaults so startup can proceed.
        if installer_smoke:
            raise
        logger.exception("Failed to load config at startup; using default config")
        _early_config = create_default_config()
        _allow_store_collection = False
        _log_path = _default_log_path
    # Honour a user-customised log_path by re-pointing the handler (idempotent,
    # so no duplicate sink). No-op in the common case where it equals the default.
    if _log_path != _default_log_path:
        try:
            _configure_logging(_log_path)
        except Exception:
            logger.exception("Failed to configure custom log path; keeping startup logger")

    _log_session_boundary()

    # Clean-install nicety: make the default dicts_root exist before any
    # settings UI validates it (Issue #100 red-border state).
    _ensure_default_dicts_root(_early_config)

    # File pickers default to Qt's non-native dialogs (Issue #100 freeze).
    _seed_file_dialog_mode(_early_config)

    # Whole-UI zoom: must be set before QApplication is constructed (Qt reads
    # QT_SCALE_FACTOR once, at construction). Restart-to-apply by nature.
    _apply_ui_zoom(_early_config)

    _configure_qt_application_policy()

    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName("Anki Miner")
    app.setOrganizationName("AnkiMiner")

    # Install the crash net before any widget is built so exceptions escaping a
    # slot during tab construction are caught too (a bad path in a startup slot
    # would otherwise abort the whole process — the trailing-space batch bug).
    _install_excepthook(app, fail_fast=installer_smoke)

    # Resolve the platform's interface, fixed-width and Japanese faces before the
    # first widget exists (decision D44-B), so every widget is built and measured
    # against the font it will actually be drawn with. Fail-soft by design: a
    # missing bundled fallback logs and leaves Qt's own choice in place.
    initialize_application_fonts(app)

    # The accent focus ring is for keyboard users, and Qt's `:focus` fires on a
    # mouse click too, so clicking a pane boxed it. Installed on the application,
    # before the first widget: every dialog built later is covered without
    # knowing this exists.
    install_keyboard_focus_ring(app)

    # Set application icon
    icon_path = get_resource_dir() / "icons" / "anki_miner.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Install UI translators BEFORE any widget is built — widgets capture their
    # tr() strings at construction time, and language is restart-to-apply (no
    # live retranslateUi). Stash on `app` so the translators outlive this call.
    app._translators = install_translators(app, _early_config.ui_language)  # type: ignore[attr-defined]

    # Seed the theme singleton from the single decoded startup config. Optional
    # local theme data must never block construction of the unstyled GUI.
    try:
        Theme.initialize(
            active=_early_config.theme,
            favorites=_early_config.theme_favorites,
            user_dir=_early_config.themes_root,
            font_scale=_early_config.ui_font_scale,
        )
        Theme.apply_to_app(app)
    except Exception:
        if installer_smoke:
            raise
        logger.exception("Failed to initialize theme; continuing with Qt defaults")

    # Single-instance guard (Issue #100: double launch observed; two processes
    # contend on the shared sqlite DBs). Warn-not-block; skipped in the
    # installer smoke (no modal may ever open there). Keep the lock object
    # referenced on `app` so it lives (and auto-releases) with the process.
    if not installer_smoke:
        _instance_lock, _proceed = _acquire_instance_lock(ANKI_MINER_HOME / "instance.lock", _confirm_second_instance)
        if not _proceed:
            return
        app._instance_lock = _instance_lock  # type: ignore[attr-defined]
        _run_store_recovery_if_locked(
            _early_config,
            _instance_lock,
            allow_collection=_allow_store_collection,
        )

    composed = compose_main_window(
        _early_config,
        suppress_optional_startup=installer_smoke,
    )
    window = composed.window
    stats_service = composed.stats_service
    analytics_tab = composed.analytics_tab

    # Full widget composition and required version save now form one commit
    # boundary. No startup worker is started before this returns successfully.
    window.commit_boot(suppress_optional=installer_smoke)

    # Offer what the last session left behind (D16-C). After translators and
    # every addTab, so the question is translated and Restore has somewhere to
    # put the rows; skipped in the installer smoke, where no modal may open.
    if not installer_smoke:
        try:
            offer_recovery(window)
        except Exception:
            logger.exception("Could not offer the previous session's downloads and queues")

    # Show window first so the user sees the UI immediately. The stats DB open
    # runs off-thread below. The YouTube tab's episode processor is built even
    # lazier — on first Mine click — because the dictionary chain dominates
    # startup cost.
    if installer_smoke:
        app.setQuitOnLastWindowClosed(False)
    window.show()

    if installer_smoke:
        _schedule_installer_smoke(app, window)
        smoke_result = app.exec()
        # The failure branch of _schedule_installer_smoke calls app.exit() on
        # the very first event-loop tick, before window.close() ever runs --
        # so none of MainWindow's torn-down widgets get the extra loop
        # iterations that the success path's finish() gets for free. The
        # theme gallery alone deleteLater()s dozens of QObjects per rebuild
        # (settings-tab construction rebuilds it twice more after the
        # initial, empty one); left pending, PyQt/sip's interpreter-exit
        # wrapper cleanup walks into one and segfaults (SIGSEGV in
        # cleanup_qobject, confirmed with gdb). See _drain_deferred_deletes.
        _drain_deferred_deletes(app)
        sys.exit(smoke_result)

    # Install the main-thread stall watchdog: a heartbeat QTimer + daemon
    # monitor that logs a WARNING with the GUI stack whenever the event loop
    # blocks past the threshold. Stored on the window so it isn't GC'd; its
    # stop() is hooked into MainWindow.closeEvent (daemon=True is the backstop).
    install_stall_watchdog(window)

    _start_stats_load(window, stats_service, analytics_tab)

    # The tagger/dictionary prewarm is NOT started here. It is optional startup
    # work like every other, so it waits behind first-run setup with the rest —
    # see MainWindow._start_post_setup_boot_once.

    # Run event loop. The exit code is captured rather than handed straight to
    # sys.exit so a requested restart (D39b-A) can start the replacement only
    # after the loop has returned and this process is done with its stores.
    exit_code = app.exec()
    _relaunch_if_requested(app)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
