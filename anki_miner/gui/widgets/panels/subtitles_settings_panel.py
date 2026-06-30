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

import importlib.util
import logging
import sys
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
from anki_miner.services.asr import (
    _engine,
    cuda_pack_installer,
    ggml_model_installer,
    model_manager,
    onnx_pack_installer,
)
from anki_miner.utils import alass_resolver

logger = logging.getLogger(__name__)

# Ordered (display_label, config_value) pairs for the ASR model dropdown.
_MODEL_OPTIONS: list[tuple[str, str]] = [
    ("large-v3", "large-v3"),
    ("small", "small"),
]

# Ordered (display_label, config_value) pairs for the ASR device dropdown that
# are offered on every platform (CT2 backend: auto/cuda/cpu).
_DEVICE_OPTIONS: list[tuple[str, str]] = [
    ("Auto (GPU if available)", "auto"),
    ("GPU (CUDA)", "cuda"),
    ("CPU", "cpu"),
]

# The Vulkan engine only runs on Windows/Linux; macOS stays on CT2/Metal and
# must not offer it.
_VULKAN_DEVICE_OPTION: tuple[str, str] = ("GPU (Vulkan - AMD/Intel/NVIDIA)", "vulkan")


def _device_options(platform: str | None = None) -> list[tuple[str, str]]:
    """Return the ASR device (label, value) pairs available on *platform*.

    Vulkan is appended everywhere except macOS (``sys.platform == "darwin"``).
    ``platform`` defaults to the live ``sys.platform`` and is a parameter so the
    platform branch is reachable from tests without monkeypatching ``sys``.
    """
    if platform is None:
        platform = sys.platform
    options = list(_DEVICE_OPTIONS)
    if platform != "darwin":
        options.append(_VULKAN_DEVICE_OPTION)
    return options


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
    onnxruntime_importable: bool
    vad_pack_installed: bool
    vulkan_installed: bool


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
    #: Emitted when the user clicks "Download silence removal"; the managed
    #: install target (``config.onnx_pack_root``) is resolved by the wiring.
    vad_pack_download_requested = pyqtSignal()
    #: Emitted when the user clicks "Download Vulkan model"; carries the selected
    #: acoustic model name. The install root (``config.asr_models_root``) is
    #: resolved by the wiring. One action fetches BOTH the ggml acoustic model
    #: and the Silero VAD.
    vulkan_model_download_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        """Initialize the Subtitles settings panel."""
        super().__init__(self.tr("Subtitles"), parent=parent)
        self._models_root = None
        self._bin_root: Path | None = None
        self._alass_location: Path | None = None
        self._cuda_libs_root: Path | None = None
        self._onnx_pack_root: Path | None = None
        self._alass_supported = alass_installer.alass_install_supported()
        # Device options offered on this platform (Vulkan omitted on macOS).
        # Computed once via the platform-aware helper so the dropdown,
        # set_device/get_device, and the load_from_config hygiene check all share
        # one source of truth.
        self._device_options = _device_options()
        # The Vulkan model action is offered everywhere except macOS (Vulkan is
        # unsupported there). Mirrors the device-option macOS handling so the
        # button + its row are omitted entirely on macOS.
        self._vulkan_supported = sys.platform != "darwin"
        # In-flight guards: a download disables its button until the worker
        # finishes. Without these, any state refresh re-run (config reload
        # mid-download) would re-enable the button and clobber the status label.
        self._asr_download_active = False
        self._alass_download_active = False
        self._cuda_pack_active = False
        self._vad_pack_active = False
        self._vulkan_active = False
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
        # Last-known install/download flags from the most recent SUCCESSFUL probe.
        # A probe *failure* (_on_state_error) must not claim an installed model /
        # pack is missing: forcing these to False mislabels an on-disk model as
        # "Not downloaded" and disables its button until a later probe succeeds.
        # On error we reuse these instead (default False before any probe lands).
        self._model_downloaded_cache = False
        self._cuda_pack_installed_cache = False
        self._vad_pack_installed_cache = False
        self._alass_installed_cache = False
        self._vulkan_installed_cache = False
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
        for label, _value in self._device_options:
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

        # Silence removal (VAD) pack download. onnxruntime powers Whisper's VAD,
        # which strips silence/music so it is not transcribed as hallucinated
        # text. It ships with source ([asr]) installs; bundled installs download
        # it here. Gated by _refresh_vad_pack_status on importability + platform.
        self.download_vad_button = ModernButton(self.tr("Download silence removal"), variant="secondary")
        self.download_vad_button.setToolTip(
            self.tr(
                "Download the silence-removal (VAD) library into Anki Miner's folder. "
                "It prevents silence and music being transcribed as garbage text."
            )
        )
        self.download_vad_button.clicked.connect(self._on_vad_pack_download_clicked)

        self.vad_status_label = QLabel("")
        self.vad_status_label.setObjectName("settings-save-status")

        vad_container = QWidget()
        vad_row = QHBoxLayout(vad_container)
        vad_row.setContentsMargins(0, 0, 0, 0)
        vad_row.addWidget(self.download_vad_button)
        vad_row.addWidget(self.vad_status_label)
        vad_row.addStretch()
        self.add_field(self.tr("Silence removal"), vad_container)

        # Guidance shown when VAD is already available (no download needed) or
        # unavailable on this platform.
        self._vad_guidance_label = QLabel("")
        self._vad_guidance_label.setWordWrap(True)
        self._vad_guidance_label.setVisible(False)
        self.add_field("", self._vad_guidance_label)

        # Vulkan model download. One action fetches BOTH the ggml acoustic model
        # and the Silero VAD the whisper.cpp (Vulkan/CPU) backend loads off disk.
        # The button + its row are omitted on macOS (Vulkan unsupported there),
        # mirroring the device-option macOS handling; the attributes stay set to
        # None so set_vulkan_status/notify can no-op safely.
        self.download_vulkan_button: ModernButton | None = None
        self.vulkan_status_label: QLabel | None = None
        if self._vulkan_supported:
            self.download_vulkan_button = ModernButton(self.tr("Download Vulkan model"), variant="secondary")
            self.download_vulkan_button.setToolTip(
                self.tr(
                    "Download the whisper.cpp ggml model and Silero VAD into Anki Miner's folder. "
                    "Required for GPU (Vulkan) transcription on AMD/Intel/NVIDIA cards."
                )
            )
            self.download_vulkan_button.clicked.connect(self._on_vulkan_download_clicked)

            self.vulkan_status_label = QLabel("")
            self.vulkan_status_label.setObjectName("settings-save-status")

            vulkan_container = QWidget()
            vulkan_row = QHBoxLayout(vulkan_container)
            vulkan_row.setContentsMargins(0, 0, 0, 0)
            vulkan_row.addWidget(self.download_vulkan_button)
            vulkan_row.addWidget(self.vulkan_status_label)
            vulkan_row.addStretch()
            self.add_field(self.tr("Vulkan model"), vulkan_container)

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
        for index, (_label, option_value) in enumerate(self._device_options):
            if option_value == value:
                self.device_combo.setCurrentIndex(index)
                return
        self.device_combo.setCurrentIndex(0)

    def get_device(self) -> str:
        """Return the device config value currently selected in the dropdown."""
        index = self.device_combo.currentIndex()
        if 0 <= index < len(self._device_options):
            return self._device_options[index][1]
        return "auto"

    def _engine_available_now(self) -> bool:
        """Cached faster-whisper availability for GUI-thread guards.

        Returns the last off-thread probe result; falls back to ``False`` until
        the first probe lands so a click can never start a doomed worker before
        the engine state is known.
        """
        return bool(self._engine_available_cache)

    def _onnxruntime_importable_now(self) -> bool:
        """Best-effort onnxruntime importability for the probe-error fallback.

        ``find_spec`` is light (a sys.path scan, no import); only used on the rare
        probe-failure path, so a direct check here is fine on the GUI thread.
        """
        try:
            return importlib.util.find_spec("onnxruntime") is not None
        except Exception:  # noqa: BLE001 — degrade to "not importable"
            return False

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
            # "Available" (not "Downloaded"): the binary may be bundled in the
            # frozen build, on PATH, or set via an explicit path — none of which
            # were "downloaded" by the in-app installer.
            self.set_alass_status(self.tr("Available"))
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
    # Vulkan model download flow
    # ------------------------------------------------------------------

    def _on_vulkan_download_clicked(self) -> None:
        """Disable the button in flight and request the Vulkan model download.

        Carries the selected acoustic model name; the install root
        (``config.asr_models_root``) is resolved by the wiring. One action fetches
        BOTH the ggml acoustic model and the Silero VAD.
        """
        if self.download_vulkan_button is None:
            return
        self._vulkan_active = True
        self.download_vulkan_button.setEnabled(False)
        self.vulkan_model_download_requested.emit(self.get_model())

    def set_vulkan_status(self, text: str) -> None:
        """Set the Vulkan status label text (no-op when the button is omitted)."""
        if self.vulkan_status_label is not None:
            self.vulkan_status_label.setText(text)

    def notify_vulkan_download_finished(self, ok: bool, msg: str) -> None:
        """Clear the in-flight guard, set the status label + installed cache,
        and re-enable the button after a Vulkan model download.

        Wired to the download worker's finish callback (success or failure). The
        worker's result *message* is shown verbatim (unlike the re-probe paths,
        the worker already carries it), the installed cache is set to ``ok`` so a
        later refresh reflects the new on-disk state, and the button is
        re-enabled. A no-op on macOS where the button is omitted.
        """
        self._vulkan_active = False
        self._vulkan_installed_cache = ok
        self.set_vulkan_status(msg)
        if self.download_vulkan_button is not None:
            self.download_vulkan_button.setEnabled(True)

    def _apply_vulkan_state(self, installed: bool) -> None:
        """Reflect whether the Vulkan model (ggml + VAD) is present; re-enable.

        Applies a pre-probed ``installed`` flag (gathered off-thread = both
        ``is_ggml_downloaded`` AND ``is_vad_downloaded``). A no-op on macOS where
        the button is omitted; a download in flight keeps the button disabled and
        leaves any "Downloading…"/result status untouched.
        """
        if self.download_vulkan_button is None:
            return
        if self._vulkan_active:
            self.download_vulkan_button.setEnabled(False)
            return
        self.download_vulkan_button.setEnabled(True)
        if installed:
            self.set_vulkan_status(self.tr("Installed"))
        else:
            self.set_vulkan_status(self.tr("Not installed"))

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
        alass_location = self._alass_location
        onnx_pack_root = self._onnx_pack_root
        alass_supported = self._alass_supported
        vulkan_supported = self._vulkan_supported
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
                    # Probe actual resolvability (override / bundled / managed /
                    # PATH), not just the managed-download dir — otherwise a
                    # bundled or PATH alass is mislabeled "Not downloaded" while
                    # the retime tab (which uses the same resolver) works fine.
                    alass_installed = alass_resolver.alass_available(alass_location, bin_root)
                except Exception:  # noqa: BLE001 — guard any resolver probe failure
                    alass_installed = False

            # onnxruntime importability (find_spec scans sys.path) is the heavy
            # VAD probe; the install flag is a cheap dir check.
            try:
                onnxruntime_importable = importlib.util.find_spec("onnxruntime") is not None
            except Exception:  # noqa: BLE001 — degrade to "not importable"
                onnxruntime_importable = False

            vad_pack_installed = False
            if onnx_pack_root is not None:
                try:
                    vad_pack_installed = onnx_pack_installer.is_installed(onnx_pack_root)
                except Exception:  # noqa: BLE001 — guard any installer probe failure
                    vad_pack_installed = False

            # The Vulkan model is "installed" only when BOTH ggml files are
            # present: the acoustic model for the selected model AND the shared
            # Silero VAD. Each presence check is a cheap dir stat.
            vulkan_installed = False
            if vulkan_supported and models_root is not None:
                try:
                    vulkan_installed = ggml_model_installer.is_ggml_downloaded(
                        name, models_root
                    ) and ggml_model_installer.is_vad_downloaded(models_root)
                except Exception:  # noqa: BLE001 — guard any installer probe failure
                    vulkan_installed = False

            return _AsrState(
                cuda_libs_root=cuda_libs_root,
                engine_available=engine_available,
                cuda_device_count=cuda_device_count,
                model_downloaded=model_downloaded,
                cuda_pack_installed=cuda_pack_installed,
                alass_installed=alass_installed,
                onnxruntime_importable=onnxruntime_importable,
                vad_pack_installed=vad_pack_installed,
                vulkan_installed=vulkan_installed,
            )

        run_off_thread(self, _probe, self._on_state_ready, self._on_state_error)

    def _show_checking_status(self) -> None:
        """Disable the download buttons + show a neutral status while probing."""
        if not self._asr_download_active:
            self.download_model_button.setEnabled(False)
        if not self._cuda_pack_active:
            self.download_cuda_button.setEnabled(False)
        if not self._vad_pack_active:
            self.download_vad_button.setEnabled(False)
        if self.download_vulkan_button is not None and not self._vulkan_active:
            self.download_vulkan_button.setEnabled(False)
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
        # Remember the install/download flags so a later probe FAILURE can fall
        # back to this last-known-good state instead of asserting "missing".
        self._model_downloaded_cache = result.model_downloaded
        self._cuda_pack_installed_cache = result.cuda_pack_installed
        self._vad_pack_installed_cache = result.vad_pack_installed
        self._alass_installed_cache = result.alass_installed
        self._vulkan_installed_cache = result.vulkan_installed

        self._apply_engine_state(result.engine_available)
        self._apply_model_state(result.engine_available, result.model_downloaded)
        self._apply_cuda_pack_state(result.cuda_libs_root, result.cuda_device_count, result.cuda_pack_installed)
        self._apply_vad_pack_state(result.onnxruntime_importable, result.vad_pack_installed)
        self._apply_alass_state(result.alass_installed)
        self._apply_vulkan_state(result.vulkan_installed)

        self._redispatch_pending_state()

    def _on_state_error(self, msg: str) -> None:
        """Surface a probe failure without leaving the panel stuck on Checking…."""
        self._state_in_flight = False
        logger.warning("ASR state probe failed: %s", msg)
        # Fall back to the last-known-good install/download flags rather than
        # forcing False — a probe failure is not evidence that an installed
        # model/pack disappeared, and asserting "missing" would mislabel them and
        # lock their buttons until a later probe succeeds.
        self._apply_engine_state(self._engine_available_now())
        self._apply_model_state(self._engine_available_now(), self._model_downloaded_cache)
        self._apply_cuda_pack_state(
            self._cuda_libs_root, self._cuda_device_count_cache or 0, self._cuda_pack_installed_cache
        )
        self._apply_vad_pack_state(self._onnxruntime_importable_now(), self._vad_pack_installed_cache)
        self._apply_alass_state(self._alass_installed_cache)
        self._apply_vulkan_state(self._vulkan_installed_cache)
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
    # Silence-removal (VAD) pack download flow
    # ------------------------------------------------------------------

    def _on_vad_pack_download_clicked(self) -> None:
        """Disable the button in flight and request the VAD pack download.

        A click while the pack is unsupported is a no-op (the button is disabled
        in that state, but guard so a stray click never starts a doomed worker).
        ``onnx_pack_supported()`` is cheap (sys.platform/version), so no off-thread
        probe is needed on the click path.
        """
        if not onnx_pack_installer.onnx_pack_supported():
            self._refresh_state_async(self.get_model(), self._models_root, self._cuda_libs_root)
            return
        self._vad_pack_active = True
        self.download_vad_button.setEnabled(False)
        self.vad_pack_download_requested.emit()

    def set_vad_pack_status(self, text: str) -> None:
        """Set the VAD-pack status label text (shown next to the Download button)."""
        self.vad_status_label.setText(text)

    def notify_vad_pack_download_finished(self, onnx_pack_root) -> None:
        """Clear the in-flight guard and refresh the VAD-pack button after a download.

        Wired to the download worker's finish callback (success or failure).
        Re-probes onnxruntime importability + the install flag (off-thread) so the
        label/button reflect the new on-disk state.
        """
        self._vad_pack_active = False
        self._onnx_pack_root = onnx_pack_root
        self._refresh_state_async(self.get_model(), self._models_root, self._cuda_libs_root)

    def _apply_vad_pack_state(self, onnxruntime_importable: bool, installed: bool) -> None:
        """Gate the VAD-pack button on onnxruntime availability + platform support.

        Applies pre-probed ``onnxruntime_importable`` / ``installed`` values
        (gathered off-thread):

        * a download in flight keeps the button disabled and the status intact;
        * onnxruntime already importable (source [asr] install) → hide the button,
          say silence removal is available;
        * unsupported platform/Python → hide+disable the button, show guidance;
        * supported → enable, reflect the installed state.
        """
        if self._vad_pack_active:
            # A download is in flight: keep the button disabled and leave the
            # "Downloading…" status untouched, regardless of config reloads.
            self.download_vad_button.setEnabled(False)
            return

        if onnxruntime_importable:
            self.download_vad_button.setEnabled(False)
            self.download_vad_button.setVisible(False)
            self.set_vad_pack_status("")
            self._vad_guidance_label.setText(self.tr("Silence removal is available."))
            self._vad_guidance_label.setVisible(True)
            return

        # onnx_pack_supported() is cheap (sys.platform/version) — fine on the GUI thread.
        if not onnx_pack_installer.onnx_pack_supported():
            self.download_vad_button.setEnabled(False)
            self.download_vad_button.setVisible(False)
            self.set_vad_pack_status("")
            self._vad_guidance_label.setText(self.tr("Silence removal is not available on this platform."))
            self._vad_guidance_label.setVisible(True)
            return

        self.download_vad_button.setVisible(True)
        self._vad_guidance_label.setVisible(False)
        self.download_vad_button.setEnabled(True)
        if installed:
            self.set_vad_pack_status(self.tr("Installed"))
        else:
            self.set_vad_pack_status(self.tr("Not installed"))

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
        # Persisted-device hygiene: a value not offered on this platform (e.g. a
        # config carrying "vulkan" opened on macOS, or any unknown value) must
        # not silently round-trip. Fall back to "auto" so get_device()/contribute()
        # then persist "auto" instead of the stale/foreign value.
        available_devices = {value for _label, value in self._device_options}
        self.set_device(config.asr_device if config.asr_device in available_devices else "auto")
        # alass
        self.alass_selector.set_path(str(config.alass_location) if config.alass_location else "")
        self._bin_root = config.bin_root
        self._alass_location = config.alass_location
        self._onnx_pack_root = config.onnx_pack_root
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
