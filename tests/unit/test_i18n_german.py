"""German catalog: registered, well-formed, structurally parity with English.

Parity (every English source has a DE entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check`
is the guard that flags newly-added strings missing from the DE catalog. The
numerus check asserts the German two-form plural rule on translated entries.

Beyond the French-parity set, `test_de_numerus_fully_translated` enforces that
every plural string is actually translated with two forms: German ships as a
fully-translated UI, and the "exactly-2-forms" assert alone only fires on
already-translated numerus entries (an unfinished one is valid drift-free state
that both it and `check` would silently accept).
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


def test_german_is_registered():
    assert i18n.available_languages()["de"] == "Deutsch"


def test_de_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_de.ts")  # raises on malformed XML


def test_de_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_de.ts").getroot()
    assert root.get("language") == "de_DE"


def test_de_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    de = _sources_by_context(TS_DIR / "anki_miner_de.ts")
    for context, sources in en.items():
        assert context in de, f"DE catalog missing context {context!r}"
        missing = sources - de[context]
        assert not missing, f"DE context {context!r} missing sources: {sorted(missing)[:5]}"


def test_de_translated_numerus_have_two_forms():
    """German needs two plural forms (one/other); every translated numerus
    entry must carry exactly two <numerusform>s."""
    root = ET.parse(TS_DIR / "anki_miner_de.ts").getroot()
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


def test_de_numerus_fully_translated():
    """German-only completeness guard: every numerus message is fully translated
    with two non-empty forms. Unlike the cloned two-forms assert (which skips
    unfinished entries), this fails if any plural string is left untranslated —
    the German UI is shipped fully translated."""
    root = ET.parse(TS_DIR / "anki_miner_de.ts").getroot()
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            if msg.get("numerus") != "yes":
                continue
            source = msg.find("source").text or ""
            tr = msg.find("translation")
            assert tr is not None, f"numerus {source!r} has no <translation>"
            assert tr.get("type") != "unfinished", f"numerus {source!r} is unfinished"
            forms = tr.findall("numerusform")
            assert len(forms) == 2, f"numerus {source!r} has {len(forms)} forms, expected 2"
            for i, f in enumerate(forms):
                assert (f.text or "").strip(), f"numerus {source!r} form {i} is empty"


def test_de_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_de.qm")) is True


def test_install_translators_de_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "de")
    try:
        assert any("anki_miner_de" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
