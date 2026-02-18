"""Service for managing custom word blacklists and whitelists."""

import logging
from pathlib import Path

from anki_miner.exceptions import SetupError

logger = logging.getLogger(__name__)


class WordListService:
    """Manages word blacklists and whitelists.

    Reads plain text files with one word per line.
    Blank lines and lines starting with # are ignored.
    """

    def __init__(
        self,
        blacklist_path: Path | None = None,
        whitelist_path: Path | None = None,
    ):
        """Initialize the word list service.

        Args:
            blacklist_path: Path to blacklist file, or None to skip.
            whitelist_path: Path to whitelist file, or None to skip.
        """
        self._blacklist_path = blacklist_path
        self._whitelist_path = whitelist_path
        self._blacklist: set[str] = set()
        self._whitelist: set[str] = set()
        self._loaded = False

    def load(self) -> None:
        """Load word lists from files.

        Raises:
            SetupError: If a specified file cannot be read.
        """
        if self._blacklist_path is not None:
            self._blacklist = self._read_word_file(self._blacklist_path)
            logger.info(f"Loaded {len(self._blacklist)} blacklisted words")

        if self._whitelist_path is not None:
            self._whitelist = self._read_word_file(self._whitelist_path)
            logger.info(f"Loaded {len(self._whitelist)} whitelisted words")

        self._loaded = True

    def is_available(self) -> bool:
        """Check if the service has been loaded.

        Returns:
            True if load() has been called successfully.
        """
        return self._loaded

    def is_blacklisted(self, word: str) -> bool:
        """Check if a word is on the blacklist.

        Args:
            word: Word to check.

        Returns:
            True if the word is blacklisted.
        """
        return word in self._blacklist

    def is_whitelisted(self, word: str) -> bool:
        """Check if a word is on the whitelist.

        Args:
            word: Word to check.

        Returns:
            True if the word is whitelisted.
        """
        return word in self._whitelist

    def get_blacklist(self) -> set[str]:
        """Return the full blacklist set.

        Returns:
            Set of blacklisted words.
        """
        return set(self._blacklist)

    def get_whitelist(self) -> set[str]:
        """Return the full whitelist set.

        Returns:
            Set of whitelisted words.
        """
        return set(self._whitelist)

    @staticmethod
    def _read_word_file(path: Path) -> set[str]:
        """Read a word list file.

        Args:
            path: Path to the file.

        Returns:
            Set of words from the file.

        Raises:
            SetupError: If the file cannot be read.
        """
        if not path.exists():
            raise SetupError(f"Word list file not found: {path}")

        try:
            words: set[str] = set()
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        words.add(stripped)
            return words
        except Exception as e:
            raise SetupError(f"Error reading word list file {path}: {e}") from e
