"""Tests for DeckBuilderTab GUI widget."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.models.deck_build import DeckBuildPreview, DeckBuildRequest, DeckSelectionMode
from anki_miner.utils.file_pairing import FilePair

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = DeckBuilderTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    yield widget
    widget.deleteLater()


def _make_pairs(tmp_path: Path) -> list[FilePair]:
    """Return a list with one canned FilePair whose paths exist on disk."""
    video = tmp_path / "ep01.mkv"
    subtitle = tmp_path / "ep01.ass"
    video.touch()
    subtitle.touch()
    return [FilePair(video=video, subtitle=subtitle)]


def _fake_preview() -> DeckBuildPreview:
    return DeckBuildPreview(
        total_tokens=5000,
        unique_lemmas=800,
        candidate_count=300,
        projected_coverage_pct=91.5,
        known_skipped=50,
        card_count=250,
    )


# ---------------------------------------------------------------------------
# 1. Construction & initial button state
# ---------------------------------------------------------------------------


def test_tab_constructs_without_error(tab):
    assert tab is not None


def test_build_button_disabled_initially(tab):
    assert not tab.build_button.isEnabled()


def test_cancel_button_disabled_initially(tab):
    assert not tab.cancel_button.isEnabled()


def test_preview_button_enabled_initially(tab):
    assert tab.preview_button.isEnabled()


# ---------------------------------------------------------------------------
# 2. Deck-name auto-fill from video folder
# ---------------------------------------------------------------------------


def test_deck_name_autofilled_from_video_folder(tab, tmp_path):
    folder = tmp_path / "Jujutsu Kaisen"
    folder.mkdir()
    tab.video_folder_selector.set_path(str(folder))
    # path_changed fires on set_path → _on_video_folder_changed
    assert tab.deck_name_edit.text() == "Jujutsu Kaisen"
    assert tab._last_auto_deck_name == "Jujutsu Kaisen"


def test_auto_fill_updates_when_still_auto(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    assert tab.deck_name_edit.text() == "Show A"

    # Change folder while name still equals auto value → should update
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "Show B"


def test_manual_deck_name_not_overwritten(tab, tmp_path):
    folder1 = tmp_path / "Show A"
    folder2 = tmp_path / "Show B"
    folder1.mkdir()
    folder2.mkdir()

    tab.video_folder_selector.set_path(str(folder1))
    # Simulate a manual edit by the user
    tab.deck_name_edit.setText("My Custom Deck")
    # _last_auto_deck_name is still "Show A"; current text ≠ auto → no overwrite
    tab.video_folder_selector.set_path(str(folder2))
    assert tab.deck_name_edit.text() == "My Custom Deck"


# ---------------------------------------------------------------------------
# 3. Mode change toggles value widgets
# ---------------------------------------------------------------------------


def test_mode_all_hides_both_spinboxes(tab):
    # Set to something else first, then back to ALL
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    tab.mode_combo.setCurrentIndex(0)  # ALL
    # Use isHidden() — isVisible() requires a fully shown widget hierarchy.
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
# 4. _on_preview_clicked — happy path: worker started with correct request
# ---------------------------------------------------------------------------


def test_preview_clicked_creates_correct_request(tab, tmp_path):
    video_folder = tmp_path / "videos"
    subtitle_folder = tmp_path / "subs"
    video_folder.mkdir()
    subtitle_folder.mkdir()

    pairs = _make_pairs(tmp_path)

    # Set the mode to TOP_N with a specific value
    tab.mode_combo.setCurrentIndex(1)  # TOP_N
    tab.top_n_spinbox.setValue(500)

    # Set up widget values
    tab.deck_name_edit.setText("My Deck")
    tab.collection_filter_checkbox.setChecked(True)

    mock_worker_instance = MagicMock(name="DeckBuilderWorker")

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs,
        ),
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker",
            return_value=mock_worker_instance,
        ) as mock_worker_cls,
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(video_folder))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(subtitle_folder))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    mock_worker_cls.assert_called_once()
    # First positional arg is the DeckBuildRequest
    passed_request: DeckBuildRequest = mock_worker_cls.call_args[0][0]
    assert passed_request.pairs == pairs
    assert passed_request.deck_name == "My Deck"
    assert passed_request.mode == DeckSelectionMode.TOP_N
    assert passed_request.value == 500.0
    assert passed_request.collection_filter is True
    mock_worker_instance.start.assert_called_once()


def test_preview_clicked_disables_preview_enables_cancel(tab, tmp_path):
    pairs = _make_pairs(tmp_path)
    tab.deck_name_edit.setText("Deck")

    mock_worker_instance = MagicMock(name="DeckBuilderWorker")

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs,
        ),
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker",
            return_value=mock_worker_instance,
        ),
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(tmp_path / "v"))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path / "s"))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    assert not tab.preview_button.isEnabled()
    assert tab.cancel_button.isEnabled()


def test_preview_clicked_coverage_mode_passes_float_value(tab, tmp_path):
    pairs = _make_pairs(tmp_path)
    tab.mode_combo.setCurrentIndex(2)  # COVERAGE_PCT
    tab.coverage_spinbox.setValue(85.0)
    tab.deck_name_edit.setText("Deck")

    mock_worker_instance = MagicMock(name="DeckBuilderWorker")

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs,
        ),
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker",
            return_value=mock_worker_instance,
        ) as mock_worker_cls,
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    passed_request: DeckBuildRequest = mock_worker_cls.call_args[0][0]
    assert passed_request.mode == DeckSelectionMode.COVERAGE_PCT
    assert passed_request.value == 85.0


def test_second_preview_cancels_lingering_worker(tab, tmp_path):
    """A previewed-but-not-built worker is cancelled before a new preview starts."""
    pairs = _make_pairs(tmp_path)
    tab.deck_name_edit.setText("Deck")

    worker1 = MagicMock(name="worker1")
    worker2 = MagicMock(name="worker2")

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs,
        ),
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker",
            side_effect=[worker1, worker2],
        ),
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()  # creates worker1 (blocks on gate)
        assert tab._worker is worker1
        tab._on_preview_clicked()  # should cancel worker1, create worker2

    worker1.cancel.assert_called_once()
    assert tab._worker is worker2


# ---------------------------------------------------------------------------
# 5. _on_preview_clicked — validation failures (no worker started)
# ---------------------------------------------------------------------------


def test_preview_no_worker_when_no_video_folder(tab):
    tab.subtitle_folder_selector.get_path = MagicMock(return_value="/some/subs")
    tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)
    # video folder is empty (default)

    with patch("anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker") as mock_worker_cls:
        tab._on_preview_clicked()

    mock_worker_cls.assert_not_called()


def test_preview_no_worker_when_empty_deck_name(tab, tmp_path):
    tab.deck_name_edit.setText("")
    pairs = _make_pairs(tmp_path)

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=pairs,
        ),
        patch("anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker") as mock_worker_cls,
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    mock_worker_cls.assert_not_called()


def test_preview_no_worker_when_empty_pairs(tab, tmp_path):
    tab.deck_name_edit.setText("Deck")

    with (
        patch(
            "anki_miner.gui.widgets.deck_builder_tab.FilePairMatcher.find_pairs_by_episode_number",
            return_value=[],
        ),
        patch("anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker") as mock_worker_cls,
    ):
        tab.video_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.video_folder_selector.is_valid = MagicMock(return_value=True)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    mock_worker_cls.assert_not_called()


def test_preview_no_worker_when_invalid_folders(tab, tmp_path):
    tab.deck_name_edit.setText("Deck")

    with patch("anki_miner.gui.widgets.deck_builder_tab.DeckBuilderWorker") as mock_worker_cls:
        tab.video_folder_selector.get_path = MagicMock(return_value="/nonexistent")
        tab.video_folder_selector.is_valid = MagicMock(return_value=False)
        tab.subtitle_folder_selector.get_path = MagicMock(return_value=str(tmp_path))
        tab.subtitle_folder_selector.is_valid = MagicMock(return_value=True)

        tab._on_preview_clicked()

    mock_worker_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 6. _on_preview_ready — enables Build, shows preview numbers
# ---------------------------------------------------------------------------


def test_preview_ready_enables_build_button(tab):
    tab._on_preview_ready(_fake_preview())
    assert tab.build_button.isEnabled()


def test_preview_ready_shows_preview_frame(tab):
    tab.preview_frame.hide()
    tab._on_preview_ready(_fake_preview())
    assert not tab.preview_frame.isHidden()


def test_preview_ready_populates_labels(tab):
    preview = _fake_preview()
    tab._on_preview_ready(preview)
    assert "5,000" in tab._preview_labels["total_tokens"].text()
    assert "800" in tab._preview_labels["unique_lemmas"].text()
    assert "300" in tab._preview_labels["candidate_count"].text()
    assert "91.5" in tab._preview_labels["projected_coverage_pct"].text()
    assert "50" in tab._preview_labels["known_skipped"].text()
    assert "250" in tab._preview_labels["card_count"].text()


# ---------------------------------------------------------------------------
# 7. _on_build_clicked calls worker.confirm()
# ---------------------------------------------------------------------------


def test_build_clicked_calls_worker_confirm(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    mock_worker.request = MagicMock()
    mock_worker.request.deck_name = "TestDeck"
    tab._worker = mock_worker

    tab._on_build_clicked()

    mock_worker.confirm.assert_called_once()


def test_build_clicked_disables_build_and_preview(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    mock_worker.request = MagicMock()
    mock_worker.request.deck_name = "TestDeck"
    tab._worker = mock_worker
    tab.build_button.setEnabled(True)

    tab._on_build_clicked()

    assert not tab.build_button.isEnabled()
    assert not tab.preview_button.isEnabled()


# ---------------------------------------------------------------------------
# 8. _on_cancel_clicked calls worker.cancel()
# ---------------------------------------------------------------------------


def test_cancel_clicked_calls_worker_cancel(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    tab._worker = mock_worker

    tab._on_cancel_clicked()

    mock_worker.cancel.assert_called_once()


def test_cancel_clicked_restores_buttons(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    tab._worker = mock_worker

    tab._on_cancel_clicked()

    assert tab.preview_button.isEnabled()
    assert not tab.build_button.isEnabled()
    assert not tab.cancel_button.isEnabled()


def test_cancel_clicked_clears_worker_reference(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    tab._worker = mock_worker

    tab._on_cancel_clicked()

    assert tab._worker is None


# ---------------------------------------------------------------------------
# 9. build_finished — restores buttons, logs success
# ---------------------------------------------------------------------------


def test_build_finished_restores_buttons(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    mock_worker.request = MagicMock()
    mock_worker.request.deck_name = "TestDeck"
    tab._worker = mock_worker
    # Simulate mid-run state
    tab.build_button.setEnabled(False)
    tab.preview_button.setEnabled(False)
    tab.cancel_button.setEnabled(True)

    tab._on_build_finished(200, 88.5)

    assert tab.preview_button.isEnabled()
    assert not tab.build_button.isEnabled()
    assert not tab.cancel_button.isEnabled()


def test_build_finished_clears_worker_reference(tab):
    mock_worker = MagicMock(name="DeckBuilderWorker")
    mock_worker.request = MagicMock()
    mock_worker.request.deck_name = "TestDeck"
    tab._worker = mock_worker

    tab._on_build_finished(200, 88.5)

    assert tab._worker is None


# ---------------------------------------------------------------------------
# 10. _on_error — restores buttons, clears worker
# ---------------------------------------------------------------------------


def test_error_restores_buttons(tab):
    tab._worker = MagicMock(name="DeckBuilderWorker")
    tab.preview_button.setEnabled(False)
    tab.cancel_button.setEnabled(True)

    tab._on_error("Something went wrong")

    assert tab.preview_button.isEnabled()
    assert not tab.cancel_button.isEnabled()


def test_error_clears_worker_reference(tab):
    tab._worker = MagicMock(name="DeckBuilderWorker")

    tab._on_error("boom")

    assert tab._worker is None


# ---------------------------------------------------------------------------
# 11. update_config stores new config
# ---------------------------------------------------------------------------


def test_update_config_stores_new_config(tab, test_config):
    from dataclasses import replace

    new_config = replace(test_config, anki_deck_name="updated_deck")
    tab.update_config(new_config)
    assert tab.config is new_config
