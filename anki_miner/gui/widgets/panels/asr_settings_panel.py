"""ASR (automatic speech recognition) settings panel."""

from dataclasses import replace

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.services.asr import model_manager

# Ordered (display_label, config_value) pairs for the model dropdown.
_MODEL_OPTIONS: list[tuple[str, str]] = [
    ("large-v3", "large-v3"),
    ("small", "small"),
]


class AsrSettingsPanel(FormPanel):
    """Panel for ASR model selection and download.

    Provides:
    - Model dropdown (``large-v3`` / ``small``)
    - "Download model" button + status label
    """

    #: Emitted when the user clicks "Download model". Carries the selected
    #: model name. The wiring (SettingsTab → download flow) lives outside the panel.
    asr_download_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        """Initialize the ASR settings panel."""
        super().__init__(self.tr("ASR Settings"), parent=parent)
        self._models_root = None
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Model selection dropdown
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

        # Download button + status label
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

        self.add_stretch()

    def _on_download_clicked(self) -> None:
        """Emit the download-requested signal with the selected model name."""
        # Disable while in flight so a second click isn't silently swallowed by
        # the controller's isRunning guard with no visible feedback. Re-enabled
        # by _refresh_status, which the download flow calls on completion.
        self.download_model_button.setEnabled(False)
        self.asr_download_requested.emit(self.model_combo.currentText())

    def set_model_status(self, text: str) -> None:
        """Set the status label text (shown next to the Download button)."""
        self.model_status_label.setText(text)

    # ------------------------------------------------------------------
    # Value helpers (config <-> widget conversion)
    # ------------------------------------------------------------------

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

    def _refresh_status(self, name: str, models_root) -> None:
        """Update the status label to reflect whether *name* is downloaded.

        Also re-enables the Download button: this runs both on config load and
        when a download completes (success or failure), which is exactly when
        the button should become clickable again.
        """
        self.download_model_button.setEnabled(True)
        try:
            if model_manager.is_downloaded(name, models_root):
                self.set_model_status(self.tr("Downloaded"))
            else:
                self.set_model_status(self.tr("Not downloaded"))
        except Exception:  # noqa: BLE001 — guard any model_manager failure
            pass

    # ------------------------------------------------------------------
    # Config marshalling contract
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from *config*.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        self._models_root = config.asr_models_root
        self.set_model(config.asr_model)
        self._refresh_status(config.asr_model, config.asr_models_root)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is
        preserved. Called by :meth:`SettingsTab._on_save_clicked` as part of
        the contribute fold.
        """
        return replace(config, asr_model=self.get_model())
