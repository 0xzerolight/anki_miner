"""Tests for render_frequency_html (card breakdown HTML)."""

from __future__ import annotations

from anki_miner.services.frequency.render import render_frequency_html


def test_empty_list_returns_empty_string():
    assert render_frequency_html([]) == ""


def test_single_source_no_display_uses_rank():
    assert render_frequency_html([("JPDB", 100, None)]) == "<ul><li>JPDB: 100</li></ul>"


def test_multiple_sources_in_order():
    html = render_frequency_html([("JPDB", 100, None), ("BCCWJ", 42, None), ("Novel", 7, None)])
    assert html == "<ul><li>JPDB: 100</li><li>BCCWJ: 42</li><li>Novel: 7</li></ul>"


def test_name_is_html_escaped():
    html = render_frequency_html([("A & B <x>", 5, None)])
    assert html == "<ul><li>A &amp; B &lt;x&gt;: 5</li></ul>"


def test_display_value_wins_over_rank():
    # Yomitan card rule: displayValue ?? frequency — the human string shows.
    assert render_frequency_html([("JPDB", 1099, "1099/72000")]) == "<ul><li>JPDB: 1099/72000</li></ul>"


def test_display_value_is_html_escaped():
    html = render_frequency_html([("JPDB", 5, "<b>5</b> & up")])
    assert html == "<ul><li>JPDB: &lt;b&gt;5&lt;/b&gt; &amp; up</li></ul>"


def test_mixed_display_and_rank_rows():
    html = render_frequency_html([("JPDB", 1099, "1099/72000"), ("BCCWJ", 42, None)])
    assert html == "<ul><li>JPDB: 1099/72000</li><li>BCCWJ: 42</li></ul>"
