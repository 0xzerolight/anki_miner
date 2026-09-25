"""pt's variety combo shows for pt only and writes the already-scoped script_variant (D7).

Both variant combos write the same field. The gate shows at most one of them,
and contribute() writes only a visible one, so neither can drift the other
language's value.

Lives on the Mining Language page (T10), beside the selector whose language it
varies with.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.language_gate import field_row_widgets
from anki_miner.gui.widgets.panels.mining_language_settings_panel import MiningLanguageSettingsPanel
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS


def _mining_language(qtbot, config: AnkiMinerConfig) -> MiningLanguageSettingsPanel:
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def _pt(config: AnkiMinerConfig, **overrides) -> AnkiMinerConfig:
    return replace(config, language="pt", **overrides)


def test_the_combo_offers_the_two_varieties(qtbot, test_config):
    combo = _mining_language(qtbot, test_config).regional_variant_combo
    assert [combo.itemData(index) for index in range(combo.count())] == ["br", "pt"]


def test_ja_and_zh_hide_it(qtbot, test_config):
    for config in (test_config, replace(test_config, language="zh", script_variant="simplified")):
        panel = _mining_language(qtbot, config)
        assert not panel.regional_variant_combo.isVisibleTo(panel)
        assert not panel._regional_variants_section_label.isVisibleTo(panel)


def test_pt_shows_it_with_the_configured_variety_and_hides_the_zh_combo(qtbot, test_config):
    panel = _mining_language(qtbot, _pt(test_config, script_variant="pt"))
    assert panel.regional_variant_combo.isVisibleTo(panel)
    assert panel.regional_variant_combo.currentData() == "pt"
    assert panel._regional_variants_section_label.isVisibleTo(panel)
    assert not panel.script_variant_combo.isVisibleTo(panel)
    assert not panel._script_variants_section_label.isVisibleTo(panel)


def test_a_pt_save_round_trips_the_variety(qtbot, test_config):
    config = _pt(test_config, script_variant="br")
    panel = _mining_language(qtbot, config)
    panel.regional_variant_combo.setCurrentIndex(panel.regional_variant_combo.findData("pt"))
    assert panel.contribute(config).script_variant == "pt"


def test_neither_ja_nor_zh_saves_are_touched_by_the_pt_combo(qtbot, test_config):
    assert _mining_language(qtbot, test_config).contribute(test_config).script_variant == ""
    zh = replace(test_config, language="zh", script_variant="traditional")
    assert _mining_language(qtbot, zh).contribute(zh).script_variant == "traditional"


def test_a_switch_back_to_ja_hides_it_again(qtbot, test_config):
    panel = _mining_language(qtbot, _pt(test_config, script_variant="pt"))
    panel.load_from_config(test_config)
    assert not panel.regional_variant_combo.isVisibleTo(panel)
    assert panel.contribute(test_config).script_variant == ""


def test_the_gated_row_hides_its_label_too(qtbot, test_config):
    panel = _mining_language(qtbot, test_config)
    label, widget = field_row_widgets(panel, panel.regional_variant_combo)
    assert not label.isVisibleTo(panel) and not widget.isVisibleTo(panel)


def test_the_variety_is_already_a_scoped_field():
    """R26: no field is added; the pt value is parked per language like zh's."""
    assert "script_variant" in LANGUAGE_SCOPED_FIELDS
