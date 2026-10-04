"""Dismissible banner widget for update notifications."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton
from anki_miner.services.app_updater import in_place_supported
from anki_miner.services.update_checker import UpdateInfo
from anki_miner.utils.i18n import tr_format

# What the banner is showing. Driven by AppUpdateController for the in-place
# flow; a banner on an install that cannot update itself stays in _OFFER.
_OFFER = "offer"
_DOWNLOADING = "downloading"
_READY = "ready"
_FAILED = "failed"


class UpdateBanner(QFrame):
    """A dismissible banner shown when an update is available.

    Where the install can update itself (services/app_updater.py, AppImage and
    the Windows installer), the primary button reads *Update now*, then
    *Cancel* while the download runs, then *Restart now*. Everywhere else, and
    after a failed in-place update, it is the deep-linked browser download
    (e.g. "Download .deb", "Download installer"). "Skip this version" and the
    "X" dismiss control sit beside it.

    Held as a singleton on :class:`MainWindow`; on subsequent update checks the
    same instance is reused via :meth:`update_info` rather than reconstructed,
    to avoid races against in-flight Qt callbacks.

    Signals:
        skip_requested: Emitted with the version string when the user clicks
            "Skip this version". MainWindow handles persisting it to config.
        install_requested: Emitted with the :class:`UpdateInfo` on *Update now*.
        cancel_requested: Emitted on *Cancel* while the download runs.
        restart_requested: Emitted on *Restart now*.
    """

    skip_requested = pyqtSignal(str)
    install_requested = pyqtSignal(object)  # UpdateInfo
    cancel_requested = pyqtSignal()
    restart_requested = pyqtSignal()

    def __init__(self, info: UpdateInfo, parent=None):
        """Initialize the update banner.

        Args:
            info: :class:`UpdateInfo` carrying version, asset URL, and release page URL.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._info = info
        self._state = _OFFER

        self.setObjectName("update-banner")

        layout = QHBoxLayout()
        layout.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        layout.setSpacing(SPACING.xs)

        self._label = QLabel()
        layout.addWidget(self._label)
        layout.addStretch()

        # E05: the calm banner shape. The download (or View release) is the one
        # accent; Skip and the dismiss glyph are quiet, as on the screen banner.
        self._download_btn = ModernButton("", variant="primary")
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)
        layout.addWidget(self._download_btn)

        self._skip_btn = ModernButton(self.tr("Skip this version"), variant="ghost")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        layout.addWidget(self._skip_btn)

        self._dismiss_btn = ModernButton("✕", variant="ghost", square=True)
        self._dismiss_btn.setAccessibleName(self.tr("Close"))
        self._dismiss_btn.setToolTip(self.tr("Close"))
        self._dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dismiss_btn.clicked.connect(self._on_dismiss)
        layout.addWidget(self._dismiss_btn)

        self.setLayout(layout)
        self._refresh()

    # ------------------------------------------------------------------ helpers

    def _format_label(self, info: UpdateInfo) -> str:
        if self._state == _DOWNLOADING:
            return tr_format(self.tr("Downloading Anki Miner v%1…"), info.version)
        if self._state == _READY:
            return tr_format(self.tr("Anki Miner v%1 is downloaded. Restart to finish updating."), info.version)
        if self._state == _FAILED:
            return tr_format(self.tr("Anki Miner v%1 could not be installed automatically."), info.version)
        return tr_format(self.tr("Anki Miner v%1 is available"), info.version)

    def _download_label(self, info: UpdateInfo) -> str:
        """The primary button's label for the current state."""
        if self._state == _DOWNLOADING:
            return self.tr("Cancel")
        if self._state == _READY:
            return self.tr("Restart now")
        if self._state == _OFFER and in_place_supported(info):
            return self.tr("Update now")
        url = info.asset_url
        if url is None:
            return self.tr("View release")
        lowered = url.lower()
        if lowered.endswith(".deb"):
            return self.tr("Download .deb")
        if lowered.endswith(".appimage"):
            return self.tr("Download AppImage")
        if lowered.endswith("setup.exe"):
            return self.tr("Download installer")
        if lowered.endswith(".dmg"):
            return self.tr("Download disk image")
        return self.tr("View release")

    def _refresh(self) -> None:
        self._label.setText(self._format_label(self._info))
        self._download_btn.setText(self._download_label(self._info))
        # Skipping the version the user is installing would contradict the click.
        self._skip_btn.setVisible(self._state in (_OFFER, _FAILED))

    def _set_state(self, state: str) -> None:
        self._state = state
        self._refresh()
        self.setVisible(True)

    # ------------------------------------------------------------------ public

    def update_info(self, info: UpdateInfo) -> None:
        """Mutate the existing banner to reflect a new :class:`UpdateInfo`.

        Used for singleton reuse: the banner is held on :class:`MainWindow`
        and updated in place across update checks instead of being destroyed
        and recreated. Ignored while a download runs or waits for its restart:
        a later check (Help → Check for Updates) must not drop the run the
        user started.
        """
        if self._state in (_DOWNLOADING, _READY):
            return
        self._info = info
        self._state = _OFFER
        self._refresh()

    def show_offer(self) -> None:
        """Back to the offer (a cancelled download)."""
        self._set_state(_OFFER)

    def show_downloading(self) -> None:
        """The in-place download is running; the primary button cancels it."""
        self._set_state(_DOWNLOADING)

    def show_ready(self) -> None:
        """The update is staged; the primary button restarts into it."""
        self._set_state(_READY)

    def show_failed(self) -> None:
        """The in-place update failed; fall back to the browser download."""
        self._set_state(_FAILED)

    # ------------------------------------------------------------------ slots

    def _on_download(self) -> None:
        """Route the primary button by state; the browser download is the fallback."""
        if self._state == _DOWNLOADING:
            self.cancel_requested.emit()
            return
        if self._state == _READY:
            self.restart_requested.emit()
            return
        if self._state == _OFFER and in_place_supported(self._info):
            self.install_requested.emit(self._info)
            return

        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        url = self._info.asset_url or self._info.release_page_url
        QDesktopServices.openUrl(QUrl(url))

    def _on_skip(self) -> None:
        """Notify the parent that this version should be skipped, then hide."""
        self.skip_requested.emit(self._info.version)
        self.setVisible(False)

    def _on_dismiss(self) -> None:
        """Hide the banner for this launch (non-persistent)."""
        # Singleton-safe: hide rather than deleteLater so MainWindow can reuse
        # the same instance on the next update check. A running download keeps
        # going; show_ready() brings the banner back when it lands.
        self.setVisible(False)
