"""Grammar-cut words joined back after tagging (思わず mined as 思う).

unidic cuts a few lexicalized words at their grammar: 思わず is 思わ + ず, 実は is
実 + は, くだらない is くだら + ない. The compound matcher cannot rejoin them,
because a span may not end on a particle or an auxiliary (the rule that keeps
inflected headwords such as 気をつけて off card fronts). So the card names the
wrong word (思う 'think' for 思わず 'involuntarily', 実 'fruit' for 実は
'actually'), or, for the kana adjectives, there is no card at all.

A short curated table, not a rule: the general pattern (noun + に) would also
turn その時に into 時に 'by the way'. Each row is a JMdict entry whose meaning
differs from its first token's, chosen by hand against the real tagger.

Runs after the compound matcher, so a longer attested span over the same tokens
keeps priority. Every join is attested against the run's offline dictionaries
(term or reading: くだらない is a reading of 下らない), so a dictionary without
the entry keeps the split words and their cards. Lives outside ``morphology.py``
for the same reason ``masu_stem_nominalizer.py`` does: it holds an injected
lookup and a cache.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from anki_miner.services.compound_matcher import CompoundDictionaryMatcher, CompoundSyntheticToken, _pos1
from anki_miner.services.morphology import extract_orth_base, extract_reading

# Batch term-OR-reading probe (DefinitionService.has_offline_definitions).
AttestLookup = Callable[[list[str]], dict[str, bool]]

# Adverbs and conjunctions the tagger cuts at a particle or at 未然形 + ず, as
# exact token surfaces; the card front is the joined surfaces. Left out on
# purpose because the split words are as common as the joined one: 時に (その時に
# 'at that time'), 先に and 前に (place + に), 残らず (何も残らず 'nothing was
# left'), 本当に (本当 already teaches it).
_FIXED_JOINS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("実", "は"),
        ("常", "に"),
        ("一緒", "に"),
        ("一気", "に"),
        ("互い", "に"),
        ("仮", "に"),
        ("確か", "に"),
        ("別", "に"),
        ("共", "に"),
        ("次第", "に"),
        ("徐々", "に"),
        ("実", "に"),
        ("同時", "に"),
        ("急", "に"),
        ("一斉", "に"),
        ("一向", "に"),
        ("非常", "に"),
        ("未だ", "に"),
        ("要する", "に"),
        ("因み", "に"),
        ("故", "に"),
        ("何", "か"),
        ("誰", "か"),
        ("何故", "か"),
        ("何", "と", "か"),
        ("何", "だ", "か"),
        ("思わ", "ず"),
        ("絶え", "ず"),
        ("相", "変わら", "ず"),
    }
)

# Verb orthBase in 未然形 + the negative auxiliary ない (なかっ, なく, ねえ), mapped
# to the i-adjective the pair spells. Kanji 貯まる/溜まる ('accumulate') carry
# their own orthBase and are not rows. Kana たまる/つまる also spell 'does not
# accumulate/clog' (お金がたまらない); accepted, as the adjectives are far
# more common.
_NEGATIVE_ADJECTIVES: dict[str, str] = {
    "くだる": "くだらない",
    "下る": "下らない",
    "つまる": "つまらない",
    "詰まる": "詰まらない",
    "たまる": "たまらない",
    "堪る": "堪らない",
    "思いがける": "思いがけない",
    "否める": "否めない",
}

_GRAMMAR_POS1 = frozenset({"助詞", "助動詞"})
_MAX_FIXED_TOKENS = max(len(key) for key in _FIXED_JOINS)

# 思わず read as a negated verb, not the adverb: followed by に (思わずに
# 'without thinking') or after とは/とも (そうとは思わず 'not thinking so'). A bare
# と is left out: UniDic tags the conditional と (見ると思わず笑った) the same as the
# quoting one, so a bare 「…」と思わず quote still joins. Accepted. 絶えず and
# 相変わらず are never a negated verb, so the check is for 思わず alone.
_QUOTATIONS = (("と", "は"), ("と", "も"))


class _Join(NamedTuple):
    start: int
    end: int  # inclusive
    headword: str
    pos1: str


def _negated_verb_context(tokens: list, start: int, end: int) -> bool:
    """Whether a 思わず span sits where the verb is negated rather than adverbial."""
    if end + 1 < len(tokens) and tokens[end + 1].surface == "に":
        return True
    before = tuple(token.surface for token in tokens[max(0, start - 2) : start])
    return any(before[-len(quote) :] == quote for quote in _QUOTATIONS)


def _fixed_join(tokens: list, i: int) -> _Join | None:
    """The longest fixed table entry starting at ``tokens[i]``, ending on grammar."""
    for size in range(min(_MAX_FIXED_TOKENS, len(tokens) - i), 1, -1):
        span = tokens[i : i + size]
        key = tuple(token.surface for token in span)
        if key not in _FIXED_JOINS or _pos1(span[-1]) not in _GRAMMAR_POS1:
            continue
        if key == ("思わ", "ず") and _negated_verb_context(tokens, i, i + size - 1):
            return None
        return _Join(i, i + size - 1, "".join(key), "副詞")
    return None


def _negative_adjective(tokens: list, i: int) -> _Join | None:
    """A table verb in 未然形 followed by the negative auxiliary ない."""
    verb, aux = tokens[i], tokens[i + 1]
    c_form = getattr(getattr(verb, "feature", None), "cForm", None)
    if _pos1(verb) != "動詞" or not isinstance(c_form, str) or not c_form.startswith("未然形"):
        return None
    headword = _NEGATIVE_ADJECTIVES.get(extract_orth_base(verb))
    if headword is None or _pos1(aux) != "助動詞" or extract_orth_base(aux) != "ない":
        return None
    return _Join(i, i + 1, headword, "形容詞")


def _contiguous(spans: dict[int, tuple[int, int]], start: int, end: int) -> bool:
    """Whether tokens ``start..end`` sit side by side in the source (no space between)."""
    for k in range(start, end):
        left, right = spans.get(k), spans.get(k + 1)
        if left is None or right is None or left[1] != right[0]:
            return False
    return True


class LexicalJoiner:
    """Join the table's grammar-cut words back into one token per line.

    ``allowed_pos`` is the run's inclusion rule's: a join whose POS the user
    turned off is skipped, so the split words keep their cards.
    """

    def __init__(self, attest_lookup: AttestLookup, allowed_pos: frozenset[str]) -> None:
        self._lookup = attest_lookup
        self._allowed_pos = allowed_pos
        self._attested: dict[str, bool] = {}

    def join_line(self, text: str, tokens: list) -> list:
        """Return ``tokens`` with table words joined.

        Never mutates ``tokens`` or its elements (the parser's per-file line
        cache shares them) and returns the input object when nothing joins.
        One batched lookup per line covers every headword not seen before.
        """
        joins = [join for join in self._find(tokens) if join.pos1 in self._allowed_pos]
        if not joins:
            return tokens
        spans = CompoundDictionaryMatcher._source_spans(text, tokens)
        joins = [join for join in joins if _contiguous(spans, join.start, join.end)]
        unknown = sorted({join.headword for join in joins} - self._attested.keys())
        if unknown:
            hits = self._lookup(unknown)
            for headword in unknown:
                self._attested[headword] = bool(hits.get(headword))
        by_start = {join.start: join for join in joins if self._attested[join.headword]}
        if not by_start:
            return tokens
        out: list = []
        i = 0
        while i < len(tokens):
            join = by_start.get(i)
            if join is None:
                out.append(tokens[i])
                i += 1
                continue
            span = tokens[join.start : join.end + 1]
            out.append(
                CompoundSyntheticToken(
                    surface="".join(token.surface for token in span),
                    pos1=join.pos1,
                    pos2="一般",
                    lemma=join.headword,
                    kana="".join(extract_reading(token) for token in span),
                )
            )
            i = join.end + 1
        return out

    @staticmethod
    def _find(tokens: list) -> list[_Join]:
        """Greedy left-to-right table matches, the longest fixed entry first."""
        found: list[_Join] = []
        i = 0
        while i < len(tokens) - 1:
            join = _fixed_join(tokens, i) or _negative_adjective(tokens, i)
            if join is None:
                i += 1
            else:
                found.append(join)
                i = join.end + 1
        return found
