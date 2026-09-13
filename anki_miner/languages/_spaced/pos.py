"""Universal POS tables shared by every spaCy language (spec S20).

``UPOS_ALLOWED`` is the default ``allowed_pos`` gate (tested against a duck
token's ``pos1``). ``UPOS_LABELS`` feeds ``PosDefaults.labels`` (no consumer
yet, S20) and ``PosHook``, which lowercases the label for the card.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

#: Content classes a learner mines. Function words (DET ADP PRON AUX CCONJ SCONJ
#: PART), names (PROPN) and NUM SYM PUNCT INTJ X are out by class — never by
#: ``token.is_stop``, whose lists hold content words (A.2).
UPOS_ALLOWED: tuple[str, ...] = ("ADJ", "ADV", "NOUN", "VERB")

UPOS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "ADJ": "Adjective",
        "ADP": "Adposition",
        "ADV": "Adverb",
        "AUX": "Auxiliary",
        "CCONJ": "Coordinating conjunction",
        "DET": "Determiner",
        "INTJ": "Interjection",
        "NOUN": "Noun",
        "NUM": "Numeral",
        "PART": "Particle",
        "PRON": "Pronoun",
        "PROPN": "Proper noun",
        "PUNCT": "Punctuation",
        "SCONJ": "Subordinating conjunction",
        "SYM": "Symbol",
        "VERB": "Verb",
        "X": "Other",
    }
)
