"""YouTube mining settings panel."""

import re
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QSpinBox, QWidget

from anki_miner.gui.utils import file_dialogs
from anki_miner.gui.utils.dialog_paths import resolve_start_dir
from anki_miner.gui.widgets.base import FormPanel
from anki_miner.gui.widgets.enhanced import ModernButton
from anki_miner.utils.i18n import tr_format

# Ordered pairs of (display label, config value) for the browser dropdown.
# The sentinel "None" label maps to a Python ``None`` value in the config; it is
# the one label translated (at addItem), the rest are product names.
# Values are passed verbatim to yt-dlp's ``--cookies-from-browser`` flag.
_COOKIE_BROWSER_OPTIONS: list[tuple[str, str | None]] = [
    (QT_TRANSLATE_NOOP("YouTubeSettingsPanel", "None"), None),
    ("Firefox", "firefox"),
    ("Chrome", "chrome"),
    ("Chromium", "chromium"),
    ("Edge", "edge"),
    ("Brave", "brave"),
    ("Opera", "opera"),
    ("Vivaldi", "vivaldi"),
    ("Safari", "safari"),
]

#: Item data of the combo entry naming the chosen cookies.txt file (C10).
_FILE_ITEM = "__cookies_file__"
#: Item data of the last entry, the action that opens the file picker.
_PICK_ITEM = "__pick_cookies_file__"

#: The validation verdict validation_service writes for a working yt-dlp:
#: "<version> [<origin>]" (see _classify_resolved there).
_YTDLP_VERDICT = re.compile(r"^(?P<version>\d[\w.+-]*) \[(?P<origin>[^\]]+)\]$")


