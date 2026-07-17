"""Service for parsing subtitles and extracting vocabulary."""

import collections
import logging
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.models.reading import ReadingUnit
from anki_miner.models.word import select_mined_form
from anki_miner.services.compound_matcher import CompoundDictionaryMatcher, TermLookup
from anki_miner.services.deinflection import _is_pure_hiragana, find_highlight_end, resolve_dictionary_form
from anki_miner.services.morphology import (
    AttestLookup,
    ReadingLookup,
    TokenInclusionRule,
    _edit_distance,
    apply_special_readings,
    attest_merged_readings,
    extract_lemma,
    extract_orth_base,
    extract_reading,
    iter_token_spans,
    merge_compound_suffixes,
    mining_base,
    replace_overridden_spans,
)
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.utils import (
    clean_subtitle_text,
    generate_furigana,
    generate_reading,
    katakana_to_hiragana,
    strip_inline_annotations,
    wrap_target_plain,
)
from anki_miner.utils.ja_normalize import (
    normalize_for_tokenization,
    standardize_kanji_variants,
)
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding
from anki_miner.utils.text_utils import (
    _format_furigana,
    generate_furigana_from_tokens,
    generate_reading_from_tokens,
    wrap_target_furigana_from_tokens,
)

logger = logging.getLogger(__name__)

# Config fields SubtitleParserService actually reads. Callers that reuse a
# parser instance across configs (e.g. Deck Builder Phase 2 reusing Phase 1's
# filled per-file tokenization cache) must assert every one of these is
# untouched, or cached tokenization silently goes stale.
PARSE_RELEVANT_CONFIG_FIELDS = (
    "subtitle_offset",
    "bold_target_in_sentence",
    "allowed_pos",
    "excluded_subtypes",
    "use_subtitle_regex_filter",
    "subtitle_regex_filter",
    "subtitle_regex_replacement",
    "strip_subtitle_annotations",
)

# Dictionary-attested compound matching (Yomitan longest-match principle):
# multi-token spans whose joined form is an offline-dictionary headword are
# mined as ONE word (走り出した → 走り出す, 応急処置 stays whole); longest match
# wins and consumed components are not separately mined from that occurrence.
# Requires an injected term_lookup (an enabled indexed offline dictionary);
# without one, mining behavior is unchanged. Always on — previously the hidden
# `config.compound_matching` knob (ARC-004: inlined, never surfaced in any panel).
COMPOUND_MATCHING = True

# Maximum number of files held simultaneously in the per-instance per-file
# tokenization cache.  When the cap is hit the oldest entry (insertion order)
# is evicted so the dict stays bounded while still covering the Deck Builder's
# Phase-1 → Phase-2 cross-file reuse pattern for any corpus up to this size.
_LINE_CACHE_MAX_FILES: int = 256

# Bound for the verb-front resolver memo (_front_cache). Each entry is one tiny
# resolved-form string keyed by (inflected_surface, orth_base, cType); the set
# of distinct verb/adjective forms in any corpus is small, but a clear-on-cap
# keeps a whole-corpus Deck Builder run from growing without limit (mirrors the
# compound matcher's existence cache).
_FRONT_CACHE_CAP: int = 200_000

# Term-OR-reading offline existence probe (DefinitionService.has_offline_definitions:
# lookup_many runs ``WHERE term IN (...) OR reading IN (...)``). Reading-capable on
# purpose — きれい is attested only as 綺麗's READING, so a term-only probe misses it.
# Maps each queried card front to whether any offline dictionary attests it.
KanaAttestLookup = Callable[[list[str]], dict[str, bool]]

# POS backstop for kana recovery: only inflectional content words are recovered
# from the pure-hiragana script gate. Deliberately EXCLUDES 名詞 — formal nouns
# こと/もの/ため clear content_gate_ok but are grammar noise as bare kana — and
# 副詞/代名詞 (kana adverbs/pronouns are overwhelmingly fragments).
_KANA_RECOVER_POS1: frozenset[str] = frozenset({"動詞", "形容詞", "形状詞"})

