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

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QTranslator

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
    Lang("ar", "العربية", "ar_SA", 6),
    Lang("yue", "粵語", "yue_HK", None, "YUE"),
    Lang("sl", "Slovenščina", "sl_SI", 4),
    Lang("ko", "한국어", "ko_KR", None),
    Lang("pt_pt", "Português (Portugal)", "pt_PT", 2, "pt_PT"),
    Lang("nl", "Nederlands", "nl_NL", 2),
    Lang("th", "ไทย", "th_TH", None),
    Lang("he", "עברית", "he_IL", 2),
    Lang("fa", "فارسی", "fa_IR", None),
    Lang("ca", "Català", "ca_ES", 2),
    Lang("nb", "Norsk bokmål", "nb_NO", 2),
    Lang("sv", "Svenska", "sv_SE", 2),
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


def test_every_registered_language_has_a_row():
    assert set(i18n.available_languages()) - {"en"} == {lang.code for lang in LANGUAGES}


_BRACE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


@pytest.mark.parametrize("lang", _ALL)
def test_brace_placeholders_match_source(lang):
    """A translated {name} placeholder compiles, then KeyErrors at format time."""
    root = ET.parse(TS_DIR / f"anki_miner_{lang.code}.ts").getroot()
    for msg in root.iter("message"):
        tr = msg.find("translation")
        if tr is None or tr.get("type") == "unfinished":
            continue
        source = msg.find("source").text or ""
        want = sorted(_BRACE.findall(source))
        texts = [form.text or "" for form in tr.findall("numerusform")] or [tr.text or ""]
        for text in texts:
            assert sorted(_BRACE.findall(text)) == want, (source, text)


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


# n -> the plural form Qt picks, from lrelease's rules for the catalog's locale.
_PLURAL_SLOTS = {
    "ar": {0: 0, 1: 1, 2: 2, 3: 3, 10: 3, 11: 4, 99: 4, 100: 5, 102: 5},
    "sl": {1: 0, 101: 0, 2: 1, 102: 1, 3: 2, 4: 2, 0: 3, 5: 3, 11: 3},
}


@pytest.mark.parametrize("code", sorted(_PLURAL_SLOTS))
def test_numerus_slot_examples(qapp, code):
    """Each count reaches the form written for its plural class (zero, one, two, few, many…)."""
    root = ET.parse(TS_DIR / f"anki_miner_{code}.ts").getroot()
    translator = QTranslator()
    assert translator.load(str(TS_DIR / f"anki_miner_{code}.qm"))
    qapp.installTranslator(translator)  # QTranslator.translate itself takes ASCII only in PyQt6
    checked = 0
    try:
        for ctx in root.findall("context"):
            context = ctx.find("name").text or ""
            for msg in ctx.findall("message"):
                if msg.get("numerus") != "yes":
                    continue
                source = msg.find("source").text or ""
                forms = [form.text or "" for form in msg.find("translation").findall("numerusform")]
                for n, slot in _PLURAL_SLOTS[code].items():
                    got = QCoreApplication.translate(context, source, None, n)
                    assert got == forms[slot].replace("%n", str(n)), (source, n)
                checked += 1
    finally:
        qapp.removeTranslator(translator)
    assert checked > 40
