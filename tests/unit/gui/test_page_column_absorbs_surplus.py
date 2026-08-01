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

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.base.sizing import PAGE_SCROLL_OBJECT_NAME
from anki_miner.gui.widgets.base.workflow_action_bar import _column_has_vertical_absorber
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.enhanced import SectionHeader
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
    if name == "backfill":
        # Its first show lazily fetches deck names off a real AnkiConnect. The
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
