"""Italian catalog: registered, well-formed, structurally parity with English.

Parity (every English source has an IT entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check`
is the guard that flags newly-added strings missing from the IT catalog. The
numerus check asserts the Italian two-form plural rule on translated entries.

(No German-style `fully_translated` completeness guard: that only holds once a
catalog is 100% translated; parity + `check` already guard drift.)
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


def test_italian_is_registered():
    assert i18n.available_languages()["it"] == "Italiano"


def test_it_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_it.ts")  # raises on malformed XML


def test_it_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_it.ts").getroot()
    assert root.get("language") == "it_IT"


def test_it_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    it = _sources_by_context(TS_DIR / "anki_miner_it.ts")
    for context, sources in en.items():
        assert context in it, f"IT catalog missing context {context!r}"
        missing = sources - it[context]
        assert not missing, f"IT context {context!r} missing sources: {sorted(missing)[:5]}"


def test_it_translated_numerus_have_two_forms():
    """Italian needs two plural forms (one/other); every translated numerus
    entry must carry exactly two <numerusform>s."""
    root = ET.parse(TS_DIR / "anki_miner_it.ts").getroot()
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            if msg.get("numerus") != "yes":
                continue
            tr = msg.find("translation")
            if tr is None or tr.get("type") == "unfinished":
                continue  # untranslated falls back to English
            forms = tr.findall("numerusform")
            source = msg.find("source").text or ""
            assert len(forms) == 2, f"numerus {source!r} has {len(forms)} forms, expected 2"


def test_it_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_it.qm")) is True


def test_install_translators_it_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "it")
    try:
        assert any("anki_miner_it" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
