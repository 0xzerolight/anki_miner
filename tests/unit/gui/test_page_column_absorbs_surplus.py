"""A tall window buys content height, never a taller gap around a heading.

``install_workflow_shell`` moves each screen's ``LogWidget`` into the Activity
drawer, and moving it deletes its layout item from the scrolled content column.
The log was the only expanding item in a pure form's column, so on those screens
nothing was left to take the surplus and ``setWidgetResizable`` kept handing it
to the column anyway -- Qt then split it across every item that merely *may*
grow, and a ``SectionHeader`` given 186px for a 32px title centres that title
inside a symmetric empty band. Opening Activity shrank the viewport and the
bands vanished, which is how the bug was reported: "it fixes itself while it
runs".

The oracles here are deliberately **exactly-one**, not at-least-one. The failure
this guard can introduce is a *second* absorber competing with a real queue
list, and an at-least-one assertion cannot see that -- every healthy page
already satisfies it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.base.sizing import PAGE_SCROLL_OBJECT_NAME
from anki_miner.gui.widgets.base.workflow_action_bar import _column_has_vertical_absorber
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.widgets.enhanced import SectionHeader
from anki_miner.gui.widgets.queue_item_widget import QueueItemWidget
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

#: Every page framed by ``install_workflow_shell`` -- the eight mining screens,
#: the three tools and Card Backfill. Deck Builder is deliberately absent: it
#: never installs the shell, so its log stays in its column and *is* its
#: absorber.
_SHELL_PAGES = (
    "single",
    "batch",
    "youtube",
    "audiobook",
    "manga",
    "novels",
    "subtitles",
    "text",
    "condense",
    "retime",
    "creation",
    "backfill",
    "deckfilter",
)


def _build(name: str, config: AnkiMinerConfig) -> QWidget:
    """Construct one shell page with the least machinery it will accept."""
    reading = {"config": config, "processor": None, "presenter": MagicMock(name="Presenter")}
    builders = {
        "single": lambda: SingleEpisodeTab(config, MagicMock(), MagicMock()),
        "batch": lambda: BatchProcessingTab(config, MagicMock(), MagicMock()),
        "youtube": lambda: YouTubeTab(config, None, MagicMock()),
        "audiobook": lambda: AudiobookTab(config, None, MagicMock()),
        "manga": lambda: ReadingMangaTab(**reading),
        "novels": lambda: ReadingNovelsTab(**reading),
        "subtitles": lambda: ReadingSubtitlesTab(**reading),
        "text": lambda: ReadingTextTab(**reading),
        "condense": lambda: CondenseTab(config, suppress_optional_startup=True),
        "retime": lambda: SubtitleRetimeTab(config, suppress_optional_startup=True),
        "creation": lambda: SubtitleCreationTab(config, suppress_optional_startup=True),
        "backfill": lambda: CardBackfillTab(config),
        "deckfilter": lambda: DeckFilterTab(config),
    }
    return builders[name]()


@pytest.fixture(params=_SHELL_PAGES)
def page(request, qtbot, test_config: AnkiMinerConfig):
    widget = _build(request.param, test_config)
    qtbot.addWidget(widget)
    return request.param, widget


def _column(widget: QWidget) -> QBoxLayout:
    """The scrolled content column of ``widget``'s page shell."""
    scrolls = [s for s in widget.findChildren(QScrollArea) if s.objectName() == PAGE_SCROLL_OBJECT_NAME]
    assert scrolls, "page declares no scrolled column"
    content = scrolls[0].widget()
    assert content is not None
    column = content.layout()
    assert isinstance(column, QBoxLayout)
    return column


def _absorbers(column: QBoxLayout) -> list[int]:
    """Indices of the items in ``column`` that take surplus vertical space."""
    found = []
    for index in range(column.count()):
        item = column.itemAt(index)
        if column.stretch(index) > 0 or (item is not None and item.expandingDirections() & Qt.Orientation.Vertical):
            found.append(index)
    return found


def test_every_shell_page_has_exactly_one_vertical_absorber(page):
    """One item takes the leftover height -- not zero, and not two.

    Zero is the reported bug: the surplus is dealt across the headings instead.
    Two is the regression the fix can introduce, because a blanket trailing
    stretch competes with a queue list and drags it back towards its size hint.
    """
    name, widget = page
    column = _column(widget)
    assert len(_absorbers(column)) == 1, (
        f"{name}: expected exactly one absorber, found indices {_absorbers(column)} " f"of {column.count()} items"
    )


