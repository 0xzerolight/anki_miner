"""Extra monitor width must buy information, not longer input boxes (D5).

On a 2560- or 3440-wide window a single "Video file" text box used to run the
whole width, and a form became one enormous sparse column. Each page now
declares how much horizontal *measure* it can usefully spend: a form gets a
comfortable centred column, a queue or table page gets more, because its
columns really do use the space.

The cap is a character count rendered through the live font, not a pixel
literal, so it tracks the UI text scale exactly the way ``apply_button_size``
and ``metric_row_height`` do -- one rule instead of a magic number repeated on
fourteen screens.

Complements ``test_layout_hostile_scale.py``: that file drives the *narrow*
hostile cell (1024x768, longer translations, 150% text); this one drives the
wide cell and the caps.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.base.sizing import (
    PAGE_SCROLL_OBJECT_NAME,
    PageWidth,
    configure_scrolled_page,
    page_width_cap,
)
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.settings_tab import SettingsTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

#: The declared minimum window plus the two monitor widths the complaint named.
VIEWPORT_WIDTHS = (1024, 1920, 2560, 3440)

#: Every scrolled page in the app and the width class it must declare.
#: Forms are a label beside its control, so extra width adds nothing; queues,
#: tables and analytics genuinely use it. Deck Builder is out of scope (W5 owns
#: its shell) and is deliberately absent.
EXPECTED_PAGE_WIDTH = {
    SingleEpisodeTab: PageWidth.FORM,
    ReadingMangaTab: PageWidth.FORM,
    ReadingNovelsTab: PageWidth.FORM,
    ReadingSubtitlesTab: PageWidth.FORM,
    ReadingTextTab: PageWidth.FORM,
    SubtitleCreationTab: PageWidth.FORM,
    SubtitleRetimeTab: PageWidth.FORM,
    CondenseTab: PageWidth.FORM,
    SettingsTab: PageWidth.FORM,
    BatchProcessingTab: PageWidth.DATA,
    YouTubeTab: PageWidth.DATA,
    AudiobookTab: PageWidth.DATA,
    AnalyticsTab: PageWidth.DATA,
    CardBackfillTab: PageWidth.DATA,
}

PAGE_NAMES = sorted(cls.__name__ for cls in EXPECTED_PAGE_WIDTH)


def _build_page(name: str, config):
    """Construct one page with the least machinery it will accept."""
    reading = {"config": config, "processor": None, "presenter": MagicMock(name="Presenter")}
    builders = {
        "SingleEpisodeTab": lambda: SingleEpisodeTab(config, MagicMock(), MagicMock()),
        "ReadingMangaTab": lambda: ReadingMangaTab(**reading),
        "ReadingNovelsTab": lambda: ReadingNovelsTab(**reading),
        "ReadingSubtitlesTab": lambda: ReadingSubtitlesTab(**reading),
        "ReadingTextTab": lambda: ReadingTextTab(**reading),
        "SubtitleCreationTab": lambda: SubtitleCreationTab(config, suppress_optional_startup=True),
        "SubtitleRetimeTab": lambda: SubtitleRetimeTab(config, suppress_optional_startup=True),
        "CondenseTab": lambda: CondenseTab(config, suppress_optional_startup=True),
        "SettingsTab": lambda: SettingsTab(config),
        "BatchProcessingTab": lambda: BatchProcessingTab(config, MagicMock(), MagicMock()),
        "YouTubeTab": lambda: YouTubeTab(config, None, MagicMock()),
        "AudiobookTab": lambda: AudiobookTab(config, None, MagicMock()),
        "AnalyticsTab": lambda: AnalyticsTab(MagicMock()),
        "CardBackfillTab": lambda: CardBackfillTab(config),
    }
    return builders[name]()


def _page_shells(page) -> list[QScrollArea]:
    """The scroll areas ``configure_scrolled_page`` claimed on this page."""
    return page.findChildren(QScrollArea, PAGE_SCROLL_OBJECT_NAME)


def _expected_width(name: str) -> PageWidth:
    return next(kind for cls, kind in EXPECTED_PAGE_WIDTH.items() if cls.__name__ == name)


@pytest.fixture
def quiet_show(monkeypatch):
    """Silence the three pages that fetch from Anki the first time they show."""
    monkeypatch.setattr(SettingsTab, "showEvent", lambda self, event: QWidget.showEvent(self, event))
    monkeypatch.setattr(AnalyticsTab, "refresh_data", lambda self, *a, **k: None)
    monkeypatch.setattr(CardBackfillTab, "_load_decks", lambda self, *a, **k: None)


def _scrolled_page(kind: PageWidth, *, content_minimum: int = 0):
    """Build the shell every tab builds: a scroll area around one column."""
    host = QWidget()
    outer = QVBoxLayout(host)
    outer.setContentsMargins(0, 0, 0, 0)

    scroll = QScrollArea()
    content = QWidget()
    column = QVBoxLayout(content)
    column.addWidget(QLabel("Video file"))
    if content_minimum:
        content.setMinimumWidth(content_minimum)

    configure_scrolled_page(scroll, content, kind)
    outer.addWidget(scroll)
    return host, scroll, content


class TestTheCapIsAReadableMeasure:
    def test_data_pages_get_more_measure_than_forms(self, qtbot):
        probe = QWidget()
        qtbot.addWidget(probe)

        assert page_width_cap(probe, PageWidth.DATA) > page_width_cap(probe, PageWidth.FORM)

    def test_the_cap_grows_with_the_text_scale(self, qtbot, font_scale):
        """A pixel constant would give a 150% user the same column and a third
        of the characters in it."""
        font_scale(1.0)
        normal = QWidget()
        qtbot.addWidget(normal)
        at_100 = page_width_cap(normal, PageWidth.FORM)

        font_scale(1.5)
        larger = QWidget()
        qtbot.addWidget(larger)
        at_150 = page_width_cap(larger, PageWidth.FORM)

        assert at_150 > at_100

    def test_the_measure_itself_is_constant_across_scales(self, qtbot, font_scale):
        """The pixel count changes; the number of characters it holds does not."""
        measures = []
        for scale in (0.8, 1.0, 1.5):
            font_scale(scale)
            probe = QWidget()
            qtbot.addWidget(probe)
            probe.ensurePolished()
            measures.append(page_width_cap(probe, PageWidth.FORM) // probe.fontMetrics().horizontalAdvance("0"))

        assert len(set(measures)) == 1, f"measure drifted across scales: {measures}"


class TestConfigureScrolledPage:
    @pytest.mark.parametrize("viewport_width", VIEWPORT_WIDTHS)
    @pytest.mark.parametrize("kind", list(PageWidth), ids=lambda k: k.name)
    def test_content_is_capped_and_centred(self, qtbot, viewport_width, kind):
        host, scroll, content = _scrolled_page(kind)
        qtbot.addWidget(host)
        host.resize(viewport_width, 768)
        host.show()
        qtbot.waitExposed(host)

        cap = page_width_cap(content, kind)
        assert content.width() == min(scroll.viewport().width(), cap)

        left = content.x()
        right = scroll.viewport().width() - content.x() - content.width()
        assert abs(left - right) <= 1, f"gutters {left} vs {right}"

    @pytest.mark.parametrize("kind", list(PageWidth), ids=lambda k: k.name)
    def test_a_wide_window_does_not_stretch_the_column(self, qtbot, kind):
        """The complaint itself: at 3440 the input must not be 3440 wide."""
        host, scroll, content = _scrolled_page(kind)
        qtbot.addWidget(host)
        host.resize(3440, 768)
        host.show()
        qtbot.waitExposed(host)

        assert content.width() < scroll.viewport().width()

    @pytest.mark.parametrize("kind", list(PageWidth), ids=lambda k: k.name)
    def test_the_minimum_window_needs_no_horizontal_scrolling(self, qtbot, kind):
        host, scroll, content = _scrolled_page(kind)
        qtbot.addWidget(host)
        host.resize(1024, 768)
        host.show()
        qtbot.waitExposed(host)

        assert content.width() <= scroll.viewport().width()
        assert scroll.horizontalScrollBar().maximum() == 0

    def test_vertical_scrolling_survives_the_cap(self, qtbot):
        host, scroll, content = _scrolled_page(PageWidth.FORM)
        qtbot.addWidget(host)
        column = content.layout()
        assert column is not None
        for index in range(60):
            column.addWidget(QLabel(f"row {index}"))
        host.resize(1920, 400)
        host.show()
        qtbot.waitExposed(host)

        assert scroll.verticalScrollBar().maximum() > 0

    def test_the_cap_never_narrows_content_below_its_own_minimum(self, qtbot):
        """A cap that squeezes below what the page needs would clip it behind
        the disabled horizontal scrollbar. Reachable for real at 0.8x text,
        where the FORM cap drops under the widest Settings panel's minimum.
        """
        host, scroll, content = _scrolled_page(PageWidth.FORM, content_minimum=3000)
        qtbot.addWidget(host)
        host.resize(1920, 768)
        host.show()
        qtbot.waitExposed(host)

        assert content.maximumWidth() >= 3000
        assert content.width() >= content.minimumSizeHint().width()

    def test_the_shell_keeps_what_every_page_already_set(self, qtbot):
        host, scroll, content = _scrolled_page(PageWidth.FORM)
        qtbot.addWidget(host)

        assert scroll.widgetResizable() is True
        assert scroll.widget() is content
        assert scroll.objectName() == PAGE_SCROLL_OBJECT_NAME


class TestEveryPageDeclaresItsWidth:
    """The width class belongs to the page, so a new screen cannot quietly
    inherit somebody else's measure."""

    @pytest.mark.parametrize("name", PAGE_NAMES)
    def test_declaration_matches_the_page_type(self, name, test_config, qtbot):
        page = _build_page(name, test_config)
        qtbot.addWidget(page)

        assert type(page).PAGE_WIDTH is _expected_width(name)

    @pytest.mark.parametrize("name", PAGE_NAMES)
    def test_the_page_shell_went_through_the_helper(self, name, test_config, qtbot):
        page = _build_page(name, test_config)
        qtbot.addWidget(page)

        assert _page_shells(page), f"{name} builds a scroll area the helper never saw"


