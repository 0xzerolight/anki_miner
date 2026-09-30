"""Per-language catalog checks, parametrized across every shipped UI language.

Each catalog is asserted for: registration, well-formed XML, the declared locale,
structural parity with English (every EN source is present — an untranslated entry
falls back to English at runtime, and `scripts/i18n.py check` is the guard that
flags newly-added strings missing from a catalog), QM loadability, and loading
through `install_translators`.

Languages with a plural rule (`numerus_forms` == 2 or 3) additionally assert the
translated numerus form count: Russian needs three forms (one/few/many); the
two-form languages (de/fr/es/it/pt_br) need two. Single-form languages
(zh_cn/zh_tw/vi/id/ja — `%n` is just the number) have no numerus check.

Mixed-case region codes (pt_br, zh_cn, zh_tw) add a config-normalizer round-trip
guard: `AnkiMinerConfig.__post_init__` lowercases `ui_language`, so the catalog
filename/key MUST be lowercase or the shipped app silently falls back to English.

German keeps an extra completeness gate in `test_i18n_german.py`; `de` is also
covered by the generic rows here (harmless double coverage).
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from PyQt6.QtCore import QTranslator

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui import i18n

TS_DIR = Path(__file__).resolve().parents[2] / "anki_miner" / "gui" / "resources" / "translations"


@dataclass(frozen=True)
class Lang:
    code: str
    display_name: str
    locale: str
    numerus_forms: int | None
    mixed_case_input: str | None = None


LANGUAGES = [
    Lang("ru", "Русский", "ru_RU", 3),
    Lang("fr", "Français", "fr_FR", 2),
    Lang("de", "Deutsch", "de_DE", 2),
    Lang("es", "Español", "es_ES", 2),
    Lang("it", "Italiano", "it_IT", 2),
    Lang("pt_br", "Português (Brasil)", "pt_BR", 2, "pt_BR"),
    Lang("zh_cn", "简体中文", "zh_CN", None, "zh_CN"),
    Lang("zh_tw", "繁體中文", "zh_TW", None, "zh_TW"),
    Lang("id", "Bahasa Indonesia", "id_ID", None),
    Lang("ja", "日本語", "ja_JP", None),
    Lang("vi", "Tiếng Việt", "vi_VN", None),
]

_ALL = [pytest.param(lang, id=lang.code) for lang in LANGUAGES]
_NUMERUS = [pytest.param(lang, id=lang.code) for lang in LANGUAGES if lang.numerus_forms is not None]
_MIXED_CASE = [pytest.param(lang, id=lang.code) for lang in LANGUAGES if lang.mixed_case_input]


def _sources_by_context(ts_path: Path) -> dict[str, set[str]]:
    root = ET.parse(ts_path).getroot()
    out: dict[str, set[str]] = {}
    for ctx in root.findall("context"):
        name = ctx.find("name").text or ""
        out[name] = {(m.find("source").text or "") for m in ctx.findall("message")}
    return out


@pytest.mark.parametrize("lang", _ALL)
def test_language_is_registered(lang):
    assert i18n.available_languages()[lang.code] == lang.display_name


@pytest.mark.parametrize("lang", _ALL)
def test_ts_is_well_formed_xml(lang):
    ET.parse(TS_DIR / f"anki_miner_{lang.code}.ts")  # raises on malformed XML


@pytest.mark.parametrize("lang", _ALL)
def test_ts_declares_language(lang):
    root = ET.parse(TS_DIR / f"anki_miner_{lang.code}.ts").getroot()
    assert root.get("language") == lang.locale


@pytest.mark.parametrize("lang", _ALL)
def test_has_an_entry_for_every_english_source(lang):
    en = _sources_by_context(TS_DIR / "anki_miner_en.ts")
    other = _sources_by_context(TS_DIR / f"anki_miner_{lang.code}.ts")
    for context, sources in en.items():
        assert context in other, f"{lang.code} catalog missing context {context!r}"
        missing = sources - other[context]
        assert not missing, f"{lang.code} context {context!r} missing sources: {sorted(missing)[:5]}"


@pytest.mark.parametrize("lang", _NUMERUS)
def test_translated_numerus_have_expected_forms(lang):
    """Languages with a plural rule: every *translated* numerus entry must carry
    exactly `numerus_forms` <numerusform>s. Unfinished entries fall back to English
    and are skipped (a valid drift-free state that `check` also accepts)."""
    root = ET.parse(TS_DIR / f"anki_miner_{lang.code}.ts").getroot()
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            if msg.get("numerus") != "yes":
                continue
            tr = msg.find("translation")
            if tr is None or tr.get("type") == "unfinished":
                continue  # untranslated falls back to English
            forms = tr.findall("numerusform")
            source = msg.find("source").text or ""
            assert (
                len(forms) == lang.numerus_forms
            ), f"numerus {source!r} has {len(forms)} forms, expected {lang.numerus_forms}"


@pytest.mark.parametrize("lang", _ALL)
def test_qm_loads(qapp, lang):
    translator = QTranslator()
    assert translator.load(str(TS_DIR / f"anki_miner_{lang.code}.qm")) is True


@pytest.mark.parametrize("lang", _ALL)
def test_install_translators_loads_catalog(qapp, lang):
    result = i18n.install_translators(qapp, lang.code)
    try:
        assert any(f"anki_miner_{lang.code}" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)


@pytest.mark.parametrize("lang", _MIXED_CASE)
def test_config_ui_language_round_trip_loads_catalog(qapp, lang):
    """Mixed-case region codes must survive config normalization AND still load the
    catalog — the regression guard for the case-sensitive-filename landmine."""
    cfg = AnkiMinerConfig(ui_language=lang.mixed_case_input)
    assert cfg.ui_language == lang.code  # __post_init__ lowercases
    result = i18n.install_translators(qapp, cfg.ui_language)
    try:
        assert any(f"anki_miner_{lang.code}" in t.filePath() for t in result)
    finally:
        for t in result:
            qapp.removeTranslator(t)
