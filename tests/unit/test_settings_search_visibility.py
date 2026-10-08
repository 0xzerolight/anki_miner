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
from dataclasses import replace

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
    ("mining_language.script_variant_combo", "Character Set", "zh"),
    ("anki.reading_tone_color_checkbox", "Colour the reading by tone", "zh"),
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

        tab.jump_to_setting("mining_language.script_variant_combo")  # resolves; must not raise


class TestASwitchReindexes:
    """The gate moves rows; a snapshot taken before it moved is wrong either way.

    Without this the fix would trade one bug for another: the incoming
    language's rows would be on screen and unsearchable.
    """

    def test_the_incoming_languages_rows_become_searchable(self, tab_factory, test_config):
        tab = tab_factory(test_config)
        assert "mining_language.script_variant_combo" not in result_ids(tab, "Character Set")

        # What MainWindow does on a switch: adopt the config (which repaints the
        # panels and re-applies the gate), then re-point the language surfaces.
        tab.update_config(switch_language(test_config, "zh"))
        tab.set_mining_language("zh")

        assert "mining_language.script_variant_combo" in result_ids(tab, "Character Set")

    def test_the_outgoing_languages_rows_stop_being_searchable(self, tab_factory, test_config):
        tab = tab_factory(switch_language(test_config, "zh"))
        assert "anki.reading_tone_color_checkbox" in result_ids(tab, "Colour the reading by tone")

        tab.update_config(switch_language(test_config, "ja"))
        tab.set_mining_language("ja")

        assert "anki.reading_tone_color_checkbox" not in result_ids(tab, "Colour the reading by tone")


_ASR_PANEL = "anki_miner.gui.widgets.panels.subtitles_settings_panel"


def _speech_to_text(monkeypatch, *, engine: bool, model_on_disk: bool) -> None:
    """Pin the ASR probe: engine importable or not, and the selected model on disk or not."""
    monkeypatch.setattr(f"{_ASR_PANEL}._engine.available", lambda: engine)
    monkeypatch.setattr(f"{_ASR_PANEL}._engine.cuda_device_count", lambda: 0)
    monkeypatch.setattr(f"{_ASR_PANEL}.asr_pack_installer.asr_pack_supported", lambda: True)
    monkeypatch.setattr(f"{_ASR_PANEL}.asr_pack_installer.is_installed", lambda: engine)
    monkeypatch.setattr(f"{_ASR_PANEL}.model_manager.is_downloaded", lambda name, root: model_on_disk)


def _probe_settled(qtbot, tab: SettingsTab) -> None:
    panel = tab.subtitles_panel
    qtbot.waitUntil(lambda: not panel._state_in_flight, timeout=5000)


def _shown(tab: SettingsTab, monkeypatch, qtbot) -> None:
    """Show the tab for real isVisible() checks; an unpatched show() opens an AnkiConnect socket."""
    monkeypatch.setattr(tab._anki_probe, "refresh_name_lists", lambda *a, **k: None)
    tab._search_hit_ms = 0
    tab.show()
    qtbot.waitExposed(tab)


def _press_enter_on(tab: SettingsTab, qtbot, query: str) -> None:
    tab.search_box.input.setText(query)
    qtbot.keyClick(tab.search_box.input, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not tab._search_jump_timer.isActive(), timeout=2000)


class TestTheAsrProbeReindexes:
    """Edge2: the index was built before the probe hid the Model download row.

    The probe runs off the GUI thread and lands after construction, so a
    verdict taken at construction describes a row that has since moved.
    """

    def test_a_row_the_probe_hides_stops_being_searchable(self, tab_factory, test_config, monkeypatch, qtbot):
        _speech_to_text(monkeypatch, engine=False, model_on_disk=False)
        tab = tab_factory(test_config)
        _probe_settled(qtbot, tab)

        panel = tab.subtitles_panel
        assert not panel.download_model_button.isVisibleTo(panel)  # the probe really hid the row
        assert "subtitles.model_download" not in result_ids(tab, "model")

    def test_search_after_the_probe_hides_the_model_row_lands_on_a_visible_control(
        self, tab_factory, test_config, monkeypatch, qtbot
    ):
        _speech_to_text(monkeypatch, engine=False, model_on_disk=False)
        tab = tab_factory(test_config)
        _shown(tab, monkeypatch, qtbot)
        _probe_settled(qtbot, tab)

        _press_enter_on(tab, qtbot, "model")

        focused = tab.focusWidget()
        assert focused is not None and focused is not tab.search_box.input
        assert focused.isVisible()


class TestAJumpFocusesSomethingVisible:
    """B3.5b: an anchor's own focus target can be hidden by the control's state."""

    def test_an_empty_chain_jump_focuses_a_visible_chain_control(self, tab_factory, test_config, monkeypatch, qtbot):
        tab = tab_factory(replace(test_config, dictionary_chain=()))
        _shown(tab, monkeypatch, qtbot)
        panel = tab.dictionary_panel
        assert not panel._list.isVisibleTo(panel)  # the empty state hid the list

        tab.jump_to_setting("dictionaries.chain")
        qtbot.waitUntil(lambda: not tab._search_jump_timer.isActive(), timeout=2000)

        assert tab.focusWidget() in (panel._download_recommended_btn, panel._add_btn)
        assert tab.focusWidget().isVisible()

    def test_a_row_with_nothing_to_focus_moves_no_focus(self, tab_factory, test_config, monkeypatch, qtbot):
        """An installed model: the row stays (judge m6) but its Download button is hidden.

        Qt parks focus asked of a hidden widget and hands it over whenever that
        widget next appears, so a later probe offering the download again would
        pull focus out from under the user.
        """
        _speech_to_text(monkeypatch, engine=True, model_on_disk=True)
        tab = tab_factory(test_config)
        _shown(tab, monkeypatch, qtbot)
        _probe_settled(qtbot, tab)
        panel = tab.subtitles_panel
        before = tab.focusWidget()

        tab.jump_to_setting("subtitles.model_download")
        qtbot.waitUntil(lambda: not tab._search_jump_timer.isActive(), timeout=2000)
        assert panel._model_row_widgets[-1].isVisible()
        assert not panel.download_model_button.isVisible()
        panel.download_model_button.show()  # a later probe offers the download again

        assert tab.focusWidget() is before