class YouTubeSettingsPanel(FormPanel):
    """Panel for YouTube mining settings.

    Provides:
    - Cookies: none, a browser, or a cookies.txt file, as one choice (C10)
    - Max video duration cap (in minutes)
    - Whether YouTube captions are aligned to the audio (D6 item 2)
    - A manual "Update yt-dlp now" trigger + status line
    """

    ANCHOR_NAMESPACE = "youtube"

    #: Emitted when the user clicks "Update yt-dlp now". The wiring (SettingsTab
    #: → MainWindow.background_tasks.start_ytdlp_update) lives outside the panel.
    update_ytdlp_requested = pyqtSignal()

    #: A cookies file was picked or dropped through the combo. The file is not a
    #: widget value the auto-save can watch, so SettingsTab listens to this.
    edited = pyqtSignal()

    def __init__(self, parent=None):
        """Initialize the YouTube settings panel."""
        super().__init__(self.tr("YouTube"), parent=parent)
        self._setup_fields()

    def _setup_fields(self) -> None:
        """Set up the panel fields."""
        # Cookies (C10): one choice -- none, a browser, or a cookies.txt file.
        # Both config fields stay; a file wins, exactly as it did before.
        self._cookies_file = ""
        self._last_cookie_index = 0
        self.cookies_browser_combo = QComboBox()
        for label, _value in _COOKIE_BROWSER_OPTIONS:
            self.cookies_browser_combo.addItem(QCoreApplication.translate("YouTubeSettingsPanel", label))
        self.cookies_browser_combo.addItem(self.tr("From a cookies.txt file…"), _PICK_ITEM)
        self.cookies_browser_combo.activated.connect(self._on_cookies_activated)
        self.add_field(
            self.tr("Cookies from browser"),
            self.cookies_browser_combo,
            helper=self.tr(
                "Reuse a browser's login, or an exported cookies.txt file, when YouTube blocks "
                "anonymous fetches. Bilibili shows its subtitles only to logged-in users. "
                "Keep a cookies file private — it holds your login."
            ),
            anchor_text=lambda: ("Cookies file", "cookies.txt"),
        )

        # Max duration (minutes)
        self.max_duration_spinbox = QSpinBox()
        self.max_duration_spinbox.setRange(1, 600)
        self.max_duration_spinbox.setSuffix(self.tr(" minutes"))
        self.add_field(
            self.tr("YouTube max duration"),
            self.max_duration_spinbox,
            helper=self.tr("Videos longer than this are rejected before fetching."),
        )

        # Playlist max (number of videos)
        self.playlist_max_spinbox = QSpinBox()
        self.playlist_max_spinbox.setRange(1, 1000)
        self.add_field(
            self.tr("Playlist max videos"),
            self.playlist_max_spinbox,
            helper=self.tr("When adding a playlist, at most this many videos are queued."),
        )

        # D6 item 2: aligning captions is a standing choice, not a per-run one,
        # so it moved here from Video → YouTube. The worker reads the config.
        self.align_captions_checkbox = QCheckBox(self.tr("Align captions to audio"))
        self.add_field(
            "",
            self.align_captions_checkbox,
            helper=self.tr(
                "Retime the video's captions against its audio before mining. "
                "Ignored when the subtitle was transcribed locally."
            ),
            anchor="align_captions_checkbox",
        )

        # Keep yt-dlp current. This had no UI at all, so the only way to change it was
        # hand-editing gui_config.json — and it is the setting that decides whether
        # YouTube mining keeps working, since yt-dlp breaks whenever YouTube changes.
        self.auto_update_checkbox = QCheckBox(self.tr("Keep yt-dlp up to date automatically"))
        self.add_field(
            "",
            self.auto_update_checkbox,
            helper=self.tr("Checks once a day on startup; off means YouTube mining eventually stops working."),
        )

        # Nightly channel for the updater above. YouTube breakage is fixed in
        # yt-dlp nightlies days before a stable release ships (e.g. the 2026-08
        # android_vr kill, yt-dlp#17456), so this is the "keep working during the
        # gap" switch.
        self.prerelease_checkbox = QCheckBox(self.tr("Use pre-release yt-dlp builds"))
        self.add_field(
            "",
            self.prerelease_checkbox,
            helper=self.tr(
                "Updates install yt-dlp's nightly channel, which fixes YouTube "
                "breakage days before a stable release. Turning this off keeps "
                "the installed build until a newer stable version replaces it."
            ),
        )

        # yt-dlp updater: manual trigger + status. yt-dlp also self-updates in
        # the background on startup; this is the explicit "do it now" button.
        self.update_ytdlp_button = ModernButton(self.tr("Update yt-dlp now"), variant="secondary")
        self.update_ytdlp_button.setToolTip(self.tr("Downloads the latest yt-dlp into Anki Miner's own folder."))
        self.update_ytdlp_button.clicked.connect(self.update_ytdlp_requested)

        self.ytdlp_status_label = QLabel("")
        self.ytdlp_status_label.setObjectName("validation-status")

        ytdlp_container = QWidget()
        ytdlp_row = QHBoxLayout(ytdlp_container)
        ytdlp_row.setContentsMargins(0, 0, 0, 0)
        ytdlp_row.addWidget(self.update_ytdlp_button)
        ytdlp_row.addWidget(self.ytdlp_status_label)
        ytdlp_row.addStretch()
        self.add_field(
            self.tr("yt-dlp"),
            ytdlp_container,
            anchor="ytdlp_update",
            anchor_focus=self.update_ytdlp_button,
            anchor_text=lambda: (self.update_ytdlp_button.text(), self.update_ytdlp_button.toolTip()),
        )

        self.add_stretch()

    def set_ytdlp_status(self, text: str) -> None:
        """Set the yt-dlp status line (shown next to the Update button).

        A validation verdict reads "Version 2026.08.19" with where the binary
        came from in the tooltip (C16); update messages and errors show as is.
        """
        match = _YTDLP_VERDICT.match(text.strip())
        if match is None:
            self.set_status_text(self.ytdlp_status_label, text, status="info")
            return
        origins = {
            "app-managed": self.tr("Downloaded by Anki Miner"),
            "bundled": self.tr("Included with Anki Miner"),
            "system PATH": self.tr("Found on your system PATH"),
            "venv": self.tr("Installed alongside Anki Miner"),
            "custom path": self.tr("Your own copy, set in gui_config.json"),
        }
        self.set_status_text(self.ytdlp_status_label, tr_format(self.tr("Version %1"), match["version"]), status="info")
        self.ytdlp_status_label.setToolTip(origins.get(match["origin"], match["origin"]))

    def set_ytdlp_present(self, present: bool) -> None:
        """Say what the button will do, from the validation verdict.

        The app ships no yt-dlp, so on a fresh install this button is an install
        button; calling it "Update" there described something the user does not
        have. The verdict comes from startup validation (MainWindow), which
        already resolves and probes off the GUI thread — the panel must never
        run the resolver itself.
        """
        self.update_ytdlp_button.setText(
            self.tr("Update yt-dlp now") if present else self.tr("Download yt-dlp (~40 MB)")
        )

    # ------------------------------------------------------------------
    # Value helpers (config <-> widget conversion)
    # ------------------------------------------------------------------

    def set_cookies_from_browser(self, value: str | None) -> None:
        """Select the browser matching ``value`` (unknown -> "None"); this clears a file."""
        self._set_cookies_file_item("")
        combo = self.cookies_browser_combo
        combo.setCurrentIndex(0)
        for index, (_label, option_value) in enumerate(_COOKIE_BROWSER_OPTIONS):
            if option_value == value:
                combo.setCurrentIndex(index)
                break
        self._last_cookie_index = combo.currentIndex()

    def get_cookies_from_browser(self) -> str | None:
        """The selected browser's config value; ``None`` for "None" or a file."""
        index = self.cookies_browser_combo.currentIndex()
        if 0 <= index < len(_COOKIE_BROWSER_OPTIONS):
            return _COOKIE_BROWSER_OPTIONS[index][1]
        return None

    def set_cookies_file(self, value: object) -> None:
        """Choose a cookies file (Path/str), or drop it (None/"")."""
        path = str(value) if value else ""
        self._set_cookies_file_item(path)
        combo = self.cookies_browser_combo
        if path:
            combo.setCurrentIndex(combo.findData(_FILE_ITEM))
        elif combo.currentIndex() < 0 or combo.currentData() == _PICK_ITEM:
            combo.setCurrentIndex(0)
        self._last_cookie_index = combo.currentIndex()

    def get_cookies_file(self) -> str:
        """The cookies-file path ("" when none). Verbatim: a trailing space in a folder name survives."""
        return self._cookies_file

    def _set_cookies_file_item(self, path: str) -> None:
        """Show ``path`` as "<name> (file)" just above the picker action, or remove it."""
        self._cookies_file = path
        combo = self.cookies_browser_combo
        index = combo.findData(_FILE_ITEM)
        if not path:
            if index >= 0:
                combo.removeItem(index)
            return
        label = tr_format(self.tr("%1 (file)"), Path(path).name)
        if index < 0:
            index = combo.findData(_PICK_ITEM)
            combo.insertItem(index, label, _FILE_ITEM)
        else:
            combo.setItemText(index, label)
        combo.setItemData(index, path, Qt.ItemDataRole.ToolTipRole)

    def _on_cookies_activated(self, index: int) -> None:
        """A user choice: the picker action, the file item, or a browser."""
        combo = self.cookies_browser_combo
        data = combo.itemData(index)
        if data == _PICK_ITEM:
            # Never leave the action itself selected while the picker is open.
            combo.setCurrentIndex(self._last_cookie_index)
            file_dialogs.pick_open_file(
                self,
                self.tr("Choose a cookies.txt file"),
                resolve_start_dir(self._cookies_file or None, file_mode=True),
                self.tr("Cookies file (*.txt);;All Files (*)"),
                on_done=self._on_cookies_file_picked,
            )
            return
        if data != _FILE_ITEM and self._cookies_file:
            # Choosing a browser clears the file (C10).
            self._set_cookies_file_item("")
            self.edited.emit()
        self._last_cookie_index = combo.currentIndex()

    def _on_cookies_file_picked(self, path: str) -> None:
        if not path:
            return
        self.set_cookies_file(path)
        self.edited.emit()

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

    def set_auto_update_ytdlp(self, value: bool) -> None:
        """Set the auto-update checkbox."""
        self.auto_update_checkbox.setChecked(bool(value))

    def get_auto_update_ytdlp(self) -> bool:
        """Return the auto-update checkbox state."""
        return self.auto_update_checkbox.isChecked()

    def set_ytdlp_prerelease(self, value: bool) -> None:
        """Set the pre-release (nightly channel) checkbox."""
        self.prerelease_checkbox.setChecked(bool(value))

    def get_ytdlp_prerelease(self) -> bool:
        """Return the pre-release checkbox state."""
        return self.prerelease_checkbox.isChecked()

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
        self.align_captions_checkbox.setChecked(bool(config.youtube_align_captions))
        self.set_auto_update_ytdlp(config.auto_update_ytdlp)
        self.set_ytdlp_prerelease(config.ytdlp_prerelease)

    def contribute(self, config):
        """Return a new config with this panel's fields applied.

        Uses ``dataclasses.replace`` so the frozen-config invariant is preserved.
        Called by :meth:`SettingsTab.commit_settings` as part of the contribute fold.

        Note: validation of ``cookies_file`` (file must exist when non-empty)
        stays in :meth:`SettingsTab.commit_settings` — it runs before the fold
        so an invalid path aborts Save before ``contribute`` is ever called.

        ``ytdlp_location`` is not written: the path override left the GUI
        (D15 item 3) and a hand-set value is kept.
        """
        cookies_file_str = self.get_cookies_file()
        return replace(
            config,
            youtube_cookies_from_browser=self.get_cookies_from_browser(),
            youtube_cookies_file=Path(cookies_file_str) if cookies_file_str else None,
            youtube_max_duration_s=self.get_max_duration_seconds(),
            youtube_playlist_max=self.get_playlist_max(),
            youtube_align_captions=self.align_captions_checkbox.isChecked(),
            auto_update_ytdlp=self.get_auto_update_ytdlp(),
            ytdlp_prerelease=self.get_ytdlp_prerelease(),
        )
