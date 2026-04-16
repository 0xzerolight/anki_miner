"""Service for loading and looking up pitch accent data."""

import csv
import logging
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.services.frequency_service import _detect_delimiter, _is_header_row

logger = logging.getLogger(__name__)


class PitchAccentService:
    """Load and look up pitch accent patterns from CSV/TSV.

    Supports Kanjium-format and similar pitch accent files:
    - reading (kana), kanji, pitch_pattern
    - Tab or comma separated
    - Header rows are automatically skipped
    """

    def __init__(self, pitch_accent_path: Path):
        """Initialize with path to pitch accent file.

        Args:
            pitch_accent_path: Path to the pitch accent CSV/TSV file.
        """
        self._path = pitch_accent_path
        self._data: dict[str, str] | None = None
        self._entry_count: int = 0

    @property
    def entry_count(self) -> int:
        """Number of entries loaded."""
        return self._entry_count

    def load(self) -> bool:
        """Load pitch accent data from file.

        Returns:
            True if loaded successfully.

        Raises:
            SetupError: If the file is missing or unparseable.
        """
        if not self._path.exists():
            raise SetupError(
                f"Pitch accent file not found at: {self._path}. "
                f"Download pitch accent data and place it in ~/.anki_miner/"
            )

        data: dict[str, str] = {}
        try:
            with open(self._path, encoding="utf-8") as f:
                sample = f.read(4096)
                f.seek(0)
                delimiter = _detect_delimiter(sample)

                reader = csv.reader(f, delimiter=delimiter)
                first_row = True
                for row in reader:
                    if len(row) < 3:
                        continue
                    if first_row:
                        first_row = False
                        if _is_header_row(row):
                            continue
                    reading, kanji, pattern = row[0].strip(), row[1].strip(), row[2].strip()
                    # Store by kanji (primary key) and by reading (fallback)
                    if kanji and kanji not in data:
                        data[kanji] = pattern
                    if reading and reading not in data:
                        data[reading] = pattern

            self._data = data
            self._entry_count = len(data)
            logger.info(f"Loaded {len(data)} pitch accent entries from {self._path.name}")

            if len(data) == 0:
                logger.warning(
                    f"Pitch accent file {self._path.name} loaded but contained 0 valid entries. "
                    f"Expected format: reading,kanji,pattern (CSV or TSV with 3+ columns)."
                )

            return True

        except Exception as e:
            raise SetupError(f"Error loading pitch accent data: {e}") from e

    def is_available(self) -> bool:
        """Check if pitch accent data has been loaded."""
        return self._data is not None

    def lookup(self, word: str, reading: str = "") -> str | None:
        """Look up pitch accent pattern for a word.

        Args:
            word: Word to look up (kanji or kana form).
            reading: Optional kana reading for fallback lookup.

        Returns:
            Pitch accent pattern string, or None if not found.
        """
        if not self._data:
            return None

        # Try kanji/word first, then reading
        result = self._data.get(word)
        if result:
            return result

        if reading:
            return self._data.get(reading)

        return None

    def lookup_batch(self, words: list[tuple[str, str]]) -> list[str | None]:
        """Look up pitch accents for multiple (word, reading) pairs.

        Args:
            words: List of (word, reading) tuples.

        Returns:
            List of pitch accent patterns (same order as input).
        """
        return [self.lookup(word, reading) for word, reading in words]
