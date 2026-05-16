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


def _extract_word_rank(row: list[str]) -> tuple[str, int | None]:
    """Extract a word and numeric rank from a row with any number of columns.

    First tries the standard 2-column orderings ``(rank, word)`` then
    ``(word, rank)`` using only columns 0 and 1. For rows with more than
    two columns (e.g. ``term, reading, frequency, …``) falls back to a
    column-position-agnostic scan: the first non-numeric cell becomes the
    word and the first numeric cell becomes the rank.

    Args:
        row: CSV/TSV row as list of strings.

    Returns:
        Tuple of (word, rank) or ("", None) if no valid pair found.
    """
    # Try standard 2-column formats first: (rank, word) then (word, rank)
    try:
        rank = int(row[0].strip())
        word = row[1].strip()
        if word:
            return word, rank
    except ValueError:
        pass

    try:
        word = row[0].strip()
        rank = int(row[1].strip())
        if word:
            return word, rank
    except ValueError:
        pass

    # For multi-column files (e.g. term, reading, frequency, ...),
    # find first non-numeric column as word and first numeric column as rank
    if len(row) > 2:
        first_word = ""
        first_rank: int | None = None
        for cell in row:
            val = cell.strip()
            if not val:
                continue
            try:
                num = int(val)
                if first_rank is None:
                    first_rank = num
            except ValueError:
                if not first_word:
                    first_word = val
            if first_word and first_rank is not None:
                return first_word, first_rank

    return "", None


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

                    word, rank = _extract_word_rank(row)
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
