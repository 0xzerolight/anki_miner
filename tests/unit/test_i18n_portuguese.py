"""Brazilian Portuguese catalog: registered, well-formed, structural parity with
English, and — critically — loadable through the config normalizer.

Parity (every English source has a pt_br entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check` is
the guard that flags newly-added strings missing from the pt_br catalog. The numerus
check asserts the Portuguese two-form plural rule on translated entries.

The round-trip test is the one that matters for this language: pt_br is the first
mixed-case code, and `AnkiMinerConfig.__post_init__` lowercases `ui_language`, so the
catalog filename/key MUST be lowercase or the shipped app silently falls back to
English. Passing `install_translators` a literal string (as the other language tests
do) would hide that break; here we route through the real config field.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from PyQt6.QtCore import QTranslator

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui import i18n

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"


def _sources_by_context(ts_path: Path) -> dict[str, set[str]]:
    root = ET.parse(ts_path).getroot()
    out: dict[str, set[str]] = {}
    for ctx in root.findall("context"):
        name = ctx.find("name").text or ""
        out[name] = {(m.find("source").text or "") for m in ctx.findall("message")}
    return out


def test_portuguese_is_registered():
    assert i18n.available_languages()["pt_br"] == "Português (Brasil)"


def test_pt_br_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_pt_br.ts")  # raises on malformed XML


def test_pt_br_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_pt_br.ts").getroot()
    assert root.get("language") == "pt_BR"


def test_pt_br_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    pt = _sources_by_context(TS_DIR / "anki_miner_pt_br.ts")
    for context, sources in en.items():
        assert context in pt, f"pt_br catalog missing context {context!r}"
        missing = sources - pt[context]
        assert not missing, f"pt_br context {context!r} missing sources: {sorted(missing)[:5]}"


def test_pt_br_translated_numerus_have_two_forms():
    """Brazilian Portuguese needs two plural forms (one/other); every translated
    numerus entry must carry exactly two <numerusform>s."""
    root = ET.parse(TS_DIR / "anki_miner_pt_br.ts").getroot()
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


def test_pt_br_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_pt_br.qm")) is True


def test_install_translators_pt_br_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "pt_br")
    try:
        assert any("anki_miner_pt_br" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)


def test_config_ui_language_round_trip_loads_pt_br(qapp):
    """Mixed-case pt_BR must survive config normalization AND still load the catalog
    — the regression guard for the case-sensitive-filename landmine."""
    cfg = AnkiMinerConfig(ui_language="pt_BR")
    assert cfg.ui_language == "pt_br"  # __post_init__ lowercases
    result = i18n.install_translators(qapp, cfg.ui_language)
    try:
        assert any("anki_miner_pt_br" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
