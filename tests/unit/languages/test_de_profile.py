"""The German profile: data, wiring, sentence rules, known-word fold, catalogue and registration."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, NOUN_PLURAL_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.de.catalog import DE_CATALOG
from anki_miner.languages.de.morphology import DE_ABBREVIATIONS, DE_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.reading.sentence_splitter import split_sentences

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "de" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["de"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[de]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("de")
    assert (profile.code, profile.display_name, profile.english_name) == ("de", "Deutsch", "German")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"ger", "deu", "de", "german"})
    assert profile.asr_language == "de" and profile.wiktionary_code == ""
    assert profile.captions.primary == "de" and profile.captions.codes == ("de", "de-AT", "de-CH")
    assert profile.captions.orig_codes == ("de-orig",) and profile.captions.audio_pattern == "^de(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "noun_plural", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, NOUN_PLURAL_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender", "noun_plural")
    assert profile.smoke_sentence == "Er sieht sich den Film an."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == DE_EXCLUDED_SUBTYPES


def test_audio_speaks_the_front_through_google():
    audio = get_profile("de").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "de",
        "googletts_de",
        "sentencetts_de",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("Hund", "") == "Hund"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "de")
    assert config.language == "de"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == DE_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert {config.anki_fields[key] for key in ("pos", "noun_gender", "noun_plural")} == {""}
    assert config.downloader_subtitle_langs == "de"


def test_deck_fronts_fold_onto_the_mined_lemma():
    fold = get_profile("de").dedup_fold
    assert fold is not None
    assert fold("der Hund") == fold("Hund") == "hund"
    assert fold("sich freuen") == fold("freuen") == "freuen"
    assert fold("Straße") == fold("STRASSE") == fold("die Strasse") == "strasse"
    assert fold(fold("das Haus")) == fold("das Haus")


def test_abbreviations_and_german_quotes_in_the_sentence_splitter():
    rules = get_profile("de").sentence_rules
    assert rules.abbreviations == DE_ABBREVIATIONS
    assert split_sentences("Wir treffen uns z.B. am Bahnhof. Dann gehen wir.", rules=rules) == [
        "Wir treffen uns z.B. am Bahnhof.",
        "Dann gehen wir.",
    ]
    assert len(split_sentences("Obst, z. B. Äpfel, ist gesund. Er isst gern.", rules=rules)) == 2
    assert len(split_sentences("Ich mache das so. Dann gehen wir.", rules=rules)) == 2
    assert len(split_sentences("Das ist Max. Er ist nett.", rules=rules)) == 2
    paragraph = (
        "„Das ist gut“, sagte er. Dann ging er nach Hause. Es regnete. "
        "Sie blieb (wie immer) zu Hause. »Komm her!«, rief sie. Er kam."
    )
    assert len(split_sentences(paragraph, rules=rules)) == 6


def test_the_lookup_ladder_offers_the_particle_less_verb():
    assert get_profile("de").lookup.candidates("ansehen", "ansehen", None) == [("sehen", 0)]
    assert get_profile("de").lookup.candidates("sah", "", None) == []


def test_the_parser_carries_the_separable_verb_pass():
    profile = get_profile("de")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "de"))
    assert isinstance(parser._token_post_pass, SeparableVerbPass)
    assert parser.normalize is profile.normalize and parser._compound_matcher is None


def test_the_catalogue_ships_the_bilingual_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in DE_CATALOG}
    assert set(by_id) == {"wty-de-en", "opensubtitles-de"}
    dictionary, frequency = by_id["wty-de-en"], by_id["opensubtitles-de"]
    assert dictionary.kind == "dict"
    assert (
        dictionary.url
        == "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/de/en/wty-de-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert (
        frequency.url == "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in DE_CATALOG)
    assert get_profile("de").catalog == DE_CATALOG
