"""Service for parsing subtitles and extracting vocabulary."""

import collections
import logging
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.models.word import select_mined_form
from anki_miner.services.compound_matcher import CompoundDictionaryMatcher, TermLookup
from anki_miner.services.deinflection import find_highlight_end
from anki_miner.services.morphology import (
    TokenInclusionRule,
    extract_lemma,
    extract_reading,
    iter_token_spans,
    merge_compound_suffixes,
    mining_base,
)
from anki_miner.services.reading.models import ReadingUnit
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.utils import (
    clean_subtitle_text,
    generate_furigana,
    generate_reading,
    katakana_to_hiragana,
    wrap_target_plain,
)
from anki_miner.utils.text_utils import (
    generate_furigana_from_tokens,
    generate_reading_from_tokens,
    wrap_target_furigana_from_tokens,
)

logger = logging.getLogger(__name__)

# Maximum number of files held simultaneously in the per-instance per-file
# tokenization cache.  When the cap is hit the oldest entry (insertion order)
# is evicted so the dict stays bounded while still covering the Deck Builder's
# Phase-1 → Phase-2 cross-file reuse pattern for any corpus up to this size.
_LINE_CACHE_MAX_FILES: int = 256


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(self, config: AnkiMinerConfig, term_lookup: TermLookup | None = None):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
            term_lookup: Optional batch headword-existence probe
                (``DefinitionService.offline_terms_exist``). When provided AND
                ``config.compound_matching`` is on, dictionary-attested
                multi-token spans are merged into single words (Yomitan
                longest-match). ``None`` (no offline dictionary, toggle off,
                or raw-entry-only callers) keeps parsing byte-identical to
                the pre-compound-matching behavior.
        """
        self.config = config
        # Shared process-wide tagger (see services/tagger.py for the single-flight
        # invariant). __init__ may block ~2-3s on the lazy build if a user triggers
        # the first SubtitleParserService before the background prewarm worker
        # finishes; worst case is the same wait they'd incur anyway, no correctness
        # impact. GUI-thread call sites that only call parse_raw_entries never
        # tokenize, so they don't race the worker thread's .parse() calls on this
        # shared tagger.
        self.tagger = get_shared_tagger()
        # POS/subtype inclusion gate, snapshotted from the (frozen) config.
        self._inclusion_rule = TokenInclusionRule(
            allowed_pos=frozenset(config.allowed_pos),
            excluded_subtypes=frozenset(config.excluded_subtypes),
        )
        # Dictionary-attested compound matching (see services/compound_matcher.py).
        # Built only when a term lookup is injected AND the toggle is on; the
        # matcher reuses the inclusion rule so spans start only at mineable tokens.
        self._compound_matcher: CompoundDictionaryMatcher | None = None
        if term_lookup is not None and config.compound_matching:
            self._compound_matcher = CompoundDictionaryMatcher(term_lookup, self._inclusion_rule)
        self._filter_pattern: re.Pattern[str] | None = None
        if config.use_subtitle_regex_filter and config.subtitle_regex_filter:
            try:
                # ReDoS exposure: the compiled pattern is NOT timeout-protected.
                # A pathological user pattern (e.g. `(a+)+$`) on a long subtitle
                # line can cause catastrophic backtracking and hang the parser.
                # The only victim is the user themselves — this config is local,
                # never network-supplied. If we ever accept regex from an
                # untrusted source, swap to the third-party `regex` module with
                # timeout= or compile under re2.
                self._filter_pattern = re.compile(config.subtitle_regex_filter)
            except re.error as e:
                # Bad pattern at the boundary should not crash mining. Disable
                # and surface in the log; GUI validation should catch this on save.
                logger.warning(
                    "Invalid subtitle_regex_filter %r: %s; filter disabled for this run",
                    config.subtitle_regex_filter,
                    e,
                )
                self._filter_pattern = None
        # Per-parse memo caches; initialised here with type annotations so
        # mypy knows the shapes; reset at the top of each parse_* call via
        # _reset_caches() so a second invocation never sees stale entries.
        self._fg_cache: dict[str, str] = {}
        self._rd_cache: dict[str, str] = {}
        self._reset_caches()
        # Per-FILE tokenization cache (distinct lifetime from the per-parse memo
        # caches above): resolved path -> (mtime, list of line-state tuples).
        # Filled on the first _iter_parsed_lines pass over a file and reused by
        # any later pass over the SAME path+mtime (e.g. the Deck Builder's
        # count_lemmas → parse_subtitle_file double-parse). Survives across
        # parse_* calls; an mtime change invalidates the entry. _reset_caches()
        # does NOT touch this — it is not a per-parse cache.
        #
        # Size-bounded: capped at _LINE_CACHE_MAX_FILES entries via insertion-order
        # eviction (pop the oldest key when full). Prevents unbounded growth during
        # large Deck Builder builds while still caching all files touched in Phase 1
        # for Phase 2 reuse when the corpus fits within the cap.
        self._line_cache: dict[Path, tuple[float, list[tuple[str, list, list, float, float, float]]]] = {}

    # ------------------------------------------------------------------
    # Per-parse memoization helpers
    # ------------------------------------------------------------------

    def _reset_caches(self) -> None:
        """Assign fresh empty dicts to the per-parse memo caches.

        Called at the start of every public parse_* entry-point so a second
        invocation on the same service instance never serves entries from a
        previous parse run.  Also called from ``__init__`` so the shapes are
        initialised in exactly one place. Only the expression (``mined``) path
        still memoizes furigana/reading; sentence + bold furigana now reuse the
        per-line ``raw_tokens`` directly via the ``*_from_tokens`` helpers.
        """
        self._fg_cache = {}
        self._rd_cache = {}

    def _furigana(self, s: str) -> str:
        """Return generate_furigana(s, tagger), memoized within the current parse pass."""
        if s not in self._fg_cache:
            self._fg_cache[s] = generate_furigana(s, self.tagger)
        return self._fg_cache[s]

    def _reading(self, s: str) -> str:
        """Return generate_reading(s, tagger), memoized within the current parse pass."""
        if s not in self._rd_cache:
            self._rd_cache[s] = generate_reading(s, self.tagger)
        return self._rd_cache[s]

    def _apply_text_filter(self, text: str) -> str:
        """Apply the configured regex filter to a subtitle line.

        Runs after ``clean_subtitle_text`` strips tags/HTML so the pattern
        operates on human-readable text. Whitespace is renormalized because
        a stripped span can leave double spaces behind.
        """
        if self._filter_pattern is None:
            return text
        filtered = self._filter_pattern.sub(self.config.subtitle_regex_replacement, text)
        return " ".join(filtered.split())

    def _load_subs(self, subtitle_file: Path):
        """Load a subtitle file via pysubs2 with normalized error wrapping.

        Shared by every public parse_* method so error wrapping stays
        consistent regardless of entry point.
        """
        try:
            return pysubs2.load(str(subtitle_file))
        except FileNotFoundError as e:
            raise SubtitleParseError(f"Subtitle file not found: {subtitle_file}") from e
        except Exception as e:
            raise SubtitleParseError(f"Failed to parse subtitle file: {e}") from e

    def _iter_parsed_lines(
        self, subtitle_file: Path
    ) -> Iterator[tuple[str, list[Any], list[Any], float, float, float]]:
        """Yield post-tokenize per-line state for every non-empty subtitle line.

        Yields ``(text, raw_tokens, merged_tokens, start_time, end_time,
        duration)``. ``text`` is the cleaned + regex-filtered line;
        ``raw_tokens`` is the direct output of ``self.tagger(text)`` (used by
        ``_from_tokens`` helpers so the sentence is tokenized only once);
        ``merged_tokens`` is the full output of ``_merge_compound_suffixes``
        (callers apply ``_should_include_word`` themselves so the index path and
        mining path share identical token selection logic).

        Per-file cache: keyed by resolved path → (mtime, line-state list);
        bounded to ``_LINE_CACHE_MAX_FILES`` entries via oldest-first eviction.
        On a cache HIT for the same path+mtime the subtitle file is neither
        reloaded nor re-tokenized — the stored line-state (the very tuples a
        fresh parse would yield, including ``_SyntheticToken``s) is replayed.
        An mtime mismatch (file edited between passes) invalidates the entry and
        forces a fresh load + tokenize. The multi-entry cache supports the Deck
        Builder's Phase-1 (``count_lemmas``) → Phase-2 (``parse_subtitle_file``)
        cross-file reuse pattern: every file visited in Phase 1 remains cached
        for Phase 2, eliminating a second full MeCab pass over the corpus.
        Consumers MUST NOT mutate the yielded ``merged_tokens`` lists/tokens, as
        they are shared across passes; current consumers only read them.
        """
        key = subtitle_file.resolve()
        try:
            mtime = subtitle_file.stat().st_mtime
        except OSError:
            # Can't stat (e.g. missing file): fall through to _load_subs, which
            # raises the normalized SubtitleParseError. Bypass the cache.
            mtime = None

        if mtime is not None:
            cached = self._line_cache.get(key)
            if cached is not None and cached[0] == mtime:
                yield from cached[1]
                return

        subs = self._load_subs(subtitle_file)

        # Tokenize lazily and yield each line as it is produced — preserving the
        # exact interleaving of tokenizer calls with any per-word tagger work a
        # consumer does between iterations (real fugashi is stateless, but tests
        # mock it with an order-sensitive side_effect). The cache entry is only
        # committed once the generator is fully consumed, so a consumer that
        # abandons iteration early does not leave a truncated entry.
        line_states: list[tuple[str, list, list, float, float, float]] = []
        for line in subs:
            # Skip ASS/SSA Comment events (karaoke, sign TL, staff credits…).
            # pysubs2 SSAEvent.is_comment is a bool; we check ``is True`` (strict
            # identity) so that a missing attribute (SRT/VTT, or a mock object
            # whose auto-created attr is a truthy non-bool) never triggers the skip.
            if getattr(line, "is_comment", None) is True:
                continue
            text = self._apply_text_filter(clean_subtitle_text(line.text))
            if not text:
                continue

            # Convert timing from milliseconds to seconds and apply offset
            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)

            line_state = self._build_line_state(text, start_time, end_time)
            line_states.append(line_state)
            yield line_state

        # mtime is None only when stat() failed, in which case _load_subs above
        # already raised, so this assignment is reachable only with a real mtime.
        #
        # Evict the oldest entry when the cache is at capacity so growth stays
        # bounded (see _LINE_CACHE_MAX_FILES). dict preserves insertion order in
        # Python 3.7+, so next(iter(...)) yields the oldest key.
        if mtime is not None:
            if len(self._line_cache) >= _LINE_CACHE_MAX_FILES:
                self._line_cache.pop(next(iter(self._line_cache)))
            self._line_cache[key] = (mtime, line_states)

    def _build_line_state(
        self, text: str, start: float, end: float
    ) -> tuple[str, list[Any], list[Any], float, float, float]:
        """Tokenize one cleaned line into its per-line parse-state 6-tuple.

        Returns ``(text, raw_tokens, merged_tokens, start, end, duration)``:
        ``raw_tokens`` is the direct ``self.tagger(text)`` output,
        ``merged_tokens`` is that run through ``_merge_compound_suffixes`` and
        the optional compound matcher, and ``duration`` is ``end - start``.
        Shared by the subtitle path (``_iter_parsed_lines``) and the future
        text-unit path so per-line tokenization stays in one place.
        """
        raw_tokens = list(self.tagger(text))
        merged_tokens = self._merge_compound_suffixes(raw_tokens)
        if self._compound_matcher is not None:
            merged_tokens = self._compound_matcher.merge_line(text, merged_tokens)
        return (text, raw_tokens, merged_tokens, start, end, end - start)

    @staticmethod
    def _iter_token_spans(text: str, tokens: list) -> Iterator[tuple[Any, int, int]]:
        """Single-source token-span locator (see morphology.iter_token_spans)."""
        return iter_token_spans(text, tokens)

    def _find_highlight_end(self, text: str, raw_tokens: list, tok_start: int, tok_end: int, word_token: Any) -> int:
        """Full-inflected-form end offset.

        See deinflection.find_highlight_end. Both mining passes call this
        identically so the emitted highlight_end stays byte-identical between
        parse_subtitle_file and _with_index.
        """
        return find_highlight_end(text, raw_tokens, tok_start, tok_end, word_token)

    def _emit_word(
        self,
        word_token: Any,
        tok_start: int,
        tok_end: int,
        *,
        highlight_end: int,
        text: str,
        raw_tokens: list,
        start_time: float,
        end_time: float,
        duration: float,
        sentence_furigana: str,
        sentence_reading: str,
        seen_lemmas: set[str],
    ) -> TokenizedWord | None:
        """Build the ``TokenizedWord`` for one included token, lemma-deduped.

        Shared tail of ``parse_subtitle_file`` and
        ``parse_subtitle_file_with_index``: lemma-keyed dedup (first
        occurrence wins, recorded in ``seen_lemmas``), reading/expression
        assembly and the optional bold-target sentence variants. Returns
        ``None`` when the token's lemma was already emitted.
        """
        # Get lemma (dictionary form) for lookups and deduplication
        lemma = self._extract_lemma(word_token)
        surface = word_token.surface

        # Dedup on lemma alone: surface variants of the same dictionary
        # form should collapse, not block each other.
        if lemma in seen_lemmas:
            return None
        seen_lemmas.add(lemma)

        # Get reading if available
        reading = self._extract_reading(word_token)
        # Strict ``is True`` (like the is_comment guard above): a MagicMock
        # token auto-creates a truthy ``compound`` attribute in tests.
        if getattr(word_token, "compound", False) is True:
            # Compound-matcher synthetics carry concatenated component kana,
            # which is visibly wrong for cross-particle merges (気がする →
            # キガシ: particle kana + non-base verb stem). ``reading`` reaches
            # the curation dialog's Reading column and TSV export, so
            # regenerate it from the headword via the memoized tagger path
            # (same source as expression_reading below).
            reading = self._reading(lemma)

        # ExpressionFurigana/Reading match the mined card front: orthBase
        # (source-orthography dictionary form) for verbs/adjectives, surface
        # for nouns (see TokenizedWord.mined_form / select_mined_form for
        # the trade-off).
        pos = word_token.feature.pos1
        orth_base = self._mining_base(word_token)
        mined = select_mined_form(pos, orth_base, lemma, surface)
        if mined == surface and getattr(word_token, "compound", False) is not True:
            # Single source of truth for the target reading (Task 1.2). When the
            # card front IS the surface token, keep the context-disambiguated
            # reading this token already carries instead of re-tokenizing the
            # surface in isolation: an isolated pass picks a context-free reading
            # for polyphonic nouns (方 かた/ほう, 中 なか/ちゅう), which would
            # split the card's ExpressionReading, expression furigana, and the
            # JPod101/audio-pack identity pair (mined_form + expression_reading)
            # from what the learner heard. This applies Yomitan's invariant —
            # one reading flows from the matched headword everywhere, and
            # anki-note-builder.js `getReading` overrides the parser token
            # reading with the entry reading (upstream e2ed450) — but inverted:
            # here the MeCab token IS the trustworthy contextual source, so we
            # propagate it outward rather than re-derive. ``reading`` here
            # equals extract_reading(word_token)
            # (the compound branch above, excluded by the guard, is the only
            # thing that overrides it). Compound synthetics carry wrong
            # concatenated component kana, so they take the else branch and keep
            # the headword-regenerated reading.
            expression_reading = katakana_to_hiragana(reading)
            expression_furigana = generate_furigana_from_tokens([word_token])
        else:
            # Verbs/adjectives mine as orthBase, whose reading is genuinely not
            # the surface token's kana (蒔い→蒔く); compound synthetics
            # regenerate from the headword. Both re-derive from ``mined``.
            expression_furigana = self._furigana(mined)
            expression_reading = self._reading(mined)
        # Lemma reading for the JPod101 audio retry: when the mined form
        # misses, the loop retries with the lemma kanji and needs the lemma's
        # OWN reading (探す→さがす), not the surface reading (さがし). For
        # most verb/adjective tokens ``mined`` (orthBase) equals the lemma,
        # so reuse the value; a kanji-variant divergence (乞う vs 請う)
        # recomputes the lemma's reading like the surface-mined case.
        lemma_reading = expression_reading if mined == lemma else self._reading(lemma)

        if self.config.bold_target_in_sentence:
            # Bold the full inflected form (verb/adjective + auxiliary
            # chain), not just the stem morpheme: 蒔いた, not 蒔い.
            sentence_bolded = wrap_target_plain(text, tok_start, highlight_end)
            sentence_furigana_bolded = wrap_target_furigana_from_tokens(text, raw_tokens, tok_start, highlight_end)
        else:
            sentence_bolded = ""
            sentence_furigana_bolded = ""

        return TokenizedWord(
            surface=surface,
            lemma=lemma,
            orth_base=orth_base,
            reading=reading,
            sentence=text,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            expression_furigana=expression_furigana,
            expression_reading=expression_reading,
            lemma_reading=lemma_reading,
            sentence_furigana=sentence_furigana,
            sentence_reading=sentence_reading,
            pos=word_token.feature.pos1,
            surface_start=tok_start,
            surface_end=tok_end,
            highlight_end=highlight_end,
            sentence_bolded=sentence_bolded,
            sentence_furigana_bolded=sentence_furigana_bolded,
        )

    def _emit_line_words_and_index(
        self,
        line_state: tuple[str, list[Any], list[Any], float, float, float],
        seen_lemmas: set[str],
        *,
        collect_index: bool,
    ) -> tuple[list[TokenizedWord], LineLemmas | None]:
        """Emit one line's deduped words plus its optional per-line lemma index.

        Returns ``(line_words, line_lemmas)``. ``line_words`` is the list of
        ``TokenizedWord`` objects emitted from this line, lemma-deduped against
        ``seen_lemmas`` (first occurrence across the whole file wins).
        ``line_lemmas`` is the line's ``LineLemmas`` index entry when
        ``collect_index`` is set — or ``None`` when ``collect_index`` is set but
        the line has zero content lemmas (skipped, so it returns ``([], None)``),
        or whenever ``collect_index`` is unset. ``collect_index`` gates exactly
        the index-only extras: ``lemma_first_span``, the zero-content-lemma line
        skip, and the ``LineLemmas`` build; word emission is unaffected.
        """
        text, raw_tokens, merged_tokens, start_time, end_time, duration = line_state

        # First pass: collect every content-word lemma/token on this line.
        # _should_include_word handles particle/aux/proper-noun filtering.
        # When collecting the index we also record (surface, start, end) for the
        # FIRST occurrence of each content lemma — the i+1 filter uses this to
        # re-bold against the swapped-in line.
        line_lemmas: set[str] = set()
        included_tokens: list = []
        included_spans: list[tuple[int, int, int]] = []
        lemma_first_span: dict[str, tuple[str, int, int, int]] = {}
        # Spans come from the shared locator — same offset and drop rule as
        # parse_subtitle_file (Issue #20 / T-38, see _iter_token_spans).
        for word_token, tok_start, tok_end in self._iter_token_spans(text, merged_tokens):
            if not self._should_include_word(word_token):
                continue
            lemma_here = self._extract_lemma(word_token)
            line_lemmas.add(lemma_here)
            included_tokens.append(word_token)
            # Computed once per token here and reused by the second pass, so
            # parse_subtitle_file and _with_index stay output-identical.
            highlight_end = self._find_highlight_end(text, raw_tokens, tok_start, tok_end, word_token)
            included_spans.append((tok_start, tok_end, highlight_end))
            if collect_index:
                lemma_first_span.setdefault(lemma_here, (word_token.surface, tok_start, tok_end, highlight_end))

        # A line with zero content words can never be i+1 — skip it from the
        # index entirely. (Word emission is also skipped trivially.)
        if collect_index and not line_lemmas:
            return [], None

        # Compute sentence-level furigana/reading ONCE for this line using the
        # already-parsed raw_tokens; shared by the LineLemmas entry and every
        # word emitted from this line.
        sentence_furigana = generate_furigana_from_tokens(raw_tokens)
        sentence_reading = generate_reading_from_tokens(raw_tokens)

        line_lemmas_entry: LineLemmas | None = None
        if collect_index:
            line_lemmas_entry = LineLemmas(
                line_text=text,
                lemmas=frozenset(line_lemmas),
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                sentence_furigana=sentence_furigana,
                sentence_reading=sentence_reading,
                lemma_spans=tuple(
                    (lemma_key, surface, span_start, span_end, span_highlight_end)
                    for lemma_key, (surface, span_start, span_end, span_highlight_end) in lemma_first_span.items()
                ),
            )

        # Second pass: emit deduped TokenizedWord entries (lemma-keyed).
        line_words: list[TokenizedWord] = []
        for word_token, (tok_start, tok_end, highlight_end) in zip(included_tokens, included_spans, strict=True):
            word = self._emit_word(
                word_token,
                tok_start,
                tok_end,
                highlight_end=highlight_end,
                text=text,
                raw_tokens=raw_tokens,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                sentence_furigana=sentence_furigana,
                sentence_reading=sentence_reading,
                seen_lemmas=seen_lemmas,
            )
            if word is not None:
                line_words.append(word)

        return line_words, line_lemmas_entry

    def parse_raw_entries(self, subtitle_file: Path) -> list[tuple[float, float, str]]:
        """Parse subtitle file and return raw timing entries without tokenization.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            List of (start_seconds, end_seconds, text) tuples

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        subs = self._load_subs(subtitle_file)

        entries = []
        for line in subs:
            # Skip ASS/SSA Comment events (same guard as _iter_parsed_lines).
            if getattr(line, "is_comment", None) is True:
                continue
            text = self._apply_text_filter(clean_subtitle_text(line.text))
            if not text:
                continue

            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)
            entries.append((start_time, end_time, text))

        return entries

    def parse_subtitle_file(self, subtitle_file: Path) -> list[TokenizedWord]:
        """Parse subtitle file and extract vocabulary words.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            List of TokenizedWord objects

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        # Reset per-parse memo caches so a second call on the same instance
        # does not serve entries from a previous parse run.
        self._reset_caches()

        all_words: list[TokenizedWord] = []
        seen_lemmas: set[str] = set()  # Track unique words by dictionary form (lemma).

        for (
            text,
            raw_tokens,
            merged_tokens,
            start_time,
            end_time,
            duration,
        ) in self._iter_parsed_lines(subtitle_file):
            # Sentence-level furigana/reading depend only on ``text`` — compute
            # once per line and share across every word emitted from this line.
            # Use raw_tokens (pre-merge tagger output) so the sentence is
            # tokenized only once per line. raw_tokens == tagger(text) so
            # output is byte-identical to generate_furigana(text, self.tagger).
            sentence_furigana = generate_furigana_from_tokens(raw_tokens)
            sentence_reading = generate_reading_from_tokens(raw_tokens)

            # Spans come from the shared locator (Issue #20 / T-38 — see
            # _iter_token_spans for the cursor+find and drop-rule rationale).
            for word_token, tok_start, tok_end in self._iter_token_spans(text, merged_tokens):
                if not self._should_include_word(word_token):
                    continue

                highlight_end = self._find_highlight_end(text, raw_tokens, tok_start, tok_end, word_token)
                word = self._emit_word(
                    word_token,
                    tok_start,
                    tok_end,
                    highlight_end=highlight_end,
                    text=text,
                    raw_tokens=raw_tokens,
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    sentence_furigana=sentence_furigana,
                    sentence_reading=sentence_reading,
                    seen_lemmas=seen_lemmas,
                )
                if word is not None:
                    all_words.append(word)

        return all_words

    def parse_subtitle_file_with_index(self, subtitle_file: Path) -> tuple[list[TokenizedWord], list[LineLemmas]]:
        """Parse a subtitle file and produce both the deduped mining list and a per-line lemma index.

        ``all_words`` is identical to ``parse_subtitle_file(subtitle_file)`` —
        same dedup-by-lemma semantics, same first-wins ordering.

        ``line_index`` is a parallel structure keyed by line: each entry holds
        every content lemma that appeared on that line (NO dedup against
        previously-seen words — the i+1 filter needs to count actual unknown
        lemmas per line). Lines with zero content lemmas are skipped since
        they can never qualify as i+1.

        Performance: ``sentence_furigana`` and ``sentence_reading`` are
        computed ONCE per line and shared by both ``TokenizedWord`` entries
        emitted from that line and the matching ``LineLemmas`` entry.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            Tuple of (deduped word list, per-line lemma index).

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        # Reset per-parse memo caches; see parse_subtitle_file for rationale.
        self._reset_caches()

        all_words: list[TokenizedWord] = []
        line_index: list[LineLemmas] = []
        seen_lemmas: set[str] = set()

        for line_state in self._iter_parsed_lines(subtitle_file):
            line_words, line_lemmas_entry = self._emit_line_words_and_index(line_state, seen_lemmas, collect_index=True)
            if line_lemmas_entry is not None:
                line_index.append(line_lemmas_entry)
            all_words.extend(line_words)

        return all_words, line_index

    def parse_text_units(
        self,
        units: Sequence[ReadingUnit],
        want_line_index: bool,
    ) -> tuple[list[TokenizedWord], list[LineLemmas] | None, collections.Counter[str]]:
        """Parse reading-tab text units into mining words, index, and lemma counts.

        The reading pipeline (manga volumes / novels) hands mined text as
        ``ReadingUnit``s — one paragraph or manga text block each — instead of a
        subtitle file. Each unit's ``text`` becomes the card sentence *verbatim*:
        there is no re-windowing, no ``clean_subtitle_text``, no regex filter,
        no pysubs2 and no per-file line cache on this path. ``unit.index``
        (document order) doubles as the dummy start AND end time, so
        ``duration`` is ``0.0`` and every duration-based optional filter is inert
        by design.

        One tokenize pass per unit: ``_build_line_state`` tokenizes once and both
        the returned Counter and the emitted words reuse its ``merged_tokens``.
        The Counter accumulates over ``_iter_token_spans`` (NOT the raw
        ``merged_tokens``) so a span-undroppable token is excluded from the count
        exactly as it is from mining — the T-38 mine-vs-count consistency guard
        (see ``count_lemmas`` / ``_iter_token_spans``). Emission flows through
        ``_emit_line_words_and_index`` so mining_base folding and lemma-tail
        stripping are inherited, never re-implemented here.

        Args:
            units: Ordered reading units (only ``.text``/``.index`` are read).
            want_line_index: When True, build the per-unit ``LineLemmas`` index
                (i+1 filter input) alongside the words; when False the index
                element of the returned tuple is ``None``.

        Returns:
            ``(words, line_index, counts)``. ``words`` is lemma-deduped
            (first-occurrence-wins across the whole call, like the subtitle
            entrypoints); ``line_index`` is the ``LineLemmas`` list when
            ``want_line_index`` else ``None``; ``counts`` maps lemma → total
            included occurrences (``count_lemmas`` semantics, no dedup).
        """
        # Public parse_* convention: reset the per-parse memo caches so a
        # multi-volume queue on one shared processor never serves stale
        # furigana/reading entries and cache growth stays bounded across units.
        self._reset_caches()

        all_words: list[TokenizedWord] = []
        line_index: list[LineLemmas] = []
        seen_lemmas: set[str] = set()
        counts: collections.Counter[str] = collections.Counter()

        for unit in units:
            # Dummy timing: the index is both start and end (duration 0.0). No
            # re-windowing exists — unit.text is the card sentence verbatim.
            line_state = self._build_line_state(unit.text, float(unit.index), float(unit.index))
            text, _raw_tokens, merged_tokens, *_ = line_state

            # Count through the SAME locator as the mining loop below (and
            # count_lemmas): a token mining drops (find == -1) is counted
            # nowhere it is not mined, or the preview over-promises (T-38 — see
            # _iter_token_spans for the drop-rule rationale).
            for token, _tok_start, _tok_end in self._iter_token_spans(text, merged_tokens):
                if self._should_include_word(token):
                    counts[self._extract_lemma(token)] += 1

            line_words, line_lemmas_entry = self._emit_line_words_and_index(
                line_state, seen_lemmas, collect_index=want_line_index
            )
            all_words.extend(line_words)
            if line_lemmas_entry is not None:
                line_index.append(line_lemmas_entry)

        return all_words, (line_index if want_line_index else None), counts

    def count_lemmas(self, subtitle_file: Path) -> collections.Counter[str]:
        """Return raw in-corpus lemma occurrence counts for a subtitle file.

        Unlike ``parse_subtitle_file``, this method counts every occurrence of a
        lemma (including repeats within and across lines) without deduplication.
        The same word-inclusion rules as mining apply — only tokens that
        ``_should_include_word`` accepts are counted.

        Args:
            subtitle_file: Path to subtitle file (.ass, .srt, .ssa)

        Returns:
            Counter mapping lemma → total occurrence count across all lines.

        Raises:
            SubtitleParseError: If subtitle file cannot be parsed
        """
        counts: collections.Counter[str] = collections.Counter()
        for text, _raw_tokens, merged_tokens, *_ in self._iter_parsed_lines(subtitle_file):
            # Spans come from the SAME locator as the mining loops in
            # parse_subtitle_file* — a token mining drops (find == -1),
            # counting drops too, or the count-vs-mine sets diverge and the
            # Deck Builder preview over-promises (T-38). The cursor+find and
            # drop-rule rationale lives on _iter_token_spans; do not inline a
            # divergent copy here.
            for token, _tok_start, _tok_end in self._iter_token_spans(text, merged_tokens):
                if self._should_include_word(token):
                    counts[self._extract_lemma(token)] += 1
        return counts

    # ------------------------------------------------------------------
    # Morphology delegates
    #
    # Implementations live in services/morphology.py (pure token-level
    # logic, no I/O). These one-line wrappers keep the service's private
    # seams stable for tests and patch-based callers.
    # ------------------------------------------------------------------

    def _merge_compound_suffixes(self, tokens: list) -> list:
        """Run all compound-merge passes (see morphology.merge_compound_suffixes)."""
        return merge_compound_suffixes(tokens)

    def _extract_lemma(self, word_token) -> str:
        """Extract lemma (dictionary form) from a token (see morphology.extract_lemma)."""
        return extract_lemma(word_token)

    def _mining_base(self, word_token) -> str:
        """Source-orthography dictionary form for mining, with derived
        sub-lemma folding (see morphology.mining_base)."""
        return mining_base(word_token)

    def _extract_reading(self, word_token) -> str:
        """Extract kana reading from a token (see morphology.extract_reading)."""
        return extract_reading(word_token)

    def _should_include_word(self, word_token) -> bool:
        """POS/subtype/script inclusion gate (see morphology.TokenInclusionRule.should_include)."""
        return self._inclusion_rule.should_include(word_token)
