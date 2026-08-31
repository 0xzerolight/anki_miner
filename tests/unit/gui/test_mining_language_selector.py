"""The selector offers only languages this build can actually build.

``AVAILABLE_LANGUAGES`` is the DECLARED set - config validates against a copy of
it and it names ``ko`` from Stage 0 onward. A code only becomes selectable once
``get_profile`` can build its profile, which for ``ko`` is Stage 3. Listing a
declared-but-unregistered code made building the Settings panel raise.
"""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import language_choices
from anki_miner.gui.widgets.panels.mining_language_settings_panel import MiningLanguageSettingsPanel
from anki_miner.languages.ko import availability as ko_availability
from anki_miner.languages.registry import get_profile
from anki_miner.languages.zh import availability


@pytest.fixture(autouse=True)
def ko_stack_absent(monkeypatch):
    """Pin the ko engine as missing, so these lists do not read the machine.

    ``kiwipiepy`` is an optional extra: present on a dev box with ``[ko]``,
    absent from CI until the languages extra lands. Every assertion below is an
    exact list, so leaving the probe live would make the file pass or fail on
    what happens to be installed. ko's own selector coverage - dropped when the
    engine is missing, offered as 한국어 when it is there - lives in
    ``tests/unit/languages/test_ko_availability.py``.
    """
    monkeypatch.setattr(ko_availability, "find_spec", lambda _name: None)


def _panel(qtbot, config: AnkiMinerConfig) -> MiningLanguageSettingsPanel:
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def test_a_declared_but_unbuildable_language_is_not_offered(monkeypatch):
    monkeypatch.setattr(language_choices, "AVAILABLE_LANGUAGES", ("ja", "zh", "xx"))
    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja", "zh"]


def test_a_language_whose_stack_is_missing_is_not_offered(monkeypatch):
    """A profile builds fine without its optional stack; it still can't mine.

    ``unavailable_reason`` is the profile's own runtime probe (zh answers it
    from ``find_spec``), so a build missing the tokenizer drops the language
    from the selector instead of offering a switch that cannot mine a word.
    ``ja`` leaves the field ``None`` and is therefore always offered.
    """

    def fake_get_profile(code: str):
        profile = get_profile(code)
        if code == "zh":
            return dataclasses.replace(profile, unavailable_reason=lambda: "needs jieba")
        return profile

    monkeypatch.setattr(language_choices, "get_profile", fake_get_profile)

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja"]


def test_a_missing_optional_package_keeps_the_language_offered(monkeypatch):
    """R11a: opencc is optional, so its absence degrades a feature, not the menu.

    The profile's gate probes the REQUIRED packages; ``zh_unavailable_reason``
    keeps naming the optional one for whoever wants the full list.
    """
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "opencc" else object())

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja", "zh"]


def test_a_missing_required_package_drops_the_language(monkeypatch):
    monkeypatch.setattr(availability, "find_spec", lambda name: None if name == "jieba" else object())

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja"]


def test_offered_languages_carry_their_native_names():
    names = dict(language_choices.available_mining_languages())
    assert names["ja"] == "日本語"
    assert names["zh"] == "中文"


def test_the_panel_builds_and_lists_only_buildable_languages(qtbot, test_config):
    combo = _panel(qtbot, test_config).mining_language_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["ja", "zh"]
    assert combo.currentData() == "ja"


def test_changing_the_combo_only_requests_a_switch(qtbot, test_config):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)

    panel.mining_language_combo.setCurrentIndex(1)

    assert requested == ["zh"]


def test_set_mining_language_repoints_without_re_requesting(qtbot, test_config):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)

    panel.set_mining_language("zh")

    assert panel.mining_language_combo.currentData() == "zh"
    assert requested == []


def test_the_panel_writes_no_config_field(qtbot, test_config):
    """The switch owns ``language``: it has to stash the outgoing language's
    scoped values first, and a second writer would race it."""
    panel = _panel(qtbot, test_config)

    assert not hasattr(panel, "contribute")


def test_the_panel_title_matches_its_navigator_label(qtbot):
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)

    assert panel._title_label.text() == "Mining Language"


def test_the_panel_anchors_the_selector_and_every_pack_row(qtbot):
    """One anchor per row, named for the language: search reaches each download."""
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)

    ids = {anchor.stable_id for anchor in panel.setting_anchors()}

    assert ids == {
        "mining_language.mining_language_combo",
        "mining_language.language_pack_ko",
        "mining_language.language_pack_zh",
    }


def test_a_pack_row_is_searchable_by_its_english_name(qtbot):
    """The row names its language natively; "Korean" has to reach it too.

    Everything on the row - label, button caption, tooltip - renders 한국어 /
    中文, so a user searching Settings for the English name found nothing.
    """
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)

    text = {anchor.stable_id: anchor.search_text() for anchor in panel.setting_anchors()}

    assert "Korean" in text["mining_language.language_pack_ko"]
    assert "Chinese" in text["mining_language.language_pack_zh"]


def test_repopulating_keeps_the_selection_and_proposes_nothing(qtbot, test_config):
    """A finished pack download rebuilds the combo under the user's selection."""
    panel = _panel(qtbot, test_config)
    panel.set_mining_language("zh")
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)

    panel._repopulate_mining_languages()

    assert panel.mining_language_combo.currentData() == "zh"
    assert requested == []
