"""The Greek profile: data, wiring, catalogue and registration (hard-requires spaCy + el_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD, LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import GreekScript, nfc_normalize
from anki_miner.languages.el import catalog as el_catalog
from anki_miner.languages.el.catalog import EL_CATALOG
from anki_miner.languages.el.morphology import EL_ABBREVIATIONS, EL_SUBTITLE_REGEX, EL_TERMINATORS
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "el" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["el"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[el]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("el")
    assert (profile.code, profile.display_name, profile.english_name) == ("el", "Ελληνικά", "Greek")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, GreekScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is nfc_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1253")
    assert profile.audio_track_codes == frozenset({"ell", "gre", "el", "greek"})
    assert profile.asr_language == "el" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "el",
        ("el",),
        ("el-orig",),
    )
    assert profile.captions.audio_pattern == "^el(-|$)" and profile.captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (DECIDED 6)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender", "aspect_pair")
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Το βιβλίο είναι στο σπίτι."
    assert profile.sentence_rules.abbreviations == EL_ABBREVIATIONS
    assert profile.sentence_rules.terminators == EL_TERMINATORS


def test_normalize_folds_the_greek_question_mark_and_polytonic_oxia():
    normalize = get_profile("el").normalize
    assert normalize("Τι κάνεις\u037e") == "Τι κάνεις;"
    assert normalize("\u1f71λλο") == "άλλο"  # ά with oxia composes to tonos


def test_the_probe_ladder_offers_nothing_but_real_variants():
    lookup = get_profile("el").lookup
    assert lookup.candidates("έγραψα", "", None) == []
    assert lookup.candidates("γράφω", "Έγραψα", None) == [("Έγραψα", 0), ("έγραψα", 0)]


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("el").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "el",
        "googletts_el",
        "sentencetts_el",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("γράφω", "") == "γράφω"
    assert audio.candidates is not None


def test_scoped_defaults_turn_on_the_greek_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "el")
    assert config.language == "el" and config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == EL_SUBTITLE_REGEX
    assert {key: config.anki_fields[key] for key in ("pos", "noun_gender", "aspect_pair")} == {
        "pos": "",
        "noun_gender": "",
        "aspect_pair": "",
    }
    assert config.downloader_subtitle_langs == "el"
    assert [entry.kind for entry in config.expression_audio_chain] == ["googletts"]


def test_a_deck_front_with_an_article_meets_the_mined_lemma():
    fold = get_profile("el").dedup_fold
    assert fold is not None
    assert fold("το βιβλίο") == fold("βιβλίο") and fold("Ένας φίλος") == fold("φίλος")
    assert fold(fold("Η Οδός")) == fold("Η Οδός") == fold("οδός")


def test_the_parser_is_the_spaced_factory_with_no_post_pass():
    profile = get_profile("el")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "el"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None
    assert parser._token_post_pass is None


def test_the_tagger_folds_apostrophes_and_prunes_dotted_words():
    from anki_miner.languages.tagger_provider import get_tagger

    tagger = get_tagger("el")  # LockedTagger.__getattr__ forwards to the SpacyTagger
    assert tagger._tag_char_map == APOSTROPHE_FOLD
    rules = tagger.nlp.tokenizer.rules
    assert "κ." in rules and "κ.λπ." in rules
    assert "Νικ." not in rules and "αν." not in rules


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in EL_CATALOG}
    assert set(by_id) == {"wty-el-en", "opensubtitles-el"}
    dictionary, frequency = by_id["wty-el-en"], by_id["opensubtitles-el"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/el/en/wty-el-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert (
        frequency.url == "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/el/el_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in EL_CATALOG)
    doc = el_catalog.__doc__ or ""
    assert "CC BY-NC-SA 3.0" in doc and "wty-el-el" in doc and "wty-el-en-gloss" in doc and "SUBTLEX-GR" in doc
    assert get_profile("el").catalog == EL_CATALOG
