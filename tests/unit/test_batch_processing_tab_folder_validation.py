"""Regression: _get_validated_folders must not strip a validated path.

The trailing-space core dump was a validate-raw / use-stripped mismatch:
is_valid() validated the RAW selector text, but the folder handed to the
matcher was .strip()-ed, so a directory whose name ends in a space became a
nonexistent path -> FileNotFoundError -> abort. This asserts the folders come
back verbatim (spaces intact) when the selectors report valid.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = BatchProcessingTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_validated_folders_preserve_trailing_space(tab):
    """A valid selector holding a trailing-space path yields that exact Path."""
    tab.video_folder_selector.input.setText("/media/Season 02 ")
    tab.video_folder_selector._is_valid = True
    tab.subtitle_folder_selector.input.setText("/media/subs ")
    tab.subtitle_folder_selector._is_valid = True

    result = tab._get_validated_folders()

    assert result == (Path("/media/Season 02 "), Path("/media/subs "))


def test_validated_folders_none_when_whitespace_only(tab):
    """Whitespace-only input is treated as empty -> None, no false Path."""
    tab.video_folder_selector.input.setText("   ")
    tab.video_folder_selector._is_valid = True
    tab.subtitle_folder_selector.input.setText("/media/subs")
    tab.subtitle_folder_selector._is_valid = True

    assert tab._get_validated_folders() is None


def test_validated_folders_none_when_invalid(tab):
    """A non-validating selector (red indicator) yields None."""
    tab.video_folder_selector.input.setText("/media/Season 02 ")
    tab.video_folder_selector._is_valid = False
    tab.subtitle_folder_selector.input.setText("/media/subs")
    tab.subtitle_folder_selector._is_valid = True

    assert tab._get_validated_folders() is None
