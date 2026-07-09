"""Tests for the VideoTab container.

The former flat Episode Mining / Batch Mining / YouTube top-level tabs now
nest inside a thin ``VideoTab`` container. The child-tab behaviour keeps its
own modules (``test_single_episode_tab*.py``, ``test_batch_processing_tab*.py``,
``test_youtube_tab.py``); this module covers only the container:

- Inner ``QTabWidget`` has exactly three tabs: "Single" (0), "Batch" (1),
  "YouTube" (2), holding the real sub-tab classes.
- Each child is constructed with its OWN presenter (Single/Batch wire presenter
  signals into their log widgets — sharing would cross-post), YouTube with
  ``processor=None`` + the fetcher, ``stats_service`` reaching all three.
- ``update_config`` stores config and fans out to all three children.
- ``shutdown`` fans out to all three, each guarded independently.
- ``release_dictionary_resources`` evaluates ALL children (no short-circuit)
  and returns their ``and``.
- ``iter_close_workers`` yields exactly the still-live child workers (the
  close-contract divergence from ``ReadingTab``: Single/Batch base
  ``shutdown()`` never joins, so the controller must).
- No ``worker_thread`` attribute; ``open_subtab`` switches the inner tab; the
  class name stays exactly ``"VideoTab"``.

Modelled on ``test_reading_tab.py``.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.video_tab import VideoTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

_SINGLE_CLS = "anki_miner.gui.widgets.video_tab.SingleEpisodeTab"
_BATCH_CLS = "anki_miner.gui.widgets.video_tab.BatchProcessingTab"
_YOUTUBE_CLS = "anki_miner.gui.widgets.video_tab.YouTubeTab"


def _make_tab(qtbot, config: AnkiMinerConfig, **overrides) -> VideoTab:
    kwargs = {
        "episode_presenter": MagicMock(name="EpisodePresenter"),
        "episode_progress": MagicMock(name="EpisodeProgress"),
        "batch_presenter": MagicMock(name="BatchPresenter"),
        "batch_progress": MagicMock(name="BatchProgress"),
        "youtube_presenter": MagicMock(name="YouTubePresenter"),
        "youtube_fetcher": MagicMock(name="YouTubeFetcher"),
    }
    kwargs.update(overrides)
    widget = VideoTab(config, **kwargs)
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig) -> VideoTab:
    """Construct a VideoTab with real children (cheap: no worker starts)."""
    return _make_tab(qtbot, test_config)


# ---------------------------------------------------------------------------
# Inner tab structure
# ---------------------------------------------------------------------------


class TestInnerTabs:
    def test_inner_tab_count(self, tab):
        assert tab._inner_tabs.count() == 3

    def test_inner_tab_labels(self, tab):
        assert tab._inner_tabs.tabText(0) == "Single"
        assert tab._inner_tabs.tabText(1) == "Batch"
        assert tab._inner_tabs.tabText(2) == "YouTube"

    def test_children_order(self, tab):
        assert tab._inner_tabs.widget(0) is tab.single_tab
        assert tab._inner_tabs.widget(1) is tab.batch_tab
        assert tab._inner_tabs.widget(2) is tab.youtube_tab

    def test_children_are_real_classes(self, tab):
        assert isinstance(tab.single_tab, SingleEpisodeTab)
        assert isinstance(tab.batch_tab, BatchProcessingTab)
        assert isinstance(tab.youtube_tab, YouTubeTab)


# ---------------------------------------------------------------------------
# Construction contract (per-child presenters, processor=None for YouTube)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_each_child_gets_its_own_presenter(self, tab):
        # Single/Batch wire presenter signals into their per-tab log widgets, so
        # the three presenters must be pairwise distinct (no cross-posting).
        presenters = (
            tab.single_tab.presenter,
            tab.batch_tab.presenter,
            tab.youtube_tab._presenter,
        )
        assert all(p is not None for p in presenters)
        assert len({id(p) for p in presenters}) == 3

    def test_youtube_built_with_none_processor(self, tab):
        assert tab.youtube_tab._processor is None

    def test_ctor_args_forwarded_to_children(self, qtbot, test_config):
        """Each child gets its own presenter/progress; stats reach all three."""
        from PyQt6.QtWidgets import QWidget

        episode_presenter = MagicMock(name="EpisodePresenter")
        episode_progress = MagicMock(name="EpisodeProgress")
        batch_presenter = MagicMock(name="BatchPresenter")
        batch_progress = MagicMock(name="BatchProgress")
        youtube_presenter = MagicMock(name="YouTubePresenter")
        fetcher = MagicMock(name="YouTubeFetcher")
        stats = MagicMock(name="StatsService")
        # Patched child classes must return real QWidgets so QTabWidget.addTab
        # accepts them; the class mock still records the constructor call.
        with (
            patch(_SINGLE_CLS, return_value=QWidget()) as single_cls,
            patch(_BATCH_CLS, return_value=QWidget()) as batch_cls,
            patch(_YOUTUBE_CLS, return_value=QWidget()) as youtube_cls,
        ):
            widget = _make_tab(
                qtbot,
                test_config,
                episode_presenter=episode_presenter,
                episode_progress=episode_progress,
                batch_presenter=batch_presenter,
                batch_progress=batch_progress,
                youtube_presenter=youtube_presenter,
                youtube_fetcher=fetcher,
                stats_service=stats,
            )
            assert isinstance(widget, VideoTab)

            args, kwargs = single_cls.call_args
            assert args[0] is test_config
            assert args[1] is episode_presenter
            assert args[2] is episode_progress
            assert kwargs["stats_service"] is stats

            args, kwargs = batch_cls.call_args
            assert args[0] is test_config
            assert args[1] is batch_presenter
            assert args[2] is batch_progress
            assert kwargs["stats_service"] is stats

            _, kwargs = youtube_cls.call_args
            assert kwargs["config"] is test_config
            assert kwargs["processor"] is None
            assert kwargs["fetcher"] is fetcher
            assert kwargs["presenter"] is youtube_presenter
            assert kwargs["stats_service"] is stats


# ---------------------------------------------------------------------------
# open_subtab
# ---------------------------------------------------------------------------


class TestOpenSubtab:
    @pytest.mark.parametrize(
        ("key", "expected_index"),
        [("single", 0), ("batch", 1), ("youtube", 2)],
    )
    def test_switches_inner_tab(self, tab, key, expected_index):
        tab._inner_tabs.setCurrentIndex(2 if expected_index == 0 else 0)

        tab.open_subtab(key)

        assert tab._inner_tabs.currentIndex() == expected_index

    def test_unknown_key_is_ignored(self, tab):
        tab._inner_tabs.setCurrentIndex(1)

        tab.open_subtab("definitely-not-a-subtab")

        assert tab._inner_tabs.currentIndex() == 1


# ---------------------------------------------------------------------------
# update_config fan-out
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_propagates_to_all_children(self, tab, test_config):
        new_config = replace(test_config, subtitle_offset=2.5)
        tab.single_tab.update_config = MagicMock()
        tab.batch_tab.update_config = MagicMock()
        tab.youtube_tab.update_config = MagicMock()

        tab.update_config(new_config)

        tab.single_tab.update_config.assert_called_once_with(new_config)
        tab.batch_tab.update_config.assert_called_once_with(new_config)
        tab.youtube_tab.update_config.assert_called_once_with(new_config)

    def test_stores_config(self, tab, test_config):
        new_config = replace(test_config, subtitle_offset=2.5)
        tab.single_tab.update_config = MagicMock()
        tab.batch_tab.update_config = MagicMock()
        tab.youtube_tab.update_config = MagicMock()

        tab.update_config(new_config)

        assert tab.config is new_config


# ---------------------------------------------------------------------------
# shutdown fan-out (each child guarded independently)
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_fans_out_to_all_children(self, tab):
        tab.single_tab = MagicMock(name="single")
        tab.batch_tab = MagicMock(name="batch")
        tab.youtube_tab = MagicMock(name="youtube")

        tab.shutdown()

        tab.single_tab.shutdown.assert_called_once_with()
        tab.batch_tab.shutdown.assert_called_once_with()
        tab.youtube_tab.shutdown.assert_called_once_with()

    def test_earlier_children_raising_do_not_strand_later(self, tab):
        """Exceptions from the first AND second child must not skip the rest."""
        tab.single_tab = MagicMock(name="single")
        tab.single_tab.shutdown.side_effect = RuntimeError("boom")
        tab.batch_tab = MagicMock(name="batch")
        tab.batch_tab.shutdown.side_effect = RuntimeError("boom too")
        tab.youtube_tab = MagicMock(name="youtube")

        tab.shutdown()  # must not raise

        tab.single_tab.shutdown.assert_called_once_with()
        tab.batch_tab.shutdown.assert_called_once_with()
        tab.youtube_tab.shutdown.assert_called_once_with()


# ---------------------------------------------------------------------------
# iter_close_workers (the divergence from ReadingTab)
# ---------------------------------------------------------------------------


class TestIterCloseWorkers:
    def test_yields_nothing_when_no_worker_is_live(self, tab):
        # Fresh tabs have worker_thread=None everywhere.
        assert list(tab.iter_close_workers()) == []

    def test_yields_only_the_live_worker(self, tab):
        # A YouTube-style child whose shutdown() already joined+nulled its
        # worker is skipped; only the still-live Single/Batch worker surfaces.
        live = MagicMock(name="live-worker")
        tab.batch_tab.worker_thread = live

        assert list(tab.iter_close_workers()) == [live]

    def test_yields_all_live_workers_in_child_order(self, tab):
        w_single = MagicMock(name="single-worker")
        w_batch = MagicMock(name="batch-worker")
        w_youtube = MagicMock(name="youtube-worker")
        tab.single_tab.worker_thread = w_single
        tab.batch_tab.worker_thread = w_batch
        tab.youtube_tab.worker_thread = w_youtube

        assert list(tab.iter_close_workers()) == [w_single, w_batch, w_youtube]


# ---------------------------------------------------------------------------
# release_dictionary_resources truth table (all evaluated, then AND)
# ---------------------------------------------------------------------------


class TestReleaseDictionaryResources:
    @pytest.mark.parametrize(
        ("single_ret", "batch_ret", "youtube_ret", "expected"),
        [
            (True, True, True, True),
            (False, True, True, False),
            (True, False, True, False),
            (True, True, False, False),
            (False, False, False, False),
        ],
    )
    def test_truth_table(self, tab, single_ret, batch_ret, youtube_ret, expected):
        tab.single_tab = MagicMock(name="single")
        tab.single_tab.release_dictionary_resources.return_value = single_ret
        tab.batch_tab = MagicMock(name="batch")
        tab.batch_tab.release_dictionary_resources.return_value = batch_ret
        tab.youtube_tab = MagicMock(name="youtube")
        tab.youtube_tab.release_dictionary_resources.return_value = youtube_ret

        assert tab.release_dictionary_resources() is expected

    def test_later_children_evaluated_even_when_first_refuses(self, tab):
        """No short-circuit: every child is released even if an earlier one said no."""
        tab.single_tab = MagicMock(name="single")
        tab.single_tab.release_dictionary_resources.return_value = False
        tab.batch_tab = MagicMock(name="batch")
        tab.batch_tab.release_dictionary_resources.return_value = True
        tab.youtube_tab = MagicMock(name="youtube")
        tab.youtube_tab.release_dictionary_resources.return_value = True

        result = tab.release_dictionary_resources()

        assert result is False
        tab.single_tab.release_dictionary_resources.assert_called_once_with()
        tab.batch_tab.release_dictionary_resources.assert_called_once_with()
        tab.youtube_tab.release_dictionary_resources.assert_called_once_with()


# ---------------------------------------------------------------------------
# Close-contract surface
# ---------------------------------------------------------------------------


class TestCloseContractSurface:
    def test_no_worker_thread_attribute(self, tab):
        # BackgroundTaskController does getattr(tab, "worker_thread", None) AFTER
        # shutdown(); the container must not expose one (children keep theirs).
        assert not hasattr(tab, "worker_thread")

    def test_class_name_is_video_tab(self, tab):
        # main_window._MAIN_TAB_CLASSES["video"] matches by type name.
        assert type(tab).__name__ == "VideoTab"
