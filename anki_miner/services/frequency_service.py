"""Service for loading and looking up word frequency data."""

import csv
import logging
from pathlib import Path

from anki_miner.exceptions import SetupError

logger = logging.getLogger(__name__)

# Common header keywords that indicate a header row (case-insensitive)
_HEADER_KEYWORDS = {"word", "rank", "frequency", "freq", "lemma", "reading", "kana", "kanji"}


def _detect_delimiter(sample: str) -> str:
    """Detect whether a file uses tab or comma as delimiter.

    Args:
        sample: First few lines of the file.

    Returns:
        Detected delimiter character.
    """
    tab_count = sample.count("\t")
    comma_count = sample.count(",")
    return "\t" if tab_count > comma_count else ","


def _is_header_row(row: list[str]) -> bool:
    """Check if a row looks like a header based on common keywords."""
    return any(cell.strip().lower() in _HEADER_KEYWORDS for cell in row)


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
                delimiter = _detect_delimiter(sample)

                reader = csv.reader(f, delimiter=delimiter)
                first_row = True
                for row in reader:
                    if len(row) < 2:
                        continue
                    if first_row:
                        first_row = False
                        if _is_header_row(row):
                            continue
                    # Auto-detect format
                    try:
                        # Format: rank, word
                        rank = int(row[0])
                        word = row[1].strip()
                    except ValueError:
                        # Format: word, rank
                        word = row[0].strip()
                        try:
                            rank = int(row[1])
                        except ValueError:
                            continue  # Skip unparseable rows

                    if word and word not in data:
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

    def lookup_batch(self, words: list[str]) -> list[int | None]:
        """Look up frequency ranks for multiple words.

        Args:
            words: List of words.

        Returns:
            List of ranks (same order as input).
        """
        return [self.lookup(word) for word in words]