class TestRealPagesRespectTheirCap:
    """The primitive is easy to get right in isolation; the value is that the
    real shells route through it."""

    @pytest.mark.parametrize("name", PAGE_NAMES)
    def test_a_wide_window_leaves_gutters(self, name, test_config, qtbot, quiet_show):
        page = _build_page(name, test_config)
        qtbot.addWidget(page)
        page.resize(3440, 900)
        page.show()
        qtbot.waitExposed(page)

        kind = _expected_width(name)
        checked = 0
        for scroll in _page_shells(page):
            content = scroll.widget()
            if content is None or not scroll.isVisible():
                continue
            checked += 1
            cap = page_width_cap(content, kind)
            assert content.width() <= cap, f"{name}: {content.width()} > cap {cap}"
            assert content.width() < scroll.viewport().width()
        page.hide()

        assert checked, f"{name}: no visible page shell to measure"

    @pytest.mark.parametrize("name", PAGE_NAMES)
    def test_no_horizontal_scrolling_at_the_declared_minimum(self, name, test_config, qtbot, quiet_show):
        page = _build_page(name, test_config)
        qtbot.addWidget(page)
        page.resize(1024, 768)
        page.show()
        qtbot.waitExposed(page)

        for scroll in _page_shells(page):
            if not scroll.isVisible():
                continue
            overflow = scroll.horizontalScrollBar().maximum()
            assert overflow == 0, f"{name}: content overflows by {overflow}px"
        page.hide()
