"""Field-level settings search and the jump it performs (D11).

Two properties carry the whole feature and both are easy to lose silently:

* the index is built from **translated** strings resolved after the translator
  is installed, so a non-English user searches the words they actually see;
* activating a result lands on the exact control -- right page, scrolled into
  view, focused, briefly marked -- rather than merely opening its panel.

Everything else here guards the edges: a renamed destination still answers to
its old name, searching mutates nothing, and the index can be rebuilt.
"""

from __future__ import annotations

import contextlib

import pytest
from PyQt6.QtCore import Qt, QTranslator
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLineEdit,
    QListWidget,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.settings_search import (
    BREADCRUMB_SEPARATOR,
    SEARCH_HIT_PROPERTY,
    SettingsSearchBox,
    flash_search_hit,
    search,
)
from anki_miner.gui.widgets.settings_tab import SettingsTab


def _build_tab(config: AnkiMinerConfig, qtbot) -> SettingsTab:
    """Construct a SettingsTab the way the other Settings suites do."""
    widget = SettingsTab(config)
    qtbot.addWidget(widget)
    # The highlight is a dwell, not an animation: zero makes it clear on the
    # next event-loop turn so no assertion waits on the clock.
    widget._search_hit_ms = 0
    return widget


def _teardown_tab(widget: SettingsTab, qtbot) -> None:
    widget.shutdown()
    for worker in widget.iter_close_workers():
        if worker is not None:
            worker.wait(3000)
    qtbot.wait(10)
    with contextlib.suppress(RuntimeError):
        widget.deleteLater()


@pytest.fixture
def tab(test_config: AnkiMinerConfig, qtbot):
    widget = _build_tab(test_config, qtbot)
    yield widget
    _teardown_tab(widget, qtbot)


@pytest.fixture
def entries(tab: SettingsTab):
    return tab.setting_search_entries()


def _by_id(entries_, stable_id: str):
    for entry in entries_:
        if entry.anchor.stable_id == stable_id:
            return entry
    raise AssertionError(f"no index entry for {stable_id}")


def _ids(results) -> list[str]:
    return [entry.anchor.stable_id for entry in results]


class TestIndex:
    def test_every_anchor_is_indexed(self, tab, entries):
        assert len(entries) == len(tab.setting_anchors())

    def test_each_page_namespace_is_its_navigator_key(self, tab):
        """The id prefix IS the page key; deep links depend on nothing else."""
        for entry in tab.setting_search_entries():
            if not entry.page_key:
                continue
            assert entry.anchor.stable_id.startswith(f"{entry.page_key}.")
            assert entry.page_key in tab._subtab_index

    def test_a_result_names_the_setting_and_its_group_and_panel(self, entries):
        entry = _by_id(entries, "filtering.frequency_rank_range")

        assert entry.title == "Frequency Rank Range"
        assert entry.breadcrumb == f"Mining{BREADCRUMB_SEPARATOR}Filtering"

    def test_a_tab_level_setting_belongs_to_no_page(self, entries):
        entry = _by_id(entries, "app.check_for_updates")

        assert entry.page_key == ""
        assert entry.breadcrumb == "Settings"

    def test_rebuilding_picks_up_a_newly_registered_anchor(self, tab):
        checkbox = QCheckBox("Synthetic later setting", tab.media_panel)
        tab.media_panel.register_setting("synthetic_later", checkbox, lambda: (checkbox.text(),))

        tab.refresh_setting_search_index()

        assert _ids(search(tab.setting_search_entries(), "Synthetic later")) == ["media.synthetic_later"]


class TestMatching:
    def test_an_empty_query_matches_nothing(self, entries):
        assert search(entries, "   ") == ()

    def test_the_exact_label_ranks_first(self, entries):
        assert _ids(search(entries, "Audio Format"))[0] == "media.audio_format_combo"

    def test_every_word_of_the_query_must_appear(self, entries):
        assert _ids(search(entries, "audio format")) == _ids(search(entries, "format audio"))
        assert search(entries, "audio nonsensetoken") == ()

    def test_helper_text_is_searchable(self, entries):
        """The helper is where a setting explains itself; users quote it."""
        results = _ids(search(entries, "subdecks"))

        assert "filtering.excluded_decks" in results

    def test_matching_ignores_case(self, entries):
        """Doubles as the net for the legacy label: the row was "Max Frequency
        Rank" before the minimum joined it, and that term is carried forward in
        the row's anchor_text."""
        assert _ids(search(entries, "MAX FREQUENCY RANK"))[0] == "filtering.frequency_rank_range"

    def test_either_end_of_the_band_is_searchable_by_name(self, entries):
        """Neither end has a label of its own, so both names live in anchor_text."""
        assert _ids(search(entries, "Min Frequency Rank"))[0] == "filtering.frequency_rank_range"


