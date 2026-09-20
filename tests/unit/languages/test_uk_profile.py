"""The Ukrainian profile: data, wiring, catalogue, registration and the availability probe."""

from __future__ import annotations

from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced import availability
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD, NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook, drop_romanisation
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.pos import UPOS_ALLOWED, UPOS_LABELS
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import CyrillicScript
from anki_miner.languages._spaced.style import SPACED_CONTENT_STYLE
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.languages.uk.catalog import UK_CATALOG
from anki_miner.languages.uk.morphology import (
    UK_DEDUP_FOLD,
    UK_KEYS,
    UK_SENTENCE_RULES,
    UK_SUBTITLE_REGEX,
    StressedHeadwordReading,
    uk_normalize,
)

ROOT = Path(__file__).resolve().parents[3]
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"


def test_registration():
    """Membership only: a sibling lead merging after this one shifts every index."""
    assert "uk" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    assert get_profile("uk") is get_profile("uk")


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("uk")
    assert profile.code == "uk" and profile.display_name == "Українська"
    assert profile.english_name == "Ukrainian" and profile.wiktionary_code == ""
    assert isinstance(profile.script, CyrillicScript)
    assert isinstance(profile.mined_form, SpacedMinedForm)
    assert isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.reading, StressedHeadwordReading)
    assert profile.sentence_annotator is None
    assert profile.normalize is uk_normalize and profile.dict_keys is UK_KEYS
    assert profile.dedup_fold is UK_DEDUP_FOLD
    assert profile.sentence_rules is UK_SENTENCE_RULES
    assert profile.import_encodings == ("utf-8-sig", "cp1251")
    assert profile.audio_track_codes == frozenset({"ukr", "uk", "ukrainian"})
    assert profile.asr_language == "uk"
    assert profile.catalog is UK_CATALOG
    assert profile.content_style is SPACED_CONTENT_STYLE
    assert profile.pos_defaults.allowed_pos == UPOS_ALLOWED and profile.pos_defaults.labels is UPOS_LABELS
    assert profile.pos_defaults.excluded_subtypes == ()
    assert profile.smoke_sentence == "Студент учора прочитав цікаву книжку."


def test_the_capabilities_are_the_five_the_engine_and_the_dictionary_support():
    assert get_profile("uk").capabilities == frozenset(
        {"pos_tag", "noun_gender", "aspect_pairs", "stress_marks", "lemmatised_frequency"}
    )


def test_the_card_fields_carry_pos_gender_and_aspect():
    profile = get_profile("uk")
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
    assert {"pos", "noun_gender", "aspect_pair"} <= set(profile.card_field_defaults)
    assert all(profile.card_field_defaults[key] == "" for key in ("pos", "noun_gender", "aspect_pair"))


def test_the_grammar_hook_uses_ukrainian_labels_and_folds_the_romanisation():
    pos_hook, grammar_hook = get_profile("uk").render_hooks
    assert isinstance(pos_hook, PosHook) and isinstance(grammar_hook, GrammarTagHook)
    assert grammar_hook.field_names() == ("noun_gender", "aspect_pair")
    assert dict(grammar_hook._gender_labels) == {"masc": "ч.", "fem": "ж.", "neut": "с."}
    assert grammar_hook._head_fold is drop_romanisation
    # P9: wty-uk-en prints a stressed partner and the reading field is stressed too, so it is kept.
    assert grammar_hook._partner_fold is None


def test_word_audio_speaks_the_front_through_google():
    audio = get_profile("uk").audio
    assert audio.gtts_lang == "uk" and audio.custom_fetcher_language == "uk"
    assert audio.cache_stem_prefix == "googletts_uk"
    assert audio.sentence_cache_stem_prefix == "sentencetts_uk"
    assert audio.papago_speaker is None
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]


def test_the_audio_caches_never_collide_with_russians():
    uk, ru = get_profile("uk").audio, get_profile("ru").audio
    assert uk.cache_stem_prefix != ru.cache_stem_prefix
    assert uk.sentence_cache_stem_prefix != ru.sentence_cache_stem_prefix


def test_the_caption_codes_name_ukrainian():
    captions = get_profile("uk").captions
    assert captions.primary == "uk" and captions.codes == ("uk",)
    assert captions.orig_codes == ("uk-orig",)
    assert captions.audio_pattern == "^uk(-|$)" and captions.bare_fallback is True


def test_scoped_defaults_carry_the_ukrainian_sdh_filter_and_the_extra_fields():
    assert set(get_profile("uk").scoped_defaults) == set(LANGUAGE_SCOPED_FIELDS)
    config = switch_language(AnkiMinerConfig(), "uk")
    assert config.language == "uk" and config.downloader_subtitle_langs == "uk"
    assert config.allowed_pos == UPOS_ALLOWED and config.excluded_subtypes == ()
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == UK_SUBTITLE_REGEX
    assert config.anki_fields["noun_gender"] == "" and config.anki_fields["aspect_pair"] == ""
    # The stressed headword the S24 fallback fills reaches a card only once the user maps Expression
    # Reading (Settings -> Anki Fields): the mapped field name IS the switch, as for frequency,
    # pitch and expression audio. Pinned so a later reader does not take the blank for a wiring bug.
    assert config.anki_fields["expression_reading"] == ""
    assert config.anki_fields["expression_furigana"] == ""


def test_the_parser_turns_the_stressed_headword_on_and_folds_its_reading_probe():
    """S24 is wired and NOT inert (P7): wty-uk-en attests stress on its non-lemma rows."""
    profile = get_profile("uk")
    seen: list[list[str]] = []

    def lookup(terms: list[str]) -> dict[str, list[str]]:
        seen.append(terms)
        return {}

    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "uk"), reading_lookup=lookup)
    assert parser._attested_reading_fallback is True
    parser._reading_lookup([f"М{RSQUO}ЯЧ"])
    assert seen == [["м'яч"]]
    assert profile.create_parser(switch_language(AnkiMinerConfig(), "uk"))._reading_lookup is None


def test_the_catalogue_offers_wiktionary_and_a_lemmatised_frequency_list():
    ids = [spec.id for spec in UK_CATALOG]
    assert ids == ["wty-uk-en", "opensubtitles-uk"]
    by_id = {spec.id: spec for spec in UK_CATALOG}
    assert by_id["wty-uk-en"].kind == "dict" and by_id["wty-uk-en"].url.endswith("/uk/en/wty-uk-en.zip")
    assert by_id["opensubtitles-uk"].kind == "freq" and by_id["opensubtitles-uk"].lemmatise is True
    assert all("CC BY-SA" in spec.license_note for spec in UK_CATALOG)


def test_the_probe_names_a_missing_morphology_package(monkeypatch):
    """The availability probe covers pymorphy3 and its uk dictionaries, not just the model (P1)."""
    monkeypatch.setattr(availability, "_importable", lambda name: name != "pymorphy3_dicts_uk")
    monkeypatch.setattr(availability, "_pack_component_present", lambda code, name: False)
    monkeypatch.setattr(availability.sys, "frozen", False, raising=False)
    reason = get_profile("uk").unavailable_reason
    assert reason is not None
    message = reason()
    assert message is not None and "pymorphy3_dicts_uk" in message and "Ukrainian" in message


def test_the_probe_is_silent_when_the_engine_is_installed():
    reason = get_profile("uk").unavailable_reason
    assert reason is not None and reason() is None
