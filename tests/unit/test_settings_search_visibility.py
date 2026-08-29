"""Settings search never offers a control the active language has hidden.

The index is built once, at the end of ``SettingsTab`` construction, from every
registered anchor. The language gate had already hidden eight of them by then --
the Korean script filters and Hanja field under a zh config, the Chinese
character set, tone colour, pinyin, traditional and measure-word rows under a ja
one -- and search indexed them anyway. Typing "Character Set" on a Japanese
config therefore listed a row that jumped to, focused and flashed a combo box
nobody could see.

Two halves, and the second is what makes the first safe:

* the **searchable** set is only what is on screen for the active language;
* the **address book** behind ``jump_to_setting`` still holds every anchor, so
  System Health's Fix deep links keep resolving by id.

Local helpers rather than test_settings_search.py's: that file is pre-existing
and its fixtures are private to it.
"""

from __future__ import annotations

import contextlib

import pytest
from PyQt6.QtCore import Qt

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.languages.switching import switch_language

#: Anchors the gate hides, with a query that reaches each one, and the language
#: each is on screen for. One per capability family, not the whole set: this
#: guards the predicate, not the capability table.
GATED = [
    ("anki.hanja_field_input", "Hanja Field", "ko"),
    ("anki.measure_word_field_input", "Measure Word Field", "zh"),
    ("filtering.script_variant_combo", "Character Set", "zh"),
    ("filtering.reading_tone_color_checkbox", "Colour the reading by tone", "zh"),
    ("filtering.script_filter_hangul_only", "Hangul", "ko"),
]


@pytest.fixture
def tab_factory(qtbot):
    """Build a SettingsTab per language and shut each one down cleanly."""
    built: list[SettingsTab] = []

    def build(config: AnkiMinerConfig) -> SettingsTab:
        widget = SettingsTab(config)
        qtbot.addWidget(widget)
        built.append(widget)
        return widget

    yield build

    for widget in built:
        widget.shutdown()
        for worker in widget.iter_close_workers():
            if worker is not None:
                worker.wait(3000)
        qtbot.wait(10)
        with contextlib.suppress(RuntimeError):
            widget.deleteLater()


def result_ids(tab: SettingsTab, query: str) -> list[str]:
    """Stable ids of the rows the search box lists for ``query``.

    Driven through the box rather than ``search()`` on purpose: the box is what
    the user picks from, and it is the only surface a hidden anchor must not
    reach.
    """
    tab.search_box.input.setText(query)
    results = tab.search_box.results
    ids = [results.item(row).data(Qt.ItemDataRole.UserRole) for row in range(results.count())]
    tab.search_box.clear()
    return [stable_id for stable_id in ids if stable_id]


def anchor_of(tab: SettingsTab, stable_id: str):
    entry = tab.setting_search_entries()
    for candidate in entry:
        if candidate.stable_id == stable_id:
            return candidate.anchor
    raise AssertionError(f"no anchor for {stable_id}")


class TestHiddenAnchorsAreNotOffered:
    @pytest.mark.parametrize(("stable_id", "query", "language"), GATED)
    def test_a_gated_control_is_unsearchable_under_a_language_that_hides_it(
        self, tab_factory, test_config, stable_id, query, language
    ):
        other = "ja" if language != "ja" else "zh"
        tab = tab_factory(switch_language(test_config, other))

        # The gate really hid it: without this the assertion below could pass
        # on a query that simply matches nothing.
        assert anchor_of(tab, stable_id).widget.isHidden()
        assert stable_id not in result_ids(tab, query)

    @pytest.mark.parametrize(("stable_id", "query", "language"), GATED)
    def test_the_same_control_is_searchable_under_its_own_language(
        self, tab_factory, test_config, stable_id, query, language
    ):
        tab = tab_factory(switch_language(test_config, language))

        assert not anchor_of(tab, stable_id).widget.isHidden()
        assert stable_id in result_ids(tab, query)

    @pytest.mark.parametrize("code", ["ja", "zh", "ko"])
    def test_an_always_visible_setting_is_searchable_in_every_language(self, tab_factory, test_config, code):
        """The predicate must not take the ungated settings down with it."""
        tab = tab_factory(switch_language(test_config, code))

        assert "filtering.frequency_rank_range" in result_ids(tab, "Frequency Rank Range")


class TestTheAddressBookStaysComplete:
    """Hidden is unsearchable, not unaddressable.

    ``jump_to_setting`` resolves System Health's Fix buttons and every other deep
    link by id against the same map. Dropping hidden anchors from it would turn
    those into buttons that silently do nothing.
    """

    def test_every_anchor_still_has_an_entry_under_a_gated_language(self, tab_factory, test_config):
        tab = tab_factory(switch_language(test_config, "zh"))

        assert len(tab.setting_search_entries()) == len(tab.setting_anchors())

    def test_a_hidden_anchor_is_still_reachable_by_id(self, tab_factory, test_config):
        tab = tab_factory(switch_language(test_config, "ja"))

        tab.jump_to_setting("filtering.script_variant_combo")  # resolves; must not raise


class TestASwitchReindexes:
    """The gate moves rows; a snapshot taken before it moved is wrong either way.

    Without this the fix would trade one bug for another: the incoming
    language's rows would be on screen and unsearchable.
    """

    def test_the_incoming_languages_rows_become_searchable(self, tab_factory, test_config):
        tab = tab_factory(test_config)
        assert "filtering.script_variant_combo" not in result_ids(tab, "Character Set")

        # What MainWindow does on a switch: adopt the config (which repaints the
        # panels and re-applies the gate), then re-point the language surfaces.
        tab.update_config(switch_language(test_config, "zh"))
        tab.set_mining_language("zh")

        assert "filtering.script_variant_combo" in result_ids(tab, "Character Set")

    def test_the_outgoing_languages_rows_stop_being_searchable(self, tab_factory, test_config):
        tab = tab_factory(switch_language(test_config, "zh"))
        assert "filtering.reading_tone_color_checkbox" in result_ids(tab, "Colour the reading by tone")

        tab.update_config(switch_language(test_config, "ja"))
        tab.set_mining_language("ja")

        assert "filtering.reading_tone_color_checkbox" not in result_ids(tab, "Colour the reading by tone")
