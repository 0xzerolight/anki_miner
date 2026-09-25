"""Tests for DeckBuilderTab's inputs and page contract (Task 6).

Preview/Build/Cancel are stubs until a later task wires ``DeckBuilderWorker``
to this screen, so this file covers only what Task 6 actually builds: the
folder/settings inputs, their seeding and persistence, ``_build_request``'s
validation, and the pinned action bar / log wiring every mining screen shares.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.presenters import GUIPresenter
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.models.deck_build import DeckSelectionMode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tab(qtbot, config) -> DeckBuilderTab:
    widget = DeckBuilderTab(
        config=config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(qtbot, test_config) -> DeckBuilderTab:
    return _tab(qtbot, test_config)


# ---------------------------------------------------------------------------
# Deck-name auto-fill from video folder
# ---------------------------------------------------------------------------


def test_deck_name_autofilled_from_video_folder(tab, tmp_path):
    folder = tmp_path / "Jujutsu Kaisen"
    folder.mkdir()
    tab.video_folder_selector.set_path(str(folder))
    # path_changed fires on set_path -> _on_video_folder_changed
    assert tab.deck_name_edit.text() == "Jujutsu Kaisen"
    assert tab._last_auto_deck_name == "Jujutsu Kaisen"


def test_auto_fill_updates_when_still_auto(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    assert tab.deck_name_edit.text() == "Show A"

    # Change folder while name still equals auto value -> should update.
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "Show B"


def test_manual_deck_name_not_overwritten(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    # Simulate a manual edit by the user.
    tab.deck_name_edit.setText("My Custom Deck")
    # _last_auto_deck_name is still "Show A"; current text != auto -> no overwrite.
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "My Custom Deck"


# ---------------------------------------------------------------------------
# Mode <-> spinbox visibility
# ---------------------------------------------------------------------------


def test_mode_all_hides_both_spinboxes(tab):
    # Set to something else first, then back to ALL.
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    tab.mode_combo.setCurrentIndex(0)  # ALL
    # isHidden(), not isVisible(): the latter requires a fully shown hierarchy.
    assert tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_mode_top_n_shows_n_spinbox(tab):
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    assert not tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_mode_coverage_pct_shows_coverage_spinbox(tab):
    tab.mode_combo.setCurrentIndex(2)  # COVERAGE_PCT
    assert tab.top_n_spinbox.isHidden()
    assert not tab.coverage_spinbox.isHidden()


# ---------------------------------------------------------------------------
# Seeding from config
# ---------------------------------------------------------------------------


def test_seeds_defaults_from_config(tab, test_config):
    assert tab.mode_combo.currentData() == DeckSelectionMode.ALL
    assert tab.top_n_spinbox.value() == test_config.deck_builder_top_n
    assert tab.coverage_spinbox.value() == test_config.deck_builder_coverage_pct
    assert tab.skip_known_checkbox.isChecked() == test_config.deck_builder_skip_known
    assert tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()


def test_a_restored_top_n_mode_opens_with_its_spinbox_shown(qtbot, test_config):
    """Regression: a restored non-default mode must not open with a hidden control."""
    config = replace(test_config, deck_builder_mode="top_n", deck_builder_top_n=250)
    tab = _tab(qtbot, config)

    assert tab.mode_combo.currentData() == DeckSelectionMode.TOP_N
    assert not tab.top_n_spinbox.isHidden()
    assert tab.coverage_spinbox.isHidden()
    assert tab.top_n_spinbox.value() == 250


# ---------------------------------------------------------------------------
# persist_run_options on each control
# ---------------------------------------------------------------------------


def test_mode_change_persists(tab):
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    assert tab.config.deck_builder_mode == "top_n"


def test_top_n_change_persists(tab):
    tab.top_n_spinbox.setValue(42)
    assert tab.config.deck_builder_top_n == 42


def test_coverage_change_persists(tab):
    tab.mode_combo.setCurrentIndex(2)  # COVERAGE_PCT
    tab.coverage_spinbox.setValue(77.5)
    assert tab.config.deck_builder_coverage_pct == 77.5


def test_skip_known_change_persists(tab):
    original = tab.skip_known_checkbox.isChecked()
    tab.skip_known_checkbox.setChecked(not original)
    assert tab.config.deck_builder_skip_known == (not original)


def test_review_words_change_persists(tab):
    original = tab.review_words_checkbox.isChecked()
    tab.review_words_checkbox.setChecked(not original)
    assert tab.config.review_words_before_mining == (not original)


def test_seeding_a_restored_mode_does_not_re_persist(qtbot, test_config):
    """Constructing from a non-default config must not immediately re-save it."""
    config = replace(test_config, deck_builder_mode="top_n", deck_builder_top_n=250)
    seen = []
    tab = DeckBuilderTab(config=config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)
    tab.run_options_changed.connect(seen.append)

    tab.update_config(config)

    assert seen == []


# ---------------------------------------------------------------------------
# Translation folder gated on the shared setting (F7)
# ---------------------------------------------------------------------------


def test_translation_rows_follow_secondary_subtitle_enabled(tab, test_config):
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(replace(test_config, secondary_subtitle_enabled=True))
    assert tab.secondary_folder_selector.isVisibleTo(tab)
    assert tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(test_config)
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)


# ---------------------------------------------------------------------------
# Folder history key (D7)
# ---------------------------------------------------------------------------


def test_every_folder_selector_uses_the_deckbuilder_history_key(tab):
    for selector in (
        tab.video_folder_selector,
        tab.subtitle_folder_selector,
        tab.secondary_folder_selector,
    ):
        assert selector._history_key == "video.deckbuilder.inputs"


# ---------------------------------------------------------------------------
# _build_request validation
# ---------------------------------------------------------------------------


def _setup_no_folders(tab: DeckBuilderTab, tmp_path: Path) -> None:
    pass


def _setup_missing_folder(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.video_folder_selector.set_path(str(tmp_path / "gone"))
    tab.subtitle_folder_selector.set_path(str(tmp_path))


def _setup_empty_deck_name(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.video_folder_selector.set_path(str(tmp_path))
    tab.subtitle_folder_selector.set_path(str(tmp_path))
    tab.deck_name_edit.setText("")


def _setup_bad_translation_folder(tab: DeckBuilderTab, tmp_path: Path) -> None:
    tab.update_config(replace(tab.config, secondary_subtitle_enabled=True))
    tab.video_folder_selector.set_path(str(tmp_path))
    tab.subtitle_folder_selector.set_path(str(tmp_path))
    tab.deck_name_edit.setText("Deck")
    tab.secondary_folder_selector.set_path(str(tmp_path / "gone"))


@pytest.mark.parametrize(
    "setup",
    [_setup_no_folders, _setup_missing_folder, _setup_empty_deck_name, _setup_bad_translation_folder],
    ids=["no_folders", "missing_folder", "empty_deck_name", "bad_translation_folder"],
)
def test_build_request_refusals_raise_screen_issue(tab, tmp_path, setup):
    setup(tab, tmp_path)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    assert tab._build_request() is None
    assert shown


def test_build_request_returns_a_complete_request_on_success(tab, tmp_path):
    video_folder = tmp_path / "video"
    subtitle_folder = tmp_path / "subs"
    video_folder.mkdir()
    subtitle_folder.mkdir()

    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(subtitle_folder))
    tab.deck_name_edit.setText("  My Deck  ")
    tab.skip_known_checkbox.setChecked(True)
    tab.review_words_checkbox.setChecked(True)
    tab.offset_spinbox.setValue(1.5)

    request = tab._build_request()

    assert request is not None
    assert request.video_folder == video_folder
    assert request.subtitle_folder == subtitle_folder
    # Stripped, not the raw field text.
    assert request.deck_name == "My Deck"
    assert request.skip_known is True
    assert request.review is True
    assert request.subtitle_offset == 1.5
    assert request.secondary_folder is None


# ---------------------------------------------------------------------------
# Pinned action bar (D6)
# ---------------------------------------------------------------------------


def test_action_bar_primary_is_build_and_secondaries_are_preview_cancel(tab):
    assert tab.action_bar is not None
    assert tab.action_bar.current_primary() is tab.build_button
    assert tab.action_bar._secondary == [tab.preview_button, tab.cancel_button]


def test_build_button_disabled_and_preview_enabled_initially(tab):
    assert not tab.build_button.isEnabled()
    assert tab.preview_button.isEnabled()
    assert not tab.cancel_button.isEnabled()


# ---------------------------------------------------------------------------
# Presenter -> log wiring
# ---------------------------------------------------------------------------


def test_presenter_messages_reach_the_log(qtbot, test_config):
    presenter = GUIPresenter()
    widget = DeckBuilderTab(config=test_config, presenter=presenter, progress_callback=MagicMock())
    qtbot.addWidget(widget)

    presenter.info_signal.emit("info line")
    presenter.success_signal.emit("success line")
    presenter.warning_signal.emit("warning line")
    presenter.error_signal.emit("error line")

    messages = [entry.message for entry in widget.log_widget._entries]
    assert messages == ["info line", "success line", "warning line", "error line"]


# ---------------------------------------------------------------------------
# Config update
# ---------------------------------------------------------------------------


def test_update_config_stores_new_config(tab, test_config):
    new_config = replace(test_config, anki_deck_name="updated_deck")
    tab.update_config(new_config)
    assert tab.config is new_config
