"""Main GUI application entry point."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Protocol, runtime_checkable

from PyQt6.QtCore import QCoreApplication, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.gui.i18n import install_translators
from anki_miner.gui.main_window import MainWindow
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.service_factory import create_youtube_fetcher
from anki_miner.gui.utils.stall_watchdog import install_stall_watchdog
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from anki_miner.services.stats_service import StatsService
from anki_miner.utils import alass_resolver

logger = logging.getLogger(__name__)


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


def _run_bundled_smoke() -> int:
    """Env-var-gated smoke path for PyInstaller bundle validation.

    Triggered by ANKI_MINER_SMOKE=youtube. Verifies yt-dlp and its extractor
    registry survived PyInstaller's collect_all by walking the registry
    offline and resolving the Youtube extractor. No network, no YoutubeDL,
    no bot challenge. Not a CLI surface — the flag is hidden, env-var-only,
    and exits before any Qt init.
    """
    try:
        from yt_dlp.extractor import (  # type: ignore[import-untyped]
            gen_extractors,
            get_info_extractor,
        )

        extractor_count = sum(1 for _ in gen_extractors())
        if extractor_count < 1000:
            raise RuntimeError(
                f"extractor registry shrunk: {extractor_count} < 1000 "
                "(expected ~1600; PyInstaller collect_all may have dropped extractors)"
            )

        youtube_ie = get_info_extractor("Youtube")
        if youtube_ie is None:
            raise RuntimeError("Youtube extractor not resolvable from bundle")

        if not youtube_ie.suitable("https://www.youtube.com/watch?v=9bZkp7q19f0"):
            raise RuntimeError("YoutubeIE.suitable() rejected a canonical YouTube URL")
    except Exception as exc:
        print(f"BUNDLED_SMOKE_FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"BUNDLED_SMOKE_PASS: yt_dlp extractors={extractor_count}")
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
            raise RuntimeError(
                "faster-whisper or ctranslate2 not importable from bundle " "(available() returned False)"
            )
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
                "pywhispercpp + ggml-vulkan not available from bundle " "(whisper_cpp_available() returned False)"
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


def _configure_logging(log_path: Path) -> None:
    """Attach (or re-point) a RotatingFileHandler on the root logger.

    Called from main() so all modules that already call
    ``logging.getLogger(__name__)`` have their records captured to disk.
    Two 2 MB backup files → at most ~6 MB on disk at any time.

    Idempotent: a handler attached by a previous call is removed and replaced,
    so calling this twice — bootstrap default-path → config-path re-point (F3),
    or a second ``main()``/in-process re-launch (test/E2E harness) — never stacks
    handlers writing each record N times (F5).
    """
    log_path = Path(log_path)  # tolerate a str caller; .parent below needs a Path
    root = logging.getLogger()
    # Drop the handler we previously attached so a re-point / re-call doesn't
    # duplicate it. Tagged with a sentinel attribute to avoid removing handlers
    # installed by anything else.
    for existing in list(root.handlers):
        if getattr(existing, "_anki_miner_sink", False):
            root.removeHandler(existing)
            existing.close()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    handler.setLevel(logging.DEBUG)
    handler._anki_miner_sink = True  # type: ignore[attr-defined]  # sentinel for idempotent replacement
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(fmt)
    # Root logger at WARNING so third-party libs (yt-dlp, fugashi, …) only
    # write WARNING+ to the file; the project namespace gets full DEBUG coverage.
    # A record must clear both its logger's effective level AND the handler's
    # level — setting the handler to DEBUG here means the handler itself never
    # silences anything; filtering happens at the logger level.
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    logging.getLogger("anki_miner").setLevel(logging.DEBUG)


def _apply_ui_zoom(config: AnkiMinerConfig) -> None:
    """Inject the whole-UI zoom factor as ``QT_SCALE_FACTOR``.

    Qt reads ``QT_SCALE_FACTOR`` only once, when the first ``QApplication`` is
    constructed, which is why this must run before that and why the setting is
    restart-to-apply. An explicit user-set env override wins (we never clobber
    it), and the no-op 1.0 case is left unset so the env stays clean.
    """
    if "QT_SCALE_FACTOR" in os.environ:
        return
    if config.ui_zoom != 1.0:
        os.environ["QT_SCALE_FACTOR"] = repr(float(config.ui_zoom))


@runtime_checkable
class _HasUpdateConfig(Protocol):
    """Structural type for tab widgets that accept config updates."""

    def update_config(self, config: AnkiMinerConfig) -> None: ...


def register_mining_tab(window: "MainWindow", tab: "_HasUpdateConfig", presenter: "GUIPresenter", label: str) -> None:
    """Register a mining tab and wire its presenter to the main window.

    One call replaces the hand-repeated boilerplate that used to appear at
    three separate sites in ``main()``:

    1. ``window.tabs.addTab(tab, label)``
    2. Six presenter-signal → ``window._on_*`` handler connections.
    3. ``window.config_refreshed`` → ``tab.update_config`` (non-settings refreshes,
       e.g. JMdict migration finishing in the background).

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
    """
    assert isinstance(tab, QWidget), "tab must be a QWidget"

    window.tabs.addTab(tab, label)

    presenter.info_signal.connect(window._on_info_message)
    presenter.success_signal.connect(window._on_success_message)
    presenter.warning_signal.connect(window._on_warning_message)
    presenter.error_signal.connect(window._on_error_message)
    presenter.processing_result_signal.connect(window._on_processing_result)
    presenter.word_preview_signal.connect(window._on_word_preview)

    window.config_refreshed.connect(tab.update_config)


def _connect_settings_validation(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Connect the Settings tab's validation requests to the window (T-53).

    ``SettingsTab.validation_requested`` is emitted by Test Connection and the
    deck/note-type sync buttons (the Anki panel forwards all three into it).
    It was declared and forwarded but never connected, so those buttons did
    nothing and the connection badge stuck at "Checking connection...". Wiring
    it to ``_run_validation`` runs a validation pass; the result flows back
    through ``_on_validation_result``, which now updates the badge.

    Extracted from ``main()`` so the connection is unit-testable without
    standing up the whole app.
    """
    settings_tab.validation_requested.connect(window._run_validation)


