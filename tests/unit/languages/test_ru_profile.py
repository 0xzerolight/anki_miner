"""The Russian profile: data, wiring, catalogue, registration, the availability probe and the smoke leg."""

from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui import app as app_module
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced import availability
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import CyrillicScript
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.registry import get_profile
from anki_miner.languages.ru.catalog import RU_CATALOG
from anki_miner.languages.ru.morphology import (
    RU_DEDUP_FOLD,
    RU_KEYS,
    RU_SENTENCE_RULES,
    RU_SUBTITLE_REGEX,
    StressedHeadwordReading,
    ru_normalize,
)
from anki_miner.languages.switching import switch_language

ROOT = Path(__file__).resolve().parents[3]


def test_registration_and_the_extra():
    assert "ru" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["ru"] == ["spacy>=3.8,<3.8.15", "pymorphy3>=2.0.6", "pymorphy3-dicts-ru"]
    assert "anki-miner[ru]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("ru")
    assert (profile.code, profile.display_name, profile.english_name) == ("ru", "Русский", "Russian")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, CyrillicScript) and profile.dict_keys is RU_KEYS
    assert isinstance(profile.reading, StressedHeadwordReading) and profile.sentence_annotator is None
    assert profile.normalize is ru_normalize and profile.dedup_fold is RU_DEDUP_FOLD
    assert profile.import_encodings == ("utf-8-sig", "cp1251")
    assert profile.audio_track_codes == frozenset({"rus", "ru", "russian"})
    assert profile.asr_language == "ru" and profile.wiktionary_code == ""
    assert (profile.captions.primary, profile.captions.codes, profile.captions.orig_codes) == (
        "ru",
        ("ru",),
        ("ru-orig",),
    )
    assert profile.captions.audio_pattern == "^ru(-|$)" and profile.captions.bare_fallback is True
    assert profile.capabilities == frozenset(
        {"pos_tag", "noun_gender", "aspect_pairs", "stress_marks", "lemmatised_frequency"}
    )
    assert "wiktionary_audio" not in profile.capabilities  # DECIDED 1: no Stage W
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.render_hooks[1].field_names() == ("noun_gender", "aspect_pair")
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.sentence_rules == RU_SENTENCE_RULES and profile.content_style is SPACED_CONTENT_STYLE
    assert profile.smoke_sentence == "Студент вчера прочитал интересную книгу."
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("ru").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "ru",
        "googletts_ru",
        "sentencetts_ru",
    )
    assert audio.custom_fetcher_language == "ru" and audio.papago_speaker is None
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]


def test_scoped_defaults_carry_the_russian_sdh_filter_and_the_extra_fields():
    config = switch_language(AnkiMinerConfig(), "ru")
    assert config.language == "ru" and config.downloader_subtitle_langs == "ru"
    assert config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB") and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == RU_SUBTITLE_REGEX
    assert config.anki_fields["noun_gender"] == "" and config.anki_fields["aspect_pair"] == ""
    assert config.anki_fields["expression_furigana"] == ""
    # D1: the stressed headword the S24 fallback fills reaches a card only once the user maps
    # Expression Reading (Settings -> Anki Fields). The mapped field name IS the switch
    # (_spaced/fields.py:28-31), as for frequency, pitch and expression audio — pinned so a later
    # reader does not take the blank for a wiring bug.
    assert config.anki_fields["expression_reading"] == ""


def test_the_parser_turns_the_stressed_headword_on_and_folds_its_reading_probe():
    profile = get_profile("ru")
    seen: list[list[str]] = []

    def lookup(terms: list[str]) -> dict[str, list[str]]:
        seen.append(terms)
        return {}

    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "ru"), reading_lookup=lookup)
    assert parser._attested_reading_fallback is True and parser._compound_matcher is None
    parser._reading_lookup(["Чёрный"])
    assert seen == [["черный"]]
    assert profile.create_parser(switch_language(AnkiMinerConfig(), "ru"))._reading_lookup is None


def test_the_catalogue_leaves_openrussian_first_in_the_chain(tmp_path):
    """Each downloaded dictionary is PREPENDED (resource_setup.py:75): wty is listed first so opr ends first."""
    from anki_miner.gui.utils.resource_setup import apply_download_summary
    from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult, ResourceDownloadSummary

    assert [spec.id for spec in RU_CATALOG] == ["wty-ru-en", "opr-ru-en", "opensubtitles-ru"]
    assert RU_CATALOG[2].kind == "freq" and RU_CATALOG[2].lemmatise is True
    assert all("CC BY-SA 4.0" in spec.license_note for spec in RU_CATALOG)
    assert get_profile("ru").catalog == RU_CATALOG
    config = replace(switch_language(AnkiMinerConfig(), "ru"), dicts_root=tmp_path)
    results = [
        ResourceDownloadResult(
            spec_id=spec.id,
            kind="dict",
            display_name=spec.display_name,
            url=spec.url,
            ok=True,
            detail="",
            dict_id=spec.id,
        )
        for spec in RU_CATALOG
        if spec.kind == "dict"
    ]
    summary = ResourceDownloadSummary(results=results, requested_count=2, dicts_root=tmp_path)
    chain = apply_download_summary(config, summary).dictionary_chain
    assert [entry.dict_id for entry in chain][:2] == ["opr-ru-en", "wty-ru-en"]


def test_the_probe_names_a_missing_morphology_package(monkeypatch):
    monkeypatch.setattr(availability, "_importable", lambda name: name != "pymorphy3_dicts_ru")
    monkeypatch.setattr(availability, "_pack_component_present", lambda code, name: False)
    monkeypatch.setattr(availability.sys, "frozen", False, raising=False)
    reason = get_profile("ru").unavailable_reason
    assert reason is not None
    message = reason()
    assert message is not None and "pymorphy3_dicts_ru" in message and "Russian" in message


def test_the_ru_smoke_leg_passes_in_process(capsys):
    assert app_module._run_language_bundled_smoke("ru") == 0
    assert "BUNDLED_SMOKE_PASS: language ru" in capsys.readouterr().out
