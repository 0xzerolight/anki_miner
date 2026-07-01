"""Traditional Chinese catalog: registered, well-formed, structural parity with
English, and — critically — loadable through the config normalizer.

Parity (every English source has a zh_tw entry) is asserted, not completeness — an
untranslated entry falls back to English at runtime, and `scripts/i18n.py check` is
the guard that flags newly-added strings missing from the zh_tw catalog. Chinese is
single-form (%n is just the number), so there is no numerus-form-count check.

zh_tw is a distinct, hand-translated catalog — NOT a machine conversion of zh_cn:
Taiwanese software vocabulary diverges (檔案 vs 文件, 軟體 vs 软件, 設定 vs 设置).

The round-trip test is the one that matters for this language: zh_tw is a mixed-case
region code, and `AnkiMinerConfig.__post_init__` lowercases `ui_language`, so the
catalog filename/key MUST be lowercase or the shipped app silently falls back to
English.
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


def test_chinese_traditional_is_registered():
    assert i18n.available_languages()["zh_tw"] == "繁體中文"


def test_zh_tw_ts_is_well_formed_xml():
    ET.parse(TS_DIR / "anki_miner_zh_tw.ts")  # raises on malformed XML


def test_zh_tw_ts_declares_language():
    root = ET.parse(TS_DIR / "anki_miner_zh_tw.ts").getroot()
    assert root.get("language") == "zh_TW"


def test_zh_tw_has_an_entry_for_every_english_source():
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    zh = _sources_by_context(TS_DIR / "anki_miner_zh_tw.ts")
    for context, sources in en.items():
        assert context in zh, f"zh_tw catalog missing context {context!r}"
        missing = sources - zh[context]
        assert not missing, f"zh_tw context {context!r} missing sources: {sorted(missing)[:5]}"


def test_zh_tw_qm_loads(qapp):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / "anki_miner_zh_tw.qm")) is True


def test_install_translators_zh_tw_loads_catalog(qapp):
    result = i18n.install_translators(qapp, "zh_tw")
    try:
        assert any("anki_miner_zh_tw" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)


def test_config_ui_language_round_trip_loads_zh_tw(qapp):
    """Mixed-case zh_TW must survive config normalization AND still load the catalog
    — the regression guard for the case-sensitive-filename landmine."""
    cfg = AnkiMinerConfig(ui_language="zh_TW")
    assert cfg.ui_language == "zh_tw"  # __post_init__ lowercases
    result = i18n.install_translators(qapp, cfg.ui_language)
    try:
        assert any("anki_miner_zh_tw" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
