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
from anki_miner.languages._spaced import availability as spaced_availability
from anki_miner.languages.ko import availability as ko_availability
from anki_miner.languages.registry import get_profile
from anki_miner.languages.th import availability as th_availability
from anki_miner.languages.yue import availability as yue_availability
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
    monkeypatch.setattr(ko_availability, "module_importable", lambda _name: False)
    # Same for every spaCy language: the shared probe reads spaCy and its model.
    monkeypatch.setattr(spaced_availability, "find_spec", lambda _name: None)
    # id has no engine at all: like ja it leaves ``unavailable_reason`` None and is
    # always offered, so it is in every exact list below.
    # Same for th: pythainlp is an optional extra, present on a dev box.
    monkeypatch.setattr(th_availability, "module_importable", lambda _name: False)
    # Same for yue: pycantonese is an optional extra, present on a dev box.
    monkeypatch.setattr(yue_availability, "module_importable", lambda _name: False)
    # zh's probe is the shared one too, so the spaCy pin above would drop it; every
    # exact list below offers zh, so pin its stack present.
    monkeypatch.setattr(availability, "module_importable", lambda _name: True)
    # D12: no pack downloads here, so the combo lists what can be mined now.
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda _code: None)


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

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja", "he", "id"]


def test_a_missing_optional_package_keeps_the_language_offered(monkeypatch):
    """R11a: opencc is optional, so its absence degrades a feature, not the menu.

    The profile's gate probes the REQUIRED packages; ``zh_unavailable_reason``
    keeps naming the optional one for whoever wants the full list.
    """
    monkeypatch.setattr(availability, "module_importable", lambda name: name != "opencc")

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja", "zh", "he", "id"]


def test_a_missing_required_package_drops_the_language(monkeypatch):
    monkeypatch.setattr(availability, "module_importable", lambda name: name != "jieba")

    assert [code for code, _name in language_choices.available_mining_languages()] == ["ja", "he", "id"]


def test_offered_languages_carry_native_and_english_names():
    names = dict(language_choices.available_mining_languages())
    assert names["ja"] == "日本語 — Japanese"
    assert names["zh"] == "中文 — Chinese"


def test_the_panel_builds_and_lists_only_buildable_languages(qtbot, test_config):
    combo = _panel(qtbot, test_config).mining_language_combo
    assert [combo.itemData(i) for i in range(combo.count())] == ["ja", "zh", "he", "id"]
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


def test_the_panel_never_writes_the_language_field(qtbot, test_config):
    """The switch owns ``language``: it has to stash the outgoing language's
    scoped values first, and a second writer would race it. Since T10 the
    panel does have a ``contribute`` (it writes ``script_variant``), but it
    must never touch ``language`` itself.

    Loaded under zh so the combo is visible and ``contribute`` actually takes
    its ``replace()`` path (a ja load leaves ``config`` untouched and would
    make this assertion pass trivially). ``contribute`` is then called with a
    base config naming a DIFFERENT language, so a stray ``language=`` in that
    ``replace()`` would fail this rather than agreeing with the loaded config
    by coincidence.
    """
    config = dataclasses.replace(test_config, language="zh", script_variant="traditional")
    panel = _panel(qtbot, config)

    result = panel.contribute(dataclasses.replace(test_config, language="pt"))

    assert result.script_variant == "traditional"
    assert result.language == "pt"


def test_the_panel_title_matches_its_navigator_label(qtbot):
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)

    assert panel._title_label.text() == "Mining Language"


def test_the_panel_anchors_only_its_three_combos(qtbot):
    """D12: the 22 pack rows are gone; the list itself offers the downloads."""
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)
    ids = {anchor.stable_id for anchor in panel.setting_anchors()}
    assert ids == {
        "mining_language.mining_language_combo",
        "mining_language.script_variant_combo",
        "mining_language.regional_variant_combo",
    }


def test_the_selector_is_searchable_by_english_names(qtbot, german_downloadable):
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)
    text = {anchor.stable_id: anchor.search_text() for anchor in panel.setting_anchors()}
    assert "German" in text["mining_language.mining_language_combo"]
    assert "Chinese" in text["mining_language.mining_language_combo"]


