"""English POS gate, sentence abbreviations and the known-word leading-word table.

``EN_EXCLUDED_SUBTYPES`` is empty on evidence, not by default: over
``tests/fixtures/en/pos_corpus.jsonl`` the real ``en_core_web_sm`` output puts
only ``NN NNS VB VBD VBG VBN VBP VBZ JJ JJR JJS RB RBR RBS WRB`` under
ADJ/ADV/NOUN/VERB, and the fine tags A.1 listed (POS HYPH NFP ADD XX LS)
never carry an allowed UPOS, so they would be dead config
(``test_en_pos_corpus.py`` pins the observed set).

``EN_ABBREVIATIONS`` is seeded from spaCy's English tokenizer exceptions (keys
ending in ``.``, casefolded, final dot dropped) minus every key that is also
an ordinary word or a bare initial — a state abbreviation such as ``ill`` or
``wash`` would stop ``I'm ill.`` ending its sentence — plus the common
abbreviations spaCy leaves out (``etc u.s u.k capt col lt sgt sr``).
"""

from __future__ import annotations

from anki_miner.languages._spaced.pos import UPOS_ALLOWED

EN_ALLOWED_POS: tuple[str, ...] = UPOS_ALLOWED
EN_EXCLUDED_SUBTYPES: tuple[str, ...] = ()

EN_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        # titles and ranks
        "mr", "mrs", "ms", "messrs", "dr", "prof", "jr", "sr", "st", "gov", "sen", "rep", "rev", "adm",
        "capt", "col", "lt", "sgt",
        # Latin and business
        "e.g", "i.e", "etc", "vs", "v.s", "inc", "ltd", "corp", "bros", "ph.d",
        # time and places
        "a.m", "p.m", "d.c", "u.s", "u.k", "n.y", "mt",
        # months
        "jan", "feb", "apr", "jul", "aug", "sep", "sept", "oct", "nov",
    }
)  # fmt: skip

#: Leading words a deck front carries that the mined lemma never does (S3): ``to go`` meets ``go``.
EN_LEADING_WORDS: frozenset[str] = frozenset({"a", "an", "the", "to"})


#: The model package the tokenizer loads and the availability probe looks for.
EN_MODEL_PACKAGE = "en_core_web_sm"
