"""Service for filtering vocabulary words."""

from __future__ import annotations

import dataclasses
import logging
import unicodedata
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, NamedTuple

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.models.word import select_mined_form
from anki_miner.utils import (
    generate_furigana,
    generate_reading,
    is_hiragana_only,
    is_kana_only,
    is_katakana_only,
    is_mixed_kana_only,
    wrap_target_furigana,
    wrap_target_plain,
)

if TYPE_CHECKING:
    from anki_miner.services.word_list_service import WordListService
    from anki_miner.services.wordset_service import WordsetService

logger = logging.getLogger(__name__)

#: Joiner between merged cue texts (Issue #120 line expansion). A space
#: mirrors _clean_line_text's own physical-line flattening (" ".join), and the
#: curator preview concatenates with the same constant so the cell text and the
#: mined sentence stay byte-identical.
CUE_JOINER = " "


class MergedLineWindow(NamedTuple):
    """A run of adjacent subtitle cues merged into one sentence/media window."""

    start: float  # min start over the merged cues (entry timeline)
    end: float  # max end over the merged cues
    text: str  # CUE_JOINER.join of the cue texts, file order
    prefix_len: int  # chars prepended before the word's own line (incl. joiner)


def find_cue_index(
    entries: Sequence[tuple[float, float, str]],
    start_time: float,
    sentence: str,
    *,
    offset: float = 0.0,
    tolerance: float = 0.05,
) -> int | None:
    """Locate the cue a word was mined from.

    Text equality is the primary key (``_clean_line_text`` is shared by the
    mining parse and ``parse_raw_entries``, so the word's own cue matches
    verbatim); the nearest clamped time — ``max(0, start + offset)``, mirroring
    ``parse_raw_entries``'s own clamp — breaks duplicate-text ties (OP/ED
    lyrics). Falls back to time-only within ``tolerance`` when no text matches.
    Returns None when nothing lands within ``tolerance``.
    """

    def dist(i: int) -> float:
        return abs(max(0.0, entries[i][0] + offset) - start_time)

    text_hits = [i for i, (_, _, text) in enumerate(entries) if text == sentence]
    pool: Sequence[int] = text_hits or range(len(entries))
    best = min(pool, key=dist, default=None)
    if best is None or dist(best) > tolerance:
        return None
    return best


