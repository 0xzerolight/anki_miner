"""Dictionary-attested compound matching (Yomitan longest-match principle).

Fragment fix (走り出した mined as 走り, 応急処置 split into 応急+処置): MeCab/
unidic short-unit tokens are mined per token, so multi-token dictionary words
never surface whole. Yomitan never fragments because the DICTIONARY defines
word boundaries — it generates candidates longest-first, deinflects, and ranks
matches by source length. This module adapts that to whole-line mining: scan
positions come from MeCab tokens, and deinflection is delegated to MeCab (the
span's tail token contributes its ``orthBase`` dictionary form).

Runs AFTER ``morphology.merge_compound_suffixes``. For spans of adjacent
tokens starting at a structurally contentful token, the longest span whose
candidate string is an exact offline-dictionary headword is merged into one
synthetic token; consumed tokens are skipped (greedy left-to-right, like
Yomitan's scan).

Lives outside ``morphology.py`` because the matcher is a stateful object — it
holds a mutable existence cache and an injected lookup dependency — unlike
morphology's pure stateless token helpers. Import direction:
``compound_matcher`` imports from ``morphology``; ``subtitle_parser`` imports
from both.
"""

from __future__ import annotations

from typing import Callable

from anki_miner.services.morphology import (
    SyntheticToken,
    TokenInclusionRule,
    extract_orth_base,
    extract_reading,
    iter_token_spans,
)

# Batch existence probe: returns the subset of the input strings that exist as
# exact dictionary headwords (DefinitionService.offline_terms_exist).
TermLookup = Callable[[list[str]], set[str]]

# Same batch shape, kept as a separate type name so parser wiring cannot
# accidentally substitute dictionary-form attestation for raw name membership.
NameLookup = Callable[[list[str]], set[str]]

# Span tails that conjugate: their candidate uses orthBase (dictionary form in
# the token's own orthography — unidic's lemma is kanji-canonical, し→為る,
# which is NOT what dictionaries store for kana idioms like 気がする).
# 助動詞 is deliberately NOT here: no legitimate compound ends in a bare
# auxiliary, and JMdict attests some aux-inclusive strings (やった) that must
# never become card fronts.
_INFLECTABLE_POS1 = frozenset({"動詞", "形容詞"})

# A span must END on a content token. Without this rule, JMdict's thousands of
# inflected-form headwords (気にするな, 気をつけて, ああ言った …) would match a
# raw surface join and ship an inflected card front.
_NON_CONTENT_POS1 = frozenset({"助詞", "助動詞", "記号", "補助記号", "空白"})

# Over-merge guards. Deliberately module constants, not config: the char cap is
# a safety bound and should not be a user footgun; the token cap bounds
# candidate generation. 16 chars (was 12; Yomitan's point-scan default is 10)
# admits the 2-token katakana tech compounds JMdict attests at 13-16 chars
# (アプリケーションプログラム) that 12 rejected. The junk classes 13-16 chars
# would otherwise admit are carried by the OTHER guards, fixture-proven: 13+
# char attested phrases/proverbs run >=6 tokens (お誕生日おめでとうございます is
# 7) and die on the 5-token cap even when attested; inflected-phrase headwords
# die on the end-on-content rule; everything else dies on attestation.
# Exact name matching uses 24 chars, the longest current bundled name-wordset
# entry; curated-resource membership is itself the boundary attestation.
_MAX_SPAN_CHARS = 16
_MAX_NAME_SPAN_CHARS = 24
_MAX_SPAN_TOKENS = 5

# Existence-cache bound (positive AND negative results). Clear-on-cap keeps
# whole-corpus Deck Builder runs from growing without limit.
_EXIST_CACHE_CAP = 200_000


class CompoundSyntheticToken(SyntheticToken):
    """Matcher-produced merged token.

    Distinct subclass (not an instance attribute — the base declares
    ``__slots__``) so ``_emit_word`` can detect matcher output via
    ``getattr(token, "compound", False)`` and regenerate the emitted reading:
    the concatenated component kana is wrong for cross-particle compounds
    (気がする → キガシ) and ``TokenizedWord.reading`` reaches the curation
    dialog and TSV export.
    """

    __slots__ = ()
    compound = True


def _pos1(token) -> str | None:
    try:
        pos1 = token.feature.pos1
    except AttributeError:
        return None
    return str(pos1) if pos1 else None


def _pos2(token) -> str | None:
    try:
        pos2 = token.feature.pos2
    except AttributeError:
        return None
    return str(pos2) if pos2 else None


