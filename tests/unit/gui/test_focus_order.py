"""Tab moves through every screen in reading order (D48-B).

"Keep tabbing sane" is one of the four things D48-B authorises, and the way it
stays sane is measured rather than declared: this walks each screen's real focus
chain and compares it against the order the controls are actually painted in.

That is the guarantee worth having, because the failure it caught is one no
per-screen ``setTabOrder`` call could have prevented -- it *was* a
``setTabOrder`` call. Single Episode chained Process Episode after the offset
field, which was correct until D6 reparented that button into the pinned action
bar at the foot of the page; from then on Tab jumped from the offset field down
to the bar and back up again. An explicit order that names a widget somebody
else may move is a liability, so most screens deliberately carry none and let
construction order -- which the pinned bar builds in reading order -- stand.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

#: Rows within this many pixels count as the same line, so a label and the
#: control beside it are compared left-to-right rather than by a 1px offset.
_ROW_TOLERANCE = 8


def _presenter() -> MagicMock:
    presenter = MagicMock(name="Presenter")
    for signal in ("info_signal", "success_signal", "warning_signal", "error_signal"):
        getattr(presenter, signal).connect = MagicMock()
    return presenter


def _progress_callback() -> MagicMock:
    callback = MagicMock(name="ProgressCallback")
    for signal in ("stage_signal", "start_signal", "progress_signal", "complete_signal", "error_signal"):
        getattr(callback, signal).connect = MagicMock()
    return callback


def _build(name: str, config: AnkiMinerConfig) -> QWidget:
    if name == "single":
        return SingleEpisodeTab(config, _presenter(), _progress_callback())
    if name == "batch":
        return BatchProcessingTab(config, _presenter(), _progress_callback())
    if name == "youtube":
        return YouTubeTab(config, MagicMock(), MagicMock(), MagicMock())
    if name == "audiobook":
        return AudiobookTab(config, MagicMock(), MagicMock())
    if name == "backfill":
        return CardBackfillTab(config)
    if name == "deckfilter":
        return DeckFilterTab(config)
    if name == "download":
        return DownloadTab(config, suppress_optional_startup=True)
    if name in {"condense", "generate", "retime"}:
        return {"condense": CondenseTab, "generate": SubtitleCreationTab, "retime": SubtitleRetimeTab}[name](config)
    return {
        "manga": ReadingMangaTab,
        "novels": ReadingNovelsTab,
        "subtitles": ReadingSubtitlesTab,
        "text": ReadingTextTab,
    }[name](config, MagicMock(), MagicMock())


SCREENS = [
    "single",
    "batch",
    "youtube",
    "audiobook",
    "manga",
    "novels",
    "subtitles",
    "text",
    "condense",
    "generate",
    "retime",
    "backfill",
    "deckfilter",
    "download",
]


def _focusables(root: QWidget) -> set[QWidget]:
    return {
        widget
        for widget in root.findChildren(QWidget)
        if widget.focusPolicy() != Qt.FocusPolicy.NoFocus and widget.isVisibleTo(root) and widget.isEnabled()
    }


def _chain_order(root: QWidget) -> list[QWidget]:
    """The focusable widgets of ``root``, in the order Tab will reach them."""
    allowed = _focusables(root)
    ordered: list[QWidget] = []
    node = root
    for _ in range(5000):
        node = node.nextInFocusChain()
        if node is root:
            break
        if node in allowed and node not in ordered:
            ordered.append(node)
    return ordered


def _reading_key(widget: QWidget, root: QWidget) -> tuple[int, int]:
    point = widget.mapTo(root, widget.rect().topLeft())
    return (point.y() // _ROW_TOLERANCE, point.x())


def _describe(widget: QWidget) -> str:
    """A label a failure message can be read from, not an identity."""
    reader = getattr(widget, "text", None)
    text = reader() if callable(reader) else ""
    if not isinstance(text, str):
        text = ""
    return f"{type(widget).__name__}({widget.objectName() or '-'}|{text[:20]})"


@pytest.fixture(params=SCREENS)
def screen(request, qtbot, test_config: AnkiMinerConfig):
    """One shown screen, wide enough that nothing wraps into a false row order.

    The stubs stay open for the whole test, not just for construction: Card
    Backfill asks Anki for its decks from ``showEvent``, so a patch closed after
    ``_build`` would let the fetch reach a real socket and trip the tripwire.
    """
    with ExitStack() as stack:
        stack.enter_context(patch("anki_miner.gui.utils.service_factory.create_episode_processor", MagicMock()))
        stack.enter_context(patch("anki_miner.gui.widgets.backfill_tab.AnkiService"))
        stack.enter_context(patch("anki_miner.gui.widgets.backfill_tab.FetchDecksWorker"))
        stack.enter_context(patch("anki_miner.gui.widgets.deck_filter_tab.AnkiService"))
        stack.enter_context(patch("anki_miner.gui.widgets.deck_filter_tab.FetchDecksWorker"))
        widget = _build(request.param, test_config)
        qtbot.addWidget(widget)
        widget.resize(1000, 800)
        widget.show()
        qtbot.waitExposed(widget)
        yield request.param, widget


def test_tab_follows_reading_order(screen):
    """Top to bottom, left to right -- including into the pinned action bar."""
    name, widget = screen
    chain = _chain_order(widget)
    assert chain, f"{name} exposes nothing to the keyboard"

    reading = sorted(chain, key=lambda w: _reading_key(w, widget))

    assert [_describe(w) for w in chain] == [_describe(w) for w in reading], f"{name} tabs out of reading order"


def test_the_primary_action_is_reached_last(screen):
    """The run button closes the page, so it closes the tab chain too.

    Only when it is reachable at all: most screens start with the primary
    disabled until an input is chosen, and a disabled button is correctly
    skipped by Tab rather than being a chain-order defect.
    """
    name, widget = screen
    bar = getattr(widget, "action_bar", None)
    if bar is None:
        pytest.skip(f"{name} pins no action bar")
    primary = bar.current_primary()
    if primary is None or primary.focusPolicy() == Qt.FocusPolicy.NoFocus:
        pytest.skip(f"{name} has no focusable primary action")
    if not primary.isEnabled():
        pytest.skip(f"{name} starts with its primary action disabled")

    chain = _chain_order(widget)
    assert chain[-1] is primary, f"{name} does not finish on its primary action, but on {_describe(chain[-1])}"
