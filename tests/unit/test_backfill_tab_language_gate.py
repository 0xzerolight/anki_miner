"""Card Backfill offers each mining language exactly the groups it can fill."""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.card_backfiller import FIELD_GROUPS


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


def _switched(mapped_config, language: str):
    """``mapped_config`` under *language*, with every group's target mapped.

    ``switch_language`` swaps ``anki_fields`` for that language's defaults,
    which leave the ja keys AND the language's own card fields unmapped — the
    pre-existing field gate would then disable every group on its own and the
    test could pass without a capability gate existing. Each extra field is
    mapped to its own placeholder, named by the profile rather than by this
    test, so a language that declares a new one is covered without an edit.
    """
    switched = switch_language(mapped_config, language)
    fields = {
        **switched.anki_fields,
        **mapped_config.anki_fields,
        **{spec.key: spec.placeholder for spec in get_profile(language).extra_card_fields},
    }
    return replace(switched, anki_fields=fields)


def _hook_groups(tab: CardBackfillTab) -> set[str]:
    """The tab's profile-derived groups: everything the core six do not cover."""
    return set(tab.field_checkboxes) - set(FIELD_GROUPS)


def test_ja_offers_every_core_group_and_no_profile_group(qtbot, mapped_config):
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)
    assert not tab.field_checkboxes["pitch"].isHidden()
    assert not tab.field_checkboxes["reading"].isHidden()
    assert tab.field_checkboxes["pitch"].isEnabled()
    # ja renders its own fields inline in the pipeline, so it declares none.
    assert _hook_groups(tab)
    assert all(tab.field_checkboxes[group].isHidden() for group in _hook_groups(tab))


def test_zh_hides_the_ja_only_groups_and_offers_its_own(qtbot, mapped_config):
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)
    tab.field_checkboxes["pitch"].setChecked(True)
    tab.field_checkboxes["reading"].setChecked(True)

    tab.update_config(_switched(mapped_config, "zh"))

    assert tab.field_checkboxes["pitch"].isHidden()
    assert tab.field_checkboxes["reading"].isHidden()
    # Hidden but ticked would still be scanned: _selected_field_keys reads
    # isChecked(), never visibility.
    assert not tab.field_checkboxes["pitch"].isChecked()
    assert not tab.field_checkboxes["reading"].isChecked()
    assert tab._selected_field_keys() == frozenset()
    assert not tab.field_checkboxes["definition"].isHidden()
    assert not tab.field_checkboxes["word_audio"].isHidden()
    for spec in get_profile("zh").extra_card_fields:
        assert not tab.field_checkboxes[spec.key].isHidden()
        assert tab.field_checkboxes[spec.key].isEnabled()


def test_switching_back_to_ja_re_offers_them(qtbot, mapped_config):
    """The gate is two-way, so a capability the language has puts its group back."""
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)

    tab.update_config(_switched(mapped_config, "zh"))
    tab.update_config(mapped_config)

    assert not tab.field_checkboxes["pitch"].isHidden()
    assert not tab.field_checkboxes["reading"].isHidden()
    assert all(tab.field_checkboxes[spec.key].isHidden() for spec in get_profile("zh").extra_card_fields)


@pytest.mark.parametrize(("language", "stranger"), [("yue", "zh"), ("ko", "zh")])
def test_a_language_gets_its_own_declared_fields_and_no_others(qtbot, mapped_config, language, stranger):
    """Nothing in the tab names a key: each group follows its spec's capability."""
    tab = CardBackfillTab(mapped_config)
    qtbot.addWidget(tab)

    tab.update_config(_switched(mapped_config, language))

    own = {spec.key for spec in get_profile(language).extra_card_fields}
    assert own
    for key in own:
        assert not tab.field_checkboxes[key].isHidden()
    for key in {spec.key for spec in get_profile(stranger).extra_card_fields} - own:
        assert tab.field_checkboxes[key].isHidden()


def test_an_unmapped_profile_field_leaves_its_group_offered_but_inert(qtbot, mapped_config):
    """The mapping gate disables, the capability gate hides — as for pitch."""
    config = _switched(mapped_config, "zh")
    config = replace(config, anki_fields={**config.anki_fields, "measure_word": ""})
    tab = CardBackfillTab(config)
    qtbot.addWidget(tab)

    assert not tab.field_checkboxes["measure_word"].isHidden()
    assert not tab.field_checkboxes["measure_word"].isEnabled()
    assert tab.field_checkboxes["expression_pinyin"].isEnabled()


def test_a_ticked_profile_group_reaches_the_scan(qtbot, mapped_config):
    tab = CardBackfillTab(_switched(mapped_config, "zh"))
    qtbot.addWidget(tab)

    tab.field_checkboxes["expression_pinyin"].setChecked(True)

    assert tab._selected_field_keys() == frozenset({"expression_pinyin"})
