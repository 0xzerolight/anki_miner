"""Service for filtering vocabulary words."""

from __future__ import annotations

import dataclasses
import unicodedata
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import LineLemmas, TokenizedWord

if TYPE_CHECKING:
    from anki_miner.services.word_list_service import WordListService


def _normalize_sentence(text: str) -> str:
    """Normalize a sentence for dedup-key purposes only.

    NFKC fold (full/half-width punctuation, kana, digits) + whitespace
    collapse. The original sentence on each word is left untouched.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


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

        Comparison is by lemma only. ``existing_vocabulary`` is populated from
        AnkiService.get_existing_vocabulary(), which reads the raw first field
        (Expression) of every note. Cards written by this pipeline now store
        the lemma there, so the comparison is self-consistent for newly-mined
        cards. Legacy cards with surface-form Expressions still block their
        own surface (because their stored Expression IS the surface string),
        but will not block a re-mining of the same word under its lemma form
        — that's a known intentional consequence of switching to lemma-based
        mining; see CHANGELOG.

        Args:
            all_words: List of all discovered words.
            existing_vocabulary: Set of Expression-field values already in
                Anki (a mix of lemmas and, for legacy cards, surfaces).

        Returns:
            List of unknown words (lemma not in existing vocabulary).
        """
        return [word for word in all_words if word.lemma not in existing_vocabulary]

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

        return [word for word in words if word.frequency_rank is None or word.frequency_rank <= max_rank]

    def filter_by_word_lists(
        self,
        words: list[TokenizedWord],
        word_list_service: WordListService,
    ) -> list[TokenizedWord]:
        """Filter words using blacklist/whitelist.

        Blacklist/whitelist entries match against ``word.lemma`` (dictionary
        form). Users should enter dictionary forms in their list files
        (e.g. 食べる, not 食べた). Whitelist short-circuits the blacklist
        check, so an entry on both lists is kept.

        Args:
            words: List of words to filter.
            word_list_service: Service providing blacklist/whitelist lookups.

        Returns:
            Filtered list of words.
        """
        result = []
        for word in words:
            if word_list_service.is_whitelisted(word.lemma) or not word_list_service.is_blacklisted(word.lemma):
                result.append(word)
        return result

    def deduplicate_by_sentence(
        self,
        words: list[TokenizedWord],
    ) -> list[TokenizedWord]:
        """Remove words that share a sentence with an already-selected word.

        For each unique sentence text, only the first word is kept. The dedup
        key is NFKC-normalized with whitespace collapsed so that punctuation
        and spacing variants do not slip through.

        Args:
            words: List of words to deduplicate.

        Returns:
            Deduplicated list of words.
        """
        seen_sentences: set[str] = set()
        result = []
        for word in words:
            key = _normalize_sentence(word.sentence)
            if key not in seen_sentences:
                seen_sentences.add(key)
                result.append(word)
        return result

    def filter_i_plus_one(
        self,
        mineable_unknowns: list[TokenizedWord],
        line_index: list[LineLemmas],
    ) -> list[TokenizedWord]:
        """Restrict mining to words covered by at least one i+1 example sentence.

        An "i+1" line is a subtitle line whose intersection with the target
        unknown-lemma set has exactly one element — i.e. the line contains
        exactly one of the words being considered for mining. For each
        candidate word, the earliest such line in ``line_index`` order wins
        the tie-break; words with no i+1 line are dropped.

        The returned words have their sentence/timing/sentence_furigana/
        sentence_reading swapped to those of the selected line. Per-word
        fields (``surface``, ``lemma``, ``reading``, ``expression_furigana``,
        ``expression_reading``, ``frequency_rank``, ``pos``, ``video_file``)
        are preserved unchanged.

        Args:
            mineable_unknowns: Words remaining after blacklist, frequency,
                and word-list filters (the count basis for "unknown").
            line_index: Per-line lemma index for the episode, in original
                subtitle order.

        Returns:
            Filtered list of words with i+1 sentence/timing swapped in,
            preserving the input order of ``mineable_unknowns``.
        """
        if not mineable_unknowns or not line_index:
            return []

        target_lemmas = {w.lemma for w in mineable_unknowns}

        earliest: dict[str, LineLemmas] = {}
        for line in line_index:
            intersect = line.lemmas & target_lemmas
            if len(intersect) == 1:
                (only,) = intersect
                earliest.setdefault(only, line)

        result: list[TokenizedWord] = []
        for word in mineable_unknowns:
            match = earliest.get(word.lemma)
            if match is None:
                continue
            result.append(
                dataclasses.replace(
                    word,
                    sentence=match.line_text,
                    start_time=match.start_time,
                    end_time=match.end_time,
                    duration=match.duration,
                    sentence_furigana=match.sentence_furigana,
                    sentence_reading=match.sentence_reading,
                )
            )
        return result

    def filter_by_episode_count(
        self,
        words: list[TokenizedWord],
        cross_episode_counts: dict[str, int],
        min_appearances: int,
    ) -> list[TokenizedWord]:
        """Filter words by cross-episode appearance count.

        Only keeps words that appear in at least `min_appearances` episodes.

        Args:
            words: List of words to filter.
            cross_episode_counts: Mapping of lemma to episode count.
            min_appearances: Minimum number of episodes a word must appear in.

        Returns:
            Filtered list of words.
        """
        if min_appearances <= 1:
            return words

        return [word for word in words if cross_episode_counts.get(word.lemma, 0) >= min_appearances]
