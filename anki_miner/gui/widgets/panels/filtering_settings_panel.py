"""Word filtering settings panel."""

from PyQt6.QtWidgets import QLabel, QSpinBox

from anki_miner.gui.resources.icons.icon_provider import IconProvider
from anki_miner.gui.widgets.base import FormPanel


class FilteringSettingsPanel(FormPanel):
    """Panel for word filtering settings.

    Provides:
    - Minimum word length configuration
    - (Future: additional filtering options)
    """

    def __init__(self, parent=None):
        """Initialize the filtering settings panel."""
        super().__init__("Word Filtering", icon="filter", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Minimum word length
        self.min_length_spinbox = QSpinBox()
        self.min_length_spinbox.setRange(1, 10)
        self.min_length_spinbox.setToolTip("Minimum character length for words to be processed")
        self.add_field(
            "Minimum Word Length",
            self.min_length_spinbox,
            helper="Words shorter than this will be ignored during processing",
        )

        # Future options note
        helper = QLabel(
            f"{IconProvider.get_icon('info')} Additional filtering options will be added in future versions"
        )
        helper.setObjectName("helper-text")
        helper.setWordWrap(True)
        self.add_widget(helper)

        self.add_stretch()
