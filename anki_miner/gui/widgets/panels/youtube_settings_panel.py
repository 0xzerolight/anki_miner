"""YouTube mining settings panel."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtWidgets import QComboBox, QSpinBox

from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import FileSelector

# Ordered pairs of (display label, config value) for the browser dropdown.
# The sentinel "None" label maps to a Python ``None`` value in the config.
# Values are passed verbatim to yt-dlp's ``--cookies-from-browser`` flag.
_COOKIE_BROWSER_OPTIONS: list[tuple[str, str | None]] = [
    ("None", None),
    ("Firefox", "firefox"),
    ("Chrome", "chrome"),
    ("Chromium", "chromium"),
    ("Edge", "edge"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Safari", "safari"),
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
        self.add_field(
            "Cookies from browser",
            self.cookies_browser_combo,
            helper=(
                "Pick a browser whose cookies yt-dlp should reuse. "
                "Leave as 'None' unless YouTube is blocking anonymous fetches."
            ),
        )

        # Cookies file (overrides the browser dropdown above)
        self.cookies_file_selector = FileSelector(
            label="",
            file_mode=True,
            file_filter="Cookies file (*.txt);;All Files (*)",
            placeholder="Optional: path to an exported cookies.txt...",
        )
        self.add_field(
            "Cookies file",
            self.cookies_file_selector,
            helper=(
                "Optional. Overrides the browser dropdown above. Export a Netscape "
                "cookies.txt with a 'Get cookies.txt LOCALLY' browser extension — works "
                "with ANY browser (Safari, Brave, Arc...). Keep the file private; it "
                "holds your YouTube login."
            ),
        )

        # Max duration (minutes)
        self.max_duration_spinbox = QSpinBox()
        self.max_duration_spinbox.setRange(1, 600)
        self.max_duration_spinbox.setSuffix(" minutes")
        self.add_field(
            "YouTube max duration",
            self.max_duration_spinbox,
            helper="Videos longer than this are rejected before fetching.",
        )

        # Playlist max (number of videos)
        self.playlist_max_spinbox = QSpinBox()
        self.playlist_max_spinbox.setRange(1, 1000)
        self.add_field(
            "Playlist max videos",
            self.playlist_max_spinbox,
            helper="When adding a playlist, at most this many videos are queued.",
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

    def set_cookies_file(self, value: object) -> None:
        """Populate the cookies-file field from a config value (Path/str/None)."""
        self.cookies_file_selector.set_path(str(value) if value else "")

    def get_cookies_file(self) -> str:
        """Return the cookies-file path text (empty string when unset)."""
        return self.cookies_file_selector.get_path().strip()

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

    def set_playlist_max(self, value: int) -> None:
        """Set the playlist-max spinbox, clamped to the widget's range."""
        minimum = self.playlist_max_spinbox.minimum()
        maximum = self.playlist_max_spinbox.maximum()
        self.playlist_max_spinbox.setValue(max(minimum, min(maximum, value)))

    def get_playlist_max(self) -> int:
        """Return the current playlist-max spinbox value."""
        return self.playlist_max_spinbox.value()

    # ------------------------------------------------------------------
    # Config marshalling contract (OVH-019)
    # ------------------------------------------------------------------

    def load_from_config(self, config) -> None:
        """Populate all widgets from ``config``.

        Called by :meth:`SettingsTab._load_config` as part of the panel loop.
        """
        self.set_cookies_from_browser(config.youtube_cookies_from_browser)
        self.set_cookies_file(config.youtube_cookies_file)
        self.set_max_duration_seconds(config.youtube_max_duration_s)
        self.set_playlist_max(config.youtube_playlist_max)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab._on_save_clicked` as part of the contribute fold.

        Note: validation of ``cookies_file`` (file must exist when non-empty)
        stays in :meth:`SettingsTab._on_save_clicked` — it runs before the fold
        so an invalid path aborts Save before ``contribute`` is ever called.
        """
        cookies_file_str = self.get_cookies_file()
        return replace(
            config,
            youtube_cookies_from_browser=self.get_cookies_from_browser(),
            youtube_cookies_file=Path(cookies_file_str) if cookies_file_str else None,
            youtube_max_duration_s=self.get_max_duration_seconds(),
            youtube_playlist_max=self.get_playlist_max(),
        )
