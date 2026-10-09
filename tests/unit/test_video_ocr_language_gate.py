"""Video OCR (meikiocr reads Japanese game text only) is offered only for Japanese.

The gate hides the tab through the ``video_ocr`` language capability, never by
writing the user's hidden_utilities, so a switch back to Japanese restores it.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.gui.capabilities import UTILITY_SUBTABS, search
from anki_miner.languages.registry import get_profile
from tests.unit.test_subtitles_tab import _make_config, _make_tab, _visible_keys


def test_only_japanese_declares_video_ocr():
    assert "video_ocr" in get_profile("ja").capabilities
    for code in ("zh", "ko", "es", "de"):
        assert "video_ocr" not in get_profile(code).capabilities


def test_the_tab_is_hidden_for_another_language_without_touching_the_setting(qtbot, tmp_path):
    config = replace(_make_config(tmp_path), language="zh", hidden_utilities=())
    tab = _make_tab(config, qtbot)

    assert not tab._inner_tabs.isTabVisible(tab._subtab_index["videoocr"])
    assert "videoocr" not in _visible_keys(tab)
    assert tab.config.hidden_utilities == ()


def test_the_tab_shows_for_japanese(qtbot, tmp_path):
    tab = _make_tab(replace(_make_config(tmp_path), language="ja", hidden_utilities=()), qtbot)

    assert "videoocr" in _visible_keys(tab)


def test_the_gate_never_empties_the_tab(qtbot, tmp_path):
    others = tuple(key for key in UTILITY_SUBTABS if key != "videoocr")
    tab = _make_tab(replace(_make_config(tmp_path), language="zh", hidden_utilities=others), qtbot)

    assert _visible_keys(tab) != []


def test_the_usage_guide_lists_video_ocr_for_japanese_only():
    assert any(cap.id == "video-ocr" for cap in search("", get_profile("ja").capabilities))
    assert not any(cap.id == "video-ocr" for cap in search("", get_profile("zh").capabilities))