# Auxiliary pos2 subtypes rejected even inside _KANA_RECOVER_POS1. Both classes
# pass the POS set + content_gate_ok as pure-hiragana, JMdict-attested forms:
# - 助動詞語幹: grammaticalized 形状詞 auxiliaries (よう in ようだ, みたい in
#   みたいな/みたいだ, そう in そうだ) — copular/hearsay grammar, not vocabulary.
# - 非自立可能: auxiliary-capable verbs (いる/ある/くれる/おく/しまう). The tag is
#   lexical, so 見ている's いる and 猫がいる's いる are byte-identical tokens — no
#   token-local rule can split aux from main-verb use, and recovering the class
#   would mint an いる card from every ている line (the dominant kana-recovery
#   junk source). Rejecting wholesale is the deliberate precision-over-recall
#   call: standalone kana いる/ある are N5 basics that were never mined pre-WS2
#   either. Kanji-spelled 非自立可能 tokens (見る, 来る) are untouched — they pass
#   should_include and never reach this path.
_KANA_RECOVER_REJECT_POS2: frozenset[str] = frozenset({"助動詞語幹", "非自立可能"})


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(
        self,
        config: AnkiMinerConfig,
        term_lookup: TermLookup | None = None,
        reading_lookup: ReadingLookup | None = None,
        kana_attest_lookup: KanaAttestLookup | None = None,
    ):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
            kana_attest_lookup: Optional term-OR-reading offline existence probe
                (``DefinitionService.has_offline_definitions``). When provided,
                pure-hiragana content words the script gate would drop (きれい,
                ある, すごい) are recovered iff their mined-form card front is an
                attested dictionary headword (by term OR reading — きれい is only
                a reading). ``None`` (no offline dict) safe-degrades to the
                pre-recovery behavior: all pure-hiragana content words dropped.
            term_lookup: Optional batch headword-existence probe
                (``DefinitionService.offline_terms_exist``). When provided,
                dictionary-attested multi-token spans are merged into single
                words (Yomitan longest-match). ``None`` (no offline dictionary
                or raw-entry-only callers) keeps parsing byte-identical to
                the pre-compound-matching behavior.
            reading_lookup: Optional batch attested-readings probe
                (``DefinitionService.offline_term_readings``). When provided,
                merged-compound kana is corrected to the dictionary's attested
                reading (``morphology.attest_merged_readings`` — the rendaku /
                on-kun junction fix, 2026-07 audit F2). Independent of the
                compound matcher: the morphology merges it serves
                (noun-suffix/prefix/nominalizer) run regardless.
                ``None`` keeps parsing byte-identical.
        """
        self.config = config
        self._reading_lookup = reading_lookup
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
        # Same injected offline existence probe drives the verb-front resolver
        # (see _resolve_front / deinflection.resolve_dictionary_form). None ⇒ the
        # resolver safe-degrades and mining stays byte-identical to pre-resolver.
        self._term_lookup = term_lookup
        # Per-instance MEMOIZED existence probe shared by the compound-merge gate
        # (morphology.merge_compound_suffixes) AND the compound matcher: caches
        # existence per surface so a repeated corpus (count_lemmas / Deck Builder
        # coverage hot path) probes each distinct surface through the underlying
        # offline dictionary at most once. None when no dict is wired — the merge
        # passes then run UNGATED, so the no-dict output is byte-identical to the
        # pre-gate behavior (exactly like the matcher's term_lookup gating).
        self._exist_memo: dict[str, bool] = {}
        self._attest: AttestLookup | None = self._memoized_attest if term_lookup is not None else None
        # Dictionary-attested compound matching (see services/compound_matcher.py).
        # Built only when a term lookup is injected (COMPOUND_MATCHING is always
        # on); the matcher reuses the inclusion rule so spans start only at
        # mineable tokens, and the SAME memoized probe so a surface's existence is
        # looked up once across the merge gate and the matcher.
        self._compound_matcher: CompoundDictionaryMatcher | None = None
        if self._attest is not None and COMPOUND_MATCHING:
            self._compound_matcher = CompoundDictionaryMatcher(self._attest, self._inclusion_rule)
        # Reading-capable offline existence probe for kana recovery
        # (see _recover_kana_content_word). None ⇒ no recovery, safe degrade.
        self._kana_attest_lookup = kana_attest_lookup
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
        # Verb-front resolver memo (distinct lifetime from the per-parse memos,
        # like _line_cache): the deinflect + offline existence lookup is
        # deterministic per (inflected_surface, orth_base, cType), so it survives
        # across parse_* calls and is bounded by clear-on-cap (_FRONT_CACHE_CAP).
        self._front_cache: dict[tuple[str, str, str], str] = {}
        # Kana-recovery memo (same lifetime/bounding rationale as _front_cache):
        # the recovery decision — content_gate_ok + the SQLite existence probe —
        # is deterministic per (surface, pos1), so _should_include_word runs it
        # once per distinct token instead of once per occurrence (count_lemmas is
        # a hot path: tens of thousands of tokens). Caches misses too.
        self._kana_recover_cache: dict[tuple[str, str], bool] = {}

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
        self._hw_reading_cache: dict[str, str | None] = {}

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

    def _attested_headword_reading(self, headword: str) -> str | None:
        """Best attested reading for a compound HEADWORD, memoized; None on miss.

        Expression-fields fallback for inflected kind-A spans (audit F2): the
        span surface (手っ取り早く) is not a headword, so the token-level
        attestation pass skipped it — but the mined card front IS the headword
        (手っ取り早い), which the dictionary attests (てっとりばやい). Same
        selection policy as ``attest_merged_readings``, anchored on the
        headword re-tokenize concat: keep it when attested, else the single or
        edit-distance-closest attested reading. Returns hiragana. ``None``
        when no reading_lookup is wired or the dictionary attests nothing —
        callers fall back to the re-tokenize reading. Only ever called for
        compound synthetics, so plain tokens add zero lookups.
        """
        if self._reading_lookup is None:
            return None
        if headword not in self._hw_reading_cache:
            attested = self._reading_lookup([headword]).get(headword) or []
            result: str | None = None
            if attested:
                folded = [katakana_to_hiragana(r) for r in attested]
                concat = self._reading(headword)
                if concat in folded:
                    result = concat
                elif len(folded) == 1:
                    result = folded[0]
                else:
                    result = min(folded, key=lambda r: (_edit_distance(r, concat), folded.index(r)))
            self._hw_reading_cache[headword] = result
        return self._hw_reading_cache[headword]

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

    def _clean_line_text(self, raw_text: str) -> str:
        """Full per-line text pipeline shared by the mining and display paths.

        Order: ``clean_subtitle_text`` (markup strip + JP normalization) →
        ``strip_inline_annotations`` (structural SFX-caption / speaker-tag /
        inline-furigana strip, gated on ``config.strip_subtitle_annotations``,
        default ON) → ``_apply_text_filter`` (the user regex, which composes on
        top of the strip). Applied identically by ``_iter_parsed_lines`` (mining)
        and ``parse_raw_entries`` (display) so the shown cue text matches what
        mining tokenizes. A line that collapses to empty is skipped by each
        caller's existing ``if not text: continue`` guard.
        """
        cleaned = clean_subtitle_text(raw_text)
        if self.config.strip_subtitle_annotations:
            cleaned = strip_inline_annotations(cleaned)
        return self._apply_text_filter(cleaned)

    def _load_subs(self, subtitle_file: Path):
        """Load a subtitle file via pysubs2 with normalized error wrapping.

        Shared by every public parse_* method so error wrapping stays
        consistent regardless of entry point. The UTF-8 default is tried first
        (the ``pysubs2.load`` seam patched by tests); on a decode failure the
        shared cp932-first fallback (see utils/subtitle_encoding.py) runs so
        Shift-JIS subtitles parse instead of aborting the episode.
        """
        try:
            try:
                return pysubs2.load(str(subtitle_file))
            except UnicodeDecodeError as utf8_error:
                return load_with_fallback_encoding(subtitle_file, utf8_error)
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
            text = self._clean_line_text(line.text)
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
        # Dictionary reading attestation for merged compounds (audit F2): fixes
        # rendaku/junction kana on the synthetics; no-op (and no lookup) when
        # no reading_lookup is wired or the line produced no merges.
        merged_tokens = attest_merged_readings(merged_tokens, self._reading_lookup)
        return (text, raw_tokens, merged_tokens, start, end, end - start)

    @staticmethod
    def _iter_token_spans(text: str, tokens: list) -> Iterator[tuple[Any, int, int]]:
        """Single-source token-span locator (see morphology.iter_token_spans)."""
        return iter_token_spans(text, tokens)

    @staticmethod
    def _build_display_tokens(text: str, raw_tokens: list, merged_tokens: list) -> list:
        """Sentence display stream, shared by BOTH mining entrypoints.

        Order matters: attested-overridden compound spans are carried into the
        raw stream first (``replace_overridden_spans`` — spans whose merged
        kana the dictionary corrected, audit F2), then the honorific-kinship
        override (``apply_special_readings``) handles adjacent raw pairs the
        merges didn't consume. Both passes keep the concatenated surface text
        byte-identical, so span/offset math downstream is unaffected. Extracted
        as the single seam so ``parse_subtitle_file`` and
        ``_emit_line_words_and_index`` can never diverge again.
        """
        return apply_special_readings(replace_overridden_spans(text, raw_tokens, merged_tokens))

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
        display_tokens: list,
        start_time: float,
        end_time: float,
        duration: float,
        sentence_furigana: str,
        sentence_reading: str,
        seen_mined_forms: set[str],
    ) -> TokenizedWord | None:
        """Build the ``TokenizedWord`` for one included token, mined_form-deduped.

        Shared tail of ``parse_subtitle_file`` and
        ``parse_subtitle_file_with_index``: mined_form-keyed dedup (first
        occurrence wins, recorded in ``seen_mined_forms``), reading/expression
        assembly and the optional bold-target sentence variants. Returns
        ``None`` when the token's mined_form was already emitted.
        """
        # Get lemma (dictionary form) for lookups; surface is the raw token.
        lemma = self._extract_lemma(word_token)
        surface = word_token.surface

        # mined_form is the card-front spelling: orthBase (source orthography)
        # for verbs/adjectives, surface otherwise (see select_mined_form).
        pos = word_token.feature.pos1
        orth_base = self._mining_base(word_token)
        # Verb/adjective fronts: rewrite the archaic じる/ずる orthBase (感ずる) to
        # the modern JMdict headword (感じる) when the deinflected inflected span
        # attests it. No-op for every other POS, for mining_base folds, and when
        # no offline dict is wired. resolved_reading (pitch realignment) is set
        # after ``mined`` below.
        resolved_front = self._resolve_front(word_token, orth_base, text, tok_start, highlight_end)
        front_overridden = resolved_front != orth_base
        orth_base = resolved_front
        mined = select_mined_form(pos, orth_base, lemma, surface)

        # Dedup on mined_form, NOT lemma: UniDic collapses kanji-variant
        # homographs onto one canonical lemma (賭ける/掛ける → 掛ける), but they
        # are distinct card fronts driving distinct definition/frequency/audio/
        # known-word lookups, so lemma-keyed dedup silently dropped the second
        # variant. mined_form is the identity every other stage already uses.
        if mined in seen_mined_forms:
            return None
        seen_mined_forms.add(mined)

        # Get reading if available
        reading = self._extract_reading(word_token)
        kana_attested = getattr(word_token.feature, "kana_attested", False) is True
        # Strict ``is True`` (like the is_comment guard above): a MagicMock
        # token auto-creates a truthy ``compound`` attribute in tests.
        if getattr(word_token, "compound", False) is True:
            # Attested span (audit F2): the attestation pass corrected this
            # token's kana against the dictionary — trust it, folded to
            # hiragana (the compound-reading convention: curation Reading
            # column / TSV export show hiragana for compounds). Unattested
            # span (inflected kind-A: 手っ取り早く is not a headword): try the
            # HEADWORD's attested reading — the dictionary form the card
            # front shows — before falling back to the headword re-tokenize
            # (which re-concatenates per-token kana: 気がする → キガシ,
            # 手っ取り早い → てっとりはやい instead of てっとりばやい).
            if kana_attested:
                reading = katakana_to_hiragana(reading)
            else:
                reading = self._attested_headword_reading(lemma) or self._reading(lemma)

        # ExpressionFurigana/Reading match the mined card front (computed above):
        # orthBase for verbs/adjectives, surface for nouns (see
        # TokenizedWord.mined_form / select_mined_form for the trade-off).
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
        elif getattr(word_token, "compound", False) is True and kana_attested and mined == surface:
            # Attested compound whose card front IS the span surface (kind-B, or
            # a kind-A span appearing UNINFLECTED): the dictionary-corrected kana
            # IS the expression reading — re-tokenizing ``mined`` would
            # re-concatenate per-token kana and resurrect the rendaku bug (audit
            # F2). ``reading`` was folded to hiragana in the compound branch
            # above. The ``mined == surface`` guard (U6) is load-bearing: an
            # INFLECTED kind-A span (surface 絶え間なく, mined headword 絶え間ない)
            # can itself be an attested headword (絶え間なく is a JMdict adverb),
            # stamping kana_attested on the span — but its attested kana is the
            # INFLECTED reading (たえまなく), not the headword reading the card
            # front shows. Such spans (mined != surface) fall through to the
            # headword-attestation elif below, which yields たえまない.
            expression_reading = reading
            expression_furigana = _format_furigana(mined, expression_reading)
        elif (
            getattr(word_token, "compound", False) is True
            and (attested_headword := self._attested_headword_reading(mined)) is not None
        ):
            # Inflected kind-A compound (span surface unattested): the mined
            # card front IS the headword, so its attested reading applies to
            # the expression fields even though the sentence span keeps its
            # concat kana (declared residual for sentence ruby only).
            expression_reading = attested_headword
            expression_furigana = _format_furigana(mined, expression_reading)
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

        # Pitch reading realignment: when the resolver diverged the front from
        # the lemma (感じる card, but archaic lemma 感ずる), pitch must key on the
        # front's reading (かんじる), not the lemma's own (感ずる→かんずる). Derive
        # it from the resolved front's kana (== expression_reading on the
        # overridden verb path). Empty when no override fired ⇒ pitch keeps the
        # lemma_reading path unchanged.
        resolved_reading = self._reading(mined) if front_overridden else ""

        if self.config.bold_target_in_sentence:
            # Bold the full inflected form (verb/adjective + auxiliary
            # chain), not just the stem morpheme: 蒔いた, not 蒔い.
            sentence_bolded = wrap_target_plain(text, tok_start, highlight_end)
            sentence_furigana_bolded = wrap_target_furigana_from_tokens(text, display_tokens, tok_start, highlight_end)
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
            resolved_reading=resolved_reading,
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
        seen_mined_forms: set[str],
        *,
        collect_index: bool,
    ) -> tuple[list[TokenizedWord], LineLemmas | None]:
        """Emit one line's deduped words plus its optional per-line lemma index.

        Returns ``(line_words, line_lemmas)``. ``line_words`` is the list of
        ``TokenizedWord`` objects emitted from this line, mined_form-deduped
        against ``seen_mined_forms`` (first occurrence across the whole file
        wins). The per-line ``line_lemmas`` index stays lemma-keyed (the i+1
        filter counts distinct lemmas, not card fronts).
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

        # Compute sentence-level furigana/reading ONCE for this line, from the
        # shared display stream (attested-compound override + honorific-kinship
        # pass; see _build_display_tokens). Surfaces are unchanged, so
        # span/offset math is unaffected.
        display_tokens = self._build_display_tokens(text, raw_tokens, merged_tokens)
        sentence_furigana = generate_furigana_from_tokens(display_tokens)
        sentence_reading = generate_reading_from_tokens(display_tokens)

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

        # Second pass: emit deduped TokenizedWord entries (mined_form-keyed).
        line_words: list[TokenizedWord] = []
        for word_token, (tok_start, tok_end, highlight_end) in zip(included_tokens, included_spans, strict=True):
            word = self._emit_word(
                word_token,
                tok_start,
                tok_end,
                highlight_end=highlight_end,
                text=text,
                display_tokens=display_tokens,
                start_time=start_time,
                end_time=end_time,
                duration=duration,
                sentence_furigana=sentence_furigana,
                sentence_reading=sentence_reading,
                seen_mined_forms=seen_mined_forms,
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
            text = self._clean_line_text(line.text)
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
        seen_mined_forms: set[str] = set()  # Track unique words by card-front mined_form.

        for (
            text,
            raw_tokens,
            merged_tokens,
            start_time,
            end_time,
            duration,
        ) in self._iter_parsed_lines(subtitle_file):
            # Sentence-level furigana/reading depend only on ``text`` — compute
            # once per line and share across every word emitted from this line,
            # via the shared display stream (attested-compound override +
            # honorific-kinship pass; see _build_display_tokens). Surfaces are
            # unchanged so span math is unaffected.
            display_tokens = self._build_display_tokens(text, raw_tokens, merged_tokens)
            sentence_furigana = generate_furigana_from_tokens(display_tokens)
            sentence_reading = generate_reading_from_tokens(display_tokens)

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
                    display_tokens=display_tokens,
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    sentence_furigana=sentence_furigana,
                    sentence_reading=sentence_reading,
                    seen_mined_forms=seen_mined_forms,
                )
                if word is not None:
                    all_words.append(word)

        return all_words

    def parse_subtitle_file_with_index(self, subtitle_file: Path) -> tuple[list[TokenizedWord], list[LineLemmas]]:
        """Parse a subtitle file and produce both the deduped mining list and a per-line lemma index.

        ``all_words`` is identical to ``parse_subtitle_file(subtitle_file)`` —
        same dedup-by-mined_form semantics, same first-wins ordering.

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
        seen_mined_forms: set[str] = set()

        for line_state in self._iter_parsed_lines(subtitle_file):
            line_words, line_lemmas_entry = self._emit_line_words_and_index(
                line_state, seen_mined_forms, collect_index=True
            )
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
        subtitle file. Each unit's ``text`` is normalized for tokenization (the
        same ``normalize_for_tokenization`` + ``standardize_kanji_variants`` the
        subtitle path applies via ``clean_subtitle_text`` — mokuro OCR emits
        Kangxi radicals and halfwidth katakana that otherwise mis-tokenize), and
        that normalized form becomes the card sentence: there is no re-windowing,
        no markup strip, no regex filter, no pysubs2 and no per-file line cache
        on this path. ``unit.index`` (document order) doubles as the dummy start
        AND end time, so ``duration`` is ``0.0`` and every duration-based
        optional filter is inert by design.

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
            ``(words, line_index, counts)``. ``words`` is mined_form-deduped
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
        seen_mined_forms: set[str] = set()
        counts: collections.Counter[str] = collections.Counter()

        for unit in units:
            # Reading/OCR text needs the same pre-tokenization JP normalization
            # the subtitle path gets via clean_subtitle_text: mokuro OCR emits
            # Kangxi radicals (⼝) and halfwidth katakana (ﾊﾟｿｺﾝ) that mis-tokenize
            # into garbage otherwise. The normalized text is BOTH tokenized and
            # stored as the card sentence, so the displayed sentence matches what
            # was mined (as on the subtitle path). Order mirrors clean_subtitle_text
            # (normalize_for_tokenization then standardize_kanji_variants); the
            # markup strip / regex filter it also runs are subtitle-only.
            text = standardize_kanji_variants(normalize_for_tokenization(unit.text))
            # Dummy timing: the index is both start and end (duration 0.0). No
            # re-windowing exists — the normalized unit text is the card sentence.
            line_state = self._build_line_state(text, float(unit.index), float(unit.index))
            text, _raw_tokens, merged_tokens, *_ = line_state

            # Count through the SAME locator as the mining loop below (and
            # count_lemmas): a token mining drops (find == -1) is counted
            # nowhere it is not mined, or the preview over-promises (T-38 — see
            # _iter_token_spans for the drop-rule rationale).
            for token, _tok_start, _tok_end in self._iter_token_spans(text, merged_tokens):
                if self._should_include_word(token):
                    counts[self._extract_lemma(token)] += 1

            line_words, line_lemmas_entry = self._emit_line_words_and_index(
                line_state, seen_mined_forms, collect_index=want_line_index
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

    def _memoized_attest(self, surfaces: list[str]) -> set[str]:
        """Per-instance memoized offline-existence probe (see __init__).

        Wraps ``self._term_lookup``, caching each surface's existence in
        ``self._exist_memo`` so a repeated corpus probes each distinct surface at
        most once, and returns the attested subset of ``surfaces``. Shared by the
        morphology compound-merge gate and the compound matcher. Clear-on-cap
        bounds the memo on whole-corpus Deck Builder runs (mirrors _front_cache /
        the matcher's existence cache). Only bound to ``self._attest`` when a
        ``term_lookup`` exists; the ``None`` guard is defensive.
        """
        if self._term_lookup is None:
            return set()
        unknown = [s for s in dict.fromkeys(surfaces) if s not in self._exist_memo]
        if unknown:
            if len(self._exist_memo) + len(unknown) > _FRONT_CACHE_CAP:
                self._exist_memo.clear()
            hits = self._term_lookup(unknown)
            for s in unknown:
                self._exist_memo[s] = s in hits
        return {s for s in surfaces if self._exist_memo.get(s)}

    def _merge_compound_suffixes(self, tokens: list) -> list:
        """Run all compound-merge passes (see morphology.merge_compound_suffixes).

        Threads the per-instance memoized attest probe: with an offline dict the
        junk-prone noun-suffix/prefix passes are attested-or-bail gated; ``None``
        (no dict) leaves them ungated — output byte-identical to pre-gate.
        """
        return merge_compound_suffixes(tokens, attest=self._attest)

    def _extract_lemma(self, word_token) -> str:
        """Extract lemma (dictionary form) from a token (see morphology.extract_lemma)."""
        return extract_lemma(word_token)

    def _mining_base(self, word_token) -> str:
        """Source-orthography dictionary form for mining, with derived
        sub-lemma folding (see morphology.mining_base)."""
        return mining_base(word_token)

    def _resolve_front(self, word_token, orth_base: str, text: str, tok_start: int, highlight_end: int) -> str:
        """Modern JMdict dictionary form for a verb/adjective card front.

        Returns ``orth_base`` unchanged for every non-verb/adjective token, for
        ``mining_base`` folds (never un-fold a potential/ra-nuki/ク-form — its
        orth_base is the parent lemma, not the token's own orthBase), when no
        offline ``term_lookup`` is wired (safe degrade), and whenever the
        resolver can't improve on orth_base. Otherwise the archaic じる/ずる
        orthBase (感ずる) is rewritten to the deinflection-attested modern
        headword (感じる). See deinflection.resolve_dictionary_form for the
        algorithm; the deinflect + offline existence lookup is memoized per
        ``(inflected_surface, orth_base, cType)`` so identical tokens never repeat
        the work.
        """
        feature = getattr(word_token, "feature", None)
        if getattr(feature, "pos1", None) not in ("動詞", "形容詞"):
            return orth_base
        if self._term_lookup is None:
            return orth_base
        if orth_base != extract_orth_base(word_token):
            return orth_base
        # The inflected span the resolver deinflects: the token surface plus its
        # rightward highlight extension (感じた), or the bare surface when it did
        # not extend (感じ before a noun) — run either way, else 感じ-before-a-noun
        # keeps the archaic 感ずる.
        inflected_surface = text[tok_start:highlight_end]
        ctype = getattr(feature, "cType", None)
        key = (inflected_surface, orth_base, ctype if isinstance(ctype, str) else "")
        cached = self._front_cache.get(key)
        if cached is None:
            cached = resolve_dictionary_form(inflected_surface, orth_base, self._term_lookup)
            if len(self._front_cache) >= _FRONT_CACHE_CAP:
                self._front_cache.clear()
            self._front_cache[key] = cached
        return cached

    def _extract_reading(self, word_token) -> str:
        """Extract kana reading from a token (see morphology.extract_reading)."""
        return extract_reading(word_token)

    def _should_include_word(self, word_token) -> bool:
        """POS/subtype/script inclusion gate, plus JMdict-attested kana recovery.

        Tokens the pure morphology rule accepts (kanji / katakana loanwords) pass
        straight through. Anything it rejects gets ONE more chance:
        ``_recover_kana_content_word`` re-admits a pure-hiragana 動詞/形容詞/形状詞
        whose mined-form card front is an attested dictionary headword — recovering
        real kana vocabulary (きれい, すごい, わかる) that the script gate drops by
        default. count_lemmas and both mining passes call this method, so the
        recovery is identical across count and mine (the T-38 parity guard).
        """
        if self._inclusion_rule.should_include(word_token):
            return True
        return self._recover_kana_content_word(word_token)

    def _recover_kana_content_word(self, word_token) -> bool:
        """Whether an otherwise-rejected pure-hiragana content word is recoverable.

        Gate (ALL must hold; cheap checks first so the SQLite probe is the last
        resort and only distinct tokens ever reach it):

        1. A reading-capable offline probe is wired — else safe-degrade to no
           recovery (``None`` ⇒ today's behavior).
        2. ``pos1 ∈ {動詞, 形容詞, 形状詞}`` and ``pos2 ∉ {助動詞語幹, 非自立可能}``
           — the junk backstop that excludes 名詞 formal nouns (こと/もの/ため),
           grammaticalized 形状詞 auxiliaries (よう/みたい in ようだ/みたいな) and
           auxiliary-capable verbs (いる/ある/くれる in ている/てくれる)
           content_gate_ok alone would let through.
        3. The surface is pure hiragana — the only class the script gate dropped;
           everything else was already decided by ``should_include``.
        4. ``content_gate_ok`` passes and the mined-form card front is attested
           (memoized per ``(surface, pos1)`` — steps 4+ run once per distinct
           token, never per occurrence).
        """
        if self._kana_attest_lookup is None:
            return False
        feature = getattr(word_token, "feature", None)
        pos1 = getattr(feature, "pos1", None)
        if pos1 not in _KANA_RECOVER_POS1:
            return False
        if getattr(feature, "pos2", None) in _KANA_RECOVER_REJECT_POS2:
            # ようだ/みたいな stems + いる/ある-class auxiliary-capable verbs —
            # grammar, not vocabulary. See constant for the full rationale.
            return False
        surface = word_token.surface
        if not isinstance(surface, str) or not _is_pure_hiragana(surface):
            return False
        key = (surface, pos1)
        if key not in self._kana_recover_cache:
            if len(self._kana_recover_cache) >= _FRONT_CACHE_CAP:
                self._kana_recover_cache.clear()
            self._kana_recover_cache[key] = self._probe_kana_recovery(word_token, pos1, surface)
        return self._kana_recover_cache[key]

    def _probe_kana_recovery(self, word_token, pos1: str, surface: str) -> bool:
        """content_gate_ok + term-OR-reading attestation of the mined-form front.

        The form probed is the exact card front ``_emit_word`` would mint
        (``select_mined_form``): the surface for 形状詞 (きれい), the orthBase
        dictionary form for 動詞/形容詞 (わかった's わかっ token → わかる, since
        unidic's orthBase is already deinflected). Existence-gated only — the
        probe never reads ``entries.score`` (uniformly 0 on the bundled dict).
        """
        lookup = self._kana_attest_lookup
        if lookup is None:  # unreachable via _recover_kana_content_word; narrows for mypy
            return False
        if not self._inclusion_rule.content_gate_ok(word_token):
            return False
        orth_base = self._mining_base(word_token)
        lemma = self._extract_lemma(word_token)
        form = select_mined_form(pos1, orth_base, lemma, surface)
        if not form:
            return False
        return bool(lookup([form]).get(form))
