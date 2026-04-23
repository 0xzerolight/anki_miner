"""YouTube mining settings panel."""

from PyQt6.QtWidgets import QComboBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel

# Ordered pairs of (display label, config value) for the browser dropdown.
# The sentinel "None" label maps to a Python ``None`` value in the config.
_COOKIE_BROWSER_OPTIONS: list[tuple[str, str | None]] = [
    ("None", None),
    ("Firefox", "firefox"),
    ("Chrome", "chrome"),
    ("Chromium", "chromium"),
    ("Edge", "edge"),
]


class YouTubeSettingsPanel(FormPanel):
    """Panel for YouTube mining settings.

    Provides:
    - Cookies-from-browser selection (bot-detection workaround)
    - Max video duration cap (in minutes)
    """

    def __init__(self, parent=None):
        """Initialize the YouTube settings panel."""
        super().__init__("YouTube Settings", parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Cookies from browser
        self.cookies_browser_combo = QComboBox()
        for label, _value in _COOKIE_BROWSER_OPTIONS:
            self.cookies_browser_combo.addItem(label)
        self.cookies_browser_combo.setToolTip(
            "Use cookies from an installed browser to work around YouTube bot detection"
        )
        self.add_field(
            "Cookies from browser (YouTube bot-detection workaround)",
            self.cookies_browser_combo,
            helper=(
                "Select a browser whose cookies yt-dlp should reuse. "
                "Leave as 'None' unless YouTube is blocking anonymous fetches."
            ),
        )

        # Max duration (minutes)
        self.max_duration_spinbox = QSpinBox()
        self.max_duration_spinbox.setRange(1, 600)
        self.max_duration_spinbox.setSuffix(" minutes")
        self.max_duration_spinbox.setToolTip(
            "Videos longer than this will be rejected before fetching"
        )
        self.add_field(
            "YouTube max duration",
            self.max_duration_spinbox,
            helper="Videos longer than this duration are blocked from mining",
        )

        self.add_stretch()

    # ------------------------------------------------------------------
    # Value helpers (config <-> widget conversion)
    # ------------------------------------------------------------------

    def set_cookies_from_browser(self, value: str | None) -> None:
        """Select the dropdown entry matching ``value``.

        Unknown values fall back to "None".
        """
        for index, (_label, option_value) in enumerate(_COOKIE_BROWSER_OPTIONS):
            if option_value == value:
                self.cookies_browser_combo.setCurrentIndex(index)
                return
        self.cookies_browser_combo.setCurrentIndex(0)

    def get_cookies_from_browser(self) -> str | None:
        """Return the config value currently selected in the dropdown."""
        index = self.cookies_browser_combo.currentIndex()
        if 0 <= index < len(_COOKIE_BROWSER_OPTIONS):
            return _COOKIE_BROWSER_OPTIONS[index][1]
        return None

    def set_max_duration_seconds(self, seconds: int) -> None:
        """Set the spinbox from a seconds value, rounding up to the next minute."""
        minutes = max(1, (seconds + 59) // 60)
        minimum = self.max_duration_spinbox.minimum()
        maximum = self.max_duration_spinbox.maximum()
        minutes = max(minimum, min(maximum, minutes))
        self.max_duration_spinbox.setValue(minutes)

    def get_max_duration_seconds(self) -> int:
        """Return the current spinbox value converted to seconds."""
        return self.max_duration_spinbox.value() * 60
