"""Dismissible banner widget for update notifications."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from anki_miner.services.update_checker import UpdateInfo
from anki_miner.utils.i18n import tr_format


class UpdateBanner(QFrame):
    """A dismissible banner shown when an update is available.

    Displays the new version with a deep-linked download button matching the
    user's install method (e.g. "Download .deb", "Download installer") plus a
    "Skip this version" button and an "X" dismiss control.

    Held as a singleton on :class:`MainWindow`; on subsequent update checks the
    same instance is reused via :meth:`update_info` rather than reconstructed,
    to avoid races against in-flight Qt callbacks.

    Signals:
        skip_requested: Emitted with the version string when the user clicks
            "Skip this version". MainWindow handles persisting it to config.
    """

    skip_requested = pyqtSignal(str)

    def __init__(self, info: UpdateInfo, parent=None):
        """Initialize the update banner.

        Args:
            info: :class:`UpdateInfo` carrying version, asset URL, and release page URL.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._info = info

        self.setObjectName("update-banner")

        layout = QHBoxLayout()
        layout.setContentsMargins(8, 4, 8, 4)

        self._label = QLabel(self._format_label(info))
        layout.addWidget(self._label)
        layout.addStretch()

        self._download_btn = QPushButton(self._download_label(info))
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)
        layout.addWidget(self._download_btn)

        self._skip_btn = QPushButton(self.tr("Skip this version"))
        self._skip_btn.setObjectName("skipBtn")
        self._skip_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip)
        layout.addWidget(self._skip_btn)

        dismiss_btn = QPushButton("✕")
        dismiss_btn.setObjectName("dismissBtn")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self._on_dismiss)
        layout.addWidget(dismiss_btn)

        self.setLayout(layout)

    # ------------------------------------------------------------------ helpers

    def _format_label(self, info: UpdateInfo) -> str:
        return tr_format(self.tr("Anki Miner v%1 is available!"), info.version)

    def _download_label(self, info: UpdateInfo) -> str:
        """Map the asset URL extension to a user-facing button label."""
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
        if lowered.endswith(".tar.gz"):
            return self.tr("Download archive")
        return self.tr("View release")

    # ------------------------------------------------------------------ public

    def update_info(self, info: UpdateInfo) -> None:
        """Mutate the existing banner to reflect a new :class:`UpdateInfo`.

        Used for singleton reuse — the banner is held on :class:`MainWindow`
        and updated in place across update checks instead of being destroyed
        and recreated.
        """
        self._info = info
        self._label.setText(self._format_label(info))
        self._download_btn.setText(self._download_label(info))

    # ------------------------------------------------------------------ slots

    def _on_download(self) -> None:
        """Open the asset URL (or release page fallback) in the default browser."""
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
        # the same instance on the next update check.
        self.setVisible(False)
