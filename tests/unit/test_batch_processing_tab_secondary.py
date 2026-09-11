"""Translation-subtitle folder on Video -> Batch, quick path (F7 in batch)."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.utils.file_pairing import FilePair

_MATCHER = "anki_miner.utils.file_pairing.FilePairMatcher.find_pairs_by_episode_number"
# Both are imported lazily inside _start_processing_with_pairs, so the patch
# has to land on the defining module, not on the tab's namespace.
_WORKER = "anki_miner.gui.workers.manual_pair_worker.ManualPairWorkerThread"
_FACTORY = "anki_miner.gui.widgets.batch_processing_tab.create_episode_processor"


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


def test_the_translation_folder_reaches_the_matcher(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")

    with patch(_MATCHER, return_value=[]) as matcher:
        tab._process_pairs()

    assert matcher.call_args.kwargs["secondary_folder"] == tmp_path / "t"


def test_a_path_left_in_the_hidden_picker_is_ignored(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, test_config)  # feature off
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")

    with patch(_MATCHER, return_value=[]) as matcher:
        tab._process_pairs()

    assert matcher.call_args.kwargs["secondary_folder"] is None


def test_an_empty_picker_pairs_without_translations(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    tab.secondary_folder_selector.path_or_none = MagicMock(return_value=None)

    with patch(_MATCHER, return_value=[]) as matcher:
        tab._process_pairs()

    assert matcher.call_args.kwargs["secondary_folder"] is None


def test_a_missing_translation_folder_refuses_the_run(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "gone", valid=False)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    with patch(_MATCHER) as matcher:
        tab._process_pairs()

    matcher.assert_not_called()
    assert shown
    assert "translation" in shown[0].summary.lower()


def test_the_offset_reaches_the_worker(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "t")
    tab.secondary_offset_spinbox.setValue(-1.5)
    pairs = [FilePair(tmp_path / "ep1.mkv", tmp_path / "ep1.ass", tmp_path / "ep1.en.srt")]

    with (
        patch(_MATCHER, return_value=pairs),
        patch(_WORKER, return_value=MagicMock()) as worker_cls,
        patch(_FACTORY, return_value=MagicMock()),
    ):
        tab._process_pairs()

    assert worker_cls.call_args.kwargs["secondary_subtitle_offset"] == -1.5


def test_the_offset_is_zero_when_no_folder_was_picked(qtbot, test_config, tmp_path):
    """A dialled-in offset with no folder must not reach the run as a live value."""
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    tab.secondary_folder_selector.path_or_none = MagicMock(return_value=None)
    tab.secondary_offset_spinbox.setValue(-1.5)
    pairs = [FilePair(tmp_path / "ep1.mkv", tmp_path / "ep1.ass")]

    with (
        patch(_MATCHER, return_value=pairs),
        patch(_WORKER, return_value=MagicMock()) as worker_cls,
        patch(_FACTORY, return_value=MagicMock()),
    ):
        tab._process_pairs()

    assert worker_cls.call_args.kwargs["secondary_subtitle_offset"] == 0.0


def test_the_run_log_counts_the_matched_translations(qtbot, test_config, tmp_path):
    """A partial translation set is normal; the count is the only record that
    some of this run's cards will have an empty Translation field."""
    tab = _tab(qtbot, _on(test_config))
    _point(tab.secondary_folder_selector, tmp_path / "t")
    pairs = [
        FilePair(tmp_path / "ep1.mkv", tmp_path / "ep1.ass", tmp_path / "ep1.en.srt"),
        FilePair(tmp_path / "ep2.mkv", tmp_path / "ep2.ass"),
    ]

    fields = tab._quick_run_log_fields(pairs)

    assert fields["translations"] == 1
    assert fields["secondary_folder"] == str(tmp_path / "t")


def test_the_subtitle_folder_as_translation_folder_refuses_the_run(qtbot, test_config, tmp_path):
    """The matcher would refuse it with one log line; the run must say so on screen."""
    tab = _tab(qtbot, _on(test_config))
    _point(tab.video_folder_selector, tmp_path / "v")
    _point(tab.subtitle_folder_selector, tmp_path / "s")
    _point(tab.secondary_folder_selector, tmp_path / "s")
    shown: list = []
    tab.show_screen_issue = lambda issue, **_kw: shown.append(issue)  # type: ignore[method-assign]

    with patch(_MATCHER) as matcher:
        tab._process_pairs()

    matcher.assert_not_called()
    assert shown
    assert shown[0].summary == "The translation folder must be different from the subtitle folder."
