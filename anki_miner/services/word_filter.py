"""Service for filtering vocabulary words."""

from __future__ import annotations

import dataclasses
import unicodedata
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.utils import (
    generate_furigana,
    generate_reading,
    is_hiragana_only,
    is_katakana_only,
    wrap_target_furigana,
    wrap_target_plain,
)

if TYPE_CHECKING:
    from anki_miner.services.word_list_service import WordListService
    from anki_miner.services.wordset_service import WordsetService


def _normalize_sentence(text: str) -> str:
    """Normalize a sentence for dedup-key purposes only.

    NFKC fold (full/half-width punctuation, kana, digits) + whitespace
    collapse. The original sentence on each word is left untouched.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


class WordFilterService:
    """Filter vocabulary words based on various criteria (stateless service)."""

    def __init__(self, config: AnkiMinerConfig, tagger: Any | None = None):
        """Initialize the word filter service.

        Args:
            config: Configuration for filtering.
            tagger: Optional fugashi.Tagger used to rebuild bolded sentence
                fields after the i+1 filter swaps in a different example
                line. Required only when ``config.bold_target_in_sentence``
                is True AND ``filter_i_plus_one`` is called; otherwise the
                bolded-field recompute is skipped and the tagger is unused.
        """
        self.config = config
        self.tagger = tagger

    def filter_unknown(
        self,
        all_words: list[TokenizedWord],
        existing_vocabulary: set[str],
    ) -> list[TokenizedWord]:
        """Filter out words that already exist in Anki collection.

        Comparison is by ``word.mined_form`` — the same string
        ``AnkiService.create_cards_batch`` writes to the card's Expression
        (first) field, and the same string Anki itself dedups on. This is
        POS-aware: verbs/adjectives use lemma, nouns use surface (see
        ``TokenizedWord.mined_form``). Aligning the filter key with the
        stored field keeps the pipeline self-consistent and prevents the
        AnkiConnect duplicate error that surfaced when a noun's unidic
        lemma differed from its surface (e.g. 豪腕→剛腕; Issue #5).

        ``existing_vocabulary`` is populated from
        ``AnkiService.get_existing_vocabulary()``, which reads the
        dedup-normalized (HTML/media-stripped, NFC) first field of every
        note — i.e. the same ``mined_form`` strings.

        Legacy verb cards with surface-form Expressions still block their
        own surface (because their stored Expression IS the surface
        string) but will not block re-mining of the same verb under its
        lemma form — a known intentional consequence of switching to
        lemma-based mining for verbs; see CHANGELOG.

        Args:
            all_words: List of all discovered words.
            existing_vocabulary: Set of Expression-field values already in
                Anki (the ``mined_form`` of each note).

        Returns:
            List of unknown words (``mined_form`` not in existing vocabulary).
        """
        return [word for word in all_words if word.mined_form not in existing_vocabulary]

    def filter_by_frequency(
        self,
        words: list[TokenizedWord],
        max_rank: int | None = None,
    ) -> list[TokenizedWord]:
        """Filter words by frequency rank (keep only top-N most common words).

        Words without a frequency rank are excluded: if the user opts into a
        frequency cutoff, unindexed words are by definition not in the top N.
        Fixes Issue #34.

        Args:
            words: List of words to filter.
            max_rank: Maximum frequency rank to include (e.g., 10000 means
                      only words ranked 1-10000 are kept). None or 0 means no filtering.

        Returns:
            Filtered list of words.
        """
        if not max_rank or max_rank <= 0:
            return words

        return [word for word in words if word.frequency_rank is not None and word.frequency_rank <= max_rank]

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

    def filter_by_script_type(
        self,
        words: list[TokenizedWord],
        exclude_hiragana_only: bool = False,
        exclude_katakana_only: bool = False,
    ) -> list[TokenizedWord]:
        """Drop words whose card form is written entirely in a single kana script.

        The test is applied to ``word.mined_form`` — the exact text that becomes
        the card's Expression field (POS-aware: verbs/adjectives use the lemma,
        nouns and everything else use the surface). So 全部 written in the
        subtitle as ぜんぶ is excluded when ``exclude_hiragana_only`` is set, and
        katakana loanwords like コーヒー are excluded when ``exclude_katakana_only``
        is set. Mixed kana+kanji forms are never matched and are kept.

        Args:
            words: Words to filter.
            exclude_hiragana_only: Drop words whose mined form is all hiragana.
            exclude_katakana_only: Drop words whose mined form is all katakana.

        Returns:
            Filtered list of words.
        """
        result = []
        for word in words:
            form = word.mined_form
            if exclude_hiragana_only and is_hiragana_only(form):
                continue
            if exclude_katakana_only and is_katakana_only(form):
                continue
            result.append(word)
        return result

    def filter_by_wordsets(
        self,
        words: list[TokenizedWord],
        wordset_service: WordsetService,
        word_list_service: WordListService | None = None,
    ) -> list[TokenizedWord]:
        """Drop words on any enabled name wordset (Issue #59).

        Matches ``word.mined_form`` against the bundled proper-noun sets.
        The wordset data is JMnedict surface (``keb``) forms, and names are
        nouns whose card Expression is ``mined_form`` (= surface), so the
        exclusion key must be ``mined_form`` for parity with card creation,
        AnkiConnect dedup, the existing-vocab filter, and the script-type
        filter. (Keying on ``lemma`` here let a name slip through whenever
        unidic's noun lemma diverged from its surface form.)

        The user whitelist, when provided, short-circuits the drop. It stays
        keyed on ``word.lemma`` to match ``WordListService``'s documented
        convention (users enter dictionary forms in their list files), the
        same key ``filter_by_word_lists`` uses.

        Args:
            words: Words to filter.
            wordset_service: Loaded union of enabled name wordsets.
            word_list_service: Optional user blacklist/whitelist service;
                a whitelisted lemma is always kept.

        Returns:
            Filtered list of words.
        """
        result = []
        for word in words:
            whitelisted = word_list_service is not None and word_list_service.is_whitelisted(word.lemma)
            if whitelisted or not wordset_service.is_excluded(word.mined_form):
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

    def filter_by_sentence_length(
        self,
        words: list[TokenizedWord],
        max_duration: float = 0.0,
        max_chars: int = 0,
    ) -> list[TokenizedWord]:
        """Drop words whose example sentence exceeds the configured caps.

        Each cap is independent: ``0`` (or ``0.0``) disables that dimension.
        Both caps are inclusive — a word at exactly the limit is kept.

        Args:
            words: List of words to filter.
            max_duration: Maximum allowed ``word.duration`` in seconds.
                ``0.0`` means no duration cap.
            max_chars: Maximum allowed ``len(word.sentence)``. ``0`` means
                no character cap.

        Returns:
            Filtered list of words.
        """
        if max_duration <= 0.0 and max_chars <= 0:
            return words

        result = []
        for word in words:
            if max_duration > 0.0 and word.duration > max_duration:
                continue
            if max_chars > 0 and len(word.sentence) > max_chars:
                continue
            result.append(word)
        return result

    def filter_i_plus_one(
        self,
        mineable_unknowns: list[TokenizedWord],
        line_index: list[LineLemmas],
        all_unknown_lemmas: set[str] | None = None,
    ) -> list[TokenizedWord]:
        """Restrict mining to words covered by at least one i+1 example sentence.

        An "i+1" line is a subtitle line containing exactly one UNKNOWN lemma
        — checked against ``all_unknown_lemmas``, the full unknown set — and
        that one unknown must also be a mineable target. Checking against the
        mineable set alone is wrong (Issue #74): unknowns removed by optional
        filters (frequency rank, blacklist, script type, name wordsets) are
        still unknown to the learner, so a line packed with them must not
        qualify. For each candidate word, the earliest such line in
        ``line_index`` order wins the tie-break; words with no i+1 line are
        dropped.

        The returned words have their sentence/timing/sentence_furigana/
        sentence_reading swapped to those of the selected line. ``surface`` and
        ``surface_start``/``surface_end`` are also swapped to the matched
        lemma's morpheme on the new line (for bold placement and, for
        surface-mined POS, the Expression itself). For surface-mined POS
        (anything that is not 動詞/形容詞, whose ``mined_form`` IS the surface),
        ``expression_furigana``/``expression_reading`` are recomputed from the
        new surface so the Expression and its furigana/reading stay mutually
        consistent — but only when a tagger is available (production always
        supplies one); otherwise the originals are kept as a best-effort
        fallback. Verbs/adjectives mine as ``lemma``, so their
        ``expression_furigana``/``expression_reading`` are unaffected by the
        surface swap and are preserved unchanged. ``lemma``, ``reading``,
        ``frequency_rank``, ``pos`` and ``video_file`` are preserved unchanged.

        Args:
            mineable_unknowns: Words remaining after blacklist, frequency,
                and word-list filters — the candidates eligible for mining.
            line_index: Per-line lemma index for the episode, in original
                subtitle order.
            all_unknown_lemmas: Every lemma the learner doesn't know,
                snapshotted BEFORE optional filters shrink the unknown set
                (the count basis for "exactly one unknown"). ``None`` means
                "no unknowns beyond the targets" and degrades to checking
                against the mineable set only.

        Returns:
            Filtered list of words with i+1 sentence/timing swapped in,
            preserving the input order of ``mineable_unknowns``.
        """
        if not mineable_unknowns or not line_index:
            return []

        target_lemmas = {w.lemma for w in mineable_unknowns}
        # Union defensively: a caller-supplied set that somehow misses a
        # target must not make that target unmatchable.
        unknown_lemmas = (all_unknown_lemmas | target_lemmas) if all_unknown_lemmas is not None else target_lemmas

        earliest: dict[str, LineLemmas] = {}
        for line in line_index:
            unknown_in_line = line.lemmas & unknown_lemmas
            if len(unknown_in_line) == 1:
                (only,) = unknown_in_line
                if only in target_lemmas:
                    earliest.setdefault(only, line)

        result: list[TokenizedWord] = []
        for word in mineable_unknowns:
            match = earliest.get(word.lemma)
            if match is None:
                continue
            result.append(self._swap_word_to_line(word, match))
        return result

    def _swap_word_to_line(self, word: TokenizedWord, match: LineLemmas) -> TokenizedWord:
        """Rebuild ``word`` as if it had been mined from the ``match`` line.

        Shared by ``filter_i_plus_one`` (swap to the i+1 line) and
        ``attach_sentence_candidates`` (build one variant per candidate line).
        Swaps ``surface``/offsets/``sentence``/timing and the precomputed
        ``sentence_furigana``/``sentence_reading``/bolded variants to the
        matched line; recomputes ``expression_furigana``/``expression_reading``
        for surface-mined POS whose Expression follows the new surface. The
        returned word's ``sentence_candidates`` is cleared so candidate
        variants stay leaves.
        """
        # Look up the lemma's morpheme position on the matched line so the
        # bold span (and the surface form, which may have a different
        # inflection on the new line) lands on the right token after the swap.
        # If the entry is missing for any reason (e.g. legacy index without
        # lemma_spans), fall back to the original surface/offsets — bold would
        # then point at the old sentence, so we also disable the bolded fields.
        span_entry = next(
            ((s, st, en) for (lemma_key, s, st, en) in match.lemma_spans if lemma_key == word.lemma),
            None,
        )
        if span_entry is not None:
            new_surface, new_start, new_end = span_entry
        else:
            new_surface, new_start, new_end = word.surface, -1, -1

        if self.config.bold_target_in_sentence and span_entry is not None and self.tagger is not None:
            new_bolded = wrap_target_plain(match.line_text, new_start, new_end)
            new_furi_bolded = wrap_target_furigana(match.line_text, self.tagger, new_start, new_end)
        else:
            new_bolded = ""
            new_furi_bolded = ""

        # The swap above replaces ``surface``. For surface-mined POS (nouns and
        # everything that is not 動詞/形容詞), ``mined_form`` IS the surface, so
        # the new surface becomes the card's Expression — its
        # ``expression_furigana``/``expression_reading`` (computed from the
        # ORIGINAL surface at parse time) would otherwise go stale, leaving the
        # Expression inconsistent with its own furigana/reading (T-37).
        # Verbs/adjectives mine as ``lemma`` (unchanged by the swap), so their
        # Expression fields stay valid and are left untouched. Recompute
        # requires a tagger; production always supplies one (service_factory
        # wires the shared parser tagger). When absent (tagger-less unit setup),
        # the original values are kept as a best-effort fallback.
        expr_furigana = word.expression_furigana
        expr_reading = word.expression_reading
        surface_is_expression = word.pos not in ("動詞", "形容詞")
        if surface_is_expression and new_surface != word.surface and self.tagger is not None:
            expr_furigana = generate_furigana(new_surface, self.tagger)
            expr_reading = generate_reading(new_surface, self.tagger)

        return dataclasses.replace(
            word,
            surface=new_surface,
            surface_start=new_start,
            surface_end=new_end,
            sentence=match.line_text,
            start_time=match.start_time,
            end_time=match.end_time,
            duration=match.duration,
            expression_furigana=expr_furigana,
            expression_reading=expr_reading,
            sentence_furigana=match.sentence_furigana,
            sentence_reading=match.sentence_reading,
            sentence_bolded=new_bolded,
            sentence_furigana_bolded=new_furi_bolded,
            sentence_candidates=[],
        )

    def attach_sentence_candidates(
        self,
        words: list[TokenizedWord],
        line_index: list[LineLemmas],
        max_candidates: int = 12,
    ) -> None:
        """Populate ``word.sentence_candidates`` for words that repeat across lines.

        For each word, collects every ``line_index`` entry whose content lemmas
        include ``word.lemma`` (subtitle order preserved). When a word appears on
        two or more lines, builds one fully-swapped :class:`TokenizedWord`
        variant per line (capped at ``max_candidates``, earliest-first) via
        :meth:`_swap_word_to_line` and assigns the list — including the variant
        for the word's current sentence, so the curator can default-select it.
        Words on a single line are left untouched (empty candidates ⇒ no picker).

        Mutates ``words`` in place. Safe to call with an empty ``line_index``.
        """
        if not line_index:
            return
        lines_by_lemma: dict[str, list[LineLemmas]] = {}
        for line in line_index:
            for lemma in line.lemmas:
                lines_by_lemma.setdefault(lemma, []).append(line)

        for word in words:
            lines = lines_by_lemma.get(word.lemma, ())
            if len(lines) < 2:
                continue
            word.sentence_candidates = [self._swap_word_to_line(word, line) for line in lines[:max_candidates]]

    def attach_occurrence_counts(self, words: list[TokenizedWord], counts: Mapping[str, int]) -> None:
        """Set ``word.occurrence_count`` from in-episode lemma counts (Issue #88).

        ``counts`` is a lemma→occurrences mapping (e.g. the Counter from
        ``SubtitleParserService.count_lemmas``). Lemmas absent from the mapping
        get 0. Mutates ``words`` in place; display/sort-only data for the curator.
        """
        for word in words:
            word.occurrence_count = counts.get(word.lemma, 0)

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
