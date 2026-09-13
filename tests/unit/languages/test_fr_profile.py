"""The French profile: data, wiring, folds, catalogue and registration (hard-requires spaCy + fr_core_news_sm)."""

from __future__ import annotations

import itertools
import tomllib
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import NOUN_GENDER_FIELD, POS_FIELD
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages._spaced.morphology import LatinLookupStrategy, SpacedMinedForm
from anki_miner.languages._spaced.render import PosHook
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    LATIN_SUBTITLE_REGEX,
    MUSIC_PATTERN,
    PARENS_PATTERN,
    LatinScript,
)
from anki_miner.languages.fr.catalog import FR_CATALOG
from anki_miner.languages.fr.morphology import (
    FR_ABBREVIATIONS,
    FR_EXCLUDED_SUBTYPES,
    FR_LEADING_WORDS,
    FR_SPEAKER_PATTERN,
    FR_SUBTITLE_REGEX,
    FrenchVerbLemmaPass,
    fr_normalize,
)
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

ROOT = Path(__file__).resolve().parents[3]

#: Entries not in spaCy's French tokenizer exceptions (F9).
ADDITIONS = frozenset({"mgr", "me", "p", "c.-à-d"})
#: Ordinary words a sentence ends on: an abbreviation key would glue two sentences (and keep a dot on the word).
NEVER = frozenset({"sept", "vol", "ex"})


def test_registration_and_the_extra():
    assert "fr" in AVAILABLE_LANGUAGES and _LANGUAGE_CODES == AVAILABLE_LANGUAGES
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert extras["fr"] == ["spacy>=3.8,<3.8.15"]
    assert "anki-miner[fr]" in extras["languages"]


def test_the_profile_is_built_from_the_shared_substrate():
    profile = get_profile("fr")
    assert (profile.code, profile.display_name, profile.english_name) == ("fr", "Français", "French")
    assert isinstance(profile.mined_form, SpacedMinedForm) and isinstance(profile.lookup, LatinLookupStrategy)
    assert isinstance(profile.script, LatinScript) and isinstance(profile.dict_keys, CasefoldDictKeys)
    assert profile.reading is None and profile.sentence_annotator is None
    assert profile.import_encodings == ("utf-8-sig", "cp1252")
    assert profile.audio_track_codes == frozenset({"fre", "fra", "fr", "french"})
    assert profile.asr_language == "fr" and profile.wiktionary_code == ""
    assert profile.captions.primary == "fr" and profile.captions.orig_codes == ("fr-orig",)
    assert profile.captions.codes == ("fr", "fr-CA", "fr-FR") and profile.captions.audio_pattern == "^fr(-|$)"
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "lemmatised_frequency"})
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD)
    assert [type(hook) for hook in profile.render_hooks] == [PosHook, GrammarTagHook]
    assert profile.unavailable_reason is not None and profile.unavailable_reason() is None
    assert profile.pos_defaults.excluded_subtypes == FR_EXCLUDED_SUBTYPES == ()
    assert profile.smoke_sentence == "Le chat dort sur la chaise."


def test_audio_speaks_the_front_through_google():
    audio = get_profile("fr").audio
    assert (audio.gtts_lang, audio.cache_stem_prefix, audio.sentence_cache_stem_prefix) == (
        "fr",
        "googletts_fr",
        "sentencetts_fr",
    )
    assert [entry.kind for entry in audio.default_chain] == ["googletts"]
    assert audio.speakable is not None and audio.speakable("manger", "") == "manger"


def test_scoped_defaults_turn_on_the_french_sdh_filter_and_both_fields():
    config = switch_language(AnkiMinerConfig(), "fr")
    assert config.language == "fr" and config.allowed_pos == ("ADJ", "ADV", "NOUN", "VERB")
    assert config.use_subtitle_regex_filter is True and config.subtitle_regex_filter == FR_SUBTITLE_REGEX
    assert FR_SUBTITLE_REGEX != LATIN_SUBTITLE_REGEX
    assert config.anki_fields["pos"] == "" and config.anki_fields["noun_gender"] == ""
    assert config.downloader_subtitle_langs == "fr"


@pytest.mark.parametrize(
    ("cue", "kept"),
    [
        ("NARRATEUR : Il était une fois.", "Il était une fois."),  # French spacing before the colon
        ("JEAN: Viens manger.", "Viens manger."),  # the Latin shape still strips
        ("[porte qui claque] ♪ Bonjour ♪", "Bonjour"),
        ("- Bonjour. - Salut.", "Bonjour. Salut."),
        ("Attention : le train part !", "Attention : le train part !"),  # a mixed-case word before " :" is dialogue
        ("C'est bien – vraiment.", "C'est bien – vraiment."),  # a mid-sentence dash stays (item 18)
    ],
)
def test_the_french_sdh_default_strips_its_fixture(cue, kept):
    compiled = compile_subtitle_regex_filter(FR_SUBTITLE_REGEX, "")
    assert " ".join(compiled.sub("", cue).split()) == kept


