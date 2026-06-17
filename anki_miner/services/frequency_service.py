"""Service for loading and looking up word frequency data."""

import csv
import logging
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.utils.csv_utils import detect_delimiter, is_header_row

logger = logging.getLogger(__name__)

# Header first-column values that unambiguously declare (word, rank) column order.
# The Yomitan freq importer writes ``['term', 'rank']`` as its header; we recognise
# both ``term`` and ``word`` so user-exported variants are also covered.
_WORD_FIRST_HEADER_COLS = {"term", "word"}


def _is_word_first_header(row: list[str]) -> bool:
    """Return True when the header's first column names the word column.

    Detects the column-order contract written by the Yomitan freq importer
    (``['term', 'rank']``) so that FrequencyService can skip the ambiguous
    int-first auto-detect for those files.
    """
    return bool(row) and row[0].strip().lower() in _WORD_FIRST_HEADER_COLS


def _extract_word_rank(row: list[str], *, word_first: bool = False) -> tuple[str, int | None]:
    """Extract a word and numeric rank from a row with any number of columns.

    When *word_first* is True the caller has already determined that col-0 is
    the word (e.g. because the file carries a recognised header whose first
    column is ``term`` or ``word``).  In that case the ambiguous int-first
    auto-detect path is skipped, which prevents fullwidth / ASCII digit-only
    terms like ``'１０'`` or ``'2020'`` from being misread as rank values in
    the standard 2-column case.

    For 2-column ``word_first`` files: ``(col-0, int(col-1))``.
    For 3+-column ``word_first`` files: col-0 is fixed as the word; the first
    numeric cell among the remaining columns becomes the rank.  This supports
    multi-column exports like ``term, reading, frequency, …`` where the word is
    always in col-0 but the rank is not in col-1.

    Without *word_first*, the legacy auto-detect logic is preserved:
    ``(rank, word)`` is tried first, then ``(word, rank)``, then a
    column-position-agnostic scan for files with 3+ columns.

    Args:
        row: CSV/TSV row as list of strings.
        word_first: When True, treat col-0 as the word and skip int-first detect.

    Returns:
        Tuple of (word, rank) or ("", None) if no valid pair found.
    """
    if word_first:
        word = row[0].strip() if row else ""
        if not word:
            return "", None
        if len(row) == 2:
            # Standard importer output: (word, rank) — col-1 must be numeric.
            try:
                return word, int(row[1].strip())
            except ValueError:
                return "", None
        # Multi-column with word-first header: scan cols 1+ for the first int.
        for cell in row[1:]:
            val = cell.strip()
            if not val:
                continue
            try:
                return word, int(val)
            except ValueError:
                continue
        return "", None

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
