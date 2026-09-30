"""D16: no "Anki Miner" title row; the profile and theme selectors sit in the main tab row.

The frame took 171 px above the content and the title repeated the window
title. When the row is short of room the "Theme:" caption hides first and the
"Settings profile:" caption only as a last resort; tooltips and accessible names
still say what each combo is.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QToolButton

from anki_miner.gui.utils.profile_store import Profile
from anki_miner.gui.widgets.header_widget import HeaderWidget


def _profiles():
    return [Profile(id="anime", name="Anime"), Profile(id="novels", name="Novels")]


@pytest.fixture
def header(qtbot):
    widget = HeaderWidget()
    qtbot.addWidget(widget)
    widget.set_profiles(_profiles(), "anime")
    return widget


def test_the_header_no_longer_repeats_the_window_title(header):
    assert [label for label in header.findChildren(QLabel) if label.text() == "Anki Miner"] == []


def test_with_room_both_captions_show(header):
    header.fit_captions(10_000)

    assert not header.theme_label.isHidden()
    assert not header.profile_label.isHidden()


def test_the_theme_caption_hides_first(header):
    header.fit_captions(header.needed_width(theme_caption=True, profile_caption=True) - 1)

    assert header.theme_label.isHidden()
    assert not header.profile_label.isHidden()


def test_both_combos_shrink_before_the_profile_caption_goes(header):
    """D16: after "Theme:" hides, the combos give up room; "Settings profile:" is the last resort."""
    theme_hint = header.theme_combo.sizeHint().width()
    profile_hint = header.profile_combo.sizeHint().width()

    # 4 px short once "Theme:" is gone: 2 px off each combo, both captions' fate fixed.
    header.fit_captions(header.needed_width(theme_caption=False, profile_caption=True) - 4)

    assert header.theme_label.isHidden()
    assert not header.profile_label.isHidden()
    assert header.theme_combo.maximumWidth() == theme_hint - 2
    assert header.profile_combo.maximumWidth() == profile_hint - 2


def test_room_coming_back_releases_the_combos(header):
    header.fit_captions(header.needed_width(theme_caption=False, profile_caption=True) - 4)

    header.fit_captions(10_000)

    assert header.theme_combo.minimumWidth() == 0
    assert header.theme_combo.maximumWidth() > header.theme_combo.sizeHint().width()
    assert header.profile_combo.minimumWidth() == 0
    # The profile combo gets its character-budget cap back, not "unlimited".
    assert header.profile_combo.maximumWidth() >= header.profile_combo.sizeHint().width()
    assert header.profile_combo.maximumWidth() < 16777215


def test_the_profile_caption_hides_only_as_a_last_resort(header):
    header.fit_captions(0)

    assert header.theme_label.isHidden()
    assert header.profile_label.isHidden()
    # The combos still say what they are.
    assert header.theme_combo.accessibleName() == "Theme"
    assert header.profile_combo.accessibleName() == "Settings profile"


def test_no_profiles_keeps_the_profile_block_hidden_whatever_the_room(qtbot):
    widget = HeaderWidget()
    qtbot.addWidget(widget)

    widget.fit_captions(10_000)

    assert widget.profile_label.isHidden()


def test_new_profiles_are_fitted_to_the_last_room_given(qtbot):
    widget = HeaderWidget()
    qtbot.addWidget(widget)
    widget.fit_captions(0)

    widget.set_profiles(_profiles(), "anime")

    assert widget.profile_label.isHidden()
    assert not widget.profile_combo.isHidden()


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def test_the_header_is_the_main_tab_rows_corner_widget(main_window):
    assert main_window.tabs.cornerWidget(Qt.Corner.TopRightCorner) is main_window.header
    assert main_window.central_layout.indexOf(main_window.header) == -1


# Vacuity guards: the translated "Settings" main-tab label (MainWindow context).
_SETTINGS_TAB_TEXT = {"de": "Einstellungen", "es": "Configuración"}


@pytest.mark.parametrize("lang", ["en", "de", "es"])
def test_the_main_row_does_not_scroll_at_the_minimum_width(lang, qapp, qtbot, patch_heavy_init, test_config):
    """D16: Spanish has the widest main tab row (581 px at 7e4e541f, audit
    homes/RT-2es-out/tabbars.json); German has the longest header captions."""
    from anki_miner.gui.app import compose_main_window
    from anki_miner.gui.constants import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH
    from anki_miner.gui.i18n import install_translators

    # Installed before the window is built, so every self.tr() in it reads German
    # (the same install/remove pattern as test_capability_browser.py:38-57).
    translators = install_translators(qapp, lang)
    try:
        patch_heavy_init(test_config)
        window = compose_main_window(test_config).window
        qtbot.addWidget(window)
        window.header.set_profiles(_profiles(), "anime")
        window.resize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        window.show()
        qtbot.waitExposed(window)
        qtbot.wait(50)

        if lang in _SETTINGS_TAB_TEXT:
            # Vacuity guard: the catalog really loaded.
            assert _SETTINGS_TAB_TEXT[lang] in [window.tabs.tabText(i) for i in range(window.tabs.count())]
        scroll_buttons = [b for b in window.tabs.tabBar().findChildren(QToolButton) if b.isVisible()]

        assert scroll_buttons == []
        window.deleteLater()
    finally:
        for translator in translators:
            qapp.removeTranslator(translator)
