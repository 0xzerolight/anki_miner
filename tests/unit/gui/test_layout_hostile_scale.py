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

from anki_miner.gui.resources.styles.theme import Theme


@pytest.fixture
def hostile_scale(qapp):
    """Build widgets at 150% text size, then restore.

    apply_to_app is load-bearing: set_font_scale alone leaves the QSS-derived
    padding at 1.0, so the layout minimum never grows and the test silently
    passes without reproducing anything.
    """
    original = Theme.get_font_scale()
    Theme.set_font_scale(1.5)
    Theme.apply_to_app(qapp)
    yield
    Theme.set_font_scale(original)
    Theme.apply_to_app(qapp)


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
            need = label.fontMetrics().horizontalAdvance(label.text())
            avail = label.contentsRect().width()
            if avail > 0 and need > avail + 2:
                out.append(f"{label.text()!r} needs {need}px in {avail}px")
        return out

    @pytest.fixture
    def longer_translations(self):
        """Install a translator that lengthens every string.

        This is what reproduces the defect: the column was sized from the
        ENGLISH literal while the label rendered TRANSLATED, so any locale whose
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
