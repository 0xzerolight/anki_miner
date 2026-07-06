"""Tests for the ReadingTab container.

The former flat ``ReadingTab`` was split into two sub-tabs
(:class:`ReadingMangaTab` / :class:`ReadingNovelsTab`) behind a thin container.
The old flat-tab behaviour now lives in ``test_reading_mining_base.py`` (shared
worker/processor lifecycle), ``test_reading_manga_tab.py``, and
``test_reading_novels_tab.py``; this module covers only the container:

- Inner ``QTabWidget`` has exactly two tabs: "Manga" (index 0), "Novels" (1).
- Children are the real sub-tab classes, constructed with the shared presenter /
  stats_service and ``processor=None``.
- ``update_config`` stores config and fans out to both children.
- ``shutdown`` fans out to both children, each guarded independently so an
  exception from the first still lets the second run.
- ``release_dictionary_resources`` evaluates BOTH children (no short-circuit)
  and returns their ``and``.
- No ``worker_thread`` attribute and no ``iter_close_workers`` method; the class
  name stays exactly ``"ReadingTab"``.

Modelled on ``test_subtitles_tab.py``.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_tab import ReadingTab

_MANGA_CLS = "anki_miner.gui.widgets.reading_tab.ReadingMangaTab"
_NOVELS_CLS = "anki_miner.gui.widgets.reading_tab.ReadingNovelsTab"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig) -> ReadingTab:
    """Construct a ReadingTab with real children (cheap: no worker starts)."""
    widget = ReadingTab(config=test_config, presenter=MagicMock(name="Presenter"))
    qtbot.addWidget(widget)
    return widget


# ---------------------------------------------------------------------------
# Inner tab structure
# ---------------------------------------------------------------------------


class TestInnerTabs:
    def test_inner_tab_count(self, tab):
        assert tab._inner_tabs.count() == 2

    def test_inner_tab_labels(self, tab):
        assert tab._inner_tabs.tabText(0) == "Manga"
        assert tab._inner_tabs.tabText(1) == "Novels"

    def test_manga_tab_is_first(self, tab):
        assert tab._inner_tabs.widget(0) is tab.manga_tab

    def test_novels_tab_is_second(self, tab):
        assert tab._inner_tabs.widget(1) is tab.novels_tab

    def test_children_are_real_classes(self, tab):
        assert isinstance(tab.manga_tab, ReadingMangaTab)
        assert isinstance(tab.novels_tab, ReadingNovelsTab)


# ---------------------------------------------------------------------------
# Construction contract (shared presenter / stats_service, processor=None)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_children_share_the_one_presenter(self, tab):
        # One presenter handed to both children (safe: reading tabs never wire
        # presenter signals into their logs).
        assert tab.manga_tab._presenter is tab.novels_tab._presenter is not None

    def test_children_built_with_none_processor(self, tab):
        assert tab.manga_tab._processor is None
        assert tab.novels_tab._processor is None

    def test_ctor_args_forwarded_to_children(self, qtbot, test_config):
        """config, processor=None, shared presenter + stats_service reach both."""
        from PyQt6.QtWidgets import QWidget

        presenter = MagicMock(name="Presenter")
        stats = MagicMock(name="StatsService")
        # Patched child classes must return real QWidgets so QTabWidget.addTab
        # accepts them; the class mock still records the constructor call.
        with (
            patch(_MANGA_CLS, return_value=QWidget()) as manga_cls,
            patch(_NOVELS_CLS, return_value=QWidget()) as novels_cls,
        ):
            widget = ReadingTab(config=test_config, presenter=presenter, stats_service=stats)
            qtbot.addWidget(widget)

            for cls in (manga_cls, novels_cls):
                assert cls.call_count == 1
                args, kwargs = cls.call_args
                assert args[0] is test_config
                assert kwargs["processor"] is None
                assert kwargs["presenter"] is presenter
                assert kwargs["stats_service"] is stats


# ---------------------------------------------------------------------------
# update_config fan-out
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_propagates_to_both_children(self, tab, test_config):
        new_config = replace(test_config, subtitle_offset=2.5)
        tab.manga_tab.update_config = MagicMock()
        tab.novels_tab.update_config = MagicMock()

        tab.update_config(new_config)

        tab.manga_tab.update_config.assert_called_once_with(new_config)
        tab.novels_tab.update_config.assert_called_once_with(new_config)

    def test_stores_config(self, tab, test_config):
        new_config = replace(test_config, subtitle_offset=2.5)
        tab.manga_tab.update_config = MagicMock()
        tab.novels_tab.update_config = MagicMock()

        tab.update_config(new_config)

        assert tab.config is new_config


# ---------------------------------------------------------------------------
# shutdown fan-out (each child guarded independently)
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_fans_out_to_both_children(self, tab):
        tab.manga_tab = MagicMock(name="manga")
        tab.novels_tab = MagicMock(name="novels")

        tab.shutdown()

        tab.manga_tab.shutdown.assert_called_once_with()
        tab.novels_tab.shutdown.assert_called_once_with()

    def test_first_child_raising_does_not_strand_second(self, tab):
        """An exception stopping the first child must not skip the second."""
        tab.manga_tab = MagicMock(name="manga")
        tab.manga_tab.shutdown.side_effect = RuntimeError("boom")
        tab.novels_tab = MagicMock(name="novels")

        tab.shutdown()  # must not raise

        tab.manga_tab.shutdown.assert_called_once_with()
        tab.novels_tab.shutdown.assert_called_once_with()


# ---------------------------------------------------------------------------
# release_dictionary_resources truth table (both evaluated, then AND)
# ---------------------------------------------------------------------------


class TestReleaseDictionaryResources:
    @pytest.mark.parametrize(
        ("manga_ret", "novels_ret", "expected"),
        [
            (True, True, True),
            (False, True, False),
            (True, False, False),
            (False, False, False),
        ],
    )
    def test_truth_table(self, tab, manga_ret, novels_ret, expected):
        tab.manga_tab = MagicMock(name="manga")
        tab.manga_tab.release_dictionary_resources.return_value = manga_ret
        tab.novels_tab = MagicMock(name="novels")
        tab.novels_tab.release_dictionary_resources.return_value = novels_ret

        assert tab.release_dictionary_resources() is expected

    def test_second_child_evaluated_even_when_first_refuses(self, tab):
        """No short-circuit: the second child is released even if the first said no."""
        tab.manga_tab = MagicMock(name="manga")
        tab.manga_tab.release_dictionary_resources.return_value = False
        tab.novels_tab = MagicMock(name="novels")
        tab.novels_tab.release_dictionary_resources.return_value = True

        result = tab.release_dictionary_resources()

        assert result is False
        tab.manga_tab.release_dictionary_resources.assert_called_once_with()
        tab.novels_tab.release_dictionary_resources.assert_called_once_with()


# ---------------------------------------------------------------------------
# Close-contract surface (no worker_thread, no iter_close_workers)
# ---------------------------------------------------------------------------


class TestCloseContractSurface:
    def test_no_worker_thread_attribute(self, tab):
        # BackgroundTaskController does getattr(tab, "worker_thread", None) AFTER
        # shutdown(); the container must not expose one.
        assert not hasattr(tab, "worker_thread")

    def test_no_iter_close_workers_method(self, tab):
        # A post-shutdown iter would always be vestigial (children joined by
        # shutdown()); the container deliberately omits it.
        assert not hasattr(tab, "iter_close_workers")

    def test_class_name_is_reading_tab(self, tab):
        # main_window._MAIN_TAB_CLASSES["reading"] matches by type name.
        assert type(tab).__name__ == "ReadingTab"