class CompoundDictionaryMatcher:
    """Greedy longest-match merger over one line's token stream.

    ``term_lookup`` is injected (no SQLite here); ``inclusion_rule`` is the
    same gate the parser mines with, applied to completed synthetic tokens.
    Candidate boundaries use structural content checks instead.
    """

    _default_max_span_chars = _MAX_SPAN_CHARS

    def __init__(
        self,
        term_lookup: TermLookup,
        inclusion_rule: TokenInclusionRule,
        max_span_tokens: int = _MAX_SPAN_TOKENS,  # parameterized for tests only
        max_span_chars: int | None = None,
    ) -> None:
        self._lookup = term_lookup
        self._rule = inclusion_rule
        self._max_span = max(2, max_span_tokens)
        char_bound = self._default_max_span_chars if max_span_chars is None else max_span_chars
        self._max_span_chars = max(2, char_bound)
        self._exist_cache: dict[str, bool] = {}

    def merge_line(self, text: str, tokens: list) -> list:
        """Return a new token list with dictionary-attested spans merged.

        Never mutates ``tokens`` or its elements (the parser's per-file line
        cache shares them). One batched ``term_lookup`` call per line covers
        every uncached candidate.
        """
        n = len(tokens)
        if n < 2:
            return tokens

        candidates = self._generate_candidates(text, tokens)
        if not candidates:
            return tokens
        self._resolve(candidates)

        merged: list = []
        i = 0
        while i < n:
            token = tokens[i]
            replacement = None
            consumed_end = i
            if self._can_start(token):
                # Longest span first — Yomitan ranks by source length.
                for j in range(min(i + self._max_span - 1, n - 1), i, -1):
                    entry = candidates.get((i, j))
                    if entry is None:
                        continue
                    candidate, kind = entry
                    if not self._exist_cache.get(candidate):
                        continue
                    synthetic = self._build_synthetic(tokens[i : j + 1], candidate, kind)
                    # Never consume tokens for a word the gate would then drop.
                    if self._rule.should_include(synthetic):
                        replacement = synthetic
                        consumed_end = j
                        break
            if replacement is not None:
                merged.append(replacement)
                i = consumed_end + 1
            else:
                merged.append(token)
                i += 1
        return merged

    def _can_start(self, token) -> bool:
        """Whether candidate spans may start at ``token``."""
        return self._can_end(token)

    @staticmethod
    def _can_end(token) -> bool:
        """Whether candidate spans may end at ``token``."""
        pos1 = _pos1(token)
        return pos1 is not None and pos1 not in _NON_CONTENT_POS1

    @staticmethod
    def _source_spans(text: str, tokens: list) -> dict[int, tuple[int, int]]:
        """Map token indexes to their cursor-aligned source spans."""
        spans: dict[int, tuple[int, int]] = {}
        next_index = 0
        for located, start, end in iter_token_spans(text, tokens):
            while next_index < len(tokens) and tokens[next_index] is not located:
                next_index += 1
            if next_index >= len(tokens):
                break
            spans[next_index] = (start, end)
            next_index += 1
        return spans

    def _generate_candidates(self, text: str, tokens: list) -> dict[tuple[int, int], tuple[str, str]]:
        """Map ``(start, end)`` span -> ``(candidate_string, kind)``.

        kind "A" = deinflected tail (joined surfaces + tail orthBase);
        kind "B" = plain surface join (non-inflectable tail only — for an
        inflected tail the surface join is an inflected string, and matching
        it would ship inflected-headword card fronts like 気をつけて).
        """
        n = len(tokens)
        out: dict[tuple[int, int], tuple[str, str]] = {}
        source_spans = self._source_spans(text, tokens)
        for i in range(n - 1):
            start_span = source_spans.get(i)
            if start_span is None or not self._can_start(tokens[i]):
                continue
            prefix = tokens[i].surface
            source_end = start_span[1]
            for j in range(i + 1, min(i + self._max_span, n)):
                tail = tokens[j]
                tail_span = source_spans.get(j)
                # MeCab omits whitespace tokens. Candidate components still
                # must be adjacent in this occurrence; a same-spelled join
                # elsewhere in the line cannot license a merge here.
                if tail_span is None or tail_span[0] != source_end:
                    break
                joined = prefix + tail.surface
                if len(joined) > self._max_span_chars:
                    break
                # Span-end rule: non-content ends are not candidate endpoints,
                # but the span may still extend past them (気に|する|な: the
                # (0..2) span ending on する is reachable through the な).
                if self._can_end(tail):
                    candidate, kind = self._candidate_for_tail(prefix, tail, joined)
                    out[(i, j)] = (candidate, kind)
                prefix = joined
                source_end = tail_span[1]
        return out

    @staticmethod
    def _candidate_for_tail(prefix: str, tail, joined: str) -> tuple[str, str]:
        """Return the lookup candidate and synthetic-token kind for one span."""
        if _pos1(tail) in _INFLECTABLE_POS1:
            return prefix + extract_orth_base(tail), "A"
        return joined, "B"

    def _resolve(self, candidates: dict[tuple[int, int], tuple[str, str]]) -> None:
        """One batched lookup for all uncached candidate strings."""
        current = {c for c, _kind in candidates.values()}
        verdicts = {c: self._exist_cache[c] for c in current if c in self._exist_cache}
        unknown = {c for c in current if c not in verdicts}
        if not unknown:
            return
        hits = self._lookup(sorted(unknown))
        if len(self._exist_cache) + len(unknown) > _EXIST_CACHE_CAP:
            self._exist_cache.clear()
            # merge_line reads this cache after resolution; retain verdicts
            # required by the current line across the clear.
            self._exist_cache.update(verdicts)
        for candidate in unknown:
            self._exist_cache[candidate] = candidate in hits

    def _build_synthetic(self, span: list, headword: str, kind: str) -> CompoundSyntheticToken:
        """Assemble the merged token.

        surface = the joined span surfaces exactly as they appear in the text
        (locatable → correct offsets/bolding); lemma = the attested headword
        verbatim, so every lemma-keyed consumer (definitions, known words,
        frequency, pitch) sees the dictionary form.

        POS drives ``TokenizedWord.mined_form`` (lemma for 動詞/形容詞, surface
        otherwise): kind A inherits the tail's conjugating pos1 so the card
        front is the headword; its pos2 is pinned to 一般 — the real tails
        carry pos2=非自立可能, and inheriting that would make the merge's
        survival depend on the user's ``excluded_subtypes`` (the 非自立 vs
        非自立可能 trap) and silently drop compounds. Kind B inherits the
        uninflected tail's POS, which identifies the attested headword's type
        even when the first token has a different POS (動く+歩道). Surface
        equals the headword there, so mined_form is right either way. No new
        POS value is invented — a novel pos1 would silently break the ``pos in
        ("動詞", "形容詞")`` checks in word/pitch code.
        """
        surface = "".join(t.surface for t in span)
        kana = "".join(extract_reading(t) for t in span)
        if kind == "A":
            pos1 = _pos1(span[-1]) or "動詞"
            pos2 = "一般"
        else:
            # Inherit the tail's POS only when the tail is itself a content
            # word (動く歩道 → 歩道 keeps 名詞). A suffix tail (入院中, 可能性)
            # would smuggle 接尾辞 onto the synthetic and the inclusion gate
            # would reject the whole attested compound — those chains are
            # lexicalized nouns, so they take the nominal default instead.
            tail_pos1 = _pos1(span[-1])
            tail_pos2 = _pos2(span[-1])
            if tail_pos1 in ("名詞", "形状詞", "代名詞"):
                pos1 = tail_pos1
                pos2 = tail_pos2 if tail_pos2 and tail_pos2 != "*" else "普通名詞"
            else:
                pos1 = "名詞"
                pos2 = "普通名詞"
        return CompoundSyntheticToken(
            surface=surface,
            pos1=pos1,
            pos2=pos2,
            lemma=headword,
            kana=kana,
        )


