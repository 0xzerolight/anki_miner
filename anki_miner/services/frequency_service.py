"""Service for loading and looking up word frequency data."""

import csv
import logging
from pathlib import Path

from anki_miner.exceptions import SetupError

logger = logging.getLogger(__name__)


class FrequencyService:
    """Load and look up word frequency rankings from CSV.

    Supports two CSV formats (auto-detected):
    - rank, word (first column is numeric)
    - word, rank (first column is non-numeric)
    """

    def __init__(self, frequency_list_path: Path):
        """Initialize with path to frequency list CSV.

        Args:
            frequency_list_path: Path to the frequency list file.
        """
        self._path = frequency_list_path
        self._data: dict[str, int] | None = None

    def load(self) -> bool:
        """Load frequency data from CSV.

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
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 2:
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
            logger.info(f"Loaded {len(data)} frequency entries")
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
