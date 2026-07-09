"""Tests for the ReadingTab container.

The former flat ``ReadingTab`` was split into sub-tabs
(:class:`ReadingMangaTab` / :class:`ReadingNovelsTab` /
:class:`ReadingSubtitlesTab`) behind a thin container. The old flat-tab
behaviour now lives in ``test_reading_mining_base.py`` (shared
worker/processor lifecycle) and the per-sub-tab test modules; this module
covers only the container:

- Inner ``QTabWidget`` has exactly three tabs: "Manga" (index 0), "Novels" (1),
  "Subtitles" (2).
- Children are the real sub-tab classes, constructed with the shared presenter /
  stats_service and ``processor=None``.
- ``update_config`` stores config and fans out to every child.
- ``shutdown`` fans out to every child, each guarded independently so an
  exception from one still lets the others run.
- ``release_dictionary_resources`` evaluates ALL children (no short-circuit)
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
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_tab import ReadingTab

_MANGA_CLS = "anki_miner.gui.widgets.reading_tab.ReadingMangaTab"
_NOVELS_CLS = "anki_miner.gui.widgets.reading_tab.ReadingNovelsTab"
_SUBTITLES_CLS = "anki_miner.gui.widgets.reading_tab.ReadingSubtitlesTab"


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig) -> ReadingTab:
    """Construct a ReadingTab with real children (cheap: no worker starts)."""
    widget = ReadingTab(config=test_config, presenter=MagicMock(name="Presenter"))
    qtbot.addWidget(widget)
    return widget


def _mock_children(tab) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Replace all three children with MagicMocks and return them."""
    tab.manga_tab = MagicMock(name="manga")
    tab.novels_tab = MagicMock(name="novels")
    tab.subtitles_tab = MagicMock(name="subtitles")
    return tab.manga_tab, tab.novels_tab, tab.subtitles_tab


# ---------------------------------------------------------------------------
# Inner tab structure
# ---------------------------------------------------------------------------


class TestInnerTabs:
    def test_inner_tab_count(self, tab):
        assert tab._inner_tabs.count() == 3

    def test_inner_tab_labels(self, tab):
        assert tab._inner_tabs.tabText(0) == "Manga"
        assert tab._inner_tabs.tabText(1) == "Novels"
        assert tab._inner_tabs.tabText(2) == "Subtitles"

    def test_manga_tab_is_first(self, tab):
        assert tab._inner_tabs.widget(0) is tab.manga_tab

    def test_novels_tab_is_second(self, tab):
        assert tab._inner_tabs.widget(1) is tab.novels_tab

    def test_subtitles_tab_is_third(self, tab):
        assert tab._inner_tabs.widget(2) is tab.subtitles_tab

    def test_children_are_real_classes(self, tab):
        assert isinstance(tab.manga_tab, ReadingMangaTab)
        assert isinstance(tab.novels_tab, ReadingNovelsTab)
        assert isinstance(tab.subtitles_tab, ReadingSubtitlesTab)


# ---------------------------------------------------------------------------
# Construction contract (shared presenter / stats_service, processor=None)
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_children_share_the_one_presenter(self, tab):
        # One presenter handed to every child (safe: reading tabs never wire
        # presenter signals into their logs).
        assert tab.manga_tab._presenter is tab.novels_tab._presenter is not None
        assert tab.subtitles_tab._presenter is tab.manga_tab._presenter

    def test_children_built_with_none_processor(self, tab):
        assert tab.manga_tab._processor is None
        assert tab.novels_tab._processor is None
        assert tab.subtitles_tab._processor is None

    def test_ctor_args_forwarded_to_children(self, qtbot, test_config):
        """config, processor=None, shared presenter + stats_service reach all."""
        from PyQt6.QtWidgets import QWidget

        presenter = MagicMock(name="Presenter")
        stats = MagicMock(name="StatsService")
        # Patched child classes must return real QWidgets so QTabWidget.addTab
        # accepts them; the class mock still records the constructor call.
        with (
            patch(_MANGA_CLS, return_value=QWidget()) as manga_cls,
            patch(_NOVELS_CLS, return_value=QWidget()) as novels_cls,
            patch(_SUBTITLES_CLS, return_value=QWidget()) as subtitles_cls,
        ):
            widget = ReadingTab(config=test_config, presenter=presenter, stats_service=stats)
            qtbot.addWidget(widget)

            for cls in (manga_cls, novels_cls, subtitles_cls):
                assert cls.call_count == 1
                args, kwargs = cls.call_args
                assert args[0] is test_config
                assert kwargs["processor"] is None
                assert kwargs["presenter"] is presenter
                assert kwargs["stats_service"] is stats


