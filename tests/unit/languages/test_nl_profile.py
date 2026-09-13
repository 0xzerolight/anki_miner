"""The Dutch profile: data, wiring, catalogue and registration (hard-requires spaCy + nl_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX, LatinScript
from anki_miner.languages.nl.abbreviations import NL_ABBREVIATIONS
from anki_miner.languages.nl.catalog import NL_CATALOG
from anki_miner.languages.nl.morphology import NL_EXCLUDED_SUBTYPES, dutch_particle_candidates, nl_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "nl" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["nl"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[nl]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("nl")
    assert (profile.code, profile.display_name, profile.english_name) == ("nl", "Nederlands", "Dutch")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is nl_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"nld", "dut", "nl", "dutch"})
    assert profile.asr_language == "nl" and profile.wiktionary_code == ""
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("nl", ("nl",), ("nl-orig",))
    assert captions.audio_pattern == "^nl(-|$)" and captions.bare_fallback is True
    assert profile.capabilities == frozenset({"pos_tag", "noun_article", "noun_gender", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_article", "noun_gender")
    assert profile.pos_defaults.excluded_subtypes == NL_EXCLUDED_SUBTYPES
    assert profile.smoke_sentence == "De student las gisteren een interessant boek."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_sentence_rules_carry_the_abbreviations_and_the_dutch_opener():
    rules = get_profile("nl").sentence_rules
    assert rules.abbreviations == NL_ABBREVIATIONS and rules.space_aware is True
    assert "„" in rules.openers and "”" in rules.closers and "’" not in rules.closers


def test_audio_speaks_the_front_through_google():
    audio = get_profile("nl").audio
    stems = (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix)
    assert stems == ("nl", "googletts_nl", "sentencetts_nl")
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("opbellen", "") == "opbellen"


def test_scoped_defaults_turn_on_the_latin_sdh_filter_and_the_dutch_gate():
    config = switch_language(AnkiMinerConfig(), "nl")
    assert config.language == "nl" and config.downloader_subtitle_langs == "nl"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == NL_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == config.anki_fields["noun_article"] == config.anki_fields["noun_gender"] == ""


def test_a_known_word_front_meets_the_mined_lemma():
    fold = get_profile("nl").dedup_fold
    assert fold is not None
    assert fold("het boek") == fold("Boek") == "boek"
    assert fold("zich vergissen") == "vergissen" and fold("’t huis") == "huis"
    assert fold("de") == "de"


def test_the_tagger_repairs_hyphens_stashes_particles_and_splits_sentence_final_words():
    tagger = get_tagger("nl")
    features = {t.surface: t.feature for t in tagger("Hij had een auto-ongeluk en belde zijn broer op.")}
    assert features["auto-ongeluk"].lemma == "auto-ongeluk"
    assert features["belde"].particle == "op" and features["op"].pos1 == "PART"
    assert [(t.surface, t.feature.pos1) for t in tagger("Geef me je hand.")][-2:] == [("hand", "NOUN"), (".", "PUNCT")]
    assert ("Dhr.", "X") in [(t.surface, t.feature.pos1) for t in tagger("Dhr. Jansen komt.")]


def test_the_parser_joins_separable_verbs_in_the_dutch_order():
    profile = get_profile("nl")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "nl"))
    assert parser.normalize is profile.normalize and parser._compound_matcher is None
    assert isinstance(parser._token_post_pass, SeparableVerbPass)
    assert parser._token_post_pass._candidates is dutch_particle_candidates


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in NL_CATALOG}
    assert set(by_id) == {"wty-nl-en", "opensubtitles-nl"}
    dictionary, frequency = by_id["wty-nl-en"], by_id["opensubtitles-nl"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/nl/en/wty-nl-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/nl/nl_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in NL_CATALOG)
    assert get_profile("nl").catalog == NL_CATALOG
