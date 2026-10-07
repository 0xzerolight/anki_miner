"""E17: Manga OCR (mokuro's Japanese-only manga-ocr model, 1 to 4 GB) is offered only for Japanese.

Every language used to see the tool and a System Health row for it. The gate
hides the tab through the language capability, never by writing the user's
hidden_utilities. Reading -> Manga stays for every language.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.gui.capabilities import search
from anki_miner.gui.widgets.dialogs.system_health_window import SystemHealthWindow
from anki_miner.languages.registry import get_profile
from tests.unit.test_subtitles_tab import _make_config, _make_tab, _visible_keys


def test_only_japanese_declares_manga_ocr():
    assert "manga_ocr" in get_profile("ja").capabilities
    for code in ("zh", "ko", "es", "de"):
        assert "manga_ocr" not in get_profile(code).capabilities


def test_the_tab_is_hidden_for_another_language_without_touching_the_setting(qtbot, tmp_path):
    config = replace(_make_config(tmp_path), language="zh", hidden_utilities=())
    tab = _make_tab(config, qtbot)

    assert "mokuro" not in _visible_keys(tab)
    assert tab.config.hidden_utilities == ()


def test_the_tab_shows_for_japanese(qtbot, tmp_path):
    tab = _make_tab(replace(_make_config(tmp_path), language="ja", hidden_utilities=()), qtbot)

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
    )
    tab = _make_tab(replace(_make_config(tmp_path), language="zh", hidden_utilities=others), qtbot)

    assert _visible_keys(tab) != []


def test_the_usage_guide_lists_manga_ocr_for_japanese_only():
    assert any(cap.id == "manga-ocr" for cap in search("", get_profile("ja").capabilities))
    assert not any(cap.id == "manga-ocr" for cap in search("", get_profile("zh").capabilities))
    # Reading -> Manga itself stays for every language.
    assert any(cap.id == "manga-mining" for cap in search("", get_profile("zh").capabilities))


def test_system_health_hides_the_mokuro_row_for_another_language(qtbot):
    window = SystemHealthWindow()
    qtbot.addWidget(window)

    window.set_capabilities(get_profile("zh").capabilities)
    assert window._rows["tools.mokuro"].isHidden()

    window.set_capabilities(get_profile("ja").capabilities)
    assert not window._rows["tools.mokuro"].isHidden()