def _connect_alass_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download alass" button to the install worker.

    Status flows back to the panel; on a successful install the resolver's
    cached PATH-miss is dropped and config is re-propagated via
    ``config_refreshed`` so the (non-Settings) Retime tab re-runs its
    availability guard and enables. Without that, the download→retime happy
    path stays disabled until a Settings save or app restart.

    Extracted from ``main()`` so the post-install refresh is unit-testable
    without standing up the whole app.
    """

    def _on_alass_download_requested() -> None:
        def _on_alass_finished(ok: bool, message: str) -> None:
            settings_tab.set_alass_status(message)
            settings_tab.subtitles_panel.notify_alass_download_finished()
            if ok:
                alass_resolver._clear_cache()
                window.config_refreshed.emit(window.get_config())

        window.background_tasks.start_alass_download(
            window.get_config().bin_root,
            settings_tab.set_alass_status,
            _on_alass_finished,
        )

    settings_tab.alass_download_requested.connect(_on_alass_download_requested)


def _connect_cuda_pack_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download GPU acceleration" button to the worker.

    Status flows back to the panel; on finish the panel's in-flight guard is
    cleared and its installed-state label refreshed via
    ``notify_cuda_pack_download_finished``. Mirrors :func:`_connect_alass_download`.

    Extracted from ``main()`` so the wiring is unit-testable without standing up
    the whole app.
    """

    def _on_cuda_pack_download_requested() -> None:
        def _on_cuda_finished(ok: bool, message: str) -> None:
            settings_tab.set_cuda_pack_status(message)
            settings_tab.subtitles_panel.notify_cuda_pack_download_finished(window.get_config().cuda_libs_root)

        window.background_tasks.start_cuda_pack_download(
            window.get_config().cuda_libs_root,
            settings_tab.set_cuda_pack_status,
            _on_cuda_finished,
        )

    settings_tab.cuda_pack_download_requested.connect(_on_cuda_pack_download_requested)