class TestRenamedDestinations:
    def test_the_old_asr_name_still_finds_transcription_settings(self, entries):
        """D10 renamed the destination; nobody renamed the users' vocabulary."""
        results = _ids(search(entries, "ASR"))

        assert "subtitles.alass_selector" in results

    def test_the_filtering_destination_name_finds_its_settings(self, entries):
        """Filtering kept its name, so the breadcrumb alone has to match it."""
        results = _ids(search(entries, "filtering"))

        assert "filtering.use_i_plus_one_checkbox" in results

    def test_the_old_subtitles_tab_name_still_finds_its_page(self, entries):
        results = _ids(search(entries, "subtitles"))

        assert "subtitles.model_combo" in results


class TestTranslatedIndex:
    """The index must be built from what the translator produced, not literals."""

    _JA = {
        "Frequency Rank Range": "頻度ランク範囲",
        "Mining": "採掘",
        "Filtering": "フィルタリング",
    }

    @pytest.fixture
    def translated_tab(self, test_config: AnkiMinerConfig, qtbot):
        class _Stub(QTranslator):
            def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
                return TestTranslatedIndex._JA.get(source, source)

        app = QApplication.instance()
        assert app is not None
        stub = _Stub()
        app.installTranslator(stub)
        try:
            widget = _build_tab(test_config, qtbot)
            yield widget
            _teardown_tab(widget, qtbot)
        finally:
            app.removeTranslator(stub)

    def test_the_translated_label_finds_the_setting(self, translated_tab):
        results = search(translated_tab.setting_search_entries(), "頻度ランク範囲")

        assert _ids(results)[0] == "filtering.frequency_rank_range"

    def test_the_english_source_string_does_not(self, translated_tab):
        results = search(translated_tab.setting_search_entries(), "Frequency Rank Range")

        assert "filtering.frequency_rank_range" not in _ids(results)

    def test_the_breadcrumb_is_translated_too(self, translated_tab):
        entry = _by_id(translated_tab.setting_search_entries(), "filtering.frequency_rank_range")

        assert entry.title == "頻度ランク範囲"
        assert entry.breadcrumb == f"採掘{BREADCRUMB_SEPARATOR}フィルタリング"


class TestSearchBox:
    def test_no_results_are_listed_until_something_is_typed(self, tab):
        assert not tab.search_box.results.isVisibleTo(tab.search_box)

    def test_a_row_carries_the_setting_and_the_breadcrumb(self, tab):
        tab.search_box.input.setText("Frequency Rank Range")

        item = tab.search_box.results.item(0)
        assert item is not None
        assert "Frequency Rank Range" in item.text()
        assert f"Mining{BREADCRUMB_SEPARATOR}Filtering" in item.text()
        assert item.data(Qt.ItemDataRole.UserRole) == "filtering.frequency_rank_range"

    def test_a_query_with_no_match_lists_no_jumpable_row(self, tab):
        tab.search_box.input.setText("zzz-nothing-matches-this")

        assert tab.search_box.results.count() == 1
        assert tab.search_box.results.item(0).data(Qt.ItemDataRole.UserRole) is None

    def test_clearing_the_query_puts_the_list_away(self, tab):
        tab.search_box.input.setText("Audio Format")
        tab.search_box.input.setText("")

        assert not tab.search_box.results.isVisibleTo(tab.search_box)

    def test_the_arrow_keys_move_the_selection_from_the_input(self, tab, qtbot):
        tab.search_box.input.setText("Field")
        assert tab.search_box.results.count() > 1

        qtbot.keyClick(tab.search_box.input, Qt.Key.Key_Down)

        assert tab.search_box.results.currentRow() == 1

    def test_return_with_nothing_listed_emits_nothing(self, qtbot):
        box = SettingsSearchBox()
        qtbot.addWidget(box)
        box.set_entries(())

        with qtbot.assertNotEmitted(box.setting_activated, wait=10):
            qtbot.keyClick(box.input, Qt.Key.Key_Return)

    def test_the_empty_state_row_cannot_be_jumped_to(self, tab, qtbot):
        tab.search_box.input.setText("zzz-nothing-matches-this")

        with qtbot.assertNotEmitted(tab.search_box.setting_activated, wait=10):
            qtbot.keyClick(tab.search_box.input, Qt.Key.Key_Return)