def test_the_french_sdh_default_is_the_latin_one_with_a_french_speaker_rule_and_compiles_in_any_order():
    parts = (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, FR_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
    assert "|".join(parts) == FR_SUBTITLE_REGEX
    for part in parts:
        compile_subtitle_regex_filter(part, "")
    for order in itertools.permutations(parts):
        compile_subtitle_regex_filter("|".join(order), "")


def test_the_gender_field_prints_the_article():
    (hook,) = [hook for hook in get_profile("fr").render_hooks if isinstance(hook, GrammarTagHook)]
    config = AnkiMinerConfig()

    def word(pos: str, morph: str) -> SimpleNamespace:
        return SimpleNamespace(pos=pos, morph=morph, definition_html="")

    assert hook.render(word("NOUN", "Gender=Masc|Number=Sing"), config=config) == {"noun_gender": "le"}
    assert hook.render(word("NOUN", "Gender=Fem|Number=Plur"), config=config) == {"noun_gender": "la"}
    assert hook.render(word("VERB", "Gender=Fem"), config=config) == {}


def test_books_split_after_real_sentence_ends_only():
    """S8 set + «» closers: titles, guillemets and French spacing before ? and !."""
    rules = get_profile("fr").sentence_rules
    assert split_sentences("M. Dupont est arrivé. Mme Martin aussi.", rules=rules) == [
        "M. Dupont est arrivé.",
        "Mme Martin aussi.",
    ]
    assert split_sentences("« Bonjour », dit-il. Tu viens ? Oui.", rules=rules) == [
        "« Bonjour », dit-il.",
        "Tu viens ?",
        "Oui.",
    ]
    assert split_sentences("C'est mon ex. Elle est partie.", rules=rules) == ["C'est mon ex.", "Elle est partie."]


def test_normalize_composes_and_turns_french_no_break_spaces_into_spaces():
    """Item must-resolve 2: the stored line IS the normalised line, and the map is 1:1, so surfaces stay slices."""
    raw = unicodedata.normalize("NFD", "Attention\u202f: la crème est prête\u00a0!")
    assert fr_normalize(raw) == "Attention : la crème est prête !"
    assert len(fr_normalize("a\u00a0\u202fb")) == len("a  b")
    assert fr_normalize("aujourd’hui") == "aujourd’hui"  # apostrophes are a tagging-copy concern, not a stored one
    assert get_profile("fr").normalize is fr_normalize


def test_known_word_fronts_meet_the_mined_lemma():
    fold = get_profile("fr").dedup_fold
    assert fold is not None
    assert fold("le chat") == fold("Chat.") == "chat"
    assert fold("l’homme") == fold("L'homme") == "homme"
    assert fold("se lever") == "lever" and fold("s’appeler") == "appeler"
    assert fold("aujourd’hui") == "aujourd'hui" and fold("d'accord") == "d'accord"
    samples = ["le chat", "l’homme", "l'l'homme", "s'il vous plaît", "La Maison.", "aujourd’hui", "l'", "les gens"]
    assert all(fold(fold(text)) == fold(text) for text in samples)
    assert frozenset({"le", "la", "les", "un", "une", "des", "se", "l'", "s'"}) == FR_LEADING_WORDS


def test_index_keys_fold_curly_apostrophes():
    assert get_profile("fr").dict_keys.fold_term("Aujourd’hui") == "aujourd'hui"


def test_abbreviations_come_from_spacy_minus_words_that_end_sentences():
    from spacy.lang.fr.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    spacy_keys = {text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and len(text) > 1}
    assert FR_ABBREVIATIONS - spacy_keys <= ADDITIONS
    assert not FR_ABBREVIATIONS & NEVER
    assert {"m", "mme", "mlle", "dr", "st", "c.-à-d", "p", "j.-c"} <= FR_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in FR_ABBREVIATIONS)
    assert get_profile("fr").sentence_rules.abbreviations == FR_ABBREVIATIONS


def test_the_parser_is_the_spaced_factory_with_the_verb_repair():
    profile = get_profile("fr")
    parser = profile.create_parser(switch_language(AnkiMinerConfig(), "fr"))
    assert parser.normalize is profile.normalize
    assert parser._compound_matcher is None and isinstance(parser._token_post_pass, FrenchVerbLemmaPass)


def test_an_attested_infinitive_reaches_the_card_front():
    config = switch_language(AnkiMinerConfig(), "fr")
    unit = [ReadingUnit(text="Il porte une veste noire.", index=0, location_label="t")]
    known = get_profile("fr").create_parser(config, term_lookup=lambda terms: {t for t in terms if t == "porter"})
    assert "porter" in {word.mined_form for word in known.parse_text_units(unit, False)[0]}
    bare = get_profile("fr").create_parser(config)
    assert "porte" in {word.mined_form for word in bare.parse_text_units(unit, False)[0]}


def test_the_catalogue_ships_wiktionary_and_a_lemmatised_frequency_list():
    by_id = {spec.id: spec for spec in FR_CATALOG}
    assert set(by_id) == {"wty-fr-en", "opensubtitles-fr"}
    dictionary, frequency = by_id["wty-fr-en"], by_id["opensubtitles-fr"]
    assert dictionary.kind == "dict" and dictionary.url == (
        "https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/fr/en/wty-fr-en.zip"
    )
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url.endswith("/content/2018/fr/fr_50k.txt")
    assert all("CC BY-SA 4.0" in spec.license_note for spec in FR_CATALOG)
    assert get_profile("fr").catalog == FR_CATALOG
