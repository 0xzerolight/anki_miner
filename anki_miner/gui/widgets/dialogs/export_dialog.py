"""Export dialog for choosing format and saving vocabulary data."""

from pathlib import Path

from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.widgets.enhanced import ModernButton, SectionHeader
from anki_miner.models.word import WordData
from anki_miner.services.export_service import ExportService

# Format constants
FORMAT_CSV = 0
FORMAT_TSV = 1
FORMAT_VOCAB = 2

# File filters per format
_FILE_FILTERS = {
    FORMAT_CSV: "CSV Files (*.csv);;All Files (*)",
    FORMAT_TSV: "TSV Files (*.tsv);;All Files (*)",
    FORMAT_VOCAB: "Text Files (*.txt);;All Files (*)",
}

_DEFAULT_NAMES = {
    FORMAT_CSV: "words.csv",
    FORMAT_TSV: "words.tsv",
    FORMAT_VOCAB: "vocab.txt",
}


class ExportDialog(QDialog):
    """Dialog for exporting vocabulary data in various formats.

    Supports CSV, TSV, and vocabulary list (plain/takoboto/jpdb) exports.
    """

    def __init__(
        self,
        words: list[WordData],
        config: AnkiMinerConfig,
        parent=None,
    ):
        super().__init__(parent)
        self._words = words
        self._config = config
        self._output_path: Path | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setWindowTitle("Export Words")
        self.setMinimumWidth(500)
        self.resize(560, 420)

        layout = QVBoxLayout()
        layout.setSpacing(SPACING.md)
        layout.setContentsMargins(SPACING.lg, SPACING.lg, SPACING.lg, SPACING.lg)

        # Header
        header = SectionHeader("Export Words")
        layout.addWidget(header)

        # Format selection card
        format_frame = QFrame()
        format_frame.setObjectName("card")
        format_layout = QVBoxLayout()
        format_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)
        format_layout.setSpacing(SPACING.xs)

        format_label = QLabel("Export Format")
        format_label.setFont(self._make_font(13, QFont.Weight.Bold))
        format_layout.addWidget(format_label)

        self._format_group = QButtonGroup(self)
        self._radio_csv = QRadioButton("CSV (.csv)")
        self._radio_tsv = QRadioButton("TSV (.tsv)")
        self._radio_vocab = QRadioButton("Vocabulary List (.txt)")
        self._radio_csv.setChecked(True)

        self._format_group.addButton(self._radio_csv, FORMAT_CSV)
        self._format_group.addButton(self._radio_tsv, FORMAT_TSV)
        self._format_group.addButton(self._radio_vocab, FORMAT_VOCAB)

        for radio in (self._radio_csv, self._radio_tsv, self._radio_vocab):
            format_layout.addWidget(radio)

        # Vocab sub-options (hidden by default)
        self._vocab_options_frame = QFrame()
        vocab_opts_layout = QHBoxLayout()
        vocab_opts_layout.setContentsMargins(SPACING.lg, 0, 0, 0)
        vocab_opts_layout.setSpacing(SPACING.xs)

        vocab_fmt_label = QLabel("List format:")
        vocab_opts_layout.addWidget(vocab_fmt_label)

        self._vocab_format_combo = QComboBox()
        self._vocab_format_combo.addItems(["Plain (one word per line)", "Takoboto", "jpdb"])
        self._vocab_format_combo.setMinimumWidth(180)
        vocab_opts_layout.addWidget(self._vocab_format_combo)
        vocab_opts_layout.addStretch()

        self._vocab_options_frame.setLayout(vocab_opts_layout)
        self._vocab_options_frame.setVisible(False)
        format_layout.addWidget(self._vocab_options_frame)

        format_frame.setLayout(format_layout)
        layout.addWidget(format_frame)

        # Preview card
        preview_frame = QFrame()
        preview_frame.setObjectName("card")
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(SPACING.md, SPACING.sm, SPACING.md, SPACING.sm)

        with_def = sum(1 for w in self._words if w.has_definition)
        with_media = sum(1 for w in self._words if w.has_media)

        preview_text = f"{len(self._words)} words"
        if with_def:
            preview_text += f", {with_def} with definitions"
        if with_media:
            preview_text += f", {with_media} with media"

        preview_label = QLabel(preview_text)
        preview_label.setFont(self._make_font(12, QFont.Weight.Medium))
        preview_layout.addWidget(preview_label)

        preview_frame.setLayout(preview_layout)
        layout.addWidget(preview_frame)

        # File path row
        path_layout = QHBoxLayout()
        path_layout.setSpacing(SPACING.xs)

        self._path_input = QLineEdit()
        self._path_input.setPlaceholderText("Select output file...")
        self._path_input.setReadOnly(True)
        path_layout.addWidget(self._path_input)

        browse_btn = ModernButton("Browse...", icon="save", variant="secondary")
        browse_btn.clicked.connect(self._on_browse)
        path_layout.addWidget(browse_btn)

        layout.addLayout(path_layout)

        layout.addStretch()

        # Footer buttons
        footer = QHBoxLayout()
        footer.addStretch()

        cancel_btn = ModernButton("Cancel", variant="ghost")
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(cancel_btn)

        self._export_btn = ModernButton("Export", icon="save", variant="primary")
        self._export_btn.clicked.connect(self._on_export)
        self._export_btn.setEnabled(False)
        footer.addWidget(self._export_btn)

        layout.addLayout(footer)

        self.setLayout(layout)

        # Signals
        self._format_group.idToggled.connect(self._on_format_changed)

        # Escape to close
        esc = QShortcut(QKeySequence("Esc"), self)
        esc.activated.connect(self.reject)

    # ── Slots ───────────────────────────────────────────────

    def _on_format_changed(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        self._vocab_options_frame.setVisible(id == FORMAT_VOCAB)
        # Reset path when format changes
        self._output_path = None
        self._path_input.clear()
        self._export_btn.setEnabled(False)

    def _on_browse(self) -> None:
        fmt_id = self._format_group.checkedId()
        file_filter = _FILE_FILTERS.get(fmt_id, "All Files (*)")
        default_name = _DEFAULT_NAMES.get(fmt_id, "export.txt")

        path, _ = QFileDialog.getSaveFileName(self, "Export Words", default_name, file_filter)
        if path:
            self._output_path = Path(path)
            self._path_input.setText(path)
            self._export_btn.setEnabled(True)

    def _on_export(self) -> None:
        if not self._output_path:
            return

        fmt_id = self._format_group.checkedId()
        service = ExportService(self._config)

        try:
            if fmt_id == FORMAT_CSV:
                count = service.export_csv(self._words, self._output_path, include_media_refs=True)
            elif fmt_id == FORMAT_TSV:
                count = service.export_tsv(self._words, self._output_path)
            else:
                vocab_fmt = ["plain", "takoboto", "jpdb"][self._vocab_format_combo.currentIndex()]
                count = service.export_vocab_list(self._words, self._output_path, format=vocab_fmt)

            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {count} words to:\n{self._output_path}",
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export:\n{e}")

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _make_font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        font = QFont()
        font.setPixelSize(size)
        font.setWeight(weight)
        return font
