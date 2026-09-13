"""The Spanish profile: data, wiring, catalogue and registration (hard-requires spaCy + es_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.es.catalog import ES_CATALOG
from anki_miner.languages.es.morphology import ES_ABBREVIATIONS, ES_EXCLUDED_SUBTYPES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services.reading.sentence_splitter import split_sentences

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "es" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["es"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[es]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("es")
    assert (profile.code, profile.display_name, profile.english_name) == ("es", "Español", "Spanish")
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    # Mining path: word = the front, orth_base = the surface (en contract items 2-3). Plan D2: no enclitic rung.
    assert profile.lookup.candidates("dar", "Dámelo", None) == [("Dámelo", 0), ("dámelo", 0)]
    assert profile.mined_form.lookup_alternate(SimpleNamespace(surface="Dámelo")) == "Dámelo"
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"spa", "es", "spanish", "esp"})
    assert profile.asr_language == "es" and profile.wiktionary_code == ""
    assert profile.captions.primary == "es" and profile.captions.orig_codes == ("es-orig",)
    assert profile.captions.codes == ("es", "es-419", "es-ES", "es-US")
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender",)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == ES_EXCLUDED_SUBTYPES == ()
    assert profile.smoke_sentence == "El perro corre por el parque."


def test_the_gender_field_prints_the_article_and_morph_answers_without_a_dictionary():
    hook = get_profile("es").render_hooks[1]
    word = SimpleNamespace(pos="NOUN", morph="Gender=Fem|Number=Sing", definition_html="")
    assert hook.render(word, config=AnkiMinerConfig()) == {"noun_gender": "la"}


def test_audio_speaks_the_front_through_google():
    audio = get_profile("es").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "es",
        "googletts_es",
        "sentencetts_es",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("comer", "") == "comer"


def test_scoped_defaults_turn_on_the_latin_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "es")
    assert config.language == "es"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.downloader_subtitle_langs == "es"


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("es").dedup_fold
    assert fold is not None
    assert fold("el perro") == fold("perro") == "perro"
    assert fold("La casa.") == fold("casa")
    assert fold("levantarse") != fold("levantar")  # plan D13: only leading words fold


def test_abbreviations_keep_a_sentence_whole():
    rules = get_profile("es").sentence_rules
    assert rules.abbreviations == ES_ABBREVIATIONS
    text = "Por ejemplo, p. ej. esto funciona. La Sra. López vive en EE. UU. desde las 3 p. m. del lunes. ¿Vienes?"
    assert split_sentences(text, rules=rules) == [
        "Por ejemplo, p. ej. esto funciona.",
        "La Sra. López vive en EE. UU. desde las 3 p. m. del lunes.",
        "¿Vienes?",
    ]


def test_the_parser_is_the_spaced_factory():
    profile = get_profile("es")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "es"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in ES_CATALOG}
    assert set(by_id) == {"wty-es-en", "opensubtitles-es"}
    dictionary, frequency = by_id["wty-es-en"], by_id["opensubtitles-es"]
    assert dictionary.kind == "dict"
    assert dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/es/en/wty-es-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/es/es_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in ES_CATALOG)
    assert get_profile("es").catalog == ES_CATALOG
