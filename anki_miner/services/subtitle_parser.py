"""Service for parsing subtitles and extracting vocabulary."""

import collections
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pysubs2

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.services.tagger import get_shared_tagger
from anki_miner.utils import (
    clean_subtitle_text,
    generate_furigana,
    generate_reading,
    wrap_target_plain,
)
from anki_miner.utils.text_utils import (
    generate_furigana_from_tokens,
    generate_reading_from_tokens,
    wrap_target_furigana_from_tokens,
)

logger = logging.getLogger(__name__)

_NOMINAL_SUFFIX_POS2 = {"名詞的", "形状詞的", "副詞的"}

# Whitelist of 接頭辞 surfaces that productively form compounds with
# 名詞/形状詞 roots. Used by _merge_prefix_compounds to avoid false positives
# from rare/unproductive 接頭辞 entries in unidic.
_PREFIX_WHITELIST = frozenset({"無", "不", "非", "反", "超", "未", "新", "旧", "全", "半", "副", "元", "再", "最"})

# 接尾辞(名詞的) surfaces that nominalize a preceding 動詞 連用形 stem
# (e.g. 言い+方 → 言い方). Restricted to a small productive set; 者/事/物
# etc. are not included because they tokenize differently and would
# over-merge.
_VERB_NOMINALIZER_SUFFIXES = frozenset({"方", "手", "様"})


