"""yue part-of-speech defaults (pycantonese universal tags).

``allowed_pos`` matches ``feature.pos1`` -- the universal tag -- and
``excluded_subtypes`` matches ``feature.pos2``, which for Cantonese is the
tokenizer's stopword tier rather than a finer tagset (the engine has no second
level).

``excluded_subtypes`` is EMPTY: the tier is offered in Settings and off, because
the engine's 104-word stop list holds 睇, 講 and 知 -- ordinary vocabulary a
learner mines -- and zh's "over-include beats silent drop" doctrine applies (a
word the tagger never emitted cannot be recovered by any downstream filter).

Two measured consequences, accepted rather than papered over:

* 廣東話, 香港人 and 澳門 tag PROPN, so Hong Kong place and language names need
  the user's PROPN tick -- the ja/ko/zh names convention.
* the tagger glues and mis-tags particles (緊飯 NOUN, 靚啦 ADJ, 啦 NOUN), so a
  particle-bearing token can pass this gate. The dictionary miss is what stops
  it: neither 靚啦 nor 咁啦 is a headword in either catalogue row.

PART, INTJ, PRON, PROPN, NUM, ADP, AUX, DET, CCONJ, SCONJ, PUNCT, SYM and X need
no exclusion entry -- their tag is outside ``YUE_ALLOWED_POS`` already.
"""

from __future__ import annotations

from collections.abc import Mapping

YUE_ALLOWED_POS: tuple[str, ...] = ("NOUN", "VERB", "ADJ", "ADV")

YUE_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

#: Shown beside each class in Settings -> Filtering.
YUE_POS_LABELS: Mapping[str, str] = {
    "NOUN": "Noun",
    "VERB": "Verb",
    "ADJ": "Adjective",
    "ADV": "Adverb",
    "PROPN": "Proper noun",
    "PRON": "Pronoun",
    "PART": "Particle (aspect, sentence-final)",
    "INTJ": "Interjection",
    "NUM": "Numeral",
    "ADP": "Adposition",
    "AUX": "Auxiliary",
    "DET": "Determiner",
    "CCONJ": "Conjunction",
    "SCONJ": "Conjunction",
    "PUNCT": "Punctuation",
    "SYM": "Symbol",
    "X": "Other",
    "stopword": "Stopword (pronouns, particles, common verbs)",
}
