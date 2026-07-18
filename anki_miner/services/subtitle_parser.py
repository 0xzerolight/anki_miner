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
from anki_miner.services.deinflection import (
    TermCommonLookup,
    _is_pure_hiragana,
    find_highlight_end,
    resolve_dictionary_form,
)
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

# Kana-recovery QUALITY probe (DefinitionService.offline_kana_attest_quality): per
# queried card front, the raw ``rules``-column strings of its TERM-EXACT rows
# (``term_rules``) and of its COMMON rows over commonness-aware providers
# (``common_rules``). ``None`` when NO offline provider is commonness-aware — the
# gate then plays no part and recovery falls back to the existence probe above
# (byte-identical to pre-U12). Rules strings are RAW Yomitan values that may hold
# several space-separated tokens (``'v5 vt'``) and include ``''`` (a noun / na-adj
# term row); POS compatibility is decided per split token, never on the whole
# string (``_rules_pos_compatible``).
KanaQualityLookup = Callable[[list[str]], dict[str, dict[str, frozenset[str]]] | None]

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

# U12 kana-recovery quality gate. When a commonness-aware offline dict is wired the
# recovery admits a pure-hiragana front only via a row whose deinflection ``rules``
# MATCH the token's POS — killing the 以降-shape false-attest where a 動詞 token's
# kana front collides with an unrelated noun headword (rules=''). 形状詞 is
# unconstrained (na-adjective rows store '', so no rule token can gate them): the
# accepted looseness is that a pure-kana na-adj colliding with ANY common reading
# is admitted (U13 replay is the tripwire).
_KANA_QUALITY_ADJ_RULES: frozenset[str] = frozenset({"adj-i", "adj-ix"})

# U4 lexicalized-expression reject. A kana-recovery candidate that IS an attested
# headword on its own (すむ, しれる) is still junk when it is really a fragment of
# a longer grammaticalized sequence (すみません, かもしれない). The signal: joining
# the candidate's surface with the contiguous FUNCTIONAL particles/auxiliaries
# around it reproduces a form the dictionary attests (as a term OR a reading —
# かもしれない is attested only as the reading of かも知れない). 接頭辞 joins too:
# おかえりなさい = お(接頭辞)+かえり(→かえる)+なさい, and the window おかえり attests
# via お帰り's reading, so the bare かえる recovery is suppressed. Restricting the
# join to 助詞/助動詞/接頭辞 is the false-positive guard: a content neighbor
# (ものすごい's 名詞 もの) never joins, so real vocabulary (すごい) abutting a
# lexicalized homograph is never suppressed.
_KANA_RECOVER_WINDOW_FUNCTIONAL_POS1: frozenset[str] = frozenset({"助詞", "助動詞", "接頭辞"})
# Max contiguous functional neighbors joined on EACH side of the candidate. Bounds
# the window enumeration (and the attestation probe) to O(side^2) joins per rare
# recovery candidate; grammaticalized sequences are short (にとって, かもしれない).
_KANA_RECOVER_WINDOW_MAX_SIDE: int = 3

# U8 ellipsis truncation-fragment reject. Fansub/CC lines cut a word off
# mid-utterance at an ellipsis (欲し…, 合…, タ… イガ…) and the tokenizer strands
# the severed head as a full content word. Applied on BOTH _mine_token branches
# and DICT-FREE (unlike U4/U5), so the video path benefits too. A token qualifies
# only when it DIRECTLY abuts an ellipsis char, tested by SET membership
# (``ch in _ELLIPSIS_CHARS``) — never the substring form ``ch in "…‥"``: the
# line-edge sentinel "" is a substring of every string, so the substring form
# would falsely mark every line-initial token adjacent and reject it.
_ELLIPSIS_CHARS: frozenset[str] = frozenset({"…", "‥"})
# (a) Cut conjugation: a 動詞/形容詞 stranded in a stem/連用/未然/仮定 form is a
# severed inflection (欲し…→欲する). Match the cForm PREFIX — unidic emits
# hyphenated values (連用形-一般, 連用形-促音便), so bare equality would never fire.
_ELLIPSIS_CUT_POS1: frozenset[str] = frozenset({"動詞", "形容詞"})
_ELLIPSIS_CUT_CFORM: frozenset[str] = frozenset({"連用形", "未然形", "語幹", "仮定形"})
# (b) Short fragment (all-katakana or single-char surface) inside a STUTTER line
# of ≥2 ellipsis GROUPS, where a group is a maximal ellipsis run: ``……`` (the
# standard fansub double-marker) collapses to ONE group, so a lone trailing 夢……
# survives while タ… イガ… stays two groups.
_ELLIPSIS_GROUP_RE = re.compile(r"[…‥]+")
_ELLIPSIS_STUTTER_MIN_GROUPS: int = 2


