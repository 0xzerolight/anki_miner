"""Service for loading and looking up word frequency data."""

import csv
import logging
from pathlib import Path

from anki_miner.exceptions import SetupError

# Re-exported from the shared parser module so the historical import path
# ``anki_miner.services.frequency_service._extract_word_rank`` keeps working.
# Single source of truth lives in ``services/frequency/csv_parse``.
from anki_miner.services.frequency.csv_parse import (
    _WORD_FIRST_HEADER_COLS,
    _extract_word_rank,
    _is_word_first_header,
)
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row

logger = logging.getLogger(__name__)

__all__ = [
    "FrequencyService",
    "_extract_word_rank",
    "_is_word_first_header",
    "_WORD_FIRST_HEADER_COLS",
]


class FrequencyService:
    """Load and look up word frequency rankings from CSV/TSV.

    Supports two column formats (auto-detected):
    - rank, word (first column is numeric)
    - word, rank (first column is non-numeric)

    Supports both comma-separated and tab-separated files.
    Header rows are automatically skipped.
    """

    def __init__(self, frequency_list_path: Path):
        """Initialize with path to frequency list file.

        Args:
            frequency_list_path: Path to the frequency list file.
        """
        self._path = frequency_list_path
        self._data: dict[str, int] | None = None
        self._entry_count: int = 0

    @property
    def entry_count(self) -> int:
        """Number of entries loaded."""
        return self._entry_count

    def load(self) -> bool:
        """Load frequency data from file.

        Returns:
            True if loaded successfully.

        Raises:
            SetupError: If file missing or unparseable.
        """
        if not self._path.exists():
            raise SetupError(
                f"Frequency list not found at: {self._path}. "
                f"Download a Japanese frequency list and place it in ~/.anki_miner/"
            )

        data: dict[str, int] = {}
        try:
            with open(self._path, encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = detect_delimiter(sample)

                reader = csv.reader(f, delimiter=delimiter)
                first_row = True
                word_first = False
                for row in reader:
                    if len(row) < 2:
                        continue
                    if first_row:
                        first_row = False
                        if is_header_row(row):
                            # Detect importer-written headers that declare the word
                            # column unambiguously so we can skip int-first auto-detect.
                            word_first = _is_word_first_header(row)
                            continue

                    word, rank = _extract_word_rank(row, word_first=word_first)
                    if word and rank is not None and word not in data:
                        data[word] = rank

            self._data = data
            self._entry_count = len(data)
            logger.info(f"Loaded {len(data)} frequency entries from {self._path.name}")
            return True

        except Exception as e:
            raise SetupError(f"Error loading frequency data: {e}") from e

    def is_available(self) -> bool:
        """Check if frequency data has been loaded."""
        return self._data is not None

    def lookup(self, word: str) -> int | None:
        """Look up frequency rank for a word.

        Args:
            word: Word to look up.

        Returns:
            Frequency rank (1 = most common), or None if not found.
        """
        if not self._data:
            return None
        return self._data.get(word)