class NameSpanMatcher(CompoundDictionaryMatcher):
    """Merge exact name-wordset spans without trusting UniDic word forms.

    Reuses the dictionary matcher's parameterized candidate generation, batched
    lookup cache, greedy longest-first selection, and synthetic tokens. Name
    candidates differ at one load-bearing seam: every token contributes its
    raw surface. Deinflecting an adjective-misclassified tail would turn
    ``憂+太`` into ``憂太い`` and miss the actual name ``憂太``.

    A match is emitted as a nominal token so downstream mining uses the exact
    source spelling. Honorifics provide no special license; they merge only
    when the complete span itself exists in the injected name lookup.
    """

    _default_max_span_chars = _MAX_NAME_SPAN_CHARS

    @staticmethod
    def _candidate_for_tail(prefix: str, tail, joined: str) -> tuple[str, str]:
        return joined, "B"

    def _can_start(self, token) -> bool:
        # Exact raw-name attestation, not UniDic POS, licenses this boundary.
        # The combined-line probe can tag 狗 as 接尾辞 and 巻 as 固有名詞 even
        # though the same source phrase tags both 普通名詞; trusting either tag
        # here would recreate the fragment bug at a different POS boundary.
        surface = getattr(token, "surface", None)
        return bool(surface and surface.strip())

    @staticmethod
    def _can_end(token) -> bool:
        surface = getattr(token, "surface", None)
        return bool(surface and surface.strip())

    def _build_synthetic(self, span: list, headword: str, kind: str) -> CompoundSyntheticToken:
        surface = "".join(token.surface for token in span)
        kana = "".join(extract_reading(token) for token in span)
        return CompoundSyntheticToken(
            surface=surface,
            pos1="名詞",
            pos2="普通名詞",
            lemma=headword,
            kana=kana,
        )
