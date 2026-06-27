"""Subtitles settings panel.

One panel covering both halves of the Subtitles feature, mirroring the unified
Subtitles main tab:

- **Speech-to-text (ASR)** — Whisper model selection + in-app model download.
  When the optional ``[asr]`` extra is not installed the engine is unavailable;
  the panel says so plainly and shows the exact pip command instead of letting
  the download silently fail.
- **Alignment (alass)** — optional binary-path override plus an in-app
  "Download alass" button on the platforms that ship a binary (Linux/Windows).
  macOS has no upstream binary, so it shows Homebrew guidance instead.
"""

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.utils.run_off_thread import run_off_thread
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.services import alass_installer
from anki_miner.services.asr import _engine, cuda_pack_installer, model_manager

logger = logging.getLogger(__name__)

# Ordered (display_label, config_value) pairs for the ASR model dropdown.
_MODEL_OPTIONS: list[tuple[str, str]] = [
    ("large-v3", "large-v3"),
    ("small", "small"),
]

# Ordered (display_label, config_value) pairs for the ASR device dropdown.
_DEVICE_OPTIONS: list[tuple[str, str]] = [
    ("Auto (GPU if available)", "auto"),
    ("GPU (CUDA)", "cuda"),
    ("CPU", "cpu"),
]

# Exact command that installs the optional speech-to-text engine. Shown
# verbatim (and copyable) when faster-whisper is not importable.
_ASR_INSTALL_COMMAND = 'pip install "anki-miner[asr]"'

# Homebrew command for alass on macOS, where no upstream binary is published.
_ALASS_BREW_COMMAND = "brew install alass"


@dataclass(frozen=True)
class _AsrState:
    """Immutable snapshot of the ASR/alass availability probes.

    Gathered on a worker thread (see :meth:`SubtitlesSettingsPanel._probe_state`)
    so the heavy parts — ``ctranslate2`` import + CUDA driver init via
    ``_engine.cuda_device_count``, ``find_spec`` via ``_engine.available``, and
    the recursive ``model_manager.is_downloaded`` disk walk — never block the GUI
    thread (notably at app startup, when SettingsTab is built eagerly).

    Carries only the probe results read by the GUI-thread applier
    (:meth:`SubtitlesSettingsPanel._on_state_ready`). ``cuda_libs_root`` is
    needed there to drive the CUDA-pack button; the other request inputs
    (``name``/``models_root``/``bin_root``) are read live from ``self`` on
    re-dispatch and so are not carried on the snapshot.
    """

    cuda_libs_root: object
    engine_available: bool
    cuda_device_count: int
    model_downloaded: bool
    cuda_pack_installed: bool
    alass_installed: bool


