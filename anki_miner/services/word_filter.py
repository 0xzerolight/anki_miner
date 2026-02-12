"""Service for filtering vocabulary words."""

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import TokenizedWord


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