class _SyntheticToken:
    """Duck-typed token replacement for merged compounds.

    Mimics fugashi token attribute access (.surface,
    .feature.{pos1,pos2,lemma,kana}).
    """

    __slots__ = ("surface", "feature")

    def __init__(self, surface: str, pos1: str, pos2: str, lemma: str, kana: str):
        self.surface = surface
        self.feature = SimpleNamespace(pos1=pos1, pos2=pos2, lemma=lemma, kana=kana)


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(self, config: AnkiMinerConfig):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
        """
        self.config = config
        # Shared process-wide tagger (see services/tagger.py for the single-flight
        # invariant). __init__ may block ~2-3s on the lazy build if a user triggers
        # the first SubtitleParserService before prewarm_tagger() finishes; worst
        # case is the same wait they'd incur anyway, no correctness impact. GUI-
        # thread call sites that only call parse_raw_entries never tokenize, so
        # they don't race the worker thread's .parse() calls on this shared tagger.
        self.tagger = get_shared_tagger()
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

        Yields ``(text, raw_tokens, merged_tokens, start_time, end_time, duration)``.
        ``text`` is the cleaned + regex-filtered line; ``raw_tokens`` is the
        direct output of ``self.tagger(text)`` (used by ``_from_tokens`` helpers
        so the sentence is tokenized only once); ``merged_tokens`` is the full
        output of ``_merge_compound_suffixes`` (callers apply
        ``_should_include_word`` themselves so the index path and mining path
        share identical token selection logic).

        Per-file cache: keyed by resolved path → (mtime, line-state list); only
        the most-recently-parsed file is retained (bounded to one entry). On a
        cache HIT for the same path+mtime the subtitle file is neither reloaded
        nor re-tokenized — the stored line-state (the very tuples a fresh parse
        would yield, including ``_SyntheticToken``s) is replayed. An mtime
        mismatch (file edited between passes) invalidates the entry and forces a
        fresh load + tokenize, preserving today's behaviour. Consumers MUST NOT
        mutate the yielded ``merged_tokens`` lists/tokens, as they are shared
        across passes; current consumers only read them.
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
            text = self._apply_text_filter(clean_subtitle_text(line.text))
            if not text:
                continue

            # Convert timing from milliseconds to seconds and apply offset
            start_time = max(0.0, (line.start / 1000.0) + self.config.subtitle_offset)
            end_time = max(start_time, (line.end / 1000.0) + self.config.subtitle_offset)
            duration = end_time - start_time

            # Tokenize with MeCab and run compound-merge passes
            raw_tokens = list(self.tagger(text))
            merged_tokens = self._merge_compound_suffixes(raw_tokens)

            line_state = (text, raw_tokens, merged_tokens, start_time, end_time, duration)
            line_states.append(line_state)
            yield line_state

        # mtime is None only when stat() failed, in which case _load_subs above
        # already raised, so this assignment is reachable only with a real mtime.
        #
        # Bound the cache to the current file only. Cross-phase reuse (Deck
        # Builder Phase 1 -> Phase 2) re-parses the SAME file back-to-back, so
        # keeping prior files' tokenized line-state would grow the cache
        # unbounded across a many-episode build with no hit benefit. Replacing
        # the dict evicts the previous file as the new one is committed.
        if mtime is not None:
            self._line_cache = {key: (mtime, line_states)}

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

        for text, raw_tokens, merged_tokens, start_time, end_time, duration in self._iter_parsed_lines(subtitle_file):
            # Sentence-level furigana/reading depend only on ``text`` — compute
            # once per line and share across every word emitted from this line.
            # Use raw_tokens (pre-merge tagger output) so the sentence is
            # tokenized only once per line. raw_tokens == tagger(text) so
            # output is byte-identical to generate_furigana(text, self.tagger).
            sentence_furigana = generate_furigana_from_tokens(raw_tokens)
            sentence_reading = generate_reading_from_tokens(raw_tokens)

            # Locate each token's char span via ``str.find`` from a running
            # cursor. MeCab silently drops whitespace from the token stream,
            # so naive ``cursor += len(surface)`` walking drifts left by the
            # count of preceding spaces and misaligns every downstream
            # offset (bold wrapping, surface_start/end). Issue #20.
            cursor = 0
            for word_token in merged_tokens:
                surface = word_token.surface
                idx = text.find(surface, cursor)
                if idx == -1:
                    # Defensive: should not happen for unmodified MeCab
                    # surfaces. Skip rather than emit a wrong span.
                    continue
                tok_start = idx
                tok_end = idx + len(surface)
                cursor = tok_end

                if not self._should_include_word(word_token):
                    continue

                # Get lemma (dictionary form) for lookups and deduplication
                lemma = self._extract_lemma(word_token)
                surface = word_token.surface

                # Dedup on lemma alone: surface variants of the same dictionary
                # form should collapse, not block each other.
                if lemma in seen_lemmas:
                    continue
                seen_lemmas.add(lemma)

                # Get reading if available
                reading = self._extract_reading(word_token)

                # ExpressionFurigana/Reading match the mined card front:
                # lemma for verbs/adjectives, surface for nouns (see
                # TokenizedWord.mined_form for the trade-off).
                pos = word_token.feature.pos1
                mined = lemma if pos in ("動詞", "形容詞") else surface
                expression_furigana = self._furigana(mined)
                expression_reading = self._reading(mined)

                if self.config.bold_target_in_sentence:
                    sentence_bolded = wrap_target_plain(text, tok_start, tok_end)
                    sentence_furigana_bolded = wrap_target_furigana_from_tokens(text, raw_tokens, tok_start, tok_end)
                else:
                    sentence_bolded = ""
                    sentence_furigana_bolded = ""

                all_words.append(
                    TokenizedWord(
                        surface=surface,
                        lemma=lemma,
                        reading=reading,
                        sentence=text,
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        expression_furigana=expression_furigana,
                        expression_reading=expression_reading,
                        sentence_furigana=sentence_furigana,
                        sentence_reading=sentence_reading,
                        pos=word_token.feature.pos1,
                        surface_start=tok_start,
                        surface_end=tok_end,
                        sentence_bolded=sentence_bolded,
                        sentence_furigana_bolded=sentence_furigana_bolded,
                    )
                )

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

        for text, raw_tokens, merged_tokens, start_time, end_time, duration in self._iter_parsed_lines(subtitle_file):
            # First pass: collect every content-word lemma on this line.
            # _should_include_word handles particle/aux/proper-noun filtering.
            # We also record (surface, start, end) for the FIRST occurrence
            # of each content lemma — the i+1 filter uses this to re-bold
            # against the swapped-in line.
            line_lemmas: set[str] = set()
            included_tokens: list = []
            included_spans: list[tuple[int, int]] = []
            lemma_first_span: dict[str, tuple[str, int, int]] = {}
            # Same offset rule as parse_subtitle_file: locate each token's
            # span via str.find from a running cursor, because MeCab strips
            # whitespace from the token stream. Issue #20.
            cursor = 0
            for word_token in merged_tokens:
                surface = word_token.surface
                idx = text.find(surface, cursor)
                if idx == -1:
                    continue
                tok_start = idx
                tok_end = idx + len(surface)
                cursor = tok_end
                if not self._should_include_word(word_token):
                    continue
                lemma_here = self._extract_lemma(word_token)
                line_lemmas.add(lemma_here)
                included_tokens.append(word_token)
                included_spans.append((tok_start, tok_end))
                lemma_first_span.setdefault(lemma_here, (word_token.surface, tok_start, tok_end))

            # A line with zero content words can never be i+1 — skip it from
            # the index entirely. (Word emission is also skipped trivially.)
            if not line_lemmas:
                continue

            # Compute sentence-level furigana/reading ONCE for this line using
            # the already-parsed raw_tokens (tokenized at the top of the loop).
            sentence_furigana = generate_furigana_from_tokens(raw_tokens)
            sentence_reading = generate_reading_from_tokens(raw_tokens)

            line_index.append(
                LineLemmas(
                    line_text=text,
                    lemmas=frozenset(line_lemmas),
                    start_time=start_time,
                    end_time=end_time,
                    duration=duration,
                    sentence_furigana=sentence_furigana,
                    sentence_reading=sentence_reading,
                    lemma_spans=tuple(
                        (lemma_key, surface, span_start, span_end)
                        for lemma_key, (surface, span_start, span_end) in lemma_first_span.items()
                    ),
                )
            )

            # Second pass: emit deduped TokenizedWord entries (lemma-keyed).
            for word_token, (tok_start, tok_end) in zip(included_tokens, included_spans, strict=True):
                lemma = self._extract_lemma(word_token)
                surface = word_token.surface

                if lemma in seen_lemmas:
                    continue
                seen_lemmas.add(lemma)

                reading = self._extract_reading(word_token)

                # ExpressionFurigana/Reading match the mined card front
                # (lemma for verbs/adjectives, surface for nouns).
                pos = word_token.feature.pos1
                mined = lemma if pos in ("動詞", "形容詞") else surface
                expression_furigana = self._furigana(mined)
                expression_reading = self._reading(mined)

                if self.config.bold_target_in_sentence:
                    sentence_bolded = wrap_target_plain(text, tok_start, tok_end)
                    sentence_furigana_bolded = wrap_target_furigana_from_tokens(text, raw_tokens, tok_start, tok_end)
                else:
                    sentence_bolded = ""
                    sentence_furigana_bolded = ""

                all_words.append(
                    TokenizedWord(
                        surface=surface,
                        lemma=lemma,
                        reading=reading,
                        sentence=text,
                        start_time=start_time,
                        end_time=end_time,
                        duration=duration,
                        expression_furigana=expression_furigana,
                        expression_reading=expression_reading,
                        sentence_furigana=sentence_furigana,
                        sentence_reading=sentence_reading,
                        pos=word_token.feature.pos1,
                        surface_start=tok_start,
                        surface_end=tok_end,
                        sentence_bolded=sentence_bolded,
                        sentence_furigana_bolded=sentence_furigana_bolded,
                    )
                )

        return all_words, line_index

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
            # Resolve each token's span via str.find from a running cursor —
            # IDENTICAL to the mining loops in parse_subtitle_file* (Issue #20).
            # A merged compound whose components were whitespace-separated in
            # the source concatenates to a space-free surface that is NOT
            # find-able in ``text``; mining drops it (find == -1), so counting
            # must drop it too or the count-vs-mine sets diverge and the Deck
            # Builder preview over-promises (T-38). Keep these paths symmetric.
            cursor = 0
            for token in merged_tokens:
                surface = token.surface
                idx = text.find(surface, cursor)
                if idx == -1:
                    continue
                cursor = idx + len(surface)
                if self._should_include_word(token):
                    counts[self._extract_lemma(token)] += 1
        return counts

    def _merge_compound_suffixes(self, tokens: list) -> list:
        """Run all compound-merge passes in dependency order.

        Order matters:
        1. _merge_prefix_compounds  — 接頭辞 + 名詞/形状詞 (e.g. 不+可能 → 不可能).
           Must run first so that downstream 名詞-suffix merge sees the
           synthetic 不可能 (pos1=名詞) as a valid head and chains correctly
           into 不可能性, 不可能的, etc.
        2. _merge_noun_suffixes     — 名詞 + 接尾辞(名詞的/形状詞的/副詞的)
           chains (e.g. 刑務+所 → 刑務所, 入院+中+的 → 入院中的).
        3. _merge_verb_nominalizers — 動詞(連用形) + 接尾辞(名詞的) where the
           suffix is a verb-stem nominalizer (方/手/様). Independent of (1)
           and (2) so order is irrelevant.
        """
        tokens = self._merge_prefix_compounds(tokens)
        tokens = self._merge_noun_suffixes(tokens)
        tokens = self._merge_verb_nominalizers(tokens)
        return tokens

    def _merge_noun_suffixes(self, tokens: list) -> list:
        """Merge 名詞 + 接尾辞(名詞的/形状詞的/副詞的) chains into a single token.

        Walks tokens left-to-right. When a 名詞 head is followed by one or
        more nominal-suffix tokens, both base and suffixes are consumed and
        replaced by a single _SyntheticToken whose surface is the concatenated
        form and whose lemma is reconstructed from each component's
        feature.lemma (falling back to surface when unidic emits "*"/None).
        Nouns rarely conjugate, so lemma usually equals surface, but morphemes
        like ~性 / ~中 / ~的 carry their own dictionary form and we preserve it.
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "名詞":
                j = i + 1
                chain: list = []
                while j < n:
                    try:
                        p1 = tokens[j].feature.pos1
                        p2 = tokens[j].feature.pos2
                    except AttributeError:
                        break
                    if p1 == "接尾辞" and p2 in _NOMINAL_SUFFIX_POS2:
                        chain.append(tokens[j])
                        j += 1
                    else:
                        break
                if chain:
                    surf = head.surface + "".join(t.surface for t in chain)
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    suffix_kanas = []
                    for t in chain:
                        try:
                            suffix_kanas.append(t.feature.kana or t.surface)
                        except AttributeError:
                            suffix_kanas.append(t.surface)
                    kana = head_kana + "".join(suffix_kanas)
                    try:
                        head_pos2 = head.feature.pos2 or "普通名詞"
                    except AttributeError:
                        head_pos2 = "普通名詞"
                    try:
                        head_lemma = self._extract_lemma(head)
                    except AttributeError:
                        head_lemma = head.surface
                    suffix_lemmas: list[str] = []
                    for t in chain:
                        try:
                            suffix_lemmas.append(self._extract_lemma(t))
                        except AttributeError:
                            suffix_lemmas.append(t.surface)
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2=head_pos2,
                            lemma=head_lemma + "".join(suffix_lemmas),
                            kana=kana,
                        )
                    )
                    i = j
                    continue
            merged.append(head)
            i += 1
        return merged

    def _merge_prefix_compounds(self, tokens: list) -> list:
        """Merge 接頭辞 + 名詞/形状詞 pairs into a single token.

        Only fires when the 接頭辞 surface is in _PREFIX_WHITELIST — this
        avoids over-merging on rare/unproductive prefixes (e.g. お+金).
        Empirically: 不+可能 → root is 形状詞, 無+関心 → root is 名詞, so
        both pos1 values are accepted as merge heads. The synthetic is
        emitted as pos1=名詞 (the compound is treated as a vocabulary unit,
        and 名詞 is what _merge_noun_suffixes expects as a head — this
        enables chaining like 不+可能+性 → 不可能 → 不可能性). pos2 inherits
        from the root, defaulting to 普通名詞 when unidic emits "*".
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "接頭辞" and head.surface in _PREFIX_WHITELIST and i + 1 < n:
                root = tokens[i + 1]
                try:
                    root_pos1 = root.feature.pos1
                    raw_root_pos2 = root.feature.pos2
                except AttributeError:
                    merged.append(head)
                    i += 1
                    continue
                if root_pos1 in {"名詞", "形状詞"}:
                    # Treat unidic's "*" placeholder as missing pos2.
                    root_pos2 = raw_root_pos2 if raw_root_pos2 and raw_root_pos2 != "*" else "普通名詞"
                    surf = head.surface + root.surface
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    try:
                        root_kana = root.feature.kana or root.surface
                    except AttributeError:
                        root_kana = root.surface
                    try:
                        head_lemma = self._extract_lemma(head)
                    except AttributeError:
                        head_lemma = head.surface
                    try:
                        root_lemma = self._extract_lemma(root)
                    except AttributeError:
                        root_lemma = root.surface
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2=root_pos2,
                            lemma=head_lemma + root_lemma,
                            kana=head_kana + root_kana,
                        )
                    )
                    i += 2
                    continue
            merged.append(head)
            i += 1
        return merged

    def _merge_verb_nominalizers(self, tokens: list) -> list:
        """Merge 動詞(連用形) + 接尾辞(名詞的) verb-stem nominalizers.

        Only fires when the suffix surface is in _VERB_NOMINALIZER_SUFFIXES
        ({方, 手, 様}). Crucially uses the verb's CONJUGATED surface
        (連用形, e.g. 言い/読み/生き) — NOT its lemma — so the merged form
        is 言い方 not 言う方. The synthetic is emitted as pos1=名詞,
        pos2=普通名詞 (the compound is nominalized).

        ``lemma`` is set to the merged surface (NOT head.lemma + suffix.lemma)
        because the dictionary entry IS 言い方 / 読み方 — using 言う + 方 would
        yield 言う方, which is not a headword and would miss dictionary lookups.
        """
        merged: list = []
        i, n = 0, len(tokens)
        while i < n:
            head = tokens[i]
            try:
                head_pos1 = head.feature.pos1
            except AttributeError:
                merged.append(head)
                i += 1
                continue
            if head_pos1 == "動詞" and i + 1 < n:
                suffix = tokens[i + 1]
                try:
                    suf_pos1 = suffix.feature.pos1
                    suf_pos2 = suffix.feature.pos2
                except AttributeError:
                    merged.append(head)
                    i += 1
                    continue
                if suf_pos1 == "接尾辞" and suf_pos2 == "名詞的" and suffix.surface in _VERB_NOMINALIZER_SUFFIXES:
                    surf = head.surface + suffix.surface
                    try:
                        head_kana = head.feature.kana or head.surface
                    except AttributeError:
                        head_kana = head.surface
                    try:
                        suf_kana = suffix.feature.kana or suffix.surface
                    except AttributeError:
                        suf_kana = suffix.surface
                    merged.append(
                        _SyntheticToken(
                            surface=surf,
                            pos1="名詞",
                            pos2="普通名詞",
                            lemma=surf,
                            kana=head_kana + suf_kana,
                        )
                    )
                    i += 2
                    continue
            merged.append(head)
            i += 1
        return merged

    def _extract_lemma(self, word_token) -> str:
        """Extract lemma (dictionary form) from word token.

        Args:
            word_token: MeCab word token

        Returns:
            Lemma string
        """
        try:
            lemma = word_token.feature.lemma or word_token.surface
        except AttributeError:
            lemma = word_token.surface

        # Clean lemma - strip unidic's English-gloss tail
        # (e.g. "スクランブル-scramble" -> "スクランブル") but leave Japanese
        # names like "メル-ビル" intact: only split when the tail is ASCII.
        if "-" in lemma:
            head, _, tail = lemma.partition("-")
            if tail.isascii():
                lemma = head

        return str(lemma)

    def _extract_reading(self, word_token) -> str:
        """Extract kana reading from word token.

        Args:
            word_token: MeCab word token

        Returns:
            Kana reading string
        """
        try:
            return str(word_token.feature.kana or word_token.surface)
        except AttributeError:
            return str(word_token.surface)

    def _should_include_word(self, word_token) -> bool:
        """Determine if a word should be included based on POS and other criteria.

        Args:
            word_token: MeCab word token

        Returns:
            True if word should be included, False otherwise
        """
        surface = word_token.surface

        # Skip empty or whitespace-only tokens
        if not surface or not surface.strip():
            return False

        # Get part-of-speech tags
        try:
            pos1 = word_token.feature.pos1  # Main POS
            pos2 = word_token.feature.pos2  # Sub POS
        except AttributeError:
            return False

        # Skip particles, auxiliary verbs, symbols, punctuation
        if pos1 in ["助詞", "助動詞", "記号", "補助記号"]:
            return False

        # Skip interjections and fillers
        if pos1 in ["感動詞", "フィラー"]:
            return False

        # Check if it's a content word (noun, verb, adjective, adverb)
        if pos1 not in self.config.allowed_pos:
            return False

        # Check for excluded subtypes
        if pos2 and pos2 in self.config.excluded_subtypes:
            return False

        # Skip if no lemma available
        try:
            lemma = word_token.feature.lemma
            if not lemma:
                return False
        except AttributeError:
            return False

        # Check if word contains meaningful characters
        has_kanji = any("\u4e00" <= c <= "\u9fff" for c in surface)
        is_katakana = all("\u30a0" <= c <= "\u30ff" or c in "ー・" for c in surface if c.strip())

        # For katakana-only words, apply stricter filtering
        if is_katakana and not has_kanji:
            # Skip onomatopoeia patterns
            stripped = surface.replace("ッ", "").replace("ー", "").replace("・", "")
            unique_chars = set(stripped)

            # If only 1-2 unique characters, likely onomatopoeia
            if len(unique_chars) <= 2 and len(surface) <= 4:
                return False

            # If ends in small tsu and is short, likely sound effect
            if surface.endswith("ッ") and len(surface) <= 3:
                return False

            # Must be at least 2 chars to be valid katakana word
            return len(surface) >= 2

        # For words with kanji, always include (POS/subtype gates above apply).
        # Pure hiragana words (no kanji, not katakana) fall through and are rejected.
        return bool(has_kanji)