class SubtitlesSettingsPanel(FormPanel):
    """Settings panel for subtitle generation (ASR) and retiming (alass)."""

    #: Emitted when the user clicks "Download model"; carries the selected model
    #: name. Wiring (SettingsTab → download flow) lives outside the panel.
    asr_download_requested = pyqtSignal(str)
    #: Emitted when the user clicks "Download alass"; the managed install target
    #: (``config.bin_root``) is resolved by the wiring, not the panel.
    alass_download_requested = pyqtSignal()
    #: Emitted when the user clicks "Download GPU acceleration"; the managed
    #: install target (``config.cuda_libs_root``) is resolved by the wiring.
    cuda_pack_download_requested = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the Subtitles settings panel."""
        super().__init__(self.tr("Subtitles"), parent=parent)
        self._models_root = None
        self._bin_root: Path | None = None
        self._cuda_libs_root: Path | None = None
        self._alass_supported = alass_installer.alass_install_supported()
        # In-flight guards: a download disables its button until the worker
        # finishes. Without these, any state refresh re-run (config reload
        # mid-download) would re-enable the button and clobber the status label.
        self._asr_download_active = False
        self._alass_download_active = False
        self._cuda_pack_active = False
        # Off-thread state probe coordination. The heavy probes (ctranslate2
        # import + CUDA init, find_spec, model.bin disk walk) run on a worker;
        # _state_in_flight + _state_refresh_pending give the same single-shot
        # re-dispatch the other settings panels use, so a reload mid-probe isn't
        # dropped and the latest config wins.
        self._state_in_flight = False
        self._state_refresh_pending = False
        # Latest (name, models_root, cuda_libs_root) requested while a probe was
        # in flight; bin_root is read live from self._bin_root on re-dispatch.
        self._pending_state_request: tuple[str, object, object] | None = None
        # Process-lifetime caches: GPU hardware presence and faster-whisper
        # importability are stable, so the first successful probe is reused on
        # later reloads — re-importing ctranslate2 each time is the freeze we're
        # fixing. The install/download flags are NOT cached (they change after a
        # download) and are re-probed every refresh.
        self._engine_available_cache: bool | None = None
        self._cuda_device_count_cache: int | None = None
        self._setup_fields()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_fields(self) -> None:
        """Build the Speech-to-text and Alignment sections."""
        self._setup_asr_section()
        self._setup_alass_section()
        self.add_stretch()

    def _setup_asr_section(self) -> None:
        """Whisper model dropdown, download button, and engine guidance."""
        self.add_section(self.tr("Speech-to-text"))

        self.model_combo = QComboBox()
        for label, _value in _MODEL_OPTIONS:
            self.model_combo.addItem(label)
        self.add_field(
            self.tr("ASR model"),
            self.model_combo,
            helper=self.tr(
                "Select the Whisper model to use for subtitle generation. "
                "'large-v3' gives the best accuracy; 'small' is faster but less accurate."
            ),
        )

        self.device_combo = QComboBox()
        for label, _value in _DEVICE_OPTIONS:
            self.device_combo.addItem(label)
        self.add_field(
            self.tr("ASR device"),
            self.device_combo,
            helper=self.tr(
                "Auto uses the GPU when one is available and falls back to CPU. "
                "GPU needs an NVIDIA card plus the GPU acceleration pack (bundled "
                "installs) or the [asr-cuda] extra (source installs)."
            ),
        )

        self.download_model_button = ModernButton(self.tr("Download model"), variant="secondary")
        self.download_model_button.setToolTip(
            self.tr(
                "Download the selected Whisper model weights into Anki Miner's ASR models folder. "
                "Required before subtitle generation can run."
            )
        )
        self.download_model_button.clicked.connect(self._on_download_clicked)

        self.model_status_label = QLabel("")
        self.model_status_label.setObjectName("settings-save-status")

        download_container = QWidget()
        download_row = QHBoxLayout(download_container)
        download_row.setContentsMargins(0, 0, 0, 0)
        download_row.addWidget(self.download_model_button)
        download_row.addWidget(self.model_status_label)
        download_row.addStretch()
        self.add_field(self.tr("Model download"), download_container)

        # Guidance shown only when faster-whisper is not installed. The engine is
        # a Python package (not a downloadable binary), so the app can't fetch it
        # for the user — point them at the one-line pip command instead of
        # surfacing a cryptic ImportError after a dead "Download model" click.
        self._asr_engine_guidance = self._build_engine_guidance()
        self.add_field("", self._asr_engine_guidance)

        # GPU acceleration pack download. Mirrors the model-download row; gated by
        # _refresh_cuda_pack_status on platform support + NVIDIA-GPU presence.
        self.download_cuda_button = ModernButton(self.tr("Download GPU acceleration"), variant="secondary")
        self.download_cuda_button.setToolTip(
            self.tr(
                "Download the cuDNN + cuBLAS GPU libraries into Anki Miner's folder. "
                "Required for GPU (CUDA) transcription on bundled installs."
            )
        )
        self.download_cuda_button.clicked.connect(self._on_cuda_pack_download_clicked)

        self.cuda_status_label = QLabel("")
        self.cuda_status_label.setObjectName("settings-save-status")

        cuda_container = QWidget()
        cuda_row = QHBoxLayout(cuda_container)
        cuda_row.setContentsMargins(0, 0, 0, 0)
        cuda_row.addWidget(self.download_cuda_button)
        cuda_row.addWidget(self.cuda_status_label)
        cuda_row.addStretch()
        self.add_field(self.tr("GPU acceleration"), cuda_container)

        # Short guidance shown when GPU acceleration is unavailable (no support
        # on this platform, or no NVIDIA GPU detected).
        self._cuda_guidance_label = QLabel("")
        self._cuda_guidance_label.setWordWrap(True)
        self._cuda_guidance_label.setVisible(False)
        self.add_field("", self._cuda_guidance_label)

    def _setup_alass_section(self) -> None:
        """alass path override plus in-app download (or Homebrew guidance)."""
        self.add_section(self.tr("Alignment"))

        self.alass_selector = FileSelector(
            label="",
            file_mode=True,
            file_filter="All Files (*)",
            placeholder=self.tr("Optional: path to the alass executable"),
        )
        self.add_field(
            self.tr("alass binary"),
            self.alass_selector,
            helper=self.tr(
                "Optional: path to the alass executable used for subtitle retiming. "
                "Leave blank to use a downloaded, bundled, or PATH alass."
            ),
        )

        if self._alass_supported:
            self.download_alass_button = ModernButton(self.tr("Download alass"), variant="secondary")
            self.download_alass_button.setToolTip(
                self.tr(
                    "Download the alass subtitle-alignment binary into Anki Miner's bin folder. "
                    "Required for subtitle retiming unless alass is already on your PATH."
                )
            )
            self.download_alass_button.clicked.connect(self._on_alass_download_clicked)

            self.alass_status_label = QLabel("")
            self.alass_status_label.setObjectName("settings-save-status")

            alass_container = QWidget()
            alass_row = QHBoxLayout(alass_container)
            alass_row.setContentsMargins(0, 0, 0, 0)
            alass_row.addWidget(self.download_alass_button)
            alass_row.addWidget(self.alass_status_label)
            alass_row.addStretch()
            self.add_field(self.tr("alass download"), alass_container)
        else:
            # macOS: no upstream v2.0.0 binary — point users at Homebrew.
            guidance = self._build_guidance(
                self.tr("No alass binary is published for macOS. Install it with Homebrew:"),
                _ALASS_BREW_COMMAND,
            )
            self.add_field("", guidance)

    def _build_engine_guidance(self) -> QWidget:
        """Build the (initially hidden) 'install the ASR engine' guidance block."""
        guidance = self._build_guidance(
            self.tr("ASR engine not installed. Subtitle generation needs the faster-whisper engine. Install it with:"),
            _ASR_INSTALL_COMMAND,
        )
        guidance.setVisible(False)
        return guidance

    def _build_guidance(self, message: str, command: str) -> QWidget:
        """A wrapped message label above a read-only command row with a Copy button."""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        message_label = QLabel(message)
        message_label.setWordWrap(True)
        layout.addWidget(message_label)

        command_row = QWidget()
        row_layout = QHBoxLayout(command_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        command_field = QLineEdit(command)
        command_field.setReadOnly(True)
        command_field.setObjectName("command-text")
        copy_button = ModernButton(self.tr("Copy"), variant="secondary")
        copy_button.clicked.connect(lambda: self._copy_to_clipboard(command))
        row_layout.addWidget(command_field)
        row_layout.addWidget(copy_button)
        layout.addWidget(command_row)

        return container

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        """Copy *text* to the system clipboard, if one is available."""
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    # ------------------------------------------------------------------
    # ASR download flow
    # ------------------------------------------------------------------

    def _on_download_clicked(self) -> None:
        """Emit the download request, or surface engine guidance if unavailable."""
        if not self._engine_available_now():
            # Should be unreachable (the button is disabled when unavailable),
            # but guard so a stray click never starts a doomed worker. Uses the
            # cached probe result so the GUI thread never re-imports the engine.
            self._asr_engine_guidance.setVisible(True)
            return
        # Disable while in flight so a second click isn't silently swallowed by
        # the controller's isRunning guard. The flag keeps it disabled across any
        # _refresh_status re-run (config reload); cleared by
        # notify_asr_download_finished.
        self._asr_download_active = True
        self.download_model_button.setEnabled(False)
        self.asr_download_requested.emit(self.model_combo.currentText())

    def set_model_status(self, text: str) -> None:
        """Set the ASR status label text (shown next to the Download button)."""
        self.model_status_label.setText(text)

    def set_model(self, value: str) -> None:
        """Select the dropdown entry matching *value*; falls back to 'large-v3'."""
        for index, (_label, option_value) in enumerate(_MODEL_OPTIONS):
            if option_value == value:
                self.model_combo.setCurrentIndex(index)
                return
        self.model_combo.setCurrentIndex(0)

    def get_model(self) -> str:
        """Return the config value currently selected in the dropdown."""
        index = self.model_combo.currentIndex()
        if 0 <= index < len(_MODEL_OPTIONS):
            return _MODEL_OPTIONS[index][1]
        return "large-v3"

    def set_device(self, value: str) -> None:
        """Select the device dropdown entry matching *value*; falls back to 'auto'."""
        for index, (_label, option_value) in enumerate(_DEVICE_OPTIONS):
            if option_value == value:
                self.device_combo.setCurrentIndex(index)
                return
        self.device_combo.setCurrentIndex(0)

    def get_device(self) -> str:
        """Return the device config value currently selected in the dropdown."""
        index = self.device_combo.currentIndex()
        if 0 <= index < len(_DEVICE_OPTIONS):
            return _DEVICE_OPTIONS[index][1]
        return "auto"

    def _engine_available_now(self) -> bool:
        """Cached faster-whisper availability for GUI-thread guards.

        Returns the last off-thread probe result; falls back to ``False`` until
        the first probe lands so a click can never start a doomed worker before
        the engine state is known.
        """
        return bool(self._engine_available_cache)

    def notify_asr_download_finished(self, name: str, models_root) -> None:
        """Clear the in-flight guard and refresh the button/status after a download.

        Wired to the download worker's finish callback (success or failure).
        Re-probes the model-downloaded flag (off-thread) so the label/button
        reflect the new on-disk state.
        """
        self._asr_download_active = False
        self._models_root = models_root
        self._refresh_state_async(name, models_root, self._cuda_libs_root)

    # ------------------------------------------------------------------
    # alass download flow
    # ------------------------------------------------------------------

    def _on_alass_download_clicked(self) -> None:
        """Disable the button in flight and request the alass download."""
        self._alass_download_active = True
        self.download_alass_button.setEnabled(False)
        self.alass_download_requested.emit()

    def notify_alass_download_finished(self) -> None:
        """Clear the in-flight guard and refresh the alass button after a download.

        Re-probes the managed-binary presence (off-thread) so the label/button
        reflect the new on-disk state.
        """
        self._alass_download_active = False
        self._refresh_state_async(self.get_model(), self._models_root, self._cuda_libs_root)

    def set_alass_status(self, text: str) -> None:
        """Set the alass status label text (no-op on unsupported platforms)."""
        if self._alass_supported:
            self.alass_status_label.setText(text)

    def _apply_alass_state(self, installed: bool) -> None:
        """Reflect whether the managed alass binary is present; re-enable the button.

        Applies a pre-probed ``installed`` flag (gathered off-thread). Preserves
        every branch from the old synchronous path: unsupported platform / no
        bin_root → no-op; download in flight → keep disabled + status intact.
        """
        if not self._alass_supported or self._bin_root is None:
            return
        if self._alass_download_active:
            # Download in flight: keep disabled and leave "Downloading…" intact.
            self.download_alass_button.setEnabled(False)
            return
        self.download_alass_button.setEnabled(True)
        if installed:
            self.set_alass_status(self.tr("Downloaded"))
        else:
            self.set_alass_status(self.tr("Not downloaded"))

    # ------------------------------------------------------------------
    # GPU acceleration pack download flow
    # ------------------------------------------------------------------

    def _on_cuda_pack_download_clicked(self) -> None:
        """Disable the button in flight and request the GPU pack download.

        Guards like :meth:`_on_download_clicked`: a click while GPU acceleration
        is unsupported or no GPU is present is a no-op (the button is disabled in
        those states, but guard so a stray click never starts a doomed worker).
        """
        # Uses the cheap platform check + the cached GPU-count probe so the GUI
        # thread never re-imports ctranslate2 on a click.
        if not cuda_pack_installer.cuda_pack_supported() or (self._cuda_device_count_cache or 0) <= 0:
            self._refresh_state_async(self.get_model(), self._models_root, self._cuda_libs_root)
            return
        self._cuda_pack_active = True
        self.download_cuda_button.setEnabled(False)
        self.cuda_pack_download_requested.emit()

    def set_cuda_pack_status(self, text: str) -> None:
        """Set the GPU-pack status label text (shown next to the Download button)."""
        self.cuda_status_label.setText(text)

    def notify_cuda_pack_download_finished(self, cuda_libs_root) -> None:
        """Clear the in-flight guard and refresh the GPU-pack button after a download.

        Wired to the download worker's finish callback (success or failure).
        Re-probes the install flag (off-thread) so the label/button reflect the
        new on-disk state.
        """
        self._cuda_pack_active = False
        self._cuda_libs_root = cuda_libs_root
        self._refresh_state_async(self.get_model(), self._models_root, cuda_libs_root)

    def _apply_cuda_pack_state(self, cuda_libs_root, device_count: int, installed: bool) -> None:
        """Gate the GPU-pack button on platform support + NVIDIA-GPU presence.

        Applies pre-probed ``device_count`` / ``installed`` values (gathered
        off-thread). Preserves every branch of the old synchronous path:

        * a download in flight keeps the button disabled and the status intact;
        * unsupported platform → hide+disable the button, show guidance;
        * supported but no GPU → disable the button, show guidance;
        * supported and a GPU is present → enable, reflect the installed state.
        """
        self._cuda_libs_root = cuda_libs_root
        if self._cuda_pack_active:
            # A download is in flight: keep the button disabled and leave the
            # "Downloading…" status untouched, regardless of config reloads.
            self.download_cuda_button.setEnabled(False)
            return

        # cuda_pack_supported() is cheap (sys.platform) — fine on the GUI thread.
        supported = cuda_pack_installer.cuda_pack_supported()
        if not supported:
            self.download_cuda_button.setEnabled(False)
            self.download_cuda_button.setVisible(False)
            self.set_cuda_pack_status("")
            self._cuda_guidance_label.setText(self.tr("GPU acceleration is not available on this platform."))
            self._cuda_guidance_label.setVisible(True)
            return

        self.download_cuda_button.setVisible(True)
        if device_count <= 0:
            self.download_cuda_button.setEnabled(False)
            self.set_cuda_pack_status("")
            self._cuda_guidance_label.setText(self.tr("No NVIDIA GPU detected. GPU acceleration needs an NVIDIA card."))
            self._cuda_guidance_label.setVisible(True)
            return

        self._cuda_guidance_label.setVisible(False)
        self.download_cuda_button.setEnabled(True)
        if cuda_libs_root is None:
            return
        if installed:
            self.set_cuda_pack_status(self.tr("Installed"))
        else:
            self.set_cuda_pack_status(self.tr("Not installed"))

    # ------------------------------------------------------------------
    # Off-thread availability/state probe
    # ------------------------------------------------------------------

    def _refresh_state_async(self, name: str, models_root, cuda_libs_root) -> None:
        """Probe engine/GPU/model/install state off the GUI thread, then apply it.

        The heavy probes (``ctranslate2`` import + CUDA init via
        ``cuda_device_count``, ``find_spec`` via ``available``, the recursive
        ``model.bin`` disk walk) run on a worker so the GUI thread — including
        app startup — never blocks on them.

        While a probe is in flight the download buttons stay disabled and a
        neutral "Checking…" status shows, so a click can't race the probe. A
        refresh requested mid-flight is not dropped: the latest request is
        stashed and re-dispatched once on completion (single-shot), so the newest
        config/disk state wins.
        """
        if self._state_in_flight:
            self._state_refresh_pending = True
            self._pending_state_request = (name, models_root, cuda_libs_root)
            return

        self._state_in_flight = True
        self._show_checking_status()

        bin_root = self._bin_root
        alass_supported = self._alass_supported
        # Reuse cached process-lifetime probes; re-probe install/download flags.
        engine_cache = self._engine_available_cache
        cuda_cache = self._cuda_device_count_cache

        def _probe() -> _AsrState:
            # Each probe is guarded independently so one failure doesn't lose the
            # rest (mirrors the old per-call try/except guards).
            if engine_cache is not None:
                engine_available = engine_cache
            else:
                try:
                    engine_available = _engine.available()
                except Exception:  # noqa: BLE001 — degrade to "unavailable"
                    engine_available = False

            if cuda_cache is not None:
                cuda_device_count = cuda_cache
            else:
                try:
                    cuda_device_count = _engine.cuda_device_count()
                except Exception:  # noqa: BLE001 — degrade to "no GPU"
                    cuda_device_count = 0

            model_downloaded = False
            if engine_available and models_root is not None:
                try:
                    model_downloaded = model_manager.is_downloaded(name, models_root)
                except Exception:  # noqa: BLE001 — guard any model_manager failure
                    model_downloaded = False

            cuda_pack_installed = False
            if cuda_libs_root is not None:
                try:
                    cuda_pack_installed = cuda_pack_installer.is_installed(cuda_libs_root)
                except Exception:  # noqa: BLE001 — guard any installer probe failure
                    cuda_pack_installed = False

            alass_installed = False
            if alass_supported and bin_root is not None:
                try:
                    alass_installed = alass_installer.is_installed(bin_root)
                except Exception:  # noqa: BLE001 — guard any installer probe failure
                    alass_installed = False

            return _AsrState(
                cuda_libs_root=cuda_libs_root,
                engine_available=engine_available,
                cuda_device_count=cuda_device_count,
                model_downloaded=model_downloaded,
                cuda_pack_installed=cuda_pack_installed,
                alass_installed=alass_installed,
            )

        run_off_thread(self, _probe, self._on_state_ready, self._on_state_error)

    def _show_checking_status(self) -> None:
        """Disable the download buttons + show a neutral status while probing."""
        if not self._asr_download_active:
            self.download_model_button.setEnabled(False)
        if not self._cuda_pack_active:
            self.download_cuda_button.setEnabled(False)
        if self._alass_supported and not self._alass_download_active:
            self.download_alass_button.setEnabled(False)

    def _on_state_ready(self, state: object) -> None:
        """Apply a probed :class:`_AsrState` snapshot on the GUI thread."""
        self._state_in_flight = False
        result = cast("_AsrState", state)

        # Cache the stable probes for later reloads (avoids re-importing
        # ctranslate2 / re-running find_spec each time).
        self._engine_available_cache = result.engine_available
        self._cuda_device_count_cache = result.cuda_device_count

        self._apply_engine_state(result.engine_available)
        self._apply_model_state(result.engine_available, result.model_downloaded)
        self._apply_cuda_pack_state(result.cuda_libs_root, result.cuda_device_count, result.cuda_pack_installed)
        self._apply_alass_state(result.alass_installed)

        self._redispatch_pending_state()

    def _on_state_error(self, msg: str) -> None:
        """Surface a probe failure without leaving the panel stuck on Checking…."""
        self._state_in_flight = False
        logger.warning("ASR state probe failed: %s", msg)
        # Apply a conservative all-unavailable snapshot so buttons aren't stuck
        # disabled mid-"Checking…" and the guidance is coherent.
        self._apply_engine_state(self._engine_available_now())
        self._apply_model_state(self._engine_available_now(), False)
        self._apply_cuda_pack_state(self._cuda_libs_root, self._cuda_device_count_cache or 0, False)
        self._apply_alass_state(False)
        self._redispatch_pending_state()

    def _redispatch_pending_state(self) -> None:
        """Re-run one refresh if a reload was requested while a probe was in flight.

        Single-shot: the flag is cleared before dispatch, so only refreshes
        requested *during* this dispatch can queue another.
        """
        if not self._state_refresh_pending or self._pending_state_request is None:
            return
        self._state_refresh_pending = False
        name, models_root, cuda_libs_root = self._pending_state_request
        self._pending_state_request = None
        self._refresh_state_async(name, models_root, cuda_libs_root)

    def _apply_engine_state(self, engine_available: bool) -> None:
        """Toggle the engine-missing guidance based on faster-whisper availability."""
        self._asr_engine_guidance.setVisible(not engine_available)

    def _apply_model_state(self, engine_available: bool, model_downloaded: bool) -> None:
        """Reflect download state and gate the button on engine availability.

        Applies pre-probed values. The button is enabled only when the engine is
        importable — without it a model download cannot run. Preserves the
        in-flight guard: a download in flight keeps the button disabled and the
        "Downloading…" status untouched.
        """
        if self._asr_download_active:
            self.download_model_button.setEnabled(False)
            return
        self.download_model_button.setEnabled(engine_available)
        if not engine_available:
            self.set_model_status("")
            return
        if model_downloaded:
            self.set_model_status(self.tr("Downloaded"))
        else:
            self.set_model_status(self.tr("Not downloaded"))

    # ------------------------------------------------------------------
    # Config marshalling contract
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from *config*.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        The availability/state probes run off the GUI thread (this is the
        startup-freeze fix), so the labels/buttons settle a moment after load.
        """
        # ASR
        self._models_root = config.asr_models_root
        self.set_model(config.asr_model)
        self.set_device(config.asr_device)
        # alass
        self.alass_selector.set_path(str(config.alass_location) if config.alass_location else "")
        self._bin_root = config.bin_root
        # One unified off-thread probe drives every status label + button state.
        self._refresh_state_async(config.asr_model, config.asr_models_root, config.cuda_libs_root)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is
        preserved. Called by :meth:`SettingsTab._on_save_clicked` as part of
        the contribute fold.
        """
        path = self.alass_selector.get_path().strip()
        return replace(
            config,
            asr_model=self.get_model(),
            asr_device=self.get_device(),
            alass_location=Path(path) if path else None,
        )
