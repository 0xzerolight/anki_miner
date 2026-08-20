"""Masu-stem nominalization (差し入れ mined as 差し入れる).

unidic resolves a 連用形 (masu-stem) token by Viterbi cost over the whole line,
and a subtitle line gives it a weaker context than a sentence does: the cue's
line break is flattened to a plain ASCII space by ``clean_subtitle_text`` and
MeCab does not treat that space — nor a literal newline — as a lattice boundary.
So ``これ、差し入れ みんなで食べてよ`` tags 差し入れ as 動詞 / 連用形-一般 with
orthBase 差し入れる, while the same line punctuated with ``。`` correctly tags it
名詞. ``select_mined_form`` then ships the verb dictionary form as the card
front, splitting definition / frequency / dedup / audio identity from the noun
the learner actually read.

Re-tokenizing each whitespace-delimited segment separately was measured and
rejected: it regresses 6 of 26 realistic two-line cues (帰り|ましょう → 帰り,
疲れ|た → 疲れ, 笑い|ながら → 笑い, 動き|出した → 動き, 待っ|てて → a garbage 名詞
てて, 行っ|てきます → 助動詞 てく), because a break can land mid-inflection.

This pass instead corrects the one token in place, gated on a right-context
signal MeCab already got right. A masu-stem that is genuinely verbal is
followed by material that only a verb stem can take — an auxiliary (ます, た,
たい), a conjunctive particle (て, ながら), or the second half of a compound verb
(動き出す) — so the gate is an ALLOW-list of neighbours that are incompatible
with a verbal reading, plus dictionary attestation of the stem itself. A
deny-list was rejected: it would silently admit 形容詞, 接尾辞, 補助記号 and any
tag unidic gains later.

Deliberately NOT gated on the whitespace that caused the mis-tag. Gating on it
would make the card front depend on where the subtitle happened to wrap — the
same line unwrapped mines 差し入れる and wrapped mines 差し入れ, so one episode
could emit both cards — and a user's ``subtitle_regex_filter`` can create or
destroy that space. Measured fire rate without it, over 21 realistic sentences:
2 — 差し入れ (this bug) and 切り替え時期 → 切り替え (a correct improvement).

Runs AFTER the compound matcher, so an attested compound covering the same
token (ご存じ) is already a 名詞 synthetic and this pass skips it. Lives outside
``morphology.py`` for the same reason ``compound_matcher.py`` does: it is a
stateful object holding an injected lookup, not a pure token helper.
"""

from __future__ import annotations

from typing import Callable

from anki_miner.services.morphology import SyntheticToken, extract_reading

# Batch existence probe: the subset of the input strings that exist as exact
# offline-dictionary headwords (DefinitionService.offline_terms_exist).
TermLookup = Callable[[list[str]], set[str]]

# Neighbours that a 連用形 verb CANNOT take while staying verbal, so their
# presence marks the stem as nominalized. Allow-list, never a deny-list: the
# verbal neighbours are 助動詞 (ます/た/たい), 助詞 (て/ながら/つつ), 動詞 (compound
# verb second halves), 接続詞 (連用中止法), and every tag unidic may add later.
_NOMINALIZING_NEIGHBOUR_POS1 = frozenset({"名詞", "代名詞", "副詞", "形状詞", "連体詞", "接頭辞", "感動詞"})

# Bound stems (し, 見, 出し, つけ, 願い) are 動詞 / 非自立可能. They head no
# standalone noun, and several are attested as headwords in their own right, so
# without this guard 気を|つけ or お|願い would be rewritten out from under the
# compound matcher.
_BOUND_VERB_POS2 = "非自立可能"

# One-character stems (見, 来, し) are too ambiguous to correct on context
# alone, and their attested homographs are almost never the intended reading.
_MIN_SURFACE_LEN = 2


class MasuStemNominalizer:
    """Rewrite dictionary-attested nominalized masu-stems as nominal tokens.

    ``term_lookup`` is injected (no SQLite here) and is expected to be the
    parser's shared memoized probe, so a surface's existence is looked up once
    across the merge gate, the compound matcher and this pass.
    """

    def __init__(self, term_lookup: TermLookup) -> None:
        self._lookup = term_lookup

    def rewrite_line(self, tokens: list) -> list:
        """Return a token list with nominalized masu-stems replaced.

        Never mutates ``tokens`` or its elements (the parser's per-file line
        cache shares them) and returns the input object unchanged when nothing
        fires, so the no-op path allocates nothing. One batched ``term_lookup``
        call per line covers every candidate.
        """
        indexes = [i for i in range(len(tokens) - 1) if self._is_candidate(tokens[i], tokens[i + 1])]
        if not indexes:
            return tokens

        attested = self._lookup(sorted({tokens[i].surface for i in indexes}))
        firing = {i for i in indexes if tokens[i].surface in attested}
        if not firing:
            return tokens

        return [self._nominalize(t) if i in firing else t for i, t in enumerate(tokens)]

    @staticmethod
    def _is_candidate(token, next_token) -> bool:
        """Structural gate; dictionary attestation is checked separately."""
        feature = getattr(token, "feature", None)
        if getattr(feature, "pos1", None) != "動詞":
            return False
        if getattr(feature, "pos2", None) == _BOUND_VERB_POS2:
            return False
        c_form = getattr(feature, "cForm", None)
        if not isinstance(c_form, str) or not c_form.startswith("連用形"):
            return False
        surface = getattr(token, "surface", "")
        if len(surface) < _MIN_SURFACE_LEN:
            return False
        next_pos1 = getattr(getattr(next_token, "feature", None), "pos1", None)
        return next_pos1 in _NOMINALIZING_NEIGHBOUR_POS1

    @staticmethod
    def _nominalize(token) -> SyntheticToken:
        """Mint the nominal replacement.

        surface == lemma == the source spelling, so ``select_mined_form``'s
        nominal branch returns the surface and every lemma-keyed consumer
        (definitions, known words, frequency, pitch) agrees with the card front.
        The kana comes from the ORIGINAL token, i.e. unidic's context-
        disambiguated reading, never a re-tokenization of the surface.
        ``kana_locked=True`` keeps that reading out of
        ``morphology.attest_merged_readings`` — this is a 1:1 nominalization,
        not a dictionary-attested compound merge, so the dictionary's reading
        for the surface (which may be for an unrelated homograph) must not
        override unidic's context-disambiguated one.
        """
        return SyntheticToken(
            surface=token.surface,
            pos1="名詞",
            pos2="普通名詞",
            lemma=token.surface,
            kana=extract_reading(token),
            kana_locked=True,
        )