def _dominant_colour(widget) -> str:
    """The colour covering most of ``widget``, i.e. its rendered fill.

    Deliberately not a single sampled pixel (a checkbox's centre is its
    indicator) and deliberately not whole-image equality: the mark also changes
    the text colour, so an image compare passes on a rule whose *background*
    never applied -- which is the exact defect the test below exists to catch.
    """
    image = widget.grab().toImage()
    counts: dict[str, int] = {}
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            name = image.pixelColor(x, y).name()
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=lambda name: counts[name])


class TestTheMarkIsActuallyVisible:
    """The mark has to survive the stylesheet, not just reach the property.

    A jump focuses its target, and ``QLineEdit:focus, QSpinBox:focus, …`` in
    common.qss already sets a background. That selector outranks a bare
    attribute selector, so a mark styled without a ``:focus`` copy is set,
    cleared, and never once painted -- with every unit test still green.
    """

    @pytest.mark.parametrize("factory", [QSpinBox, QLineEdit, QCheckBox, QListWidget, QComboBox])
    def test_a_focused_control_repaints_when_marked(self, factory, qtbot):
        host = QWidget()
        # Pinned, widget-scoped: assertions below read rendered pixels, so the
        # app-wide stylesheet a sibling test may have left behind must not decide.
        host.setStyleSheet(Theme.get_stylesheet("dark"))
        layout = QVBoxLayout(host)
        control = factory()
        layout.addWidget(control)
        qtbot.addWidget(host)
        host.resize(320, 200)
        host.show()
        control.setFocus()
        qtbot.waitUntil(control.hasFocus, timeout=2000)
        before = _dominant_colour(control)

        flash_search_hit(control, duration_ms=5000)

        assert _dominant_colour(control) != before


class TestJump:
    def _jump(self, tab, qtbot, stable_id: str, query: str):
        tab.search_box.input.setText(query)
        qtbot.keyClick(tab.search_box.input, Qt.Key.Key_Return)
        anchor = _by_id(tab.setting_search_entries(), stable_id).anchor
        qtbot.waitUntil(lambda: tab.focusWidget() is anchor.focus_widget, timeout=2000)
        return anchor

    def test_enter_opens_the_panel_and_focuses_the_control(self, tab, qtbot):
        tab.open_subtab("anki")

        anchor = self._jump(tab, qtbot, "filtering.frequency_rank_range", "Frequency Rank Range")

        assert tab.pages.currentIndex() == tab._subtab_index["filtering"]
        assert tab.nav_list.currentItem().data(Qt.ItemDataRole.UserRole) == "filtering"
        assert tab.focusWidget() is anchor.focus_widget

    def test_the_control_is_scrolled_into_view(self, tab, qtbot, monkeypatch):
        page = tab.pages.widget(tab._subtab_index["filtering"])
        assert isinstance(page, QScrollArea)
        seen: list[object] = []
        monkeypatch.setattr(page, "ensureWidgetVisible", lambda w, *a, **k: seen.append(w))

        anchor = self._jump(tab, qtbot, "filtering.frequency_rank_range", "Frequency Rank Range")

        assert seen == [anchor.scroll_widget]

    def test_the_control_is_briefly_marked_then_unmarked(self, tab, qtbot):
        anchor = self._jump(tab, qtbot, "filtering.frequency_rank_range", "Frequency Rank Range")
        target = anchor.highlight_widget

        # Zero-duration seam: the mark is set, then cleared on the next turn.
        qtbot.waitUntil(lambda: not target.property(SEARCH_HIT_PROPERTY), timeout=2000)
        assert target.property(SEARCH_HIT_PROPERTY) in (False, None)

    def test_the_query_is_cleared_so_the_list_gets_out_of_the_way(self, tab, qtbot):
        self._jump(tab, qtbot, "filtering.frequency_rank_range", "Frequency Rank Range")

        assert tab.search_box.input.text() == ""
        assert not tab.search_box.results.isVisibleTo(tab.search_box)

    def test_a_tab_level_setting_needs_no_page_change(self, tab, qtbot):
        tab.open_subtab("anki")

        self._jump(tab, qtbot, "app.check_for_updates", "Check for updates on startup")

        assert tab.pages.currentIndex() == tab._subtab_index["anki"]
        assert tab.focusWidget() is tab.check_for_updates_checkbox

    def test_an_unknown_id_is_ignored(self, tab, qtbot):
        tab.jump_to_setting("nope.not-a-setting")
        qtbot.wait(10)

    def test_searching_changes_no_setting(self, tab, qtbot):
        before = tab.config

        self._jump(tab, qtbot, "filtering.frequency_rank_range", "Frequency Rank Range")

        assert tab.config == before
        assert tab._settings_dirty is False
        assert not tab._debounce_timer.isActive()