def test_repopulating_keeps_the_selection_and_proposes_nothing(qtbot, test_config):
    """A finished pack download rebuilds the combo under the user's selection."""
    panel = _panel(qtbot, test_config)
    panel.set_mining_language("zh")
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)

    panel._repopulate_mining_languages()

    assert panel.mining_language_combo.currentData() == "zh"
    assert requested == []


@pytest.fixture
def german_downloadable(monkeypatch):
    """de needs its pack here (spaCy is pinned missing above) and 70 MB would fetch it."""
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda code: 70 if code == "de" else None)


def test_a_language_needing_a_download_is_listed_with_a_suffix(qtbot, test_config, german_downloadable):
    combo = _panel(qtbot, test_config).mining_language_combo
    index = combo.findData("de")
    assert index >= 0
    assert combo.itemText(index) == "Deutsch — German (download)"


def test_picking_it_offers_the_download_instead_of_switching(qtbot, test_config, german_downloadable):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)

    panel.mining_language_combo.setCurrentIndex(panel.mining_language_combo.findData("de"))

    assert requested == []
    assert not panel.pending_download_row.isHidden()
    assert panel.pending_download_label.text() == "Deutsch needs a one-time download of about 70 MB."


def test_download_and_switch_downloads_then_switches(qtbot, test_config, german_downloadable, monkeypatch):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    downloads: list[str] = []
    panel.mining_language_requested.connect(requested.append)
    panel.language_pack_download_requested.connect(downloads.append)
    panel.mining_language_combo.setCurrentIndex(panel.mining_language_combo.findData("de"))

    panel.download_and_switch_button.click()
    assert downloads == ["de"]
    assert not panel.download_and_switch_button.isEnabled()

    # The pack landed: de is minable now.
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda _code: None)
    original = language_choices.get_profile
    monkeypatch.setattr(
        language_choices,
        "get_profile",
        lambda code: dataclasses.replace(original(code), unavailable_reason=None) if code == "de" else original(code),
    )
    panel.notify_language_pack_download_finished("de")

    assert requested == ["de"]
    assert panel.pending_download_row.isHidden()


def test_leaving_the_page_cancels_the_switch_not_the_download(qtbot, test_config, german_downloadable, monkeypatch):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)
    panel.mining_language_combo.setCurrentIndex(panel.mining_language_combo.findData("de"))
    panel.download_and_switch_button.click()

    panel.show()
    qtbot.waitExposed(panel)
    panel.hide()

    assert panel.mining_language_combo.currentData() == "ja"
    assert panel.pending_download_row.isHidden()
    panel.notify_language_pack_download_finished("de")
    assert requested == []


def test_picking_another_language_cancels_the_pending_switch(qtbot, test_config, german_downloadable):
    panel = _panel(qtbot, test_config)
    requested: list[str] = []
    panel.mining_language_requested.connect(requested.append)
    combo = panel.mining_language_combo
    combo.setCurrentIndex(combo.findData("de"))

    combo.setCurrentIndex(combo.findData("zh"))

    assert requested == ["zh"]
    assert panel.pending_download_row.isHidden()


def test_a_failed_download_keeps_the_offer_and_its_message(qtbot, test_config, german_downloadable):
    panel = _panel(qtbot, test_config)
    panel.mining_language_combo.setCurrentIndex(panel.mining_language_combo.findData("de"))
    panel.download_and_switch_button.click()

    panel.set_language_pack_status("de", "Download failed: checksum mismatch")
    panel.notify_language_pack_download_finished("de")

    assert not panel.pending_download_row.isHidden()
    assert panel.download_and_switch_button.isEnabled()
    assert panel.pending_download_status.text() == "Download failed: checksum mismatch"
    assert panel.pending_download_status.objectName() == "validation-status"


def test_a_right_to_left_name_does_not_turn_the_offer_around(qtbot, test_config, monkeypatch):
    """An Arabic name leading the line made Qt lay the whole English sentence out right to left."""
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda code: 41 if code == "ar" else None)
    panel = _panel(qtbot, test_config)
    panel.mining_language_combo.setCurrentIndex(panel.mining_language_combo.findData("ar"))

    text = panel.pending_download_label.text()
    assert text == "\u2068العربية\u2069 needs a one-time download of about 41 MB."
