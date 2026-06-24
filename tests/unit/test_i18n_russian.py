"""Russian catalog: registered, well-formed, structurally parity with English.

Parity (every English source has a RU entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check`
is the guard that flags newly-added strings missing from the RU catalog. The
numerus check asserts the Russian three-form plural rule on translated entries.
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


def test_russian_is_registered():
    assert i18n.available_languages()["ru"] == "Русский"


def test_ru_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_ru.ts")  # raises on malformed XML


def test_ru_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_ru.ts").getroot()
    assert root.get("language") == "ru_RU"


def test_ru_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    ru = _sources_by_context(TS_DIR / "anki_miner_ru.ts")
    for context, sources in en.items():
        assert context in ru, f"RU catalog missing context {context!r}"
        missing = sources - ru[context]
        assert not missing, f"RU context {context!r} missing sources: {sorted(missing)[:5]}"


def test_ru_translated_numerus_have_three_forms():
    """Russian needs three plural forms (one/few/many); every translated
    numerus entry must carry exactly three <numerusform>s."""
    root = ET.parse(TS_DIR / "anki_miner_ru.ts").getroot()
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            if msg.get("numerus") != "yes":
                continue
            tr = msg.find("translation")
            if tr is None or tr.get("type") == "unfinished":
                continue  # untranslated falls back to English
            forms = tr.findall("numerusform")
            source = msg.find("source").text or ""
            assert len(forms) == 3, f"numerus {source!r} has {len(forms)} forms, expected 3"


def test_ru_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_ru.qm")) is True


def test_install_translators_ru_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "ru")
    try:
        assert any("anki_miner_ru" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