# ---------------------------------------------------------------------------
# open_subtab
# ---------------------------------------------------------------------------


class TestOpenSubtab:
    @pytest.mark.parametrize(("key", "expected_index"), [("manga", 0), ("novels", 1)])
    def test_switches_inner_tab(self, tab, key, expected_index):
        tab._inner_tabs.setCurrentIndex(1 if expected_index == 0 else 0)

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
        tab.manga_tab.update_config = MagicMock()
        tab.novels_tab.update_config = MagicMock()
        tab.subtitles_tab.update_config = MagicMock()

        tab.update_config(new_config)

        tab.manga_tab.update_config.assert_called_once_with(new_config)
        tab.novels_tab.update_config.assert_called_once_with(new_config)
        tab.subtitles_tab.update_config.assert_called_once_with(new_config)

    def test_stores_config(self, tab, test_config):
        new_config = replace(test_config, subtitle_offset=2.5)
        tab.manga_tab.update_config = MagicMock()
        tab.novels_tab.update_config = MagicMock()
        tab.subtitles_tab.update_config = MagicMock()

        tab.update_config(new_config)

        assert tab.config is new_config


# ---------------------------------------------------------------------------
# shutdown fan-out (each child guarded independently)
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_fans_out_to_all_children(self, tab):
        manga, novels, subtitles = _mock_children(tab)

        tab.shutdown()

        manga.shutdown.assert_called_once_with()
        novels.shutdown.assert_called_once_with()
        subtitles.shutdown.assert_called_once_with()

    def test_first_child_raising_does_not_strand_the_rest(self, tab):
        """An exception stopping the first child must not skip the others."""
        manga, novels, subtitles = _mock_children(tab)
        manga.shutdown.side_effect = RuntimeError("boom")

        tab.shutdown()  # must not raise

        manga.shutdown.assert_called_once_with()
        novels.shutdown.assert_called_once_with()
        subtitles.shutdown.assert_called_once_with()


# ---------------------------------------------------------------------------
# release_dictionary_resources truth table (all evaluated, then AND)
# ---------------------------------------------------------------------------


class TestReleaseDictionaryResources:
    @pytest.mark.parametrize(
        ("manga_ret", "novels_ret", "subtitles_ret"),
        [
            (True, True, True),
            (False, True, True),
            (True, False, True),
            (True, True, False),
            (False, False, False),
        ],
    )
    def test_truth_table(self, tab, manga_ret, novels_ret, subtitles_ret):
        manga, novels, subtitles = _mock_children(tab)
        manga.release_dictionary_resources.return_value = manga_ret
        novels.release_dictionary_resources.return_value = novels_ret
        subtitles.release_dictionary_resources.return_value = subtitles_ret

        expected = manga_ret and novels_ret and subtitles_ret
        assert tab.release_dictionary_resources() is expected

    def test_all_children_evaluated_even_when_first_refuses(self, tab):
        """No short-circuit: later children are released even if the first said no."""
        manga, novels, subtitles = _mock_children(tab)
        manga.release_dictionary_resources.return_value = False
        novels.release_dictionary_resources.return_value = True
        subtitles.release_dictionary_resources.return_value = True

        result = tab.release_dictionary_resources()

        assert result is False
        manga.release_dictionary_resources.assert_called_once_with()
        novels.release_dictionary_resources.assert_called_once_with()
        subtitles.release_dictionary_resources.assert_called_once_with()


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
