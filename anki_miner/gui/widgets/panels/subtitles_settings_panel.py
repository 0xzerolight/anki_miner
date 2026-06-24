"""Subtitles settings panel."""

from dataclasses import replace
from pathlib import Path

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector


class SubtitlesSettingsPanel(FormPanel):
    """Panel for subtitle-related settings.

    Provides:
    - alass binary path selector (optional override; leave blank to use
      a bundled alass or one found on PATH)
    """

    def __init__(self, parent=None):
        """Initialize the Subtitles settings panel."""
        super().__init__(self.tr("Subtitles"), parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
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
                "Leave blank to use a bundled alass or one found on your PATH."
            ),
        )
        self.add_stretch()

    # ------------------------------------------------------------------
    # Config marshalling contract
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from *config*.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        self.alass_selector.set_path(str(config.alass_location) if config.alass_location else "")

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is
        preserved. Called by :meth:`SettingsTab._on_save_clicked` as part of
        the contribute fold.
        """
        s = self.alass_selector.get_path().strip()
        return replace(config, alass_location=Path(s) if s else None)
