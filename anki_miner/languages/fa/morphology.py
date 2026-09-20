"""Persian POS tiers, their labels and the card-front spelling.

The tag set is ``words.dat``'s own (probe P-2), not a universal one: the tokenizer
hands a row's first tag straight to ``pos1``, so the gate and the labels speak the
vocabulary the data file speaks. ``0`` -- the marker on 158,034 of its 193,350
rows -- is not a part of speech; the loader turns it into an empty tuple and the
ladder's attested rung answers ``unknown`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: Content classes a learner mines, sorted (the settings POS editor shows them in
#: order). Everything else ``words.dat`` carries is grammar scaffolding, a name
#: class or a residue marker and is out BY CLASS: NUM, PRO, P, POSTP, CONJ, DET,
#: INT, CL (classifiers), RES (residual/foreign), PL (a bare plural marker), AJC
#: (comparative -- its positive is the card) and ZVR (verbal noun, which the verb
#: table already answers as an infinitive).
#:
#: ``unknown`` is absent for a stronger reason than taste: it is the tier for the
#: 158,034 untagged rows plus everything no table answered, and admitting it would
#: put every attested string in Persian into a run.
FA_ALLOWED_POS: tuple[str, ...] = ("ADV", "AJ", "N", "V")

#: Subtypes dropped inside an allowed class -- the ``pos2`` gate. Stopword
#: membership is asked of the surface AND the lemma, so dige is dropped exactly as
#: digar is. ``informal`` is deliberately NOT here: a colloquial spelling is a real
#: word a learner meets, and its register is a card field, not a reason to skip it.
FA_EXCLUDED_SUBTYPES: tuple[str, ...] = ("stopword",)

#: Human labels for the settings POS editor. Covers every ``words.dat`` tag, the
#: three tiers the tokenizer synthesises and both subtypes, so the editor can name
#: what it is dropping as well as what it keeps.
FA_POS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "ADV": "Adverb",
        "AJ": "Adjective",
        "AJC": "Comparative adjective",
        "CL": "Classifier",
        "CONJ": "Conjunction",
        "DET": "Determiner",
        "INT": "Interjection",
        "N": "Noun",
        "NUM": "Number",
        "P": "Preposition",
        "PL": "Plural marker",
        "POSTP": "Postposition",
        "PRO": "Pronoun",
        "PUNCT": "Punctuation",
        "RES": "Residual",
        "V": "Verb",
        "ZVR": "Verbal noun",
        "informal": "Colloquial form",
        "stopword": "Stopword",
        "unknown": "Unknown",
    }
)


#: ``morph`` is fa's ONLY channel from the tokenizer to the card hooks: a
#: ``TokenizedWord`` carries ``pos`` (pos1) and ``morph``, never ``pos2`` and
#: never the duck token's ``feature`` namespace (``models/word.py``, the emit
#: site at ``services/subtitle_parser.py``'s ``_token_morph``). The string is the
#: same ``Key=Value|Key=Value`` shape spaCy's own ``str(tok.morph)`` has, so the
#: field it travels in stays one thing.
FA_MORPH_INFORMAL = "Register=Informal"
FA_MORPH_PRESENT_STEM = "PresentStem"


def fa_morph(*, informal: bool, present_stem: str) -> str:
    """The feature string for one duck token; ``""`` when it has no features."""
    features = []
    if informal:
        features.append(FA_MORPH_INFORMAL)
    if present_stem:
        features.append(f"{FA_MORPH_PRESENT_STEM}={present_stem}")
    return "|".join(features)


def fa_morph_value(morph: str, key: str) -> str:
    """One feature's value out of a ``morph`` string, or ``""``."""
    for feature in morph.split("|"):
        name, _, value = feature.partition("=")
        if name == key:
            return value
    return ""


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
