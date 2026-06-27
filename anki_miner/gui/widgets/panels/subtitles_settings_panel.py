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

from dataclasses import replace
from pathlib import Path

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

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector, ModernButton
from anki_miner.services import alass_installer
from anki_miner.services.asr import _engine, cuda_pack_installer, model_manager

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
        # finishes. Without these, any _refresh_*_status re-run (config reload
        # mid-download) would re-enable the button and clobber the status label.
        self._asr_download_active = False
        self._alass_download_active = False
        self._cuda_pack_active = False
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
        if not _engine.available():
            # Should be unreachable (the button is disabled when unavailable),
            # but guard so a stray click never starts a doomed worker.
            self._refresh_engine_state()
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

    def _refresh_engine_state(self) -> None:
        """Toggle the engine-missing guidance based on faster-whisper availability."""
        self._asr_engine_guidance.setVisible(not _engine.available())

    def notify_asr_download_finished(self, name: str, models_root) -> None:
        """Clear the in-flight guard and refresh the button/status after a download.

        Wired to the download worker's finish callback (success or failure).
        Must run before ``_refresh_status`` can re-enable the button.
        """
        self._asr_download_active = False
        self._refresh_status(name, models_root)

    def _refresh_status(self, name: str, models_root) -> None:
        """Reflect download state and gate the button on engine availability.

        Runs on config load and when a download completes (success or failure).
        The button is enabled only when the engine is importable — without it a
        model download cannot run, so the click would be a no-op.
        """
        if self._asr_download_active:
            # A download is in flight: keep the button disabled and leave the
            # "Downloading…" status untouched, regardless of config reloads.
            self.download_model_button.setEnabled(False)
            return
        available = _engine.available()
        self.download_model_button.setEnabled(available)
        if not available:
            self.set_model_status("")
            return
        try:
            if model_manager.is_downloaded(name, models_root):
                self.set_model_status(self.tr("Downloaded"))
            else:
                self.set_model_status(self.tr("Not downloaded"))
        except Exception:  # noqa: BLE001 — guard any model_manager failure
            pass

    # ------------------------------------------------------------------
    # alass download flow
    # ------------------------------------------------------------------

    def _on_alass_download_clicked(self) -> None:
        """Disable the button in flight and request the alass download."""
        self._alass_download_active = True
        self.download_alass_button.setEnabled(False)
        self.alass_download_requested.emit()

    def notify_alass_download_finished(self) -> None:
        """Clear the in-flight guard and refresh the alass button after a download."""
        self._alass_download_active = False
        self._refresh_alass_status()

    def set_alass_status(self, text: str) -> None:
        """Set the alass status label text (no-op on unsupported platforms)."""
        if self._alass_supported:
            self.alass_status_label.setText(text)

    def _refresh_alass_status(self) -> None:
        """Reflect whether the managed alass binary is present; re-enable the button."""
        if not self._alass_supported or self._bin_root is None:
            return
        if self._alass_download_active:
            # Download in flight: keep disabled and leave "Downloading…" intact.
            self.download_alass_button.setEnabled(False)
            return
        self.download_alass_button.setEnabled(True)
        try:
            if alass_installer.is_installed(self._bin_root):
                self.set_alass_status(self.tr("Downloaded"))
            else:
                self.set_alass_status(self.tr("Not downloaded"))
        except Exception:  # noqa: BLE001 — guard any installer probe failure
            pass

    # ------------------------------------------------------------------
    # GPU acceleration pack download flow
    # ------------------------------------------------------------------

    def _on_cuda_pack_download_clicked(self) -> None:
        """Disable the button in flight and request the GPU pack download.

        Guards like :meth:`_on_download_clicked`: a click while GPU acceleration
        is unsupported or no GPU is present is a no-op (the button is disabled in
        those states, but guard so a stray click never starts a doomed worker).
        """
        if not cuda_pack_installer.cuda_pack_supported() or _engine.cuda_device_count() <= 0:
            self._refresh_cuda_pack_status(self._cuda_libs_root)
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
        Must run before ``_refresh_cuda_pack_status`` can re-enable the button.
        """
        self._cuda_pack_active = False
        self._refresh_cuda_pack_status(cuda_libs_root)

    def _refresh_cuda_pack_status(self, cuda_libs_root) -> None:
        """Gate the GPU-pack button on platform support + NVIDIA-GPU presence.

        Runs on config load and when a download completes (success or failure):

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

        supported = cuda_pack_installer.cuda_pack_supported()
        if not supported:
            self.download_cuda_button.setEnabled(False)
            self.download_cuda_button.setVisible(False)
            self.set_cuda_pack_status("")
            self._cuda_guidance_label.setText(self.tr("GPU acceleration is not available on this platform."))
            self._cuda_guidance_label.setVisible(True)
            return

        self.download_cuda_button.setVisible(True)
        has_gpu = _engine.cuda_device_count() > 0
        if not has_gpu:
            self.download_cuda_button.setEnabled(False)
            self.set_cuda_pack_status("")
            self._cuda_guidance_label.setText(self.tr("No NVIDIA GPU detected. GPU acceleration needs an NVIDIA card."))
            self._cuda_guidance_label.setVisible(True)
            return

        self._cuda_guidance_label.setVisible(False)
        self.download_cuda_button.setEnabled(True)
        if cuda_libs_root is None:
            return
        try:
            if cuda_pack_installer.is_installed(cuda_libs_root):
                self.set_cuda_pack_status(self.tr("Installed"))
            else:
                self.set_cuda_pack_status(self.tr("Not installed"))
        except Exception:  # noqa: BLE001 — guard any installer probe failure
            pass

    # ------------------------------------------------------------------
    # Config marshalling contract
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from *config*.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        # ASR
        self._models_root = config.asr_models_root
        self.set_model(config.asr_model)
        self.set_device(config.asr_device)
        self._refresh_engine_state()
        self._refresh_status(config.asr_model, config.asr_models_root)
        self._refresh_cuda_pack_status(config.cuda_libs_root)
        # alass
        self.alass_selector.set_path(str(config.alass_location) if config.alass_location else "")
        self._bin_root = config.bin_root
        self._refresh_alass_status()

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
