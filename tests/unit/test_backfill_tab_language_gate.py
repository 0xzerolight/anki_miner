"""Card Backfill hides the pitch and furigana groups outside Japanese."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.languages.switching import switch_language


@pytest.fixture
def mapped_config(test_config):
    """A config whose pitch and reading fields are both mapped, so the two
    groups are enabled by the field gate and only the capability gate can
    hide them."""
    fields = {
        **test_config.anki_fields,
        "pitch_graph": "PitchGraph",
        "pitch_text": "PitchText",
        "expression_reading": "Reading",
    }
    return replace(test_config, anki_fields=fields)


@pytest.fixture
def zh_mapped_config(mapped_config):
    """The same mapping under zh.

    ``switch_language`` swaps ``anki_fields`` for the zh profile's defaults,
    which would leave the pitch and reading keys unmapped — and then the
    pre-existing FIELD_GROUPS gate would disable the groups on its own and the
    test could pass without a capability gate existing. Restoring the mapping
    means only the capability gate can hide them.
    """
    return replace(switch_language(mapped_config, "zh"), anki_fields=mapped_config.anki_fields)


def test_ja_offers_every_group(qtbot, mapped_config):
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)
    assert not tab.field_checkboxes["pitch"].isHidden()
    assert not tab.field_checkboxes["reading"].isHidden()
    assert tab.field_checkboxes["pitch"].isEnabled()


def test_zh_hides_and_unticks_the_ja_only_groups(qtbot, mapped_config, zh_mapped_config):
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)
    tab.field_checkboxes["pitch"].setChecked(True)
    tab.field_checkboxes["reading"].setChecked(True)

    tab.update_config(zh_mapped_config)

    assert tab.field_checkboxes["pitch"].isHidden()
    assert tab.field_checkboxes["reading"].isHidden()
    # Hidden but ticked would still be scanned: _selected_field_keys reads
    # isChecked(), never visibility.
    assert not tab.field_checkboxes["pitch"].isChecked()
    assert not tab.field_checkboxes["reading"].isChecked()
    assert tab._selected_field_keys() == frozenset()
    assert not tab.field_checkboxes["definition"].isHidden()
    assert not tab.field_checkboxes["word_audio"].isHidden()


def test_switching_back_to_ja_re_offers_them(qtbot, mapped_config, zh_mapped_config):
    """The gate is two-way, so a capability the language has puts its group back."""
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)

    tab.update_config(zh_mapped_config)
    tab.update_config(mapped_config)

    assert not tab.field_checkboxes["pitch"].isHidden()
    assert not tab.field_checkboxes["reading"].isHidden()
