"""E01: the main tab row and the sub-tab rows read as two levels, and both are slimmer.

Before this, both rows were 51 px and identical in size, weight and underline, so
the sub-tab row read as a second copy of the main one.
"""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QTabWidget, QWidget

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.base import install_animated_tab_bar
from anki_miner.gui.widgets.base.animated_tab_bar import MAIN_TAB_BAR_OBJECT_NAME, SUBTAB_BAR_OBJECT_NAME


@pytest.fixture(autouse=True)
def _themed(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(Theme.get_stylesheet("light"))
    yield
    qapp.setStyleSheet(previous)


def _tabs(qtbot, *, primary: bool) -> QTabWidget:
    tabs = QTabWidget()
    install_animated_tab_bar(tabs, primary=primary)
    for label in ("Video", "Reading", "Settings"):
        tabs.addTab(QWidget(), label)
    qtbot.addWidget(tabs)
    tabs.resize(600, 200)
    tabs.show()
    qtbot.waitExposed(tabs)
    return tabs


def test_install_names_the_level(qtbot):
    main = _tabs(qtbot, primary=True)
    sub = _tabs(qtbot, primary=False)

    assert main.tabBar().objectName() == MAIN_TAB_BAR_OBJECT_NAME
    assert sub.tabBar().objectName() == SUBTAB_BAR_OBJECT_NAME


def test_a_sub_tab_is_smaller_than_a_main_tab(qtbot):
    main = _tabs(qtbot, primary=True)
    sub = _tabs(qtbot, primary=False)

    # Index 1 is never the selected tab, so neither rect carries the selected border.
    assert sub.tabBar().tabRect(1).height() < main.tabBar().tabRect(1).height()
    assert sub.tabBar().tabRect(1).width() < main.tabBar().tabRect(1).width()


def test_the_main_row_is_slimmer_than_before(qtbot):
    # Before E01 a tab was a 32 px floor plus 8 px of padding above and below.
    main = _tabs(qtbot, primary=True)

    assert main.tabBar().tabRect(1).height() < 48


def test_the_sub_row_draws_a_thinner_underline(qtbot):
    main = _tabs(qtbot, primary=True)
    sub = _tabs(qtbot, primary=False)

    assert main.tabBar().underline_target().height() == 3
    assert sub.tabBar().underline_target().height() == 2
