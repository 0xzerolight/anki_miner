"""Service for filtering vocabulary words."""

from __future__ import annotations

from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord

if TYPE_CHECKING:
    from anki_miner.services.word_list_service import WordListService


class WordFilterService:
    """Filter vocabulary words based on various criteria (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the word filter service.

        Args:
            config: Configuration for filtering
        """
        self.config = config

    def filter_unknown(
        self,
        all_words: list[TokenizedWord],
        existing_vocabulary: set[str],
    ) -> list[TokenizedWord]:
        """Filter out words that already exist in Anki collection.

        Args:
            all_words: List of all discovered words
            existing_vocabulary: Set of words already in Anki (lemmas)

        Returns:
            List of unknown words (not in existing vocabulary)
        """
        unknown_words = []

        for word in all_words:
            # Check both lemma and surface form against existing vocabulary
            if word.lemma not in existing_vocabulary and word.surface not in existing_vocabulary:
                unknown_words.append(word)

        return unknown_words

    def filter_by_length(
        self,
        words: list[TokenizedWord],
        min_length: int | None = None,
        max_length: int | None = None,
    ) -> list[TokenizedWord]:
        """Filter words by length.

        Args:
            words: List of words to filter
            min_length: Minimum word length (defaults to config)
            max_length: Maximum word length (optional)

        Returns:
            List of words within length bounds
        """
        if min_length is None:
            min_length = self.config.min_word_length

        filtered = []
        for word in words:
            word_len = len(word.surface)
            if word_len >= min_length and (max_length is None or word_len <= max_length):
                filtered.append(word)

        return filtered

    def filter_by_frequency(
        self,
        words: list[TokenizedWord],
        max_rank: int | None = None,
    ) -> list[TokenizedWord]:
        """Filter words by frequency rank (keep only top-N most common words).

        Words without a frequency rank are always included (benefit of the doubt).

        Args:
            words: List of words to filter.
            max_rank: Maximum frequency rank to include (e.g., 10000 means
                      only words ranked 1-10000 are kept). None or 0 means no filtering.

        Returns:
            Filtered list of words.
        """
        if not max_rank or max_rank <= 0:
            return words

        return [
            word for word in words if word.frequency_rank is None or word.frequency_rank <= max_rank
        ]

    def filter_by_word_lists(
        self,
        words: list[TokenizedWord],
        word_list_service: WordListService,
    ) -> list[TokenizedWord]:
        """Filter words using blacklist/whitelist.

        Removes blacklisted words. Whitelisted words are always kept.
        If a word is on both lists, whitelist wins.

        Args:
            words: List of words to filter.
            word_list_service: Service providing blacklist/whitelist lookups.

        Returns:
            Filtered list of words.
        """
        result = []
        for word in words:
            if word_list_service.is_whitelisted(word.lemma) or not word_list_service.is_blacklisted(
                word.lemma
            ):
                result.append(word)
        return result

    def deduplicate_by_sentence(
        self,
        words: list[TokenizedWord],
    ) -> list[TokenizedWord]:
        """Remove words that share a sentence with an already-selected word.

        For each unique sentence text, only the first word is kept.

        Args:
            words: List of words to deduplicate.

        Returns:
            Deduplicated list of words.
        """
        seen_sentences: set[str] = set()
        result = []
        for word in words:
            if word.sentence not in seen_sentences:
                seen_sentences.add(word.sentence)
                result.append(word)
        return result
