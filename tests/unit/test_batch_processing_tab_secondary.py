"""Translation-subtitle folder on Video -> Batch's Add Series card (F7 in batch)."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


def _tab(qtbot, config) -> BatchProcessingTab:
    widget = BatchProcessingTab(
        config=config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    return widget


def _point(selector, path: Path, *, valid: bool = True) -> None:
    selector.get_path = MagicMock(return_value=str(path))
    selector.path_or_none = MagicMock(return_value=str(path))
    selector.is_valid = MagicMock(return_value=valid)


def _on(config):
    return replace(config, secondary_subtitle_enabled=True)


def test_rows_are_hidden_until_the_setting_is_on(qtbot, test_config):
    tab = _tab(qtbot, test_config)
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(_on(test_config))
    assert tab.secondary_folder_selector.isVisibleTo(tab)
    assert tab.secondary_offset_row.isVisibleTo(tab)

    tab.update_config(test_config)
    assert not tab.secondary_folder_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)


def test_the_queue_panel_follows_the_same_setting(qtbot, test_config):
    tab = _tab(qtbot, test_config)
    assert tab.queue_panel.secondary_subtitle_enabled is False
    tab.update_config(_on(test_config))
    assert tab.queue_panel.secondary_subtitle_enabled is True


def test_the_translation_folder_reaches_the_new_series(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.secondary_folder == tmp_path / "t"


def test_a_path_left_in_the_hidden_picker_is_ignored(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, test_config)  # feature off
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.secondary_folder is None


def test_an_empty_picker_adds_the_series_without_translations(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    tab.secondary_folder_selector.path_or_none = MagicMock(return_value=None)

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.secondary_folder is None


def test_a_missing_translation_folder_refuses_the_add(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "gone", valid=False)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    item = tab._add_series_from_pickers()

    assert item is None
    assert tab.queue_panel.queue_item_widgets == []
    assert shown
    assert "translation" in shown[0].summary.lower()


def test_the_offset_reaches_the_new_series(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")
    tab.secondary_offset_spinbox.setValue(-1.5)

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.secondary_offset == -1.5


def test_the_offset_is_zero_when_no_folder_was_picked(qtbot, test_config, tmp_path):
    """A dialled-in offset with no folder must not reach the row as a live value."""
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    tab.secondary_folder_selector.path_or_none = MagicMock(return_value=None)
    tab.secondary_offset_spinbox.setValue(-1.5)

    item = tab._add_series_from_pickers()

    assert item is not None
    assert item.secondary_offset == 0.0


def test_the_subtitle_folder_as_translation_folder_refuses_the_add(qtbot, test_config, tmp_path):
    """The panel would refuse it with one log line; the add must say so on screen."""
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "s")
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    item = tab._add_series_from_pickers()

    assert item is None
    assert shown
    assert shown[0].summary == "The translation folder must be different from the subtitle folder."
