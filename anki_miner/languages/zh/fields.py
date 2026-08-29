"""zh anki_fields defaults. Every value "" — mapped ⇒ feature on (spec 9.3)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


def _zh_fields() -> dict[str, str]:
    # Imported inside the function: languages/ must stay import-light, and the
    # ja dataclass default is the single source of the shared key set.
    from anki_miner.config.config import AnkiMinerConfig

    fields = dict(AnkiMinerConfig().anki_fields)
    # Furigana is a ja-only concept; the keys stay (REQUIRED_FIELD_KEYS) but map
    # to nothing so a zh run never writes into a ja note type's ruby fields.
    fields["expression_furigana"] = ""
    fields["sentence_furigana"] = ""
    fields.update({"measure_word": "", "expression_traditional": "", "expression_pinyin": ""})
    return fields


ZH_CARD_FIELD_DEFAULTS: Mapping[str, str] = MappingProxyType(_zh_fields())