def test_a_tall_window_never_inflates_a_heading(page, qtbot):
    """Headings are chrome. Extra window height is for content, not for gaps.

    Red before the guard on single (+154 on each of two headers), manga and
    novels; green on the nine pages that already had an absorber.
    """
    name, widget = page
    if name in {"backfill", "deckfilter"}:
        # Their first show lazily fetches deck names off a real AnkiConnect. The
        # layout does not depend on the answer, so keep it off the wire.
        widget._decks_requested = True
    widget.resize(1000, 800)
    widget.show()
    qtbot.waitExposed(widget)
    QApplication.processEvents()

    # Hidden chrome (the run receipt, Retry Failed) keeps whatever geometry it
    # last had, which is stale and not what anyone is looking at.
    headings = [
        h
        for h in (
            *widget.findChildren(SectionHeader),
            *(lbl for lbl in widget.findChildren(QLabel) if lbl.objectName() == "heading3"),
        )
        if h.isVisible()
    ]
    assert headings, f"{name}: no visible headings to check"

    inflated = [
        (type(h).__name__, h.height(), h.sizeHint().height())
        for h in headings
        if h.height() > h.sizeHint().height() + 2
    ]
    assert not inflated, f"{name}: surplus height landed on chrome: {inflated}"


#: The queue screens, and how to put one item on each. An empty queue hides its
#: list, which is the item that normally takes the page's surplus height -- so
#: these are the screens where the absorber has to change hands at runtime.
_QUEUE_SCREENS = ("youtube", "audiobook", "batch")