def _connect_vad_pack_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download silence removal" button to the worker.

    Status flows back to the panel; on finish the panel's in-flight guard is
    cleared and its installed-state label refreshed via
    ``notify_vad_pack_download_finished``. Mirrors :func:`_connect_cuda_pack_download`.

    Extracted from ``main()`` so the wiring is unit-testable without standing up
    the whole app.
    """

    def _on_vad_pack_download_requested() -> None:
        def _on_vad_finished(ok: bool, message: str) -> None:
            settings_tab.set_vad_pack_status(message)
            settings_tab.subtitles_panel.notify_vad_pack_download_finished(window.get_config().onnx_pack_root)

        window.background_tasks.start_vad_pack_download(
            window.get_config().onnx_pack_root,
            settings_tab.set_vad_pack_status,
            _on_vad_finished,
        )

    settings_tab.vad_pack_download_requested.connect(_on_vad_pack_download_requested)


def _connect_vulkan_download(window: MainWindow, settings_tab: SettingsTab) -> None:
    """Wire the Subtitles panel's "Download Vulkan model" button to the worker.

    One action fetches BOTH the ggml acoustic model and the Silero VAD. Status
    flows back to the panel; on finish the panel's in-flight guard is cleared and
    its installed-state label refreshed via ``notify_vulkan_download_finished``.
    Mirrors :func:`_connect_cuda_pack_download`.

    Extracted from ``main()`` so the wiring is unit-testable without standing up
    the whole app.
    """

    def _on_vulkan_download_requested(model_name: str) -> None:
        def _on_vulkan_finished(ok: bool, message: str) -> None:
            settings_tab.set_vulkan_status(message)
            settings_tab.subtitles_panel.notify_vulkan_download_finished(ok, message)

        window.background_tasks.start_vulkan_download(
            model_name,
            window.get_config().asr_models_root,
            settings_tab.set_vulkan_status,
            _on_vulkan_finished,
        )

    settings_tab.vulkan_model_download_requested.connect(_on_vulkan_download_requested)


def main():
    """Launch the Anki Miner GUI application."""
    _scrub_pyinstaller_env()

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

    # Attach the rotating file handler to the DEFAULT path before loading config
    # so config-load diagnostics — including the OVH-001 .bak-recovery warnings
    # emitted inside load_config — are captured: those warnings fire as soon as a
    # handler exists, so attaching here (before the load) is what makes them land
    # in the file rather than going nowhere (F3).
    # GUIConfigManager has no Qt dependency, so it can run before QApplication.
    _default_log_path = ANKI_MINER_HOME / "anki_miner.log"
    _configure_logging(_default_log_path)
    try:
        _early_config = GUIConfigManager.load_config()
        _log_path = _early_config.log_path
    except Exception:
        logger.exception("Failed to load config at startup; using default log path")
        _log_path = _default_log_path
    # Honour a user-customised log_path by re-pointing the handler (idempotent,
    # so no duplicate sink). No-op in the common case where it equals the default.
    if _log_path != _default_log_path:
        _configure_logging(_log_path)

    # Whole-UI zoom: must be set before QApplication is constructed (Qt reads
    # QT_SCALE_FACTOR once, at construction). Restart-to-apply by nature.
    _apply_ui_zoom(_early_config)

    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName("Anki Miner")
    app.setOrganizationName("AnkiMiner")

    # Set application icon
    icon_path = get_resource_dir() / "icons" / "anki_miner.svg"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Install UI translators BEFORE any widget is built — widgets capture their
    # tr() strings at construction time, and language is restart-to-apply (no
    # live retranslateUi). Stash on `app` so the translators outlive this call.
    app._translators = install_translators(app, _early_config.ui_language)  # type: ignore[attr-defined]

    # Seed the theme singleton from gui_config.json so the initial paint uses
    # the right active theme and the favorites combo is correctly populated.
    # MainWindow re-loads the same config a moment later (idempotent).
    _initial_config = GUIConfigManager.load_config()
    Theme.initialize(
        active=_initial_config.theme,
        favorites=_initial_config.theme_favorites,
        user_dir=_initial_config.themes_root,
        font_scale=_initial_config.ui_font_scale,
    )
    Theme.apply_to_app(app)

    # Create main window
    window = MainWindow()

    # Initialize stats service for analytics. ``.load()`` opens the SQLite
    # file; defer to after window.show() so the empty shell paints first
    # and the user sees feedback while disk I/O finishes.
    stats_service = StatsService(window.get_config().stats_db_path)

    # Create per-tab presenters and progress callbacks to avoid cross-tab signal pollution.
    # register_mining_tab() handles: addTab + six presenter-signal connections +
    # window.config_refreshed → tab.update_config.
    episode_presenter = GUIPresenter(window)
    episode_progress = GUIProgressCallback(window)
    episode_tab = SingleEpisodeTab(
        window.get_config(),
        episode_presenter,
        episode_progress,
        stats_service=stats_service,
    )
    register_mining_tab(
        window, episode_tab, episode_presenter, QCoreApplication.translate("MainWindow", "Episode Mining")
    )

    batch_presenter = GUIPresenter(window)
    batch_progress = GUIProgressCallback(window)
    batch_tab = BatchProcessingTab(
        window.get_config(),
        batch_presenter,
        batch_progress,
        stats_service=stats_service,
    )
    register_mining_tab(window, batch_tab, batch_presenter, QCoreApplication.translate("MainWindow", "Batch Mining"))

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

    # YouTube tab (uses its own presenter + shared stats service). The
    # processor is built lazily on the first Mine click so the dictionary
    # chain — which opens every installed dict's sqlite — does not block
    # the initial window paint. ``stats_service`` is threaded through so
    # mining sessions still land in analytics regardless of when the
    # processor materializes.
    youtube_presenter = GUIPresenter(window)
    youtube_fetcher = create_youtube_fetcher(window.get_config())
    youtube_tab = YouTubeTab(
        config=window.get_config(),
        processor=None,
        fetcher=youtube_fetcher,
        presenter=youtube_presenter,
        stats_service=stats_service,
    )
    register_mining_tab(window, youtube_tab, youtube_presenter, QCoreApplication.translate("MainWindow", "YouTube"))

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
    register_mining_tab(window, audiobook_tab, audiobook_presenter, QCoreApplication.translate("MainWindow", "Audio"))

    # Analytics tab (non-mining: no presenter, no update_config wiring)
    analytics_tab = AnalyticsTab(stats_service)
    window.tabs.addTab(analytics_tab, QCoreApplication.translate("MainWindow", "Analytics"))

    # Subtitles tab (non-mining: no presenter). Nests Generate (SubtitleCreationTab)
    # and Retime (SubtitleRetimeTab) as inner tabs. It DOES need config updates so
    # an ASR model switch in Settings reaches the model-downloaded guard and the
    # worker: config_changed is auto-wired by the loop below (it has update_config);
    # config_refreshed is wired explicitly near the SettingsTab refresh connection.
    subtitles_tab = SubtitlesTab(window.get_config())
    window.tabs.addTab(subtitles_tab, QCoreApplication.translate("MainWindow", "Subtitles"))

    settings_tab = SettingsTab(window.get_config())
    # from_settings=True suppresses the config_refreshed re-emit: SettingsTab
    # and the mining tabs are notified directly on the next lines, so a
    # re-emit would only reload SettingsTab's panels mid-save (re-entrancy).
    settings_tab.config_changed.connect(lambda cfg: window.update_config(cfg, from_settings=True))
    # Wire config_changed to every mining tab registered via register_mining_tab.
    # Iterating over window.tabs (skipping Analytics and Settings themselves)
    # avoids repeating each tab name here.
    for i in range(window.tabs.count()):
        tab_widget = window.tabs.widget(i)
        if tab_widget is not None and hasattr(tab_widget, "update_config"):
            settings_tab.config_changed.connect(tab_widget.update_config)
    # Make Test Connection + the deck/note-type sync buttons live: they all
    # emit SettingsTab.validation_requested, which was previously connected to
    # nothing (T-53). Routing it to _run_validation also drives the Anki
    # connection badge via _on_validation_result.
    _connect_settings_validation(window, settings_tab)
    # yt-dlp manual update: the YouTube panel's "Update yt-dlp now" button →
    # forced background update. Results flow back to MainWindow (status bar /
    # error dialog) and to the panel's status line.
    settings_tab.ytdlp_update_requested.connect(
        lambda: window.background_tasks.start_ytdlp_update(window.get_config(), force=True)
    )
    window.background_tasks.ytdlp_update_result.connect(settings_tab.set_ytdlp_status_from_result)

    # ASR model download: the Subtitles panel's "Download model" button →
    # background download worker. Status flows back to the panel's status label;
    # on finish refresh the downloaded-state label via _refresh_status so it
    # reflects the new on-disk state.
    def _on_asr_download_requested(model_name: str) -> None:
        def _on_asr_finished(ok: bool, message: str) -> None:
            settings_tab.set_asr_model_status(message)
            # Clear the in-flight guard and refresh the downloaded-state label
            # regardless of success/failure (re-enables the button).
            settings_tab.subtitles_panel.notify_asr_download_finished(model_name, window.get_config().asr_models_root)

        window.background_tasks.start_asr_model_download(
            model_name,
            window.get_config().asr_models_root,
            settings_tab.set_asr_model_status,
            _on_asr_finished,
        )

    settings_tab.asr_download_requested.connect(_on_asr_download_requested)

    _connect_alass_download(window, settings_tab)
    _connect_cuda_pack_download(window, settings_tab)
    _connect_vad_pack_download(window, settings_tab)
    _connect_vulkan_download(window, settings_tab)
    # Wire the Dictionary Settings panel's pre-remove hook so deleting a
    # dictionary closes cached sqlite handles across every tab first — Win11
    # rejects the rmtree otherwise (Issue #30).
    settings_tab.dictionary_panel.set_release_callback(window.release_dictionary_resources)
    # Favorites-list edits in Themes panel must repopulate the top-right combo
    # immediately; the panel doesn't know about the header so the wiring lives
    # here. Active-theme changes from the panel must update the selected entry
    # in the combo without re-emitting `theme_changed` (the theme is already
    # applied — re-emitting would loop back through `_on_theme_changed`).
    settings_tab.themes_panel.favorites_changed.connect(window.header.refresh_favorites)
    settings_tab.themes_panel.state_changed.connect(lambda *_: window.header.update_theme_selector())
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

    # All tabs are now registered — create the count-driven Ctrl+N shortcuts.
    # This must come AFTER all addTab calls so self.tabs.count() is final.
    window.setup_tab_shortcuts()

    # Show window first so the user sees the UI immediately; then run the
    # deferred init (stats DB open) on the next event loop tick. The
    # YouTube tab's episode processor is built even lazier — on first
    # Mine click — because the dictionary chain dominates startup cost.
    window.show()

    # Install the main-thread stall watchdog: a heartbeat QTimer + daemon
    # monitor that logs a WARNING with the GUI stack whenever the event loop
    # blocks past the threshold. Stored on the window so it isn't GC'd; its
    # stop() is hooked into MainWindow.closeEvent (daemon=True is the backstop).
    install_stall_watchdog(window)

    QTimer.singleShot(0, stats_service.load)

    # Pre-warm the shared MeCab tagger (get_shared_tagger) AND the dictionary
    # chain off the GUI thread, scheduled on the next event-loop tick so it
    # never blocks the first paint. The first Mine builds these on the GUI
    # thread today, freezing the UI for seconds; warming them in the background
    # makes that first real Mine materially faster. The worker warms the SHARED
    # tagger singleton that mining reuses (it builds its own sqlite connections
    # for the dict chain and discards those — connections are unsafe across
    # threads). Best-effort: clicking Mine before it finishes simply takes
    # today's cold path. The window's background-task controller holds the
    # reference (so the QThread isn't GC'd mid-run and shutdown can join it)
    # and clears it once the built-in ``finished`` signal fires.
    def _start_prewarm() -> None:
        from anki_miner.gui.workers.prewarm_worker import PrewarmWorker

        worker = PrewarmWorker(window.get_config())
        window.background_tasks.set_prewarm(worker)
        worker.start()

    QTimer.singleShot(0, _start_prewarm)

    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