def _is_katakana_surface_char(ch: str) -> bool:
    """True for any char in the katakana Unicode block U+30A0–U+30FF.

    Whether a char can belong to a katakana *surface* — used by
    ``_is_all_katakana``. The block spans the phonetic kana plus the
    prolonged-sound mark ー (U+30FC) and small tsu ッ (U+30C3), but also the
    non-phonetic separators ゠ (U+30A0) and ・ (U+30FB). Belonging to a surface
    is a broader test than *continuing a run* (``_continues_katakana_run``),
    which excludes those two separators.
    """
    return "゠" <= ch <= "ヿ"


# Non-phonetic katakana-block chars that are author-inserted SEPARATORS, not
# unmerged-run glue: ・ (U+30FB middle dot) and ゠ (U+30A0 double hyphen). A run
# broken by one of these (アイス・ベア, メリット・デメリット) is two intended words,
# not a tokenizer-fragmented compound — so they must not extend a run for the
# fragment guard even though they sit inside the katakana surface block.
_KATAKANA_RUN_SEPARATORS: frozenset[str] = frozenset({"・", "゠"})


def _continues_katakana_run(ch: str) -> bool:
    """True when ``ch`` extends a katakana run for the fragment-guard adjacency test.

    A katakana-block char (``_is_katakana_surface_char``) EXCEPT the author-inserted
    separators ・/゠ (``_KATAKANA_RUN_SEPARATORS``): those mark a deliberate word
    boundary, so a token abutting one is NOT a fragment of a longer run.
    """
    return _is_katakana_surface_char(ch) and ch not in _KATAKANA_RUN_SEPARATORS


def _is_all_katakana(surface: str) -> bool:
    """True when every non-whitespace char of ``surface`` is katakana (>=1 char).

    Mirrors the katakana-loanword branch of ``TokenInclusionRule.should_include``
    (all-katakana ⇒ no kanji): the fragment guard only ever reasons about tokens
    that branch already accepted, and deliberately ignores mixed loanword verbs
    (サボる, ヤバい) whose hiragana okurigana makes them not all-katakana. Uses the
    broad surface-char test (``_is_katakana_surface_char``), NOT the run-continuation
    test — a ・/゠ inside a surface still counts toward all-katakana.
    """
    non_ws = [c for c in surface if not c.isspace()]
    return bool(non_ws) and all(_is_katakana_surface_char(c) for c in non_ws)


def _differs_by_okurigana_only(orth_base: str, lemma: str) -> bool:
    """Whether ``orth_base`` is ``lemma`` with only its trailing okurigana changed.

    True iff the two share a common leading prefix and BOTH differing tails are
    pure hiragana — so every kanji sits in the shared stem (呼ばる/呼ぶ → stem 呼,
    tails ばる/ぶ; 抜る/抜く → stem 抜). A kanji difference pushes a kanji into a
    tail and fails (帰れる/返る → stems 帰≠返; 治せる/直す → 治≠直; 殺る/遣る → 殺≠遣).

    This is the load-bearing safety gate for the U3 attest-or-remap guard: unidic's
    canonical ``lemma`` silently collapses kanji-variant homographs (殺る→遣る,
    賭ける→掛ける, 帰れる→返る) onto a DIFFERENT-meaning or different-orthography
    headword. Remapping a card front onto such a lemma would ship the wrong
    homograph's spelling/definition — the exact bug Issues #19/#5 fix at the
    lookup layer by keying on ``mined_form``. Requiring an okurigana-only
    derivation confines the remap to genuine same-kanji suffix collapses (the
    classical passive 呼ばる, not covered by ``morphology._FOLD_SUFFIX_PAIRS``),
    where the base spelling is unambiguous.
    """
    i = 0
    limit = min(len(orth_base), len(lemma))
    while i < limit and orth_base[i] == lemma[i]:
        i += 1
    return _is_pure_hiragana(orth_base[i:]) and _is_pure_hiragana(lemma[i:])


