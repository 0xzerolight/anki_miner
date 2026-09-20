"""Persian card-front spelling (the profile's ``mined_form``).

The POS tiers and their labels land beside this in Task 9; the policy itself
lives here because the tokenizer's tests pin it.
"""

from __future__ import annotations


class PersianMinedForm:
    """MinedFormPolicy for Persian: the lemma the lookup ladder already chose.

    Every tier of ``tokenizer.to_duck_tokens`` sets ``lemma`` to the spelling
    that tier decided on -- the infinitive for a verb, the noun plus infinitive
    for a light-verb compound, the word itself for a tagged ``words.dat`` row,
    the stem where the stem is what matched, the formal spelling for a
    colloquial one. Re-stemming that here would mine mardom ("people") as mard
    ("man") and in ("this") as a bare alef: hazm's stemmer is a retrieval aid,
    not a lemmatiser, and its one-character-suffix floor does not save either.

    Never the raw ``past#present`` pair: that spelling is unstable across
    ``verbs.dat`` variants (spec C.2).
    """

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        """Return the card-front spelling for one token."""
        return lemma or orth_base or surface
