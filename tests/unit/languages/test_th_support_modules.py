"""th audio, card-field, content-style, catalogue and availability data."""

from __future__ import annotations

from types import SimpleNamespace

from anki_miner.languages.th.audio import TH_AUDIO, th_audio_candidates
from anki_miner.languages.th.availability import TH_REQUIRED_PACKAGES, th_missing_required_reason
from anki_miner.languages.th.catalog import TH_CATALOG
from anki_miner.languages.th.fields import TH_CARD_FIELD_DEFAULTS
from anki_miner.languages.th.style import TH_CONTENT_STYLE, TH_FONT_FAMILIES
from anki_miner.services.resource_catalog import RESOURCE_KINDS


def test_google_speaks_thai_script_never_the_paiboon_reading():
    assert TH_AUDIO.gtts_lang == "th"
    assert TH_AUDIO.speakable("น้ำ", "náam") == "น้ำ"


def test_cache_stems_are_namespaced_per_language():
    assert TH_AUDIO.cache_stem_prefix == "googletts_th"
    assert TH_AUDIO.sentence_cache_stem_prefix == "sentencetts_th"
    assert TH_AUDIO.custom_fetcher_language == "th"
    assert TH_AUDIO.edge_voice == ""


def test_default_chain_is_the_google_voice_only():
    assert [entry.kind for entry in TH_AUDIO.default_chain] == ["googletts"]


def test_audio_candidates_are_one_pair_and_drop_the_abbreviation_mark():
    word = SimpleNamespace(mined_form="กรุงเทพฯ", expression_reading="grung-têep")
    assert th_audio_candidates(word) == [("กรุงเทพฯ", "grung-têep"), ("กรุงเทพ", "grung-têep")]
    assert th_audio_candidates(SimpleNamespace(mined_form="", expression_reading="")) == []


def test_furigana_keys_map_to_nothing_and_the_hook_keys_exist():
    assert TH_CARD_FIELD_DEFAULTS["expression_furigana"] == ""
    assert TH_CARD_FIELD_DEFAULTS["sentence_furigana"] == ""
    assert TH_CARD_FIELD_DEFAULTS["reading_paiboon"] == ""
    assert TH_CARD_FIELD_DEFAULTS["classifier"] == ""


def test_content_style_declares_the_thai_writing_system_and_the_bundled_face():
    assert TH_CONTENT_STYLE.font_role == "th"
    assert TH_CONTENT_STYLE.direction == "ltr"
    assert TH_CONTENT_STYLE.writing_system == "Thai"
    assert TH_CONTENT_STYLE.bundled_fallback == "NotoSansThai-Regular.ttf"
    assert TH_CONTENT_STYLE.families == TH_FONT_FAMILIES
    assert TH_CONTENT_STYLE.wrap("วันนี้") == "วันนี้"  # identity until Task 11


def test_catalog_lists_the_wiktionary_dictionary():
    ids = [spec.id for spec in TH_CATALOG]
    assert ids == ["wty-th-en", "tnc-th", "ttc-th"]
    spec = TH_CATALOG[0]
    assert spec.kind in RESOURCE_KINDS and spec.kind == "dict"
    assert spec.url.endswith("/latest/dict/th/en/wty-th-en.zip")
    assert "CC BY-SA 4.0" in spec.license_note
    assert spec.lemmatise is False


def test_availability_names_pythainlp_and_says_nothing_when_it_is_there():
    assert TH_REQUIRED_PACKAGES == ("pythainlp",)
    assert th_missing_required_reason() is None  # pythainlp is installed in .venv and PY311


def test_catalog_lists_the_two_self_hosted_frequency_assets():
    for spec in TH_CATALOG[1:]:
        assert spec.kind == "freq"
        assert spec.lemmatise is False  # Thai has no inflection to aggregate
        assert "CC0" in spec.license_note
        assert spec.url.startswith("https://github.com/0xzerolight/anki_miner/releases/download/resources-")