def _rules_pos_compatible(pos1: str, rules_set: frozenset[str]) -> bool:
    """Whether any raw ``rules`` string in ``rules_set`` matches ``pos1`` (U12 gate).

    - 動詞 → a rule token starting with ``v`` (``v5``/``v1``/``v5u``/``v5k-s``...).
    - 形容詞 → an ``adj-i``/``adj-ix`` token (``_KANA_QUALITY_ADJ_RULES``).
    - 形状詞 → any row at all: na-adjective rows store ``''`` so no rule token can
      gate them, and set membership is the only signal (the accepted looseness).

    Each rules string may carry several space-separated tokens (``'v5 vt'``), so
    the check splits before matching — ``''`` splits to no tokens, so a bare noun /
    na-adj row never satisfies the 動詞/形容詞 arms.
    """
    if pos1 == "形状詞":
        return bool(rules_set)
    if pos1 == "動詞":
        return any(tok.startswith("v") for rules in rules_set for tok in rules.split())
    if pos1 == "形容詞":
        return any(tok in _KANA_QUALITY_ADJ_RULES for rules in rules_set for tok in rules.split())
    return False


class SubtitleParserService:
    """Parse subtitles and extract Japanese vocabulary words (stateless service)."""

    def __init__(
        self,
        config: AnkiMinerConfig,
        term_lookup: TermLookup | None = None,
        reading_lookup: ReadingLookup | None = None,
        kana_attest_lookup: KanaAttestLookup | None = None,
        term_common_lookup: TermCommonLookup | None = None,
        kana_quality_lookup: KanaQualityLookup | None = None,
    ):
        """Initialize the subtitle parser.

        Args:
            config: Configuration for parsing
            kana_quality_lookup: Optional per-front rule-set probe
                (``DefinitionService.offline_kana_attest_quality``). When it
                returns a map (a commonness-aware offline dict is wired), the
                kana-recovery gate TIGHTENS from mere existence to a POS-compatible
                term-exact OR common row — rejecting rare-reading collisions the
                existence probe admitted (いてる via non-common 凍てる, いえる via
                言える) while keeping わかる/きれい/すごい and the term-exact kana
                headwords あざとい/しがない. ``None`` (not wired) or a ``None`` return
                (no commonness-aware dict) keeps the existence gate byte-identical.
            term_common_lookup: Optional batch commonness probe
                (``DefinitionService.offline_term_commonness``). When provided,
                the verb-front resolver narrows its deinflection override pool to
                headwords a commonness-aware offline dict tags common, so an
                archaic/rare longer-prefix candidate (呼ばる from 呼ばれる) can't
                displace the unidic orthBase. ``None`` (or a chain with no aware
                dict) keeps the resolver byte-identical to pre-commonness.
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
        # Commonness probe for the verb-front resolver (see _memoized_term_common /
        # _resolve_front). None ⇒ the resolver keeps its full attested override
        # pool (pre-commonness behavior). _common_memo caches per-surface verdicts;
        # _common_aware caches the chain-level "is any offline dict commonness-
        # aware" answer (None = not yet probed, False = unaware → always degrade).
        self._term_common_lookup = term_common_lookup
        self._common_memo: dict[str, bool] = {}
        self._common_aware: bool | None = None
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
        # Commonness/rules quality probe that TIGHTENS kana recovery when a
        # commonness-aware offline dict is wired (U12; see _probe_kana_recovery).
        # None (not wired) or a None return (unaware chain) ⇒ the existence probe
        # above stays the gate, byte-identical to pre-U12. Called once per distinct
        # (surface, pos1) — _probe_kana_recovery runs under _kana_recover_cache, so
        # this needs no memo of its own.
        self._kana_quality_lookup = kana_quality_lookup
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
        # U4 lexicalized-window attestation memo (see _rejected_by_lexicalized_window).
        # Keyed on the JOINED WINDOW STRING, never on (surface, pos1): the window
        # verdict is context-dependent, so the same recovery candidate can be
        # rejected in one line (すみません) and recovered in another (すみます). Same
        # clear-on-cap bounding as the caches above.
        self._kana_window_cache: dict[str, bool] = {}

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
            if not self._mine_token(word_token, text, tok_start, tok_end, merged_tokens):
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
                if not self._mine_token(word_token, text, tok_start, tok_end, merged_tokens):
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
        *,
        subtitle_cleanup: bool = False,
    ) -> tuple[list[TokenizedWord], list[LineLemmas] | None, collections.Counter[str]]:
        """Parse reading-tab text units into mining words, index, and lemma counts.

        The reading pipeline (manga volumes / novels / per-cue subtitles) hands
        mined text as ``ReadingUnit``s — one paragraph, manga text block, or
        subtitle cue each — instead of a subtitle file. Each unit's ``text`` is
        normalized for tokenization (the same ``normalize_for_tokenization`` +
        ``standardize_kanji_variants`` the subtitle path applies via
        ``clean_subtitle_text`` — mokuro OCR emits Kangxi radicals and halfwidth
        katakana that otherwise mis-tokenize), and that normalized form becomes
        the card sentence. There is no re-windowing, no pysubs2 and no per-file
        line cache on this path. ``unit.index`` (document order) doubles as the
        dummy start AND end time, so ``duration`` is ``0.0`` and every
        duration-based optional filter is inert by design.

        When ``subtitle_cleanup`` is set (the Reading→Subtitles per-cue path),
        each normalized unit additionally gets the subtitle-only annotation strip
        + user regex filter the video path applies via ``_clean_line_text``
        (:423–426), config-gated and order-identical; a cue that collapses to
        empty is skipped, so a whole-line SFX caption produces no word, no count,
        and no line-index entry. Manga/OCR and book units leave it ``False`` and
        are byte-identical to before.

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
            subtitle_cleanup: When True (Reading→Subtitles cue kind), apply the
                subtitle-only annotation strip + user regex after normalization,
                mirroring the video path's ``_clean_line_text``; a cue that
                collapses to empty is dropped. Default ``False`` (manga/book).

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
            # markup strip / regex filter it also runs are applied just below,
            # subtitle-cue kind only (subtitle_cleanup).
            text = standardize_kanji_variants(normalize_for_tokenization(unit.text))
            if subtitle_cleanup:
                # Reading→Subtitles per-cue kind: the video path's subtitle-only
                # cleanup (annotation strip + user regex) the loader can't run
                # (it is config-free). Order mirrors _clean_line_text (:423–426);
                # a cue that collapses to empty is skipped, so its (empty) text
                # never reaches _build_line_state and contributes no count or
                # line-index entry — content-free lines are already skipped below.
                if self.config.strip_subtitle_annotations:
                    text = strip_inline_annotations(text)
                text = self._apply_text_filter(text)
                if not text:
                    continue
            # Dummy timing: the index is both start and end (duration 0.0). No
            # re-windowing exists — the normalized unit text is the card sentence.
            line_state = self._build_line_state(text, float(unit.index), float(unit.index))
            text, _raw_tokens, merged_tokens, *_ = line_state

            # Count through the SAME locator as the mining loop below (and
            # count_lemmas): a token mining drops (find == -1) is counted
            # nowhere it is not mined, or the preview over-promises (T-38 — see
            # _iter_token_spans for the drop-rule rationale).
            for token, tok_start, tok_end in self._iter_token_spans(text, merged_tokens):
                if self._mine_token(token, text, tok_start, tok_end, merged_tokens):
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
            for token, tok_start, tok_end in self._iter_token_spans(text, merged_tokens):
                if self._mine_token(token, text, tok_start, tok_end, merged_tokens):
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

    def _memoized_term_common(self, surfaces: list[str]) -> dict[str, bool] | None:
        """Per-instance memoized commonness probe (see _resolve_front).

        Wraps ``self._term_common_lookup`` (``offline_term_commonness``), caching
        each surface's common/not-common verdict in ``self._common_memo`` so a
        repeated corpus probes each distinct surface once. The underlying probe
        returns ``None`` when NO offline provider is commonness-aware — a static
        chain property cached in ``self._common_aware`` so later calls
        short-circuit to ``None`` without re-probing (degrade byte-identical).

        Returns ``{surface: bool}`` over the queried surfaces, or ``None``. Reads
        the per-call answer from a local ``verdicts`` snapshot, NEVER by
        re-subscripting the shared cache after the clear-on-cap below (an
        eviction of a key populated earlier this call would KeyError — the same
        cap-clear class the kana-window cache guards against, commit 27a7671).
        """
        if self._term_common_lookup is None or self._common_aware is False:
            return None
        deduped = list(dict.fromkeys(surfaces))
        uncached = [s for s in deduped if s not in self._common_memo]
        verdicts = {s: self._common_memo[s] for s in deduped if s not in uncached}
        if uncached:
            result = self._term_common_lookup(uncached)
            if result is None:
                self._common_aware = False
                return None
            self._common_aware = True
            if len(self._common_memo) + len(uncached) > _FRONT_CACHE_CAP:
                self._common_memo.clear()
            for s in uncached:
                verdicts[s] = self._common_memo[s] = bool(result.get(s))
        return verdicts

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

        Second seam (U3 attest-or-remap): when the deinflection resolver leaves
        orth_base unchanged AND that orth_base matches no dictionary headword,
        ``_attest_or_remap_front`` remaps it to the attested lemma — but only
        when the lemma/orthBase readings diverge, guarding the #19/#5
        same-reading-variant contract. See that method for the full gate.
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
            cached = resolve_dictionary_form(
                inflected_surface, orth_base, self._term_lookup, self._memoized_term_common
            )
            # The deinflection resolver only rewrites じる/ずる (and leaves every
            # other form == orth_base). Where it made no change, run the
            # garbage-orthBase net so a same-kanji derived front the dictionary
            # does not attest (呼ばる → 呼ぶ) collapses onto its attested lemma. A
            # resolver override (感じる) is dictionary-attested by construction,
            # so skip it.
            if cached == orth_base:
                cached = self._attest_or_remap_front(word_token, orth_base)
            if len(self._front_cache) >= _FRONT_CACHE_CAP:
                self._front_cache.clear()
            self._front_cache[key] = cached
        return cached

    def _attest_or_remap_front(self, word_token, orth_base: str) -> str:
        """Remap a non-attested derived 動詞/形容詞 front to its attested lemma.

        Live-audit net for garbage/derived card fronts that match no dictionary
        headword — e.g. 呼ばる minted from the classical passive 呼ばれる (its ばる/ぶ
        suffix is outside ``morphology._FOLD_SUFFIX_PAIRS``). Such fronts miss the
        exact-term definition/frequency lookup and split dedup/known-word/audio
        identity from the base verb's card.

        Remaps ``orth_base`` → ``lemma`` iff ALL hold:

        * the lemma/orthBase readings DIVERGE (``lForm`` vs ``kanaBase``,
          hiragana-folded). LOAD-BEARING: okurigana spelling variants that read the
          same (変る/変わる, 表す/表わす — both readings equal) must NEVER remap, or
          the card front stops preserving the source orthography (Issue #19/#5).
          Mirrors ``mining_base``'s fold trigger, so an equal-reading token is left
          untouched.
        * ``orth_base`` differs from ``lemma`` by TRAILING OKURIGANA ONLY
          (``_differs_by_okurigana_only`` — same kanji stem). LOAD-BEARING: unidic's
          ``lemma`` canonicalizes kanji-variant homographs onto a different-kanji
          headword (帰れる→返る "can go home" vs "revert", 殺る→遣る, 混ぜる→交ぜる).
          Remapping onto such a lemma would ship the wrong homograph — so a kanji
          change blocks the remap and the source spelling is kept (its correct
          definition still arrives via the mined-form→lemma miss fallback).
        * the offline dictionary does NOT attest ``orth_base`` as a term (exact
          headword, no kana folding) — an attested front is a real word and is
          always KEPT; attestation, not a fold table, decides.
        * the dictionary DOES attest ``lemma`` — never remap onto an unattested
          target; keep the source spelling when there is nothing better.

        Reached only for a wired ``term_lookup`` and a token whose ``mining_base``
        did not fold (``orth_base`` is the token's own orthBase). Missing / ``*`` /
        non-string readings (synthetic compounds, OOV, MagicMock fakes) cannot
        prove divergence, so the front is conservatively kept. Attestation is
        memoized via the shared ``_memoized_attest`` probe.
        """
        lemma = extract_lemma(word_token)
        if not lemma or lemma == orth_base:
            return orth_base
        feature = getattr(word_token, "feature", None)
        l_form = getattr(feature, "lForm", None)
        kana_base = getattr(feature, "kanaBase", None)
        if not isinstance(l_form, str) or not isinstance(kana_base, str):
            return orth_base
        if l_form in ("", "*") or kana_base in ("", "*"):
            return orth_base
        if katakana_to_hiragana(l_form) == katakana_to_hiragana(kana_base):
            # Equal-reading okurigana variant: preserve the source orthography.
            return orth_base
        if not _differs_by_okurigana_only(orth_base, lemma):
            # Kanji differs ⇒ unidic lemma canonicalization onto a homograph
            # (帰れる→返る, 殺る→遣る): never let it swap the card front's kanji.
            return orth_base
        attested = self._memoized_attest([orth_base, lemma])
        if orth_base in attested:
            return orth_base  # a real headword — attestation decides, keep it
        if lemma not in attested:
            return orth_base  # no attested target to remap onto
        return lemma

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

    def _mine_token(self, word_token, text: str, tok_start: int, tok_end: int, tokens: list) -> bool:
        """Context-aware mining acceptance: inclusion, minus fragment reject layers.

        The SINGLE acceptance seam every token-span call site routes through —
        both mining passes (``parse_subtitle_file`` /
        ``_emit_line_words_and_index``), ``count_lemmas`` and
        ``parse_text_units``' count loop — so a token counted is a token mined and
        the T-38 count==mine parity can never break. All four pass ``tokens`` (the
        full per-line ``merged_tokens`` list) so the recovery path can inspect the
        candidate's functional neighbors.

        Two disjoint acceptance paths, each with its own reject layer, plus the
        dict-free U8 ellipsis truncation-fragment reject
        (``_is_ellipsis_truncation_fragment``) applied on BOTH:

        - ``should_include`` accepts (kanji / katakana loanword): apply ONLY the
          U5 katakana run-fragment guard (``_is_katakana_run_fragment``). The U4
          window reject never touches a morphology-accepted token.
        - ``should_include`` rejects → last-chance ``_recover_kana_content_word``
          (pure-hiragana content word attested as its own front). On a recovery
          acceptance, apply the U4 lexicalized-window reject
          (``_rejected_by_lexicalized_window``). Recovery surfaces are pure
          hiragana, so the katakana guard can never fire on this branch.

        ``_should_include_word`` stays the token-only, span-free gate that unit
        tests and non-span callers use directly; this method reproduces its
        ``should_include``-then-recover order so the two never diverge.
        """
        if self._inclusion_rule.should_include(word_token):
            if self._is_katakana_run_fragment(word_token, text, tok_start, tok_end):
                return False
            return not self._is_ellipsis_truncation_fragment(word_token, text, tok_start, tok_end)
        if not self._recover_kana_content_word(word_token):
            return False
        if self._rejected_by_lexicalized_window(word_token, tokens):
            return False
        return not self._is_ellipsis_truncation_fragment(word_token, text, tok_start, tok_end)

    def _rejected_by_lexicalized_window(self, word_token, tokens: list) -> bool:
        """Whether a recovered kana fragment sits inside an attested lexicalized expression.

        Runs ONLY on a kana-recovery acceptance (see ``_mine_token``). Locates the
        candidate in ``tokens`` by identity — it is an element of that list (yielded
        from ``iter_token_spans`` over it) — then joins its surface with the
        contiguous functional neighbors (``_lexicalized_window_surfaces``) into every
        window that strictly contains it. If ANY joined window is attested via the
        term-OR-reading probe, the recovery is a lexicalized fragment → reject.

        Attestation is memoized on the joined WINDOW STRING (``_kana_window_cache``),
        never on ``(surface, pos1)``: the verdict is context-dependent. The uncached
        windows for one candidate are batched into a SINGLE probe call. No probe
        wired ⇒ unreachable (recovery already returned False) but guarded for safety.
        """
        lookup = self._kana_attest_lookup
        if lookup is None:  # unreachable via _mine_token (recovery gates on the probe)
            return False
        idx = next((i for i, tok in enumerate(tokens) if tok is word_token), None)
        if idx is None:  # defensive: candidate not in the list ⇒ no context to judge
            return False
        windows = self._lexicalized_window_surfaces(tokens, idx)
        if not windows:
            return False
        uncached = [w for w in windows if w not in self._kana_window_cache]
        # Snapshot the already-cached verdicts BEFORE the clear-on-cap below can
        # evict a window this candidate still needs: the shared cache may be wiped
        # mid-call, so the per-call answer is read from this local dict — never
        # re-read from the (possibly emptied) cache, which would KeyError on an
        # evicted pre-cached window. Mirrors _memoized_attest's memoize-then-decide
        # shape, but keeps a local verdict so eviction can't drop an attested hit.
        verdicts = {w: self._kana_window_cache[w] for w in windows if w not in uncached}
        if uncached:
            if len(self._kana_window_cache) + len(uncached) > _FRONT_CACHE_CAP:
                self._kana_window_cache.clear()
            hits = lookup(uncached)
            for w in uncached:
                verdicts[w] = self._kana_window_cache[w] = bool(hits.get(w))
        return any(verdicts[w] for w in windows)

    def _lexicalized_window_surfaces(self, tokens: list, idx: int) -> list[str]:
        """Joined surfaces of every functional-neighbor window strictly containing ``tokens[idx]``.

        Walks up to ``_KANA_RECOVER_WINDOW_MAX_SIDE`` contiguous FUNCTIONAL neighbors
        (``pos1 ∈ _KANA_RECOVER_WINDOW_FUNCTIONAL_POS1``) on each side, stopping at
        the first non-functional token or the line edge, then enumerates every
        contiguous ``[left, right]`` span with ``left ≤ idx ≤ right`` and
        ``(left, right) != (idx, idx)`` — i.e. windows that keep the candidate but
        add at least one neighbor. Returns the joined token surfaces, order-preserving
        de-duplicated. Empty when the candidate has no functional neighbor (ものすごい:
        the content-noun もの is not functional, so no window forms and すごい survives).
        """
        left = idx
        while (
            left - 1 >= 0
            and idx - (left - 1) <= _KANA_RECOVER_WINDOW_MAX_SIDE
            and self._is_functional_token(tokens[left - 1])
        ):
            left -= 1
        right = idx
        last = len(tokens) - 1
        while (
            right + 1 <= last
            and (right + 1) - idx <= _KANA_RECOVER_WINDOW_MAX_SIDE
            and self._is_functional_token(tokens[right + 1])
        ):
            right += 1
        windows: list[str] = []
        for start in range(left, idx + 1):
            for end in range(idx, right + 1):
                if start == idx and end == idx:
                    continue
                windows.append("".join(tokens[i].surface for i in range(start, end + 1)))
        return list(dict.fromkeys(windows))

    @staticmethod
    def _is_functional_token(token) -> bool:
        """True when ``token`` is a functional particle/auxiliary/prefix (pos1 ∈ 助詞/助動詞/接頭辞)."""
        pos1 = getattr(getattr(token, "feature", None), "pos1", None)
        return pos1 in _KANA_RECOVER_WINDOW_FUNCTIONAL_POS1

    def _is_katakana_run_fragment(self, word_token, text: str, tok_start: int, tok_end: int) -> bool:
        """Whether an accepted all-katakana token is a fragment of a longer katakana run.

        Post-acceptance REJECT layer (runs AFTER ``_should_include_word`` accepts)
        closing the katakana tokenizer-fragment junk class (デット←アンデット,
        ベア←アイスベア glossed "increase in basic salary", live-audit 2026-07):
        when an unknown katakana name/compound is short-unit segmented, its
        dictionary-matching pieces (ベア, レッド, ヒヒ are real JMdict headwords)
        clear ``should_include``'s >=2-char katakana floor. Attestation cannot
        catch them — the only signal is positional: the token sits INSIDE a longer
        unmerged katakana run in the raw line.

        Active ONLY with an offline dictionary wired, gated on the compound matcher
        (the seam that is ``None`` without a dict). Rationale: without a dict the
        matcher (see ``compound_matcher.merge_line``) can never merge a legit full
        run (スマホケース-class) into one synthetic upstream, so this positional
        rule would then reject BOTH halves of every real unspaced compound. No
        dict ⇒ returns ``False`` ⇒ mining is byte-identical to pre-guard behavior.

        A ``CompoundSyntheticToken`` is never a fragment: its span IS the merged
        full run (the matcher ran in ``_build_line_state`` before this guard), so
        it is exempt even when an unmerged katakana neighbor abuts it
        (アンデッド|ゾンビ — the synthetic survives, the residual ゾンビ is dropped).

        Rejects when the surface is all-katakana AND the raw-text char immediately
        adjacent on either side CONTINUES the katakana run (``_continues_katakana_run``:
        a katakana-block char covering ー/ッ, but NOT the author-inserted separators
        ・/゠). Whitespace, ・, ゠ or any non-katakana between katakana does NOT
        continue a run — アイ ウォン stays two tokens, アイス・ベア keeps both halves,
        スマホ|と|バッグ keeps バッグ. Deliberate precision-over-recall (plan-decided):
        an attested word abutting an unbroken katakana run (アイス|ベア) is rejected,
        and legit adjacent loanword bigrams whose full run is no headword lose both
        halves — no independent-attestation carve-out.
        """
        if self._compound_matcher is None:
            return False
        if getattr(word_token, "compound", False) is True:
            return False
        surface = getattr(word_token, "surface", None)
        if not isinstance(surface, str) or not _is_all_katakana(surface):
            return False
        left = text[tok_start - 1] if tok_start > 0 else ""
        right = text[tok_end] if tok_end < len(text) else ""
        return _continues_katakana_run(left) or _continues_katakana_run(right)

    def _is_ellipsis_truncation_fragment(self, word_token, text: str, tok_start: int, tok_end: int) -> bool:
        """Whether an accepted token is a word cut off mid-utterance at an ellipsis.

        Post-acceptance REJECT layer shared by BOTH ``_mine_token`` branches and,
        unlike the U4/U5 rejects, DICT-FREE (positional + POS/cForm only) so it
        fires on the video path too. Rejects only a token DIRECTLY abutting an
        ellipsis char (``…``/``‥``) that also matches one truncation signal:

        (a) a 動詞/形容詞 stranded in a cut conjugation — its ``cForm`` PREFIX is
            one of 連用形/未然形/語幹/仮定形 (欲し…→欲する). unidic emits hyphenated
            cForms (連用形-一般), so the prefix split is load-bearing. A verb
            buffered from the ellipsis by a 助詞/接尾辞 (待って…, 続いて…) never
            abuts, so it survives; 意志推量形 (行こう…) is not a cut form, so it
            survives too.
        (b) a short fragment (all-katakana or single-char surface) in a STUTTER
            line of ≥2 ellipsis groups (合…/タ… イガ…). ``……`` is one group, so a
            single trailing 夢…… survives.

        Adjacency is SET membership; the line-edge sentinel "" (a token at a line
        boundary) is not a member, so a boundary token is never falsely adjacent.
        Deliberate recall loss (plan ledger): trailing 連用中止法 (飲み…→飲む) and
        single-char content nouns in ≥2-group lines (声, 年) — all common words
        mined elsewhere.
        """
        left = text[tok_start - 1] if tok_start > 0 else ""
        right = text[tok_end] if tok_end < len(text) else ""
        if left not in _ELLIPSIS_CHARS and right not in _ELLIPSIS_CHARS:
            return False
        feature = getattr(word_token, "feature", None)
        # (a) severed inflectional tail of a verb/adjective.
        if getattr(feature, "pos1", None) in _ELLIPSIS_CUT_POS1:
            c_form = getattr(feature, "cForm", None)
            if isinstance(c_form, str) and c_form.split("-", 1)[0] in _ELLIPSIS_CUT_CFORM:
                return True
        # (b) short fragment in a stutter line (≥2 ellipsis groups).
        surface = getattr(word_token, "surface", None)
        return (
            isinstance(surface, str)
            and (len(surface) == 1 or _is_all_katakana(surface))
            and len(_ELLIPSIS_GROUP_RE.findall(text)) >= _ELLIPSIS_STUTTER_MIN_GROUPS
        )

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
        """content_gate_ok + attestation of the mined-form card front.

        The form probed is the exact card front ``_emit_word`` would mint
        (``select_mined_form``): the surface for 形状詞 (きれい), the orthBase
        dictionary form for 動詞/形容詞 (わかった's わかっ token → わかる, since
        unidic's orthBase is already deinflected).

        Existence-gated (``has_offline_definitions``, term-OR-reading) by default.
        When a commonness-aware offline dict is wired — ``_kana_quality_lookup``
        returns a map, not ``None`` — the gate TIGHTENS (U12): the front is admitted
        only when the dict attests it with a POS-compatible TERM-EXACT row (path i —
        non-priority kana headwords あざとい/しがない) OR a POS-compatible COMMON row
        (path ii — 分かる's common わかる reading, 綺麗's きれい, 凄い's すごい). This
        rejects the rare-reading collisions the existence probe admitted (いてる via
        non-common 凍てる, いえる via 言える, the 以降-shape noun front for a 動詞
        token). A monolingual / unaware chain (lookup ``None``) keeps the existence
        gate byte-identical. Neither probe reads ``entries.score`` (uniformly 0 on
        the bundled dict).
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
        if self._kana_quality_lookup is None:
            return bool(lookup([form]).get(form))
        quality = self._kana_quality_lookup([form])
        if quality is None:  # no commonness-aware dict ⇒ byte-identical existence gate
            return bool(lookup([form]).get(form))
        entry = quality.get(form)
        if not entry:
            return False
        return _rules_pos_compatible(pos1, entry["term_rules"]) or _rules_pos_compatible(pos1, entry["common_rules"])
