"""About dialog for Anki Miner.

Replaces the old ``QMessageBox.about`` HTML blob with a themed
:class:`EnhancedDialog`: logo + name + version + tagline, a short blurb, the
keyboard-shortcut list, and a GitHub link.

Bundled-FFmpeg (GPLv3) attribution lives in ``licenses/ffmpeg/`` in the repo,
not in this dialog.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import EnhancedDialog

GITHUB_URL = "https://github.com/0xzerolight/anki_miner"

ABOUT_TAGLINE = "Turn Immersion Into Vocabulary"
ABOUT_BLURB = (
    "Mine Japanese vocabulary cards straight from video — screenshots, audio, "
    "and definitions, automatically into Anki."
)

ABOUT_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+1..6", "Switch tabs"),
    ("Ctrl+T", "Cycle favorite themes"),
    ("Ctrl+,", "Open Settings"),
    ("Ctrl+Shift+V", "Run system validation"),
    ("F1", "Show this dialog"),
]


class AboutDialog(EnhancedDialog):
    """Compact, themed About card built on :class:`EnhancedDialog`."""

    def __init__(self, version: str, parent=None):
        """Build the dialog for the given version string."""
        super().__init__(parent, title="About Anki Miner")
        self.setMinimumWidth(440)
        self._build(version)

    def _build(self, version: str) -> None:
        self.add_content(self._header_row(version))
        self.add_content(self._blurb_label())
        self.add_content(self._shortcuts_section())

        self.add_button("GitHub", "secondary", self._open_github)
        self.add_close_button("Close")

    def _header_row(self, version: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.md)

        logo = QLabel()
        icon_path = get_resource_dir() / "icons" / "anki_miner.svg"
        if icon_path.exists():
            logo.setPixmap(QIcon(str(icon_path)).pixmap(64, 64))
        logo.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(logo)

        text = QWidget()
        text_layout = QVBoxLayout(text)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(SPACING.xxs)

        name = QLabel("Anki Miner")
        name.setObjectName("heading1")

        ver = QLabel(f"Version {version}")
        ver.setObjectName("caption")

        tagline = QLabel(ABOUT_TAGLINE)
        tagline.setObjectName("caption")

        text_layout.addWidget(name)
        text_layout.addWidget(ver)
        text_layout.addWidget(tagline)
        layout.addWidget(text, 1)

        return row

    def _blurb_label(self) -> QLabel:
        blurb = QLabel(ABOUT_BLURB)
        blurb.setWordWrap(True)
        return blurb

    def _shortcuts_section(self) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING.xs)

        heading = QLabel("Keyboard Shortcuts")
        heading.setObjectName("heading3")
        layout.addWidget(heading)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACING.md)
        grid.setVerticalSpacing(SPACING.xxs)
        for r, (key, desc) in enumerate(ABOUT_SHORTCUTS):
            key_label = QLabel(key)
            key_font = key_label.font()
            key_font.setBold(True)
            key_font.setPixelSize(FONT_SIZES.body_sm)
            key_label.setFont(key_font)
            grid.addWidget(key_label, r, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(QLabel(desc), r, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        return section

    def _open_github(self) -> None:
        QDesktopServices.openUrl(QUrl(GITHUB_URL))
