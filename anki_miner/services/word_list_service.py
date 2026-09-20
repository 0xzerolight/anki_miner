"""Service for managing custom word blacklists and whitelists."""

import logging
import unicodedata
from collections.abc import Callable
from pathlib import Path

from anki_miner.exceptions import SetupError
from anki_miner.services.reading._util import decode_with_ladder

logger = logging.getLogger(__name__)

#: Ladder for a caller with no profile to hand over (tests, ad-hoc callers):
#: UTF-8, with the BOM stripped rather than kept as part of the first entry.
_DEFAULT_ENCODINGS = ("utf-8-sig",)


class WordListService:
    """Manages word blacklists and whitelists.

    Reads plain text files with one word per line.
    Blank lines and lines starting with # are ignored.
    """

    def __init__(
        self,
        blacklist_path: Path | None = None,
        whitelist_path: Path | None = None,
        *,
        dedup_fold: Callable[[str], str] | None = None,
        encodings: tuple[str, ...] = _DEFAULT_ENCODINGS,
        script_check: Callable[[str], bool] | None = None,
    ):
        """Initialize the word list service.

        Args:
            blacklist_path: Path to blacklist file, or None to skip.
            whitelist_path: Path to whitelist file, or None to skip.
            dedup_fold: The mining language's comparison fold (S3), applied to
                every entry at load and every probe; ``None`` keeps the NFC'd
                entries and raw probes.
            encodings: The mining language's ``import_encodings`` ladder — a
                hand-made list is whatever the user's Notepad writes (GB18030 on
                a mainland machine, Big5 on a Taiwanese one), and UTF-8 alone
                dropped the whole file.
            script_check: Validates a single-byte leg of that ladder
                (``utils.subtitle_encoding.script_check_kwarg``).
        """
        self._dedup_fold = dedup_fold
        self._encodings = encodings
        self._script_check = script_check
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
            self._blacklist = {self._key(word) for word in self._read_word_file(self._blacklist_path)}
            logger.info("Loaded %d blacklisted words", len(self._blacklist))

        if self._whitelist_path is not None:
            self._whitelist = {self._key(word) for word in self._read_word_file(self._whitelist_path)}
            logger.info("Loaded %d whitelisted words", len(self._whitelist))

        self._loaded = True

    def _key(self, word: str) -> str:
        """The comparison key for an entry or a probe (identity without a fold)."""
        return word if self._dedup_fold is None else self._dedup_fold(word)

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
        return self._key(word) in self._blacklist

    def is_whitelisted(self, word: str) -> bool:
        """Check if a word is on the whitelist.

        Args:
            word: Word to check.

        Returns:
            True if the word is whitelisted.
        """
        return self._key(word) in self._whitelist

    def whitelist_entries(self) -> frozenset[str]:
        """Every whitelist entry as written in the file (NFC, stripped, and folded when the language folds).

        The run-end coverage report diffs this against what got mined; it is
        the only reason the set is exposed rather than queried one word at a
        time.

        Returns:
            The loaded whitelist, empty when none was configured.
        """
        return frozenset(self._whitelist)

    def _read_word_file(self, path: Path) -> set[str]:
        """Read a word list file.

        Args:
            path: Path to the file.

        Returns:
            Set of words from the file.

        Raises:
            SetupError: If the file cannot be read or decoded.
        """
        if not path.exists():
            # The path is diagnostics, not the sentence (rule 5).
            logger.warning("Word list file missing: path=%s", path)
            raise SetupError("Your word list file is missing.")

        try:
            with path.open("rb") as f:
                raw = f.read()
            # Bytes, not text: a hand-made list is whatever the user's editor
            # wrote, and only the shared ladder decoder knows which leg that is.
            text, _ = decode_with_ladder(raw, encodings=self._encodings, script_check=self._script_check)
            words: set[str] = set()
            for line in text.splitlines():
                stripped = unicodedata.normalize("NFC", line.strip())
                if stripped and not stripped.startswith("#"):
                    words.add(stripped)
            return words
        except MemoryError:
            raise
        except Exception as e:
            logger.warning("Word list file unreadable: path=%s exc=%s: %s", path, type(e).__name__, e)
            raise SetupError("Could not read your word list file.") from e
