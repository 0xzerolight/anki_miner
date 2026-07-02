"""Dictionary-attested compound matching (Yomitan longest-match principle).

Fragment fix (走り出した mined as 走り, 応急処置 split into 応急+処置): MeCab/
unidic short-unit tokens are mined per token, so multi-token dictionary words
never surface whole. Yomitan never fragments because the DICTIONARY defines
word boundaries — it generates candidates longest-first, deinflects, and ranks
matches by source length. This module adapts that to whole-line mining: scan
positions come from MeCab tokens, and deinflection is delegated to MeCab (the
span's tail token contributes its ``orthBase`` dictionary form).

Runs AFTER ``morphology.merge_compound_suffixes``. For spans of adjacent
tokens starting at a mineable token, the longest span whose candidate string
is an exact offline-dictionary headword is merged into one synthetic token;
consumed tokens are skipped (greedy left-to-right, like Yomitan's scan).

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
)

# Batch existence probe: returns the subset of the input strings that exist as
# exact dictionary headwords (DefinitionService.offline_terms_exist).
TermLookup = Callable[[list[str]], set[str]]

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
# the real safety bound (Yomitan's point-scan default is 10 chars) and should
# not be a user footgun; the token cap bounds candidate generation.
_MAX_SPAN_CHARS = 12
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
    same gate the parser mines with, reused for span starts so spans never
    begin at particles/aux/kana-only tokens.
    """

    def __init__(
        self,
        term_lookup: TermLookup,
        inclusion_rule: TokenInclusionRule,
        max_span_tokens: int = _MAX_SPAN_TOKENS,  # parameterized for tests only
    ) -> None:
        self._lookup = term_lookup
        self._rule = inclusion_rule
        self._max_span = max(2, max_span_tokens)
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
            if self._rule.should_include(token):
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

    def _generate_candidates(self, text: str, tokens: list) -> dict[tuple[int, int], tuple[str, str]]:
        """Map ``(start, end)`` span -> ``(candidate_string, kind)``.

        kind "A" = deinflected tail (joined surfaces + tail orthBase);
        kind "B" = plain surface join (non-inflectable tail only — for an
        inflected tail the surface join is an inflected string, and matching
        it would ship inflected-headword card fronts like 気をつけて).
        """
        n = len(tokens)
        out: dict[tuple[int, int], tuple[str, str]] = {}
        for i in range(n - 1):
            if not self._rule.should_include(tokens[i]):
                continue
            prefix = tokens[i].surface
            for j in range(i + 1, min(i + self._max_span, n)):
                tail = tokens[j]
                joined = prefix + tail.surface
                if len(joined) > _MAX_SPAN_CHARS:
                    break
                tail_pos1 = _pos1(tail)
                # Span-end rule: non-content ends are not candidate endpoints,
                # but the span may still extend past them (気に|する|な: the
                # (0..2) span ending on する is reachable through the な).
                if tail_pos1 is not None and tail_pos1 not in _NON_CONTENT_POS1:
                    if tail_pos1 in _INFLECTABLE_POS1:
                        candidate = prefix + extract_orth_base(tail)
                        kind = "A"
                    else:
                        candidate = joined
                        kind = "B"
                    # Issue #20 guard: a merged surface that is not findable in
                    # the raw text (whitespace between components) would be
                    # silently dropped by the span locator, losing the word.
                    if joined in text:
                        out[(i, j)] = (candidate, kind)
                prefix = joined
        return out

    def _resolve(self, candidates: dict[tuple[int, int], tuple[str, str]]) -> None:
        """One batched lookup for all uncached candidate strings."""
        unknown = {c for c, _kind in candidates.values() if c not in self._exist_cache}
        if not unknown:
            return
        hits = self._lookup(sorted(unknown))
        if len(self._exist_cache) + len(unknown) > _EXIST_CACHE_CAP:
            self._exist_cache.clear()
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
        非自立可能 trap) and silently drop compounds. Kind B (all-content
        span, tail uninflected) keeps the head's nominal POS; surface equals
        the headword there, so mined_form is right either way. No new POS
        value is invented — a novel pos1 would silently break the
        ``pos in ("動詞", "形容詞")`` checks in word/pitch code.
        """
        surface = "".join(t.surface for t in span)
        kana = "".join(extract_reading(t) for t in span)
        if kind == "A":
            pos1 = _pos1(span[-1]) or "動詞"
            pos2 = "一般"
        else:
            head_pos1 = _pos1(span[0])
            head_pos2 = _pos2(span[0])
            pos1 = head_pos1 if head_pos1 else "名詞"
            pos2 = head_pos2 if head_pos2 and head_pos2 != "*" else "普通名詞"
        return CompoundSyntheticToken(
            surface=surface,
            pos1=pos1,
            pos2=pos2,
            lemma=headword,
            kana=kana,
        )
