"""The aspect_pair card field's capability-gated Settings -> Anki row.

No shipped language declares the field yet (Ruling S1 lands the seam first), so the test declares it on a copy of
the German profile for its duration. The key, the capability, the closed sets and the row text are pinned by
tests/unit/languages/test_spaced_governance.py.
"""

from __future__ import annotations

import dataclasses

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.languages import registry
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD
from anki_miner.languages.registry import get_profile


def _declare_on_german(monkeypatch) -> None:
    german = get_profile("de")
    monkeypatch.setitem(
        registry._CACHE,
        "de",
        dataclasses.replace(
            german,
            extra_card_fields=(*german.extra_card_fields, ASPECT_PAIR_FIELD),
            capabilities=german.capabilities | {"aspect_pairs"},
        ),
    )


def _panel(qtbot, config) -> AnkiSettingsPanel:
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def test_a_language_declaring_the_field_gets_a_gated_row(qtbot, test_config, monkeypatch):
    _declare_on_german(monkeypatch)
    ja = _panel(qtbot, test_config)
    assert not ja.aspect_pair_field_input.isVisibleTo(ja)
    assert "aspect_pair" not in ja.get_card_fields()

    config = dataclasses.replace(
        test_config, language="de", anki_fields={**dict(test_config.anki_fields), "aspect_pair": "AspectPair"}
    )
    de = _panel(qtbot, config)
    assert de.aspect_pair_field_input.isVisibleTo(de)
    assert de.aspect_pair_field_input.placeholderText() == "AspectPair"
    assert de.get_card_fields()["aspect_pair"] == "AspectPair"
