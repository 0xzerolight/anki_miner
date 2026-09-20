"""Ukrainian abbreviations: the source of ``UK_ABBREVIATIONS`` (spec S8, plan P10).

Derived, not hand-picked: every dotted Cyrillic stem in spaCy 3.8's Ukrainian tokenizer exceptions
(``spacy/lang/uk/tokenizer_exceptions.py``, MIT) minus the seven stems pymorphy3-dicts-uk knows as
ordinary words (г, м, мкр, наб, оз, пл, ст): those exceptions glue a sentence-final word to its dot
(``Це моя м.`` would tag ``м.`` as X and mine nothing), and
``build_spacy_tagger(abbreviations=...)`` prunes every single-dot rule whose stem is not in this
set. The same set is the sentence splitter's (``UK_SENTENCE_RULES``).
``tests/unit/languages/test_uk_text_rules.py`` re-derives it, so the set is regenerated, never
hand-edited.
"""

from __future__ import annotations

UK_ABBREVIATIONS: frozenset[str] = frozenset(
    {"акад", "бул", "вул", "доц", "майд", "обл", "п", "пров", "просп", "проф", "ім"}
)
