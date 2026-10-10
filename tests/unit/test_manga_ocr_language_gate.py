"""E17: Manga OCR is offered to a language mokuro can read: Japanese natively, the rest through a PP-OCR recogniser.

A language with neither (he, vi, yue) does not see the tool or a System Health
row for it. The gate hides the tab through the language capability, never by
writing the user's hidden_utilities. Reading -> Manga stays for every language.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.gui.capabilities import search
from anki_miner.gui.widgets.dialogs.system_health_window import SystemHealthWindow
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from tests.unit.test_subtitles_tab import _make_config, _make_tab, _visible_keys

#: The languages no recogniser passed the 2026-10-10 spike for.
_NO_MANGA_OCR = {"he", "vi", "yue"}


def test_every_language_but_the_unread_ones_offers_manga_ocr():
    for code in AVAILABLE_LANGUAGES:
        assert ("manga_ocr" in get_profile(code).capabilities) is (code not in _NO_MANGA_OCR), code


def test_the_tab_is_hidden_for_hebrew_without_touching_the_setting(qtbot, tmp_path):
    config = replace(_make_config(tmp_path), language="he", hidden_utilities=())
    tab = _make_tab(config, qtbot)

    assert "mokuro" not in _visible_keys(tab)
    assert tab.config.hidden_utilities == ()


def test_the_tab_shows_for_japanese(qtbot, tmp_path):
    tab = _make_tab(replace(_make_config(tmp_path), language="ja", hidden_utilities=()), qtbot)

    assert "mokuro" in _visible_keys(tab)


def test_the_tab_shows_for_spanish(qtbot, tmp_path):
    tab = _make_tab(replace(_make_config(tmp_path), language="es", hidden_utilities=()), qtbot)

    assert "mokuro" in _visible_keys(tab)


def test_the_gate_never_empties_the_tab(qtbot, tmp_path):
    others = (
        "generate",
        "retime",
        "condense",
        "backfill",
        "deckfilter",
        "download",
        "booksync",
        "readability",
        "tracks",
        "videoocr",
    )
    tab = _make_tab(replace(_make_config(tmp_path), language="he", hidden_utilities=others), qtbot)

    assert _visible_keys(tab) != []


def test_the_usage_guide_lists_manga_ocr_where_it_is_offered():
    assert any(cap.id == "manga-ocr" for cap in search("", get_profile("ja").capabilities))
    assert any(cap.id == "manga-ocr" for cap in search("", get_profile("zh").capabilities))
    assert not any(cap.id == "manga-ocr" for cap in search("", get_profile("he").capabilities))
    # Reading -> Manga itself stays for every language.
    assert any(cap.id == "manga-mining" for cap in search("", get_profile("he").capabilities))


def test_system_health_hides_the_mokuro_row_for_a_language_without_manga_ocr(qtbot):
    window = SystemHealthWindow()
    qtbot.addWidget(window)

    window.set_capabilities(get_profile("he").capabilities)
    assert window._rows["tools.mokuro"].isHidden()

    window.set_capabilities(get_profile("ja").capabilities)
    assert not window._rows["tools.mokuro"].isHidden()

    window.set_capabilities(get_profile("ko").capabilities)
    assert not window._rows["tools.mokuro"].isHidden()
