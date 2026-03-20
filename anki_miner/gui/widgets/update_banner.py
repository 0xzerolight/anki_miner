"""Dismissible banner widget for update notifications."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class UpdateBanner(QFrame):
    """A dismissible banner that shows when an update is available.

    Displays the new version number with a "Download" button to open
    the release page and an "X" button to dismiss.
    """

    def __init__(self, latest_version: str, release_url: str, parent=None):
        """Initialize the update banner.

        Args:
            latest_version: The latest available version string
            release_url: URL to the GitHub release page
            parent: Optional parent widget
        """
        super().__init__(parent)
        self.release_url = release_url

        self.setObjectName("update-banner")

        layout = QHBoxLayout()
        layout.setContentsMargins(8, 4, 8, 4)

        label = QLabel(f"Anki Miner v{latest_version} is available!")
        layout.addWidget(label)
        layout.addStretch()

        download_btn = QPushButton("Download")
        download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        download_btn.clicked.connect(self._on_download)
        layout.addWidget(download_btn)

        dismiss_btn = QPushButton("\u2715")
        dismiss_btn.setObjectName("dismissBtn")
        dismiss_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss_btn.clicked.connect(self._on_dismiss)
        layout.addWidget(dismiss_btn)

        self.setLayout(layout)

    def _on_download(self) -> None:
        """Open the release URL in the default browser."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(self.release_url))

    def _on_dismiss(self) -> None:
        """Hide and remove the banner."""
        self.setVisible(False)
        self.deleteLater()
