"""Layout regressions found by driving the real GUI at a hostile cell.

"Hostile" = 1024x768 (the app's own WINDOW_MIN_* contract) with ui_font_scale 1.5.
Every assertion here was RED before its fix and is the falsifiable oracle for it --
the audit's mechanical checkers are throwaway, these are not.

Why font scale and not ui_zoom: QT_SCALE_FACTOR is layout-INERT (measured -- sizeHints
are byte-identical at 1.0 and 1.5, only the device-pixel ratio changes). The knob that
actually stresses layout is the font-only ui_font_scale, and because
gui/utils/fonts.py bakes pixel_size * Theme.get_font_scale() at widget construction, it
must be set BEFORE the widget is built.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture
def hostile_scale(font_scale):
    """Build widgets at 150% text size, then restore.

    Restoration goes through the shared ``font_scale`` fixture (conftest): it
    puts the application stylesheet back exactly as it was, because merely
    re-applying the original scale leaves a QSS font-size rule installed that
    overrides per-widget fonts in later modules.
    """
    font_scale(1.5)
    yield


@pytest.fixture
def longer_translations():
    """Install a translator that lengthens every string.

    This is what reproduces the clipping defects below: a column was sized from
    the ENGLISH literal while the label rendered TRANSLATED, so any locale whose
    strings are longer than English clipped. A pseudo-locale is a
    locale-independent stand-in for German/Russian/Vietnamese.
    """
    from PyQt6.QtCore import QTranslator

    class _Longer(QTranslator):
        def translate(self, context, source, disambiguation=None, n=-1):
            return f"{source}·························"[: len(source) + 25]

    tr = _Longer()
    QApplication.instance().installTranslator(tr)
    yield
    QApplication.instance().removeTranslator(tr)


def _clip_report(label) -> str | None:
    """Return a description if ``label`` renders clipped, else ``None``.

    The one need-vs-available measurement in this file. ``contentsRect`` is the
    box the text actually gets (QSS padding already subtracted); the 2px slack
    absorbs the rounding between ``horizontalAdvance`` and the painted run.
    """
    need = label.fontMetrics().horizontalAdvance(label.text())
    avail = label.contentsRect().width()
    if avail > 0 and need > avail + 2:
        return f"{label.text()!r} needs {need}px in {avail}px"
    return None


class TestExplicitMinimumNeverBelowLayoutMinimum:
    """The 9301c581 inverse trap: an explicit minimum SMALLER than the layout's own
    minimum silently overrides the larger layout-derived value, so fixed chrome
    compresses real content. It scales with text size, which is why it shows up on
    hi-DPI Windows and not on a 1x Linux dev box.
    """

    def test_section_header_respects_layout_minimum(self, qtbot, hostile_scale):
        from anki_miner.gui.widgets.enhanced.section_header import SectionHeader

        w = SectionHeader("A reasonably long section title")
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        QApplication.processEvents()

        layout = w.layout()
        assert layout is not None
        demanded = layout.minimumSize().height()
        assert w.minimumHeight() == 0 or w.minimumHeight() >= demanded, (
            f"explicit minimumHeight({w.minimumHeight()}) is below the layout's own "
            f"minimum ({demanded}) - the 9301c581 trap"
        )
        assert w.height() >= demanded

    def test_progress_widget_respects_layout_minimum(self, qtbot, hostile_scale):
        from anki_miner.gui.widgets.progress_widget import ProgressWidget

        w = ProgressWidget()
        qtbot.addWidget(w)
        w.show()
        qtbot.waitExposed(w)
        QApplication.processEvents()

        layout = w.layout()
        assert layout is not None
        demanded = layout.minimumSize().height()
        assert w.minimumHeight() == 0 or w.minimumHeight() >= demanded, (
            f"explicit minimumHeight({w.minimumHeight()}) is below the layout's own " f"minimum ({demanded})"
        )


class TestFileSelectorLabelColumnIsLocaleAware:
    """The label column width was measured on the ENGLISH literal while the label
    rendered TRANSLATED, so every non-English locale hard-clipped (13 instances
    across 7 screens; worst needed 274px in a 105px box).
    """

    @staticmethod
    def _clipped_labels(tab) -> list[str]:
        from anki_miner.gui.widgets.enhanced.file_selector import FileSelector

        out = []
        for sel in tab.findChildren(FileSelector):
            label = getattr(sel, "label", None)
            if label is None or not label.isVisible() or not label.text():
                continue
            report = _clip_report(label)
            if report is not None:
                out.append(report)
        return out

    def test_single_episode_labels_not_clipped_in_longer_locale(self, qtbot, test_config, longer_translations):
        from unittest.mock import MagicMock

        from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

        tab = SingleEpisodeTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
        qtbot.addWidget(tab)
        tab.resize(1024, 768)
        tab.show()
        qtbot.waitExposed(tab)
        QApplication.processEvents()
        assert not self._clipped_labels(tab), "clipped: " + "; ".join(self._clipped_labels(tab))

    def test_audiobook_labels_not_clipped_in_longer_locale(self, qtbot, test_config, longer_translations):
        from unittest.mock import MagicMock

        from anki_miner.gui.widgets.audiobook_tab import AudiobookTab

        tab = AudiobookTab(test_config, processor=None, presenter=MagicMock())
        qtbot.addWidget(tab)
        tab.resize(1024, 768)
        tab.show()
        qtbot.waitExposed(tab)
        QApplication.processEvents()
        assert not self._clipped_labels(tab), "clipped: " + "; ".join(self._clipped_labels(tab))


class TestAnalyticsTablesShowUsableRowCount:
    """analytics_tab set ResizeToContents on the VERTICAL header, so rows reached
    59px (110px at 1.5x) and the 200px height floor yielded 0.78 visible rows of 20 --
    Issue #102's exact class on a tab that never got the fix.
    """

    @staticmethod
    def _visible_rows(table) -> float:
        # viewport() ALREADY excludes the horizontal header -- subtracting the
        # header height again under-reports by roughly one row.
        row_h = table.sizeHintForRow(0) or 1
        return table.viewport().height() / row_h

    def test_sessions_table_shows_at_least_five_rows(self, qtbot, test_config, hostile_scale):
        from PyQt6.QtWidgets import QTableWidgetItem

        from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
        from anki_miner.services.stats_service import StatsService

        # AnalyticsTab takes a StatsService, not a config. Point it at the test
        # home's db and never load it, so showEvent's refresh is a no-op.
        tab = AnalyticsTab(StatsService(test_config.stats_db_path))
        qtbot.addWidget(tab)
        table = tab.sessions_table
        table.setRowCount(20)
        for r in range(20):
            for c in range(table.columnCount()):
                table.setItem(r, c, QTableWidgetItem(f"A fairly long cell value {r}-{c}"))
        table.show()
        tab.resize(1024, 768)
        tab.show()
        qtbot.waitExposed(tab)
        QApplication.processEvents()

        vis = self._visible_rows(table)
        assert vis >= 5, f"only {vis:.2f} rows visible (row height {table.sizeHintForRow(0)}px)"


class TestHeaderProfileBlockFitsTheWindowMinimum:
    """Once a second settings profile exists the header carries TWO captions and
    TWO combos beside the branding, and it -- not the tab stack -- is what sets
    ``MainWindow.minimumSizeHint()``. Measured at ui_font_scale 1.5: header 836px
    vs tabs 718px, against the app's own ``WINDOW_MIN_WIDTH`` contract of 1024,
    and 718px vs 836px is the profile block's own 118px share of that budget.

    The two widths measure different things and are NOT interchangeable, which
    is the trap to avoid here. ``minimumSizeHint`` is the caption widths plus the
    combos' ``minimumContentsLength``; it is what the 1024 contract is about, and
    it is deaf to item text -- a combo holding a 200-char name reports the same
    minimum as one holding "Anime". The long-name payload therefore has to be
    asserted on the PREFERRED ``sizeHint`` (measured with the cap removed: combo
    218px -> 2453px, header 846px -> 3081px, both minimums unmoved at 846px). A
    long-name assertion written against ``minimumSizeHint`` is green forever.

    Why the two fixtures are not stacked on one assertion (measured, not
    assumed): the +25-char pseudo-locale is a per-widget stand-in. Applied to a
    whole ``MainWindow`` it puts the window minimum at 1131px with the profile
    block HIDDEN and at scale 1.0 -- the tab stack alone is already over 1024 --
    so a "both fixtures, whole window" budget assertion would be red for reasons
    that have nothing to do with profiles, and green only by accident once
    someone loosened it. The width budget is therefore measured in the real
    locale at hostile scale; the pseudo-locale is pointed at the header caption,
    which is where a translated string can actually clip.
    """

    @staticmethod
    def _profiles(first_name: str = "Anime"):
        from anki_miner.gui.utils.profile_store import Profile

        return [Profile(id="anime", name=first_name), Profile(id="novels", name="Novels")]

    @pytest.fixture
    def pinned_app_stylesheet(self, qapp):
        """Save and restore the app stylesheet around the width measurements.

        ``sizeHint`` on a POLISHED widget is font-driven, the font comes from the
        app stylesheet, and every test file on an xdist worker shares one
        ``QApplication``. ``hostile_scale`` makes the sheet deterministic while a
        test runs (it applies the theme itself) but never puts the previous one
        back. List this fixture BEFORE ``hostile_scale`` in the signature so it
        finalises after it and the module stops leaking a sheet to its
        neighbours.
        """
        previous = qapp.styleSheet()
        yield
        qapp.setStyleSheet(previous)

    def test_two_profiles_keep_the_window_minimum_inside_its_own_contract(
        self, qtbot, patch_heavy_init, test_config, pinned_app_stylesheet, hostile_scale
    ):
        from anki_miner.gui.app import compose_main_window
        from anki_miner.gui.constants import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH

        # Built inside the test body, i.e. AFTER hostile_scale: fonts.py bakes
        # pixel_size * font scale at construction.
        patch_heavy_init(test_config)
        window = compose_main_window(test_config).window
        qtbot.addWidget(window)
        window.header.set_profiles(self._profiles(), "anime")
        window.resize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        window.show()
        qtbot.waitExposed(window)
        QApplication.processEvents()

        header_min = window.header.minimumSizeHint().width()
        # Vacuity guards: the block really is on screen, and the header really is
        # the constraint the window minimum is being read off.
        assert not window.header.profile_combo.isHidden()
        assert not window.header.profile_label.isHidden()
        assert header_min >= window.tabs.minimumSizeHint().width(), (
            f"the tab stack ({window.tabs.minimumSizeHint().width()}px) now out-demands the header "
            f"({header_min}px), so this no longer measures the profile block"
        )

        assert window.minimumSizeHint().width() <= WINDOW_MIN_WIDTH, (
            f"window minimum {window.minimumSizeHint().width()}px exceeds the {WINDOW_MIN_WIDTH}px "
            f"contract it sets on itself (header demands {header_min}px)"
        )
        window.deleteLater()

    def test_the_profile_caption_is_not_clipped_in_a_longer_locale(
        self, qtbot, pinned_app_stylesheet, hostile_scale, longer_translations
    ):
        from anki_miner.gui.constants import WINDOW_MIN_WIDTH
        from anki_miner.gui.widgets.header_widget import HeaderWidget

        header = HeaderWidget()
        qtbot.addWidget(header)
        header.set_profiles(self._profiles(), "anime")
        # Give the layout the width it asks for. Forcing a header below its own
        # minimum measures the squeeze rather than the defect, and under the
        # pseudo-locale this header's minimum is 1633px -- see the class
        # docstring for why that number is not itself an assertion.
        header.resize(max(WINDOW_MIN_WIDTH, header.minimumSizeHint().width()), header.sizeHint().height())
        header.show()
        qtbot.waitExposed(header)
        QApplication.processEvents()

        label = header.profile_label
        # Vacuity guard: a hidden or empty caption cannot be clipped.
        assert label.isVisible() and label.text()
        assert _clip_report(label) is None, f"clipped: {_clip_report(label)}"

    def test_a_200_char_profile_name_elides_instead_of_widening_the_header(
        self, qtbot, pinned_app_stylesheet, hostile_scale, longer_translations
    ):
        from anki_miner.gui.constants import WINDOW_MIN_WIDTH
        from anki_miner.gui.widgets.header_widget import HeaderWidget

        long_name = "X" * 200
        header = HeaderWidget()
        qtbot.addWidget(header)
        header.set_profiles(self._profiles(), "anime")
        header.resize(max(WINDOW_MIN_WIDTH, header.minimumSizeHint().width()), header.sizeHint().height())
        header.show()
        qtbot.waitExposed(header)
        QApplication.processEvents()

        combo = header.profile_combo
        # Baselines taken in the SAME visibility state they are compared in: a
        # hidden combo is unpolished and reports a stale-font hint.
        # sizeHint, NOT minimumSizeHint: QComboBox's MINIMUM ignores its item
        # text entirely, so a minimum-based assertion here is inert -- measured,
        # an unbounded combo moved sizeHint 218px -> 2453px and the header's
        # sizeHint 846px -> 3081px while both minimums sat unchanged at 846px.
        baseline_combo = combo.sizeHint().width()
        baseline_header = header.sizeHint().width()

        header.set_profiles(self._profiles(long_name), "anime")
        QApplication.processEvents()

        # RELATIVE, deliberately. The combo's hint tracks the font (measured 160px
        # at scale 1.0, 208 at 1.5, 256 at 2.0), and so does its width cap, so no
        # absolute pixel budget is assertable here at all. The property that
        # matters is that the hint does not depend on the items, at every scale.
        assert combo.sizeHint().width() == baseline_combo, (
            f"a 200-char profile name widened the combo from {baseline_combo}px to " f"{combo.sizeHint().width()}px"
        )
        assert header.sizeHint().width() == baseline_header, (
            f"a 200-char profile name widened the header from {baseline_header}px to " f"{header.sizeHint().width()}px"
        )
        # Vacuity guard: the long name really was in the combo, and elided.
        assert combo.itemData(0) == "anime"
        assert 0 < len(combo.itemText(0)) < len(long_name)
