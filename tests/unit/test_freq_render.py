"""Tests for render_frequency_html (card breakdown HTML)."""

from __future__ import annotations

from anki_miner.services.frequency.render import render_frequency_html


def test_empty_list_returns_empty_string():
    assert render_frequency_html([]) == ""


def test_single_source():
    assert render_frequency_html([("JPDB", 100)]) == "<ul><li>JPDB: 100</li></ul>"


def test_multiple_sources_in_order():
    html = render_frequency_html([("JPDB", 100), ("BCCWJ", 42), ("Novel", 7)])
    assert html == "<ul><li>JPDB: 100</li><li>BCCWJ: 42</li><li>Novel: 7</li></ul>"


def test_name_is_html_escaped():
    html = render_frequency_html([("A & B <x>", 5)])
    assert html == "<ul><li>A &amp; B &lt;x&gt;: 5</li></ul>"
