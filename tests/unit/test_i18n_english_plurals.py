"""E14: English shows real plurals ("1 card this session"), not "1 card(s) this session".

English installed no translator, so every "%n thing(s)" string showed its source
text. The English catalogue now carries the two English forms of every plural
message, and English installs that catalogue alone (Qt's own strings are
English already).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication

from anki_miner.gui import i18n

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"


def test_en_ts_declares_english():
    root = ET.parse(TS_DIR / "anki_miner_en.ts").getroot()

    assert root.get("language") == "en_US"


def test_en_numerus_fully_translated():
    """Every plural source has two finished English forms, each keeping %n.

    A new ``%n`` string must get its forms here in the same change, or English
    shows "(s)" again.
    """
    root = ET.parse(TS_DIR / "anki_miner_en.ts").getroot()
    missing: list[str] = []
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            if msg.get("numerus") != "yes":
                continue
            tr = msg.find("translation")
            forms = [form.text or "" for form in tr.findall("numerusform")] if tr is not None else []
            if tr is None or tr.get("type") == "unfinished" or len(forms) != 2 or not all("%n" in f for f in forms):
                missing.append(msg.find("source").text or "")
    assert missing == []


@pytest.fixture
def english(qapp):
    installed = i18n.install_translators(qapp, "en")
    yield installed
    for translator in installed:
        qapp.removeTranslator(translator)


def test_english_installs_only_the_app_catalogue(english):
    assert len(english) == 1


@pytest.mark.parametrize(("count", "expected"), [(1, "1 card this session"), (3, "3 cards this session")])
def test_english_plurals_render(english, count, expected):
    assert QCoreApplication.translate("StatusBarWidget", "%n card(s) this session", "", count) == expected


def test_english_leaves_every_other_string_alone(english):
    assert QCoreApplication.translate("StatusBarWidget", "Ready") == "Ready"
