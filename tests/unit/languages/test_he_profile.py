"""The Hebrew profile, its scoped defaults, its catalogue and its audio (spec F.2).

Also the one place the FULL ``import_yomitan_zip(language="he")`` route runs: that importer resolves
its key folding from the registry, so it needs Hebrew registered, and a mismatch between the import
fold and the query fold is silent -- rows land under keys no query ever builds.
"""

from __future__ import annotations

import json
import unicodedata
import zipfile
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.he import HE_CARD_FIELDS, HE_SMOKE_SENTENCE, build_profile
from anki_miner.languages.he.audio import HE_AUDIO
from anki_miner.languages.he.catalog import HE_CATALOG
from anki_miner.languages.he.morphology import HebrewLemmaPass, HebrewMinedForm, HebrewReadingSupport
from anki_miner.languages.he.pos import HE_ALLOWED_POS, HE_EXCLUDED_SUBTYPES
from anki_miner.languages.he.script import HebrewDictKeys, HebrewScript, he_fold, he_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.resource_catalog import RESOURCE_KINDS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
WTY = json.loads((FIXTURES / "wty_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def profile():
    return get_profile("he")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_hebrew_is_a_registered_mining_language(profile):
    assert "he" in AVAILABLE_LANGUAGES
    assert profile.code == "he"
    assert profile is get_profile("he")


def test_the_two_code_tuples_stay_identical():
    from anki_miner.config.config import _LANGUAGE_CODES

    assert _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_the_profile_builds_without_the_registry():
    """``build_profile`` must never reach back into ``get_profile`` (non-reentrant lock)."""
    assert build_profile().code == "he"


def test_the_display_name_is_native_and_the_english_name_is_ascii(profile):
    assert profile.display_name and all(0x05D0 <= ord(c) <= 0x05EA for c in profile.display_name)
    assert profile.english_name == "Hebrew"


# --------------------------------------------------------------------------
# The seams the profile fills
# --------------------------------------------------------------------------


def test_the_profile_wires_the_hebrew_seams(profile):
    assert isinstance(profile.mined_form, HebrewMinedForm)
    assert isinstance(profile.reading, HebrewReadingSupport)
    assert isinstance(profile.script, HebrewScript)
    assert isinstance(profile.dict_keys, HebrewDictKeys)
    assert profile.normalize is he_normalize
    assert profile.dedup_fold is he_fold
    assert profile.sentence_annotator is None
    assert profile.unavailable_reason is None, "Hebrew downloads nothing, so it can always mine"


def test_the_dedup_fold_and_the_key_fold_are_one_function(profile):
    """R6/R7 stay distinct seams; Hebrew binds both to the same callable."""
    assert profile.dedup_fold("x") == profile.dict_keys.fold_term("x")
    assert profile.dedup_fold is he_fold


def test_the_media_and_caption_codes_are_the_probed_ones(profile):
    assert profile.audio_track_codes == frozenset({"heb", "he", "iw", "hebrew"})
    assert profile.import_encodings == ("utf-8-sig", "cp1255")
    assert profile.asr_language == "he"
    assert profile.captions.primary == "iw"
    assert profile.captions.codes == ("iw", "he")
    assert profile.captions.orig_codes == ("iw-orig", "he-orig")
    assert profile.captions.bare_fallback is True


def test_the_capabilities_are_the_declared_ones(profile):
    assert profile.capabilities == frozenset(
        {
            "hebrew_transliteration",
            "hebrew_binyan",
            "word_root",
            "noun_gender",
            "noun_plural",
            "pos_tag",
            "rtl",
        }
    )
    assert "lemmatised_frequency" not in profile.capabilities, "no tagger, so nothing to lemmatise with"
    assert "wiktionary_audio" not in profile.capabilities


# --------------------------------------------------------------------------
# Scoped defaults
# --------------------------------------------------------------------------


def test_a_first_switch_lands_on_the_hebrew_defaults():
    config = switch_language(AnkiMinerConfig(), "he")
    assert config.language == "he"
    assert config.downloader_subtitle_langs == "iw"
    assert config.anki_fields["expression_reading"] == "Reading"
    assert config.expression_audio_chain == (AudioSourceEntry(kind="googletts"),)
    assert tuple(config.allowed_pos) == HE_ALLOWED_POS
    assert tuple(config.excluded_subtypes) == HE_EXCLUDED_SUBTYPES
    assert config.anki_deck_name == "Anki Miner"
    assert config.script_variant == ""


def test_every_extra_field_ships_unmapped_so_the_user_turns_it_on():
    for spec in get_profile("he").extra_card_fields:
        assert HE_CARD_FIELDS[spec.key] == ""


def test_the_japanese_only_fields_are_unmapped():
    for key in ("expression_furigana", "sentence_furigana", "pitch_position", "pitch_graph"):
        assert HE_CARD_FIELDS[key] == ""


# --------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------


def test_google_translate_is_addressed_by_its_legacy_code():
    from gtts.lang import tts_langs

    table = tts_langs()
    assert HE_AUDIO.gtts_lang == "iw"
    assert "iw" in table
    assert "he" not in table, "gTTS validates lang against its own table; he raises before sending"


def test_the_edge_voice_is_set_but_google_stays_the_default_leg():
    import re

    assert re.fullmatch(r"[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural", HE_AUDIO.edge_voice)
    assert HE_AUDIO.default_chain == (AudioSourceEntry(kind="googletts"),)


def test_the_cache_stems_are_namespaced_by_language():
    assert HE_AUDIO.cache_stem_prefix == "googletts_he"
    assert HE_AUDIO.sentence_cache_stem_prefix == "sentencetts_he"
    assert HE_AUDIO.custom_fetcher_language == "he"
    assert HE_AUDIO.papago_speaker is None


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


def test_the_catalogue_is_the_dictionary_and_one_frequency_list():
    assert [spec.id for spec in HE_CATALOG] == ["wty-he-en", "opensubtitles-he"]
    assert {spec.kind for spec in HE_CATALOG} <= RESOURCE_KINDS
    assert len({spec.id for spec in HE_CATALOG}) == len(HE_CATALOG)


def test_the_frequency_list_is_not_lemmatised():
    [freq] = [spec for spec in HE_CATALOG if spec.kind == "freq"]
    assert freq.lemmatise is False
    assert freq.variant == ""


def test_every_row_carries_its_licence_note():
    for spec in HE_CATALOG:
        assert "CC BY-SA 4.0" in spec.license_note
        assert spec.url.startswith("https://")


# --------------------------------------------------------------------------
# The parser factory
# --------------------------------------------------------------------------


def test_the_factory_builds_the_shared_service_with_the_hebrew_seams():
    from anki_miner.services.subtitle_parser import SubtitleParserService

    config = switch_language(AnkiMinerConfig(), "he")
    parser = get_profile("he").create_parser(config)
    try:
        assert type(parser) is SubtitleParserService
        assert isinstance(parser._token_post_pass, HebrewLemmaPass)
        assert parser._compound_matcher is None
        assert parser._sentence_annotation is False
        assert parser._normalize is he_normalize
    finally:
        from anki_miner.languages.tagger_provider import evict

        evict("he")


def test_the_smoke_sentence_mines_without_a_dictionary():
    """Every front is the folded surface, and nothing raises."""
    from anki_miner.languages.tagger_provider import evict, get_tagger

    try:
        tokens = get_tagger("he")(HE_SMOKE_SENTENCE)
    finally:
        evict("he")
    words = [t for t in tokens if t.feature.pos1 == "WORD"]
    assert len(words) >= 4
    assert all(t.feature.lemma == he_fold(t.surface) for t in words)


# --------------------------------------------------------------------------
# The full importer route (needs the registry, which is why it lives here)
# --------------------------------------------------------------------------


def test_the_importer_writes_the_keys_the_provider_queries(tmp_path):
    """A fold mismatch between import and query is silent: every lookup would miss."""
    archive = tmp_path / "wty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(WTY["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(WTY["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(WTY["term_rows"]))
    result = import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-he-en", language="he")
    assert result.entry_count > 500

    provider = IndexedDictProvider(
        "wty-he-en", tmp_path / "dicts" / "wty-he-en" / "index.sqlite", keys=get_profile("he").dict_keys
    )
    assert provider.load()
    try:
        kelev = "".join(unicodedata.lookup(f"HEBREW LETTER {n}") for n in ("KAF", "LAMED", "BET"))
        pointed = [row[0] for row in WTY["term_rows"] if he_fold(row[0]) == kelev and row[0] != kelev]
        assert provider.term_rows([kelev])[kelev], "the bare spelling must resolve"
        for spelling in pointed[:3]:
            assert provider.term_rows([spelling])[spelling], f"{spelling!r} must fold onto the same key"
    finally:
        provider.close()


def test_the_source_language_is_declared_so_no_mismatch_warning_fires(tmp_path):
    archive = tmp_path / "wty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(WTY["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(WTY["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(WTY["term_rows"]))
    result = import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-he-en", language="he")
    assert result.source_language == "he"
    assert result.source_language_mismatch is False
