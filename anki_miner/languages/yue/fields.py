"""yue extra card-field specs and anki_fields defaults. Every value "" -- mapped => feature on (spec 9.3)."""

from __future__ import annotations

from collections.abc import Mapping

from anki_miner.languages._spaced.fields import spaced_card_fields
from anki_miner.languages.profile import CardFieldSpec

#: One spec per render-hook field key, in hook order (spec 4.7: a key never
#: lands ahead of its hook). ``measure_word`` is zh's key reused -- the settings
#: row dedups by key and zh declares it first -- so only ``expression_jyutping``
#: needs a new ``_HOOK_FIELD_ROW_TEXTS`` pair. zh's expression_pinyin and
#: expression_traditional are NOT inherited: yue is traditional-only and its
#: reading is jyutping.
YUE_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="measure_word", capability="measure_word", placeholder="MeasureWord"),
    CardFieldSpec(key="expression_jyutping", capability="jyutping", placeholder="Jyutping", raw_html=True),
)

#: The ja default map with furigana unmapped -- the keys stay (REQUIRED_FIELD_KEYS)
#: but map to nothing, so a yue run never writes into a ja note type's ruby fields --
#: plus each extra key empty.
YUE_CARD_FIELD_DEFAULTS: Mapping[str, str] = spaced_card_fields(YUE_EXTRA_CARD_FIELDS)
