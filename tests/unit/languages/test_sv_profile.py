"""The Swedish profile: data, wiring, catalogue and registration (hard-requires spaCy + sv_core_news_sm)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES, AudioSourceEntry
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_ARTICLE_FIELD, NOUN_PLURAL_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SeparableVerbPass, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.registry import get_profile
from anki_miner.languages.sv.morphology import (
    SV_EXCLUDED_SUBTYPES,
    SV_SUBTITLE_REGEX,
    sv_normalize,
    swedish_particle_candidates,
)
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger

ROOT = Path(__file__).resolve().parents[3]


def test_swedish_is_registered_in_both_code_tuples_and_the_extras():
    assert "sv" in AVAILABLE_LANGUAGES and "sv" in _LANGUAGE_CODES
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert extras["sv"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[sv]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("sv")
    assert (profile.code, profile.display_name, profile.english_name) == ("sv", "Svenska", "Swedish")
    assert isinstance(profile.script, LatinScript) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.audio_track_codes == frozenset({"swe", "sv", "swedish"})
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.normalize is sv_normalize and profile.wiktionary_code == ""
    assert profile.capabilities == frozenset({"pos_tag", "noun_article", "noun_plural", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_ARTICLE_FIELD, NOUN_PLURAL_FIELD)
    hooks = profile.render_hooks
    assert isinstance(hooks[0], PosHook) and isinstance(hooks[1], GrammarTagHook)
    assert hooks[1].field_names() == ("noun_article", "noun_plural")
    assert profile.smoke_sentence == "Studenten läste en intressant bok."


def test_the_scoped_defaults_carry_the_swedish_subtitle_filter_and_pos_gate():
    config = switch_language(AnkiMinerConfig(), "sv")
    assert config.language == "sv" and config.subtitle_regex_filter == SV_SUBTITLE_REGEX
    assert config.excluded_subtypes == SV_EXCLUDED_SUBTYPES
    assert config.downloader_subtitle_langs == "sv"
    assert config.anki_fields["noun_article"] == "" and config.anki_fields["noun_plural"] == ""
    assert config.expression_audio_chain == (AudioSourceEntry(kind="googletts"),)


def test_the_sentence_rules_take_the_swedish_abbreviations_and_leave_the_quote_a_closer():
    rules = get_profile("sv").sentence_rules
    assert "t.ex" in rules.abbreviations
    assert "”" in rules.closers and "”" not in rules.openers


def test_the_parser_and_tagger_resolve_through_the_registry():
    config = switch_language(AnkiMinerConfig(), "sv")
    parser = get_profile("sv").create_parser(config)
    assert isinstance(parser._token_post_pass, SeparableVerbPass)  # noqa: SLF001 - the nl/de profile tests read it
    assert parser._token_post_pass._candidates is swedish_particle_candidates  # noqa: SLF001
    assert get_tagger("sv") is not None
