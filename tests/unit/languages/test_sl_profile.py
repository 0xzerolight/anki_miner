"""The Slovenian profile: fields, capabilities, the Edge audio leg and the grammar hook."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced.fields import ASPECT_PAIR_FIELD, NOUN_GENDER_FIELD, NOUN_PLURAL_FIELD, POS_FIELD
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language


@pytest.fixture(scope="module")
def profile():
    return get_profile("sl")


def test_slovenian_is_registered():
    assert "sl" in AVAILABLE_LANGUAGES
    assert AnkiMinerConfig(language="sl").language == "sl"


def test_the_identity_fields(profile):
    assert (profile.code, profile.english_name, profile.display_name) == ("sl", "Slovenian", "Slovenščina")
    assert profile.asr_language == "sl"
    assert profile.wiktionary_code == ""  # wty-sl-en declares sourceLanguage "sl"
    assert profile.import_encodings == ("utf-8-sig", "cp1250")
    assert profile.audio_track_codes == frozenset({"slv", "sl", "slovenian"})


def test_the_capabilities(profile):
    assert profile.capabilities == frozenset({"pos_tag", "noun_gender", "aspect_pairs", "lemmatised_frequency"})


def test_the_card_fields_have_no_plural_field(profile):
    """Slovenian has three numbers, so a two-way Plural field would hide the dual.

    ``card_field_defaults`` is NOT asserted to hold a ``noun_plural`` key: ``spaced_card_fields``
    starts from ``AnkiMinerConfig().anki_fields`` and adds only the extras, and ``noun_plural`` is
    not a config field.
    """
    assert profile.extra_card_fields == (POS_FIELD, NOUN_GENDER_FIELD, ASPECT_PAIR_FIELD)
    assert NOUN_PLURAL_FIELD not in profile.extra_card_fields
    assert "noun_plural" not in profile.render_hooks[1].field_names()


def test_the_audio_defaults_take_the_edge_leg(profile):
    """gTTS has no Slovenian voice, so the seam's edgetts kind is the default synthetic leg."""
    audio = profile.audio
    assert audio.gtts_lang == ""
    assert audio.edge_voice == "sl-SI-PetraNeural"
    assert audio.default_chain == (AudioSourceEntry(kind="edgetts"),)
    assert audio.cache_stem_prefix == "googletts_sl"
    assert audio.papago_speaker is None


def test_the_scoped_defaults_carry_the_edge_chain():
    config = switch_language(AnkiMinerConfig(), "sl")
    assert config.expression_audio_chain == (AudioSourceEntry(kind="edgetts"),)
    assert config.downloader_subtitle_langs == "sl"
    assert "Ncfpn" not in config.excluded_subtypes
    assert "Va-r3s-n" in config.excluded_subtypes


def test_the_captions_are_anchored(profile):
    captions = profile.captions
    assert (captions.primary, captions.codes, captions.orig_codes) == ("sl", ("sl",), ("sl-orig",))
    assert captions.audio_pattern == "^sl(-|$)" and captions.bare_fallback is True


def test_the_dedup_fold_has_no_leading_words(profile):
    """D18: Slovenian has no articles, so a deck front carries no word the lemma lacks."""
    fold = profile.dedup_fold
    assert fold is not None and fold("Knjiga") == fold("knjiga")


def _render(profile, term, html, morph="", pos="NOUN"):
    hook = profile.render_hooks[1]
    word = SimpleNamespace(pos=pos, mined_form=term, surface=term, morph=morph, definition_html=html)
    return hook.render(word, config=AnkiMinerConfig())


def test_the_hook_reads_morph_before_anything_else(profile):
    """6.8 % of noun rows carry a Grammar head line, so morph leads (the shipped GENDER_SOURCES order)."""
    assert _render(profile, "knjiga", "", morph="Case=Nom|Gender=Fem|Number=Plur") == {"noun_gender": "feminine"}
    assert _render(profile, "brati", "", morph="Aspect=Imp|VerbForm=Part", pos="VERB") == {
        "aspect_pair": "imperfective"
    }


def test_a_non_noun_renders_nothing(profile):
    assert _render(profile, "lep", "", morph="Gender=Fem", pos="ADJ") == {}
