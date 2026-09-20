"""The Hebrew lookup ladder: one rung list, shared by the form resolver and the curator (spec F.2).

Hebrew writes its prepositions, its conjunction, its definite article and its relativiser as single
letters glued to the front of the word, and ``wty-he-en`` keys almost none of those forms. Probed
against the real build: ``bsefer``, ``vehayeladim``, ``bevayit``, ``she'ani`` and ``keshehu`` have
no key at ANY spelling, so the rungs are mandatory rather than an optimisation.

``rungs`` is the single source of both ladders. ``HebrewLookupStrategy.candidates`` returns it minus
the whole-word entry -- which is what satisfies both halves of the language contract
(``word not in texts`` and ``len(texts) == len(set(texts))``) -- and ``HebrewLemmaPass`` walks the
same list with the whole word in front, so the resolver and the definition lookup cannot drift.

Enclitic possessives are deliberately absent (spec section 9): modern speech uses ``shel``, and a
blind suffix strip over-generates on two- and three-letter stems. Ktiv male and haser variants are
absent for the same reason -- wty keys both spellings for many lemmas anyway.
"""

from __future__ import annotations

from anki_miner.languages.he.script import GERESH, GERSHAYIM, MAQAF, he_fold

__all__ = ["HE_STACKS", "HE_SINGLES", "MAX_CANDIDATES", "MIN_STEM", "rungs", "HebrewLookupStrategy"]

#: Proclitic STACKS, longest first at match time. The definite ``he`` assimilates into ``be``,
#: ``ke`` and ``le``, so ``bevayit`` -> ``bayit`` needs the stack list and not just the singles.
HE_STACKS: tuple[str, ...] = (
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER KAF}\N{HEBREW LETTER SHIN}",  # ve-kshe-
    "\N{HEBREW LETTER LAMED}\N{HEBREW LETTER KAF}\N{HEBREW LETTER SHIN}",  # li-kshe-
    "\N{HEBREW LETTER KAF}\N{HEBREW LETTER SHIN}",  # kshe-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER BET}",  # u-va-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER HE}",  # ve-ha-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER LAMED}",  # ve-la-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER KAF}",  # ve-ka-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER MEM}",  # u-me-
    "\N{HEBREW LETTER VAV}\N{HEBREW LETTER SHIN}",  # ve-she-
    "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER BET}",  # she-be-
    "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER LAMED}",  # she-le-
    "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER KAF}",  # she-ke-
    "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER MEM}",  # she-me-
    "\N{HEBREW LETTER SHIN}\N{HEBREW LETTER HE}",  # she-ha-
    "\N{HEBREW LETTER MEM}\N{HEBREW LETTER HE}",  # me-ha-
)

#: The single proclitics: vav, he, bet, kaf, lamed, mem, shin.
HE_SINGLES: tuple[str, ...] = (
    "\N{HEBREW LETTER VAV}",
    "\N{HEBREW LETTER HE}",
    "\N{HEBREW LETTER BET}",
    "\N{HEBREW LETTER KAF}",
    "\N{HEBREW LETTER LAMED}",
    "\N{HEBREW LETTER MEM}",
    "\N{HEBREW LETTER SHIN}",
)

#: The ladder is capped, like every other language's.
MAX_CANDIDATES = 8
#: A rung must leave a real stem behind; a one-letter remainder is noise.
MIN_STEM = 2


def rungs(word: str) -> list[str]:
    """Every ladder rung for a FOLDED *word*, minus the word itself, first-seen, capped at 8.

    Order (spec F.2): the ASCII-typed gershayim and geresh a subtitle writes, the maqaf pair, the
    maqaf-stripped construct form, the longest matching proclitic stack, then the single letter.
    A rung that leaves the string unchanged emits nothing.
    """
    out: list[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate != word and candidate not in out and len(candidate) >= MIN_STEM:
            out.append(candidate)

    # (1) A subtitle types ASCII quotes; wty keys 183 gershayim and 397 geresh forms.
    if '"' in word or "'" in word:
        add(word.replace('"', GERSHAYIM).replace("'", GERESH))
    # (2) The maqaf pair: the 7,426 maqaf keys and the 2,396 space-keyed phrases.
    if "-" in word:
        add(word.replace("-", MAQAF))
        add(word.replace("-", " "))
    # (3) A construct surface keyed without its maqaf.
    if MAQAF in word:
        add(word.replace(MAQAF, ""))
    # (4) One proclitic stack, longest first.
    for stack in sorted(HE_STACKS, key=len, reverse=True):
        if word.startswith(stack) and len(word) - len(stack) >= MIN_STEM:
            add(word[len(stack) :])
            break
    # (5) One single proclitic.
    for single in HE_SINGLES:
        if word.startswith(single) and len(word) - len(single) >= MIN_STEM:
            add(word[len(single) :])
            break
    return out[:MAX_CANDIDATES]


class HebrewLookupStrategy:
    """LookupStrategy: the ladder above, every rung miss-only with conditions 0.

    ``orth_base`` and ``ctype`` are unused -- Hebrew duck tokens carry neither, and the resolver
    has already put its answer in the token's lemma by the time a definition is looked up.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        del orth_base, ctype
        folded = he_fold(word)
        found = [(text, 0) for text in rungs(folded)]
        if folded != word:
            found.insert(0, (folded, 0))
        return found[:MAX_CANDIDATES]
