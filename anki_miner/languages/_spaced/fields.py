"""Extra card-field specs and card-field defaults for spaCy languages (spec §4.7, A.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType

from anki_miner.languages.profile import CardFieldSpec

POS_FIELD = CardFieldSpec(key="pos", capability="pos_tag", placeholder="PartOfSpeech")
NOUN_GENDER_FIELD = CardFieldSpec(key="noun_gender", capability="noun_gender", placeholder="Gender")
NOUN_ARTICLE_FIELD = CardFieldSpec(key="noun_article", capability="noun_article", placeholder="Article")
NOUN_PLURAL_FIELD = CardFieldSpec(key="noun_plural", capability="noun_plural", placeholder="Plural")


def spaced_card_fields(extra: Sequence[CardFieldSpec]) -> Mapping[str, str]:
    """The ja ``anki_fields`` default with furigana unmapped, plus each extra key empty.

    Derived, never hand-written (the zh ``fields.py`` rule): a key the config
    gains later reaches every spaCy language without an edit here. Furigana is a
    ja-only concept, so both ruby fields map to nothing; every extra key ships
    empty because the mapped field name is the on/off switch.
    """
    from anki_miner.config.config import AnkiMinerConfig  # languages/ stays import-light

    fields = dict(AnkiMinerConfig().anki_fields)
    fields["expression_furigana"] = ""
    fields["sentence_furigana"] = ""
    fields.update({spec.key: "" for spec in extra})
    return MappingProxyType(fields)
