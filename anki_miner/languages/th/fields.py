"""th extra card-field specs and anki_fields defaults. Every value "" -- mapped => feature on (spec 9.3)."""

from __future__ import annotations

from collections.abc import Mapping

from anki_miner.languages._spaced.fields import spaced_card_fields
from anki_miner.languages.profile import CardFieldSpec

#: One spec per render-hook field key: ``test_language_contract
#: .test_extra_card_fields_match_the_render_hooks_exactly`` asserts spec keys ==
#: hook keys, so these land in the same commit as ``render.TH_RENDER_HOOKS``.
TH_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="reading_paiboon", capability="thai_reading", placeholder="Reading"),
    CardFieldSpec(key="classifier", capability="thai_classifier", placeholder="Classifier"),
)

#: The ja default map with furigana unmapped -- the keys stay (REQUIRED_FIELD_KEYS)
#: but map to nothing, so a th run never writes into a ja note type's ruby fields --
#: plus each extra key empty.
TH_CARD_FIELD_DEFAULTS: Mapping[str, str] = spaced_card_fields(TH_EXTRA_CARD_FIELDS)
