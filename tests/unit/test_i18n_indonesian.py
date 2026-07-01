"""Indonesian catalog: registered, well-formed, structurally parity with English.

Parity (every English source has an ID entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check`
is the guard that flags newly-added strings missing from the ID catalog. Indonesian
is single-form (%n is just the number), so there is no numerus-form-count check.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from PyQt6.QtCore import QTranslator

from anki_miner.gui import i18n

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"


def _sources_by_context(ts_path: Path) -> dict[str, set[str]]:
    root = ET.parse(ts_path).getroot()
    out: dict[str, set[str]] = {}
    for ctx in root.findall("context"):
        name = ctx.find("name").text or ""
        out[name] = {(m.find("source").text or "") for m in ctx.findall("message")}
    return out


def test_indonesian_is_registered():
    assert i18n.available_languages()["id"] == "Bahasa Indonesia"


def test_id_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_id.ts")  # raises on malformed XML


def test_id_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_id.ts").getroot()
    assert root.get("language") == "id_ID"


def test_id_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    idn = _sources_by_context(TS_DIR / "anki_miner_id.ts")
    for context, sources in en.items():
        assert context in idn, f"ID catalog missing context {context!r}"
        missing = sources - idn[context]
        assert not missing, f"ID context {context!r} missing sources: {sorted(missing)[:5]}"


def test_id_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_id.qm")) is True


def test_install_translators_id_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "id")
    try:
        assert any("anki_miner_id" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
