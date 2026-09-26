"""zh extra card-field specs and anki_fields defaults. Every value "" — mapped ⇒ feature on (spec 9.3)."""

from __future__ import annotations

from collections.abc import Mapping

from anki_miner.languages._spaced.fields import spaced_card_fields
from anki_miner.languages.profile import CardFieldSpec

#: One spec per ZH_RENDER_HOOKS field (render.py). Placeholders and gating
#: capabilities match the existing hand-written rows in
#: gui/widgets/panels/anki_settings_panel.py (``_language_gate_pairs``)
#: verbatim — "pinyin" gates the expression_pinyin row there, not
#: "tone_color" (which gates the separate reading_tone_color *checkbox* in
#: anki_settings_panel.py). ``raw_html=True`` matches
#: ``anki_note_builder._RAW_HTML_FIELD_KEYS`` membership exactly.
ZH_EXTRA_CARD_FIELDS: tuple[CardFieldSpec, ...] = (
    CardFieldSpec(key="measure_word", capability="measure_word", placeholder="MeasureWord"),
    CardFieldSpec(key="expression_traditional", capability="script_variants", placeholder="Traditional"),
    CardFieldSpec(key="expression_pinyin", capability="pinyin", placeholder="Pinyin", raw_html=True),
)

#: The ja default map with furigana unmapped — the keys stay (REQUIRED_FIELD_KEYS)
#: but map to nothing, so a zh run never writes into a ja note type's ruby fields —
#: plus each extra key empty.
ZH_CARD_FIELD_DEFAULTS: Mapping[str, str] = spaced_card_fields(ZH_EXTRA_CARD_FIELDS)
