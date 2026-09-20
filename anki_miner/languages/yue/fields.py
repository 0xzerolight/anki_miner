"""yue anki_fields defaults. Every value "" -- mapped => feature on (spec 9.3)."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType


def _yue_fields() -> dict[str, str]:
    # Imported inside the function: languages/ must stay import-light, and the
    # ja dataclass default is the single source of the shared key set.
    from anki_miner.config.config import AnkiMinerConfig

    fields = dict(AnkiMinerConfig().anki_fields)
    # Furigana is a ja-only concept; the keys stay (REQUIRED_FIELD_KEYS) but map
    # to nothing so a yue run never writes into a ja note type's ruby fields.
    fields["expression_furigana"] = ""
    fields["sentence_furigana"] = ""
    # zh's expression_pinyin and expression_traditional are NOT inherited: yue
    # is traditional-only and its reading is jyutping.
    fields.update({"measure_word": "", "expression_jyutping": ""})
    return fields


YUE_CARD_FIELD_DEFAULTS: Mapping[str, str] = MappingProxyType(_yue_fields())