def merge_cue_window(
    entries: Sequence[tuple[float, float, str]],
    index: int,
    prev_count: int,
    next_count: int,
) -> MergedLineWindow:
    """Merge ``entries[index - prev_count : index + next_count + 1]``.

    Counts clamp at the file edges. Times span min start to max end over the
    merged range (defensive against out-of-order ASS events).
    """
    lo = max(0, index - prev_count)
    hi = min(len(entries) - 1, index + next_count)
    cues = entries[lo : hi + 1]
    return MergedLineWindow(
        start=min(cue[0] for cue in cues),
        end=max(cue[1] for cue in cues),
        text=CUE_JOINER.join(cue[2] for cue in cues),
        prefix_len=sum(len(cue[2]) + len(CUE_JOINER) for cue in entries[lo:index]),
    )


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
        POS-aware: verbs/adjectives use the source-orthography dictionary
        form (orth_base, falling back to lemma), nouns use surface (see
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

        Kana-variant fold (``config.known_words_match_kana_variants``, default
        on): a word whose ``mined_form`` is written entirely in kana ALSO
        counts as known when its dictionary ``lemma`` is in
        ``existing_vocabulary`` — a subtitle spelling うなずく (orthBase, kana)
        must not re-mine an existing 頷く (lemma) card. The fold is one-way and
        script-gated: a kanji ``mined_form`` never falls back to the lemma,
        because unidic's canonical lemma collapses kanji-variant homographs
        (殺る→遣る, Issue #19/#5) that a learner knows separately.

        Args:
            all_words: List of all discovered words.
            existing_vocabulary: Set of Expression-field values already in
                Anki (the ``mined_form`` of each note).

        Returns:
            List of unknown words (``mined_form`` not in existing vocabulary).
        """
        fold_kana = self.config.known_words_match_kana_variants

        def _is_known(word: TokenizedWord) -> bool:
            if word.mined_form in existing_vocabulary:
                return True
            return (
                fold_kana
                and word.lemma != word.mined_form
                and word.lemma in existing_vocabulary
                and is_kana_only(word.mined_form)
            )

        return [word for word in all_words if not _is_known(word)]

    def filter_by_frequency(
        self,
        words: list[TokenizedWord],
        max_rank: int | None = None,
        *,
        min_rank: int | None = None,
        keep_unranked: bool = False,
    ) -> list[TokenizedWord]:
        """Filter words to a frequency rank band (lower rank = more common).

        ``min_rank`` drops the common end of the band, ``max_rank`` the rare end.
        Either may be 0/None to leave that end open; with both open the list is
        returned untouched.

        Words without a frequency rank are excluded unless ``keep_unranked`` is
        True: if the user opts into a band, unindexed words are by definition not
        in the top N (Issue #34). ``keep_unranked`` exists because that reasoning
        only runs one way — an unranked word is not shown to be super-common
        either, so a min-only band usually wants to keep them. The choice is a
        checkbox next to the band in Settings, not something inferred here.

        Args:
            words: List of words to filter.
            max_rank: Rarest rank to keep (e.g. 10000 keeps ranks 1-10000).
                      None or 0 leaves the rare end open.
            min_rank: Most common rank to keep (e.g. 500 skips ranks 1-499).
                      None or 0 leaves the common end open.
            keep_unranked: Keep words whose ``frequency_rank`` is None.

        Returns:
            Filtered list of words.
        """
        low = min_rank if min_rank and min_rank > 0 else None
        high = max_rank if max_rank and max_rank > 0 else None
        if low is None and high is None:
            return words

        kept: list[TokenizedWord] = []
        for word in words:
            rank = word.frequency_rank
            if rank is None:
                if keep_unranked:
                    kept.append(word)
                continue
            if low is not None and rank < low:
                continue
            if high is not None and rank > high:
                continue
            kept.append(word)
        return kept

    def filter_by_word_lists(
        self,
        words: list[TokenizedWord],
        word_list_service: WordListService,
    ) -> list[TokenizedWord]:
        """Filter words using the user blacklist.

        Blacklist entries match against ``word.mined_form`` (the card-front
        spelling) with a miss-only ``word.lemma`` fallback — a word is dropped
        when EITHER form is blacklisted. UniDic collapses kanji variants
        (賭ける→掛ける) into one lemma, so keying on lemma alone let a blacklist
        entry for the card front (賭ける) be ignored; mirrors the def/freq lookup
        convention (commit 99e2c04). Users should enter dictionary forms in
        their list files (e.g. 食べる, not 食べた). The whitelist is NOT consulted
        here: whitelisted words are force-included by
        :meth:`partition_whitelisted` before this filter runs, so they never
        reach it.

        Args:
            words: List of words to filter.
            word_list_service: Service providing blacklist lookups.

        Returns:
            Filtered list of words.
        """
        return [
            word
            for word in words
            if not (word_list_service.is_blacklisted(word.mined_form) or word_list_service.is_blacklisted(word.lemma))
        ]

    def partition_whitelisted(
        self,
        words: list[TokenizedWord],
        word_list_service: WordListService,
    ) -> tuple[list[TokenizedWord], list[TokenizedWord]]:
        """Split words into ``(forced, rest)`` by user whitelist membership.

        Force-included words bypass every optional coverage filter — the caller
        runs the filter chain on ``rest`` only and merges ``forced`` back in
        before the integrity gates. Matching is on ``word.mined_form`` (the
        card-front spelling) with a miss-only ``word.lemma`` fallback, the
        convention shared with :meth:`filter_by_word_lists`: UniDic collapses
        kanji variants (賭ける→掛ける) into one lemma, so whitelisting the card
        front must force-include it even though its lemma differs.
        This OR-match is the explicit alias policy: a canonical-lemma whitelist
        entry also admits every distinct card-front surface sharing that lemma
        (its ``lemma-siblings``), including below an occurrence floor.
        ``all_words`` is already lemma-deduped upstream
        (``SubtitleParserService``), so exactly one word per whitelisted form is
        moved to ``forced``.

        Args:
            words: List of candidate words.
            word_list_service: Service providing whitelist lookups.

        Returns:
            A ``(forced, rest)`` tuple preserving input order within each list.
        """
        forced: list[TokenizedWord] = []
        rest: list[TokenizedWord] = []
        for word in words:
            if word_list_service.is_whitelisted(word.mined_form) or word_list_service.is_whitelisted(word.lemma):
                forced.append(word)
            else:
                rest.append(word)
        return forced, rest

    def filter_by_script_type(
        self,
        words: list[TokenizedWord],
        exclude_hiragana_only: bool = False,
        exclude_katakana_only: bool = False,
    ) -> list[TokenizedWord]:
        """Drop words whose card form is written entirely in a single kana script.

        The test is applied to ``word.mined_form`` — the exact text that becomes
        the card's Expression field (POS-aware: verbs/adjectives use the
        source-orthography dictionary form, nouns and everything else use the
        surface). So 全部 written in the
        subtitle as ぜんぶ is excluded when ``exclude_hiragana_only`` is set, and
        katakana loanwords like コーヒー are excluded when ``exclude_katakana_only``
        is set. Mixed kana+kanji forms are never matched and are kept.

        Script-neutral kana marks (ー ・ ゝゞヽヾ) do not decide a word's script,
        so the colloquial long-vowel spellings すごーい / ずーっと count as
        hiragana-only. Words mixing BOTH kana scripts — the loanword verbs and
        adjectives ``morphology.should_include`` admits on purpose (サボる, ググる,
        ヤバい) — belong to neither script, so they are dropped only when both
        exclusions are on, which is the "kanji-only deck" request. Either flag
        alone leaves them mined.

        Args:
            words: Words to filter.
            exclude_hiragana_only: Drop words whose mined form is all hiragana.
            exclude_katakana_only: Drop words whose mined form is all katakana.

        Returns:
            Filtered list of words.
        """
        exclude_mixed_kana = exclude_hiragana_only and exclude_katakana_only
        result = []
        for word in words:
            form = word.mined_form
            if exclude_hiragana_only and is_hiragana_only(form):
                continue
            if exclude_katakana_only and is_katakana_only(form):
                continue
            if exclude_mixed_kana and is_mixed_kana_only(form):
                continue
            result.append(word)
        return result

    def filter_by_wordsets(
        self,
        words: list[TokenizedWord],
        wordset_service: WordsetService,
    ) -> list[TokenizedWord]:
        """Drop words on any enabled name wordset (Issue #59).

        Matches ``word.mined_form`` against the bundled proper-noun sets.
        The wordset data is JMnedict surface (``keb``) forms, and names are
        nouns whose card Expression is ``mined_form`` (= surface), so the
        exclusion key must be ``mined_form`` for parity with card creation,
        AnkiConnect dedup, the existing-vocab filter, and the script-type
        filter. (Keying on ``lemma`` here let a name slip through whenever
        unidic's noun lemma diverged from its surface form.)

        Whitelisted words never reach this filter: they are force-included by
        :meth:`partition_whitelisted` before the chain runs.

        Args:
            words: Words to filter.
            wordset_service: Loaded union of enabled name wordsets.

        Returns:
            Filtered list of words.
        """
        return [word for word in words if not wordset_service.is_excluded(word.mined_form)]

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
        ``line_index`` order whose card front remains compatible wins the
        tie-break; words with no compatible i+1 line are dropped.

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
        fallback. Verbs/adjectives mine as ``orth_base`` (their parse-time
        dictionary form, untouched by the swap), so their
        ``expression_furigana``/``expression_reading`` are unaffected by the
        surface swap and are preserved unchanged. ``lemma``, ``orth_base``,
        ``reading``, ``frequency_rank``, ``pos`` and ``video_file`` are
        preserved unchanged.

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

        lines_by_lemma: dict[str, list[LineLemmas]] = {}
        for line in line_index:
            unknown_in_line = line.lemmas & unknown_lemmas
            if len(unknown_in_line) == 1:
                (only,) = unknown_in_line
                if only in target_lemmas:
                    lines_by_lemma.setdefault(only, []).append(line)

        result: list[TokenizedWord] = []
        for word in mineable_unknowns:
            match = next(
                (line for line in lines_by_lemma.get(word.lemma, ()) if self._line_preserves_mined_form(word, line)),
                None,
            )
            if match is None:
                continue
            result.append(self._swap_word_to_line(word, match))
        return result

    @staticmethod
    def _line_preserves_mined_form(word: TokenizedWord, line: LineLemmas) -> bool:
        """Whether swapping to ``line`` keeps a surface-mined card front."""
        if word.pos in ("動詞", "形容詞"):
            return True
        surface = next(
            (surface for lemma, surface, *_ in line.lemma_spans if lemma == word.lemma),
            None,
        )
        if surface is None:
            # Legacy/hand-built indexes without spans keep the original surface
            # in _swap_word_to_line, so they cannot change the card front.
            return True
        # Thread the word's own pronunciation evidence (S4-01): omitting it takes
        # the no-evidence compatibility path, which folds lexical vowel-tail
        # nouns (舞い → 舞) and wrongly rejects every line for such words.
        return select_mined_form(word.pos, word.orth_base, word.lemma, surface, word.pronunciation) == word.mined_form

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
            ((s, st, en, he) for (lemma_key, s, st, en, he) in match.lemma_spans if lemma_key == word.lemma),
            None,
        )
        if span_entry is not None:
            new_surface, new_start, new_end, new_highlight_end = span_entry
        else:
            new_surface, new_start, new_end, new_highlight_end = word.surface, -1, -1, -1

        if self.config.bold_target_in_sentence and span_entry is not None and self.tagger is not None:
            # Bold the full inflected form on the swapped-in line (same
            # highlight_end semantics as parse-time bolding; -1 sentinel in
            # hand-built indexes falls back to the surface span).
            new_bolded, new_furi_bolded = self._bolded_pair(match.line_text, new_start, new_highlight_end, new_end)
        else:
            new_bolded = ""
            new_furi_bolded = ""

        # The swap above replaces ``surface``. For surface-mined POS (nouns and
        # everything that is not 動詞/形容詞), callers only select a surface
        # that resolves to the same card Expression. Its
        # ``expression_furigana``/``expression_reading`` (computed from the
        # ORIGINAL surface at parse time) would otherwise go stale, leaving the
        # swapped spelling inconsistent with its furigana/reading (T-37).
        # Verbs/adjectives mine as ``orth_base``, which dataclasses.replace
        # below preserves (it is not swapped), so their Expression fields stay
        # valid and are left untouched. Recompute
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
            highlight_end=new_highlight_end,
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

    def _bolded_pair(self, text: str, start: int, highlight_end: int, end: int) -> tuple[str, str]:
        """``(sentence_bolded, sentence_furigana_bolded)`` for a target span.

        Same highlight_end semantics as parse-time bolding: the -1 sentinel
        falls back to the surface span end. Caller guarantees
        ``config.bold_target_in_sentence``, tracked spans, and a tagger.
        """
        bold_end = highlight_end if highlight_end >= 0 else end
        return (
            wrap_target_plain(text, start, bold_end),
            wrap_target_furigana(text, self.tagger, start, bold_end),
        )

    def expand_word_lines(
        self,
        word: TokenizedWord,
        entries: Sequence[tuple[float, float, str]],
    ) -> TokenizedWord:
        """Materialize ``word.line_expansion`` against the full cue list (Issue #120).

        Merges the requested previous/next cues into the word's sentence and
        media window: sentence text joined with :data:`CUE_JOINER`, timings
        spanning first to last merged cue, surface/highlight offsets shifted by
        the prepended length (the ``sentence[surface_start:surface_end] ==
        surface`` invariant holds by construction), and
        ``sentence_furigana``/``sentence_reading``/bolded variants regenerated
        over the merged text — stale single-line values would be actively
        wrong, so without a tagger they are cleared, not kept. ``entries`` must
        come from ``parse_raw_entries`` on the same timeline as the word
        (``line_index`` drops zero-lemma lines and cannot supply neighbors).

        Returns ``word`` unchanged for the ``(0, 0)`` default and for a cue
        miss (pathological: the curator matched the same parse). The result's
        ``line_expansion`` resets to ``(0, 0)`` — the intent is absorbed, so a
        second pass cannot double-apply it. ``clip_override`` is preserved: the
        curator only lets an override exist that postdates the expansion.
        """
        prev_count, next_count = word.line_expansion
        if (prev_count, next_count) == (0, 0):
            return word
        index = find_cue_index(entries, word.start_time, word.sentence, tolerance=1e-3)
        if index is None:
            logger.warning(
                "line expansion: no cue matches %r at %.3fs; keeping the original line",
                word.sentence,
                word.start_time,
            )
            return word
        if entries[index][2] != word.sentence:
            logger.warning(
                "line expansion: cue at %.3fs reads %r, word expected %r; keeping the original line",
                word.start_time,
                entries[index][2],
                word.sentence,
            )
            return word
        window = merge_cue_window(entries, index, prev_count, next_count)
        shift = window.prefix_len
        new_start = word.surface_start + shift if word.surface_start >= 0 else -1
        new_end = word.surface_end + shift if word.surface_end >= 0 else -1
        new_highlight = word.highlight_end + shift if word.highlight_end >= 0 else -1
        if self.tagger is not None:
            new_furigana = generate_furigana(window.text, self.tagger)
            new_reading = generate_reading(window.text, self.tagger)
        else:
            new_furigana = new_reading = ""
        if self.config.bold_target_in_sentence and new_start >= 0 and self.tagger is not None:
            new_bolded, new_furi_bolded = self._bolded_pair(window.text, new_start, new_highlight, new_end)
        else:
            new_bolded = new_furi_bolded = ""
        return dataclasses.replace(
            word,
            sentence=window.text,
            start_time=window.start,
            end_time=window.end,
            duration=window.end - window.start,
            surface_start=new_start,
            surface_end=new_end,
            highlight_end=new_highlight,
            sentence_furigana=new_furigana,
            sentence_reading=new_reading,
            sentence_bolded=new_bolded,
            sentence_furigana_bolded=new_furi_bolded,
            sentence_candidates=[],
            line_expansion=(0, 0),
        )

    def attach_sentence_candidates(
        self,
        words: list[TokenizedWord],
        line_index: list[LineLemmas],
        max_candidates: int | None = None,
    ) -> None:
        """Populate ``word.sentence_candidates`` for words that repeat across lines.

        For each word, collects every ``line_index`` entry whose content lemmas
        include ``word.lemma`` and whose matched surface preserves the card front
        (subtitle order preserved). When a word appears on two or more compatible
        lines, builds one fully-swapped :class:`TokenizedWord` variant per line
        (earliest-first) via :meth:`_swap_word_to_line` and assigns the list —
        including the variant for the word's current sentence, so the curator can
        default-select it. Words on a single compatible line are left untouched
        (empty candidates ⇒ no picker).

        ``max_candidates`` is unbounded by default: the curator's picker scrolls,
        and a silent truncation there reads as "the word says it appears N times
        but offers fewer lines". Pass an int to bound the list. Cost is one
        :meth:`_swap_word_to_line` per compatible line (sub-millisecond, dominated
        by one furigana pass over the line), summed over words that repeat.

        Mutates ``words`` in place. Safe to call with an empty ``line_index``.
        """
        if not line_index:
            return
        lines_by_lemma: dict[str, list[LineLemmas]] = {}
        for line in line_index:
            for lemma in line.lemmas:
                lines_by_lemma.setdefault(lemma, []).append(line)

        for word in words:
            lines = [line for line in lines_by_lemma.get(word.lemma, ()) if self._line_preserves_mined_form(word, line)]
            if len(lines) < 2:
                continue
            if max_candidates is not None:
                lines = lines[:max_candidates]
            word.sentence_candidates = [self._swap_word_to_line(word, line) for line in lines]

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
