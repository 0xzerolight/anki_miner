"""th part-of-speech defaults (UD tags from perceptron/tud).

``allowed_pos`` matches ``feature.pos1`` -- the UD tag -- and ``excluded_subtypes``
matches ``feature.pos2``, which for Thai is the tokenizer's tier rather than a
finer tagset (the model has no second level). PROPN is absent from the allowed
set on purpose, and it is ALSO what makes a separate name tier unnecessary (D2):
a name the model recognises is tagged PROPN and never mines, while an OOV name
fragments into syllables the model tags NOUN -- which no tier could rescue, since
the fragments are ordinary words. Users may tick PROPN.

PRON, PART, AUX, ADP, CCONJ, SCONJ, DET, NUM, PUNCT, SYM and X need no exclusion
entry at all -- their tag is outside ``TH_ALLOWED_POS`` already.
"""

from __future__ import annotations

from collections.abc import Mapping

TH_ALLOWED_POS: tuple[str, ...] = ("NOUN", "VERB", "ADJ", "ADV")

TH_EXCLUDED_SUBTYPES: tuple[str, ...] = ("stopword", "mark")

#: Shown beside each class in Settings -> Word Filters.
TH_POS_LABELS: Mapping[str, str] = {
    "NOUN": "Noun",
    "VERB": "Verb",
    "ADJ": "Adjective",
    "ADV": "Adverb",
    "PROPN": "Proper noun",
    "PRON": "Pronoun",
    "PART": "Particle",
    "AUX": "Auxiliary",
    "ADP": "Adposition",
    "CCONJ": "Conjunction",
    "SCONJ": "Conjunction",
    "DET": "Determiner",
    "NUM": "Number",
    "PUNCT": "Punctuation",
    "SYM": "Symbol",
    "X": "Latin/other",
    "stopword": "Stopword (particles, pronouns, titles)",
    "mark": "Repetition/abbreviation mark",
}
