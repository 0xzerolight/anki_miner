"""E06, E19, E20: translation-only fixes, pinned so a later re-translation cannot undo them."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"
TARGETS = ("de", "es", "fr", "id", "it", "ja", "pt_br", "ru", "vi", "zh_cn", "zh_tw")


def _translation(lang: str, context: str, source: str) -> str:
    root = ET.parse(TS_DIR / f"anki_miner_{lang}.ts").getroot()
    for ctx in root.findall("context"):
        if ctx.find("name").text != context:
            continue
        for msg in ctx.findall("message"):
            if msg.find("source").text == source:
                return msg.find("translation").text or ""
    raise AssertionError(f"{lang}: {context} / {source!r} not found")


@pytest.mark.parametrize("lang", TARGETS)
def test_the_tools_menu_and_the_utilities_tab_have_different_names(lang):
    """E06: German used Werkzeuge for both, Chinese 工具 for both."""
    tools = _translation(lang, "MainWindow", "&Tools").replace("&", "")
    utilities = _translation(lang, "MainWindow", "Utilities")

    assert tools.split("(")[0].strip() != utilities.strip()


@pytest.mark.parametrize("lang", TARGETS)
def test_review_words_has_one_translation_per_catalog(lang):
    """E20: the same checkbox read "überprüfen" on one screen and "prüfen" on another."""
    root = ET.parse(TS_DIR / f"anki_miner_{lang}.ts").getroot()
    wordings = {
        msg.find("translation").text
        for ctx in root.findall("context")
        for msg in ctx.findall("message")
        if msg.find("source").text == "Review words before mining"
    }

    assert len(wordings) == 1


@pytest.mark.parametrize(
    ("lang", "source", "expected"),
    [
        ("de", "Card Backfill", "Nachbefüllung"),
        ("de", "Audiobook Sync", "Hörbuch-Sync"),
        ("fr", "Audiobook Sync", "Synchro livre audio"),
        ("it", "Card Backfill", "Completamento"),
        ("pt_br", "Card Backfill", "Completar cartões"),
        ("ru", "Retime", "Тайминги"),
    ],
)
def test_the_long_utilities_labels_are_shortened(lang, source, expected):
    """E19: the Utilities tab bar overflowed at 1024 px in de, fr, it, pt_br and ru."""
    assert _translation(lang, "MainWindow", source) == expected
