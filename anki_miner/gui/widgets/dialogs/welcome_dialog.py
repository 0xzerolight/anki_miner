"""First-run welcome dialog offering the recommended-resources download.

Shown once (gated by ``config.first_run_setup_done``) when a fresh install is
detected. Offers a one-click download of the recommended frequency list, pitch
accent data, and dictionary, or lets the user skip and set up manually.
"""

from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.base import EnhancedDialog

README_URL = "https://github.com/0xzerolight/anki_miner#recommended-resources"

WELCOME_BLURB = (
    "Anki Miner works best with a frequency list, pitch accent data, and a "
    "dictionary. Download the recommended set now?"
)


class WelcomeDialog(EnhancedDialog):
    """Compact first-run dialog: download recommended resources or skip.

    ``exec()`` returns ``QDialog.DialogCode.Accepted`` if the user chose to
    download, ``Rejected`` if they skipped or closed the dialog.
    """

    def __init__(self, parent=None):
        """Build the welcome dialog."""
        super().__init__(parent, title="Welcome to Anki Miner")
        self.setMinimumWidth(460)
        self._build()

    def _build(self) -> None:
        self.set_header(
            "info",
            "Welcome to Anki Miner",
            "Let's get you set up with the recommended resources.",
        )

        blurb = QLabel(WELCOME_BLURB)
        blurb.setWordWrap(True)
        self.add_content(blurb)

        link = QLabel(f'<a href="{README_URL}">What are these resources?</a>')
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(self._open_readme)
        self.add_content(link)

        self.add_button("Skip — set up manually", "secondary", self.reject)
        self.add_button("Download recommended resources", "primary", self.accept)

    def _open_readme(self) -> None:
        QDesktopServices.openUrl(QUrl(README_URL))
