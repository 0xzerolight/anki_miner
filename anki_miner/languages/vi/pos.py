"""Vietnamese POS gate defaults (VLSP 2013 tagset, spec C.4).

pos1 is the underthesea v2.0 CRF model's tag with its ``B-`` prefix stripped
(tokenizer.py); the model has no second level, so pos2 carries only the two
tiers this package adds: ``stopword`` (tokenizer.py, the curated list) and
``name`` (morphology.VietnameseNamePass). The shared gate tests pos1 against
allowed_pos and pos2 against excluded_subtypes (services/morphology.py
content_gate_ok).

Absent from the allowed classes on purpose: Np (names), P (pronouns), R
(adverbs are function-like; user-tickable), E, C/Cc, M, L, T, I, X, CH, Ny, B
and the borrowed/abbreviated variants.
"""

from __future__ import annotations

STOPWORD_TAG = "stopword"
NAME_TAG = "name"

VI_ALLOWED_POS: tuple[str, ...] = ("N", "V", "A", "Nc", "Nu")
VI_EXCLUDED_SUBTYPES: tuple[str, ...] = (STOPWORD_TAG, NAME_TAG)

#: Every tag the v2.0 model emits (31, from its estimator's label list) plus the two tiers.
VI_POS_LABELS: dict[str, str] = {
    "N": "Noun",
    "Np": "Proper noun",
    "Nc": "Classifier",
    "Nu": "Unit noun",
    "Nb": "Borrowed noun",
    "Ni": "Noun",
    "Ny": "Abbreviation",
    "V": "Verb",
    "Vb": "Borrowed verb",
    "Vy": "Abbreviation",
    "A": "Adjective",
    "Ab": "Borrowed adjective",
    "P": "Pronoun",
    "Pb": "Borrowed pronoun",
    "R": "Adverb",
    "E": "Preposition",
    "Eb": "Borrowed preposition",
    "C": "Conjunction",
    "Cc": "Conjunction",
    "Cb": "Borrowed conjunction",
    "M": "Numeral",
    "Mb": "Borrowed numeral",
    "L": "Determiner",
    "T": "Particle",
    "I": "Interjection",
    "X": "Unknown",
    "Xy": "Abbreviation",
    "Y": "Abbreviation",
    "Z": "Bound morpheme",
    "B": "Loanword",
    "CH": "Punctuation",
    STOPWORD_TAG: "Stopword (pronouns, kinship terms, particles)",
    NAME_TAG: "Personal name (heuristic)",
}
