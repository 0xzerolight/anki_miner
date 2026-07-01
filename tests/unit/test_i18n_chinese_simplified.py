"""Simplified Chinese catalog: registered, well-formed, structural parity with
English, and — critically — loadable through the config normalizer.

Parity (every English source has a zh_cn entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check` is
the guard that flags newly-added strings missing from the zh_cn catalog. Chinese is
single-form (%n is just the number), so there is no numerus-form-count check.

The round-trip test is the one that matters for this language: zh_cn is a mixed-case
region code, and `AnkiMinerConfig.__post_init__` lowercases `ui_language`, so the
catalog filename/key MUST be lowercase or the shipped app silently falls back to
English. Passing `install_translators` a literal string (as the single-code language
tests do) would hide that break; here we route through the real config field.
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


def test_chinese_simplified_is_registered():
    assert i18n.available_languages()["zh_cn"] == "简体中文"


def test_zh_cn_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_zh_cn.ts")  # raises on malformed XML


def test_zh_cn_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_zh_cn.ts").getroot()
    assert root.get("language") == "zh_CN"


def test_zh_cn_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    zh = _sources_by_context(TS_DIR / "anki_miner_zh_cn.ts")
    for context, sources in en.items():
        assert context in zh, f"zh_cn catalog missing context {context!r}"
        missing = sources - zh[context]
        assert not missing, f"zh_cn context {context!r} missing sources: {sorted(missing)[:5]}"


def test_zh_cn_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_zh_cn.qm")) is True


def test_install_translators_zh_cn_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "zh_cn")
    try:
        assert any("anki_miner_zh_cn" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)


def test_config_ui_language_round_trip_loads_zh_cn(qapp):
    """Mixed-case zh_CN must survive config normalization AND still load the catalog
    — the regression guard for the case-sensitive-filename landmine."""
    cfg = AnkiMinerConfig(ui_language="zh_CN")
    assert cfg.ui_language == "zh_cn"  # __post_init__ lowercases
    result = i18n.install_translators(qapp, cfg.ui_language)
    try:
        assert any("anki_miner_zh_cn" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
