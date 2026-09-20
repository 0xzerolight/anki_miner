"""The Turkish profile: data, wiring, catalogue, availability and registration (hard-requires zeyrek)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import POS_FIELD
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tr import availability
from anki_miner.languages.tr.abbreviations import TR_ABBREVIATIONS
from anki_miner.languages.tr.catalog import TR_CATALOG
from anki_miner.languages.tr.morphology import TR_DEDUP_FOLD, TR_KEYS, TR_SUBTITLE_REGEX, tr_normalize
from anki_miner.models.reading import ReadingUnit

ROOT = Path(__file__).resolve().parents[3]


def test_registration():
    assert "tr" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("tr")
    assert (profile.code, profile.display_name, profile.english_name) == ("tr", "Türkçe", "Turkish")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript)
    assert profile.dict_keys is TR_KEYS and profile.dedup_fold is TR_DEDUP_FOLD
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is tr_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1254")
    assert profile.audio_track_codes == frozenset({"tur", "tr", "turkish"})
    assert profile.asr_language == "tr" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("tr", ("tr",), ("tr-orig",))
    assert captions.audio_pattern == "^tr(-|$)" and captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD,)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook]
    assert profile.pos_defaults.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Öğrenci dün ilginç bir kitap okudu."
    assert profile.unavailable_reason is availability.tr_missing_reason and profile.unavailable_reason() is None


def test_the_lookup_ladder_is_the_latin_one():
    """Plan decision 4: the surface rung reaches wty's non-lemma row of the inflected form (Task 8)."""
    lookup = get_profile("tr").lookup
    assert lookup.candidates("kitap", "kitapları", None) == [("kitapları", 0)]
    assert lookup.candidates("okumak", "Okudu", None) == [("Okudu", 0), ("okudu", 0)]


def test_sentence_rules_carry_the_turkish_abbreviations():
    rules = get_profile("tr").sentence_rules
    assert rules.abbreviations == TR_ABBREVIATIONS and rules.space_aware is True


def test_audio_speaks_the_front_through_google():
    audio = get_profile("tr").audio
    stems = (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix)
    assert stems == ("tr", "googletts_tr", "sentencetts_tr")
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("okumak", "") == "okumak"


def test_scoped_defaults_turn_on_the_turkish_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "tr")
    assert config.language == "tr" and config.downloader_subtitle_langs == "tr"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == TR_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == ""


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("tr").dedup_fold
    assert fold is not None
    assert fold("IŞIK") == fold("ışık") == "ışık" and fold("İstanbul") == "istanbul"
    assert fold("Kitap!") == "kitap" and fold("bir kitap") == "bir kitap"


@pytest.fixture(scope="module")
def parser():
    """One zeyrek build per module: ``conftest`` clears the tagger cache per test and a build costs 4.3-5.6 s.

    ``SubtitleParserService.__init__`` resolves ``get_tagger(language)`` eagerly and keeps it on ``self.tagger``,
    so the parser this fixture hands out survives the per-test clear.
    """
    return get_profile("tr").create_parser(switch_language(AnkiMinerConfig(), "tr"))


def test_the_parser_is_the_spaced_factory_without_a_post_pass(parser):
    profile = get_profile("tr")
    assert parser.normalize is profile.normalize and parser._compound_matcher is None
    assert parser._token_post_pass is None


def test_a_line_mines_turkish_dictionary_forms_through_the_parser(parser):
    unit = ReadingUnit(text="İstanbul'da KİTAPLARI okudum.", index=0, location_label="t")
    words, _index, _counts = parser.parse_text_units([unit], False)
    assert sorted(word.mined_form for word in words) == ["kitap", "okumak"]  # PROPN is outside allowed_pos


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in TR_CATALOG}
    assert set(by_id) == {"wty-tr-en", "opensubtitles-tr"}
    dictionary, frequency = by_id["wty-tr-en"], by_id["opensubtitles-tr"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/tr/en/wty-tr-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/tr/tr_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in TR_CATALOG)
    assert get_profile("tr").catalog == TR_CATALOG


def test_availability_names_the_pack_in_a_bundle_and_the_extra_in_a_pip_install(monkeypatch):
    monkeypatch.setattr(availability, "_importable", lambda _name: False)
    monkeypatch.setattr(availability, "_pack_component_present", lambda _code, _name: False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert availability.tr_missing_reason() == (
        "Turkish mining needs the Turkish language pack. Download it in Settings -> Mining Language."
    )
    monkeypatch.setattr(sys, "frozen", False)
    assert 'pip install "anki-miner[tr]"' in (availability.tr_missing_reason() or "")
    monkeypatch.setattr(availability, "_pack_component_present", lambda code, name: (code, name) == ("tr", "zeyrek"))
    assert availability.tr_missing_reason() is None


_WITHOUT_ZEYREK = (
    "import sys\n"
    "sys.modules['zeyrek'] = None\n"
    "from anki_miner.languages.registry import get_profile\n"
    "print(get_profile('tr').display_name)\n"
)


def test_the_profile_builds_without_zeyrek():
    result = subprocess.run(
        [sys.executable, "-c", _WITHOUT_ZEYREK], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert result.stdout.strip().splitlines()[-1] == "Türkçe"
