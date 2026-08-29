"""The header says which language is being mined, and routes to the selector."""

from __future__ import annotations

from anki_miner.gui.widgets.header_widget import HeaderWidget


def _header(qtbot) -> HeaderWidget:
    header = HeaderWidget()
    qtbot.addWidget(header)
    return header


def test_a_fresh_header_hides_the_chip(qtbot):
    header = _header(qtbot)
    assert header.mining_language_label.isHidden()
    assert header.mining_language_button.isHidden()


def test_one_buildable_language_keeps_the_chip_hidden(qtbot):
    header = _header(qtbot)
    header.set_mining_language("日本語", choices=1)
    assert header.mining_language_button.isHidden()


def test_two_languages_show_the_active_one(qtbot):
    header = _header(qtbot)
    header.set_mining_language("中文", choices=2)
    assert not header.mining_language_label.isHidden()
    assert header.mining_language_button.text() == "中文"
    assert "中文" in header.mining_language_button.toolTip()


def test_clicking_the_chip_asks_for_the_selector(qtbot):
    header = _header(qtbot)
    header.set_mining_language("中文", choices=2)
    opened: list[bool] = []
    header.open_mining_language_settings.connect(lambda: opened.append(True))

    header.mining_language_button.click()

    assert opened == [True]
