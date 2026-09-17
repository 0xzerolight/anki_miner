"""The Romanian profile: data, wiring, catalogue, registration and the smoke leg.

Hard-requires spaCy and ``ro_core_news_sm`` (the mining language's own suite, no ``importorskip``).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui import app as app_module
from anki_miner.gui.capabilities import CAPABILITIES, search
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import LatinScript
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ro.catalog import RO_CATALOG
from anki_miner.languages.ro.morphology import (
    RO_ABBREVIATIONS,
    RO_EXCLUDED_SUBTYPES,
    RO_SUBTITLE_REGEX,
    ro_normalize,
)
from anki_miner.languages.switching import switch_language
from anki_miner.services.frequency import mode_probe, source_importer

ROOT = Path(__file__).resolve().parents[3]
STIINTA = "știință"
CEDILLA_STIINTA = "\u015etiin\u0163ă"  # \u015etiin\u0163ă


def test_registration_and_the_extra():
    assert "ro" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["ro"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[ro]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("ro")
    assert (profile.code, profile.display_name, profile.english_name) == ("ro", "Română", "Romanian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.normalize is ro_normalize
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    assert profile.audio_track_codes == frozenset({"ron", "rum", "ro", "romanian"})
    assert profile.asr_language == "ro" and profile.wiktionary_code == ""
    assert profile.captions.primary == "ro" and profile.captions.codes == ("ro",)
    assert profile.captions.orig_codes == ("ro-orig",) and profile.captions.audio_pattern == "^ro(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert "wiktionary_audio" not in profile.capabilities  # Stage W is not built (DECIDED 6)
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender",)
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == RO_EXCLUDED_SUBTYPES
    assert profile.smoke_sentence == "Studentul a citit o carte interesantă ieri."
    assert profile.sentence_rules.abbreviations == RO_ABBREVIATIONS
    assert profile.dict_keys.fold_term(CEDILLA_STIINTA) == STIINTA  # R35 at the key seam


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("ro").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "ro",
        "googletts_ro",
        "sentencetts_ro",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("carte", "") == "carte"


def test_scoped_defaults_turn_on_the_romanian_sdh_filter():
    config = switch_language(AnkiMinerConfig(), "ro")
    assert config.language == "ro"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == RO_EXCLUDED_SUBTYPES
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == RO_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.downloader_subtitle_langs == "ro"


def test_a_deck_front_with_an_article_or_cedillas_meets_the_mined_lemma():
    fold = get_profile("ro").dedup_fold
    assert fold is not None
    assert fold("o carte") == fold("carte") == "carte"
    assert fold("a merge") == "merge"
    assert fold(CEDILLA_STIINTA) == fold(STIINTA) == STIINTA


def test_the_lookup_ladder_is_the_surface_then_its_casefold():
    lookup = get_profile("ro").lookup
    assert lookup.candidates("carte", "Cărțile", None) == [("Cărțile", 0), ("cărțile", 0)]
    assert lookup.candidates("cărți", "", None) == []  # the PROBE word shape: no cedilla rung (E.2.8)


def test_the_parser_is_the_spaced_factory():
    profile = get_profile("ro")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "ro"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and parser._token_post_pass is None


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in RO_CATALOG}
    assert set(by_id) == {"wty-ro-en", "opensubtitles-ro"}
    dictionary, frequency = by_id["wty-ro-en"], by_id["opensubtitles-ro"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ro/en/wty-ro-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url == (
        "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ro/ro_50k.txt"
    )
    assert all("CC BY-SA 4.0" in spec.license_note for spec in RO_CATALOG)
    assert get_profile("ro").catalog == RO_CATALOG


def test_settings_search_finds_romanian():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("romanian") and capability in search("Română")
    assert {"romanian", "română", "ro"} <= set(capability.keywords)


def test_the_mode_probe_tables_are_ten_distinct_raw_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["ro"], mode_probe.LESS_COMMON_TERMS["ro"]
    assert len(set(common)) == len(common) == 10 and len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    # the probe matches raw list terms and the list mostly spells s/t with cedillas: neither spelling appears
    assert not set("\u015f\u0163șț") & set("".join(common + rare))


@pytest.mark.parametrize("ranked", [False, True])
def test_a_romanian_list_direction_is_detected_from_its_own_terms(ranked):
    common, rare = mode_probe.MORE_COMMON_TERMS["ro"], mode_probe.LESS_COMMON_TERMS["ro"]
    if ranked:
        values = {(term, None): 1 + i for i, term in enumerate(common)}
        values.update({(term, None): 30_000 + i for i, term in enumerate(rare)})
    else:
        values = {(term, None): 1_000_000 - i for i, term in enumerate(common)}
        values.update({(term, None): 10 + i for i, term in enumerate(rare)})
    _rows, converted = source_importer._iter_rank_rows(values, "", "ro")
    assert converted is not ranked


def test_the_ro_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("ro") == 0
    assert "BUNDLED_SMOKE_PASS: language ro" in capsys.readouterr().out