def _fill_queue(name: str, widget: QWidget) -> None:
    """Put one row on ``widget``'s queue, however that screen adds one.

    Through the queue model, not by dropping an item straight on the list:
    ``_recompute_buttons`` asks the model whether there is anything queued, so
    a list-only row would be hidden again on the next recompute.
    """
    if name == "batch":
        widget.queue_panel.register_widget(QueueItemWidget(display_name="Series 1"))
        return
    if name == "youtube":
        item = widget._queue.add("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    else:
        item = widget._queue.add(Path("book.m4b"), Path("book.srt"))
    widget._render_new_item(item)
    widget._recompute_buttons()


#: Height left over once the column fits, before asking who absorbs it. Big
#: enough that the answer cannot be "nobody, by a rounding error".
_SURPLUS_MARGIN = 120


def _grow_until_the_page_fits(widget: QWidget) -> int:
    """Resize ``widget`` taller until its column has surplus. Returns the surplus.

    "Who takes the leftover height" is only a question on a page that *has*
    leftover height. A crowded page in a short window is in the opposite regime:
    the column's size hint exceeds the viewport, so ``qGeomCalc`` shrinks every
    item towards its minimum and nobody is given anything -- the queue list sits
    on its floor and the page scrolls, which is correct behaviour, not the
    absorber bug this module guards.

    Which regime a fixed window size lands in is not a property of the code under
    test: it moves with the interface font (a bare CI runner's DejaVu Sans against
    a desktop's Noto Sans CJK JP), with the UI text scale, and with whatever else
    the page has grown since. At 1000x800 the Batch page needs 813px of column
    against a 747px viewport on DejaVu, and all three queue pages overflow at
    1.5x text -- so a fixed size makes this test an oracle for "does the page
    happen to fit today", which is why it has flipped red and green repeatedly.

    Growing the window until the page fits asks the absorber question in the only
    regime where it is defined, on any font at any scale.
    """
    scrolls = [s for s in widget.findChildren(QScrollArea) if s.objectName() == PAGE_SCROLL_OBJECT_NAME]
    assert scrolls, "page declares no scrolled column"
    scroll = scrolls[0]
    content = scroll.widget()
    assert content is not None

    # Iterate: growing the window can re-wrap text and move the hint again.
    surplus = 0
    for _ in range(8):
        QApplication.processEvents()
        surplus = scroll.viewport().height() - content.sizeHint().height()
        if surplus >= _SURPLUS_MARGIN:
            return surplus
        widget.resize(widget.width(), widget.height() + (_SURPLUS_MARGIN - surplus))
    raise AssertionError(f"page column never fit its window (last surplus {surplus}px)")


def _visible_headings(widget: QWidget) -> list[QWidget]:
    return [
        h
        for h in (
            *widget.findChildren(SectionHeader),
            *(lbl for lbl in widget.findChildren(QLabel) if lbl.objectName() == "heading3"),
        )
        if h.isVisible()
    ]


@pytest.mark.parametrize("name", _QUEUE_SCREENS)
def test_an_empty_queue_collapses_its_list_without_inflating_headings(name, qtbot, test_config):
    """Empty queue: no reserved rows of nothing, and no gap where they were.

    The list going away is only half of it. The list is what took this page's
    leftover height, so the filler has to take over in the same breath or the
    headings inflate exactly the way they did before the shell guard.
    """
    widget = _build(name, test_config)
    qtbot.addWidget(widget)
    widget.resize(1000, 800)
    widget.show()
    qtbot.waitExposed(widget)
    QApplication.processEvents()

    queue_list = widget.queue_panel.list_widget if name == "batch" else widget.list_widget
    assert not queue_list.isVisible(), f"{name}: an empty queue still reserves its list"
    assert widget.page_filler.isVisible(), f"{name}: nothing took the height the list gave up"

    inflated = [
        (h.height(), h.sizeHint().height()) for h in _visible_headings(widget) if h.height() > h.sizeHint().height() + 2
    ]
    assert not inflated, f"{name}: empty queue inflated its headings: {inflated}"


@pytest.mark.parametrize("name", _QUEUE_SCREENS)
def test_the_queue_list_takes_the_height_back_once_it_has_a_row(name, qtbot, test_config):
    """The other direction: a row arrives, the list grows, the filler stands down.

    Without this the collapse could ship as "hide the list forever" and the
    empty-state test above would still pass.
    """
    widget = _build(name, test_config)
    qtbot.addWidget(widget)
    widget.resize(1000, 800)
    widget.show()
    qtbot.waitExposed(widget)
    QApplication.processEvents()

    _fill_queue(name, widget)
    QApplication.processEvents()

    queue_list = widget.queue_panel.list_widget if name == "batch" else widget.list_widget
    assert queue_list.isVisible(), f"{name}: the list stayed hidden with a row in it"
    assert not widget.page_filler.isVisible(), f"{name}: the filler still competes with the list"

    # Holds in both regimes: a page too crowded for its window scrolls, it does
    # not pay for the overflow out of its headings.
    inflated = [
        (h.height(), h.sizeHint().height()) for h in _visible_headings(widget) if h.height() > h.sizeHint().height() + 2
    ]
    assert not inflated, f"{name}: a filled queue inflated its headings: {inflated}"

    # Now the absorber question itself, asked where it means something.
    surplus = _grow_until_the_page_fits(widget)
    QApplication.processEvents()
    assert queue_list.height() > queue_list.minimumHeight(), (
        f"{name}: the list is not taking the page's surplus "
        f"(h={queue_list.height()}, floor={queue_list.minimumHeight()}, surplus={surplus})"
    )

    inflated = [
        (h.height(), h.sizeHint().height()) for h in _visible_headings(widget) if h.height() > h.sizeHint().height() + 2
    ]
    assert not inflated, f"{name}: the grown page inflated its headings: {inflated}"


class TestColumnHasVerticalAbsorber:
    """The predicate, case by case.

    ``addStretch`` versus ``addSpacing`` is the whole reason this helper is not
    a one-line ``any(stretch(i) > 0 ...)``: Qt reports a stretch of zero for
    both, and only ``expandingDirections`` tells them apart.
    """

    def test_a_plain_widget_absorbs_nothing(self, qtbot):
        column = QVBoxLayout()
        column.addWidget(QLabel("x"))
        assert _column_has_vertical_absorber(column) is False

    def test_an_explicit_stretch_factor_absorbs(self, qtbot):
        column = QVBoxLayout()
        column.addWidget(QLabel("x"), 1)
        assert _column_has_vertical_absorber(column) is True

    def test_add_stretch_absorbs_despite_reporting_stretch_zero(self, qtbot):
        column = QVBoxLayout()
        column.addStretch()
        # The trap: Qt keeps that pull in a private flag, so `stretch(0)` is 0.
        assert column.stretch(0) == 0
        assert _column_has_vertical_absorber(column) is True

    def test_add_spacing_is_not_an_absorber(self, qtbot):
        column = QVBoxLayout()
        column.addSpacing(8)
        assert _column_has_vertical_absorber(column) is False
