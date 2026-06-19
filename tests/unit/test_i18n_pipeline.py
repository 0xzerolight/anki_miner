# tests/unit/test_i18n_pipeline.py
"""The committed English catalog is well-formed and loads via QTranslator."""

import xml.etree.ElementTree as ET
from pathlib import Path

from PyQt6.QtCore import QTranslator

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"


def test_en_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_en.ts")  # raises on malformed XML


def test_en_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_en.qm")) is True
