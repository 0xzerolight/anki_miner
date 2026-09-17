"""The Finnish profile: data, wiring, sentence rules, known-word fold, lookup ladder, catalogue and registration."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import POS_FIELD
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.fi.catalog import FI_CATALOG
from anki_miner.languages.fi.morphology import FI_ABBREVIATIONS, FI_EXCLUDED_SUBTYPES, fi_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.reading.sentence_splitter import split_sentences

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "fi" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["fi"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[fi]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("fi")
    assert (profile.code, profile.display_name, profile.english_name) == ("fi", "Suomi", "Finnish")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is fi_normalize and profile.content_style is SPACED_CONTENT_STYLE
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"fin", "fi", "finnish"})
    assert profile.asr_language == "fi" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("fi", ("fi",), ("fi-orig",))
    assert captions.audio_pattern == "^fi(-|$)" and captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD,)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook]
    assert profile.pos_defaults.excluded_subtypes == FI_EXCLUDED_SUBTYPES
    assert profile.smoke_sentence == "Opiskelija luki mielenkiintoisen kirjan eilen."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_audio_speaks_the_front_through_google():
    audio = get_profile("fi").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "fi",
        "googletts_fi",
        "sentencetts_fi",
    )
    assert audio.custom_fetcher_language == "fi"
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("kirja", "") == "kirja"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "fi")
    assert config.language == "fi"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == FI_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["expression_furigana"] == ""
    assert config.downloader_subtitle_langs == "fi"


def test_deck_fronts_fold_case_and_punctuation_but_never_letters_or_words():
    fold = get_profile("fi").dedup_fold
    assert fold is not None
    assert fold("Kirja") == fold("kirja.") == "kirja"
    assert fold("Äiti") == "äiti" != fold("aiti")
    assert fold("se kirja") == "se kirja"  # no article table: nothing is dropped (D18)
    assert fold(fold("Päärynä!")) == fold("Päärynä!") == "päärynä"


def test_abbreviations_ellipses_and_finnish_quotes_in_the_sentence_splitter():
    rules = get_profile("fi").sentence_rules
    assert rules.abbreviations == FI_ABBREVIATIONS and rules.space_aware is True
    assert split_sentences("Ostin hedelmiä, esim. omenoita. Sitten lähdin kotiin.", rules=rules) == [
        "Ostin hedelmiä, esim. omenoita.",
        "Sitten lähdin kotiin.",
    ]
    assert split_sentences("Hän asuu mm. Tampereella. Me emme.", rules=rules) == [
        "Hän asuu mm. Tampereella.",
        "Me emme.",
    ]
    assert split_sentences("”Tule tänne!” hän huusi. Hän tuli.", rules=rules) == [
        "”Tule tänne!” hän huusi.",
        "Hän tuli.",
    ]
    assert split_sentences("Odota... mitä? Ei mitään.", rules=rules) == ["Odota... mitä?", "Ei mitään."]


def test_the_lookup_ladder_is_surface_then_casefold_with_no_suffix_rung():
    lookup = get_profile("fi").lookup
    assert lookup.candidates("kirja", "Kirjassa", None) == [("Kirjassa", 0), ("kirjassa", 0)]
    assert lookup.candidates("kirjakin", "Kirjakin", None) == [("Kirjakin", 0)]
    assert lookup.candidates("linja-auton", "linja-auton", None) == [("linja", 0), ("auton", 0)]


def test_the_parser_takes_the_shared_seams_and_no_post_pass():
    profile = get_profile("fi")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "fi"))
    assert parser._token_post_pass is None
    assert parser.normalize is profile.normalize and parser._compound_matcher is None


def test_the_catalogue_ships_the_bilingual_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in FI_CATALOG}
    assert set(by_id) == {"wty-fi-en", "opensubtitles-fi"}
    dictionary, frequency = by_id["wty-fi-en"], by_id["opensubtitles-fi"]
    assert dictionary.kind == "dict" and dictionary.lemmatise is False
    assert (
        dictionary.url
        == "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/fi/en/wty-fi-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert (
        frequency.url == "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fi/fi_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in FI_CATALOG)
    assert get_profile("fi").catalog == FI_CATALOG
