"""_spaced word audio (S1/S2, D10), the availability probe and the scoped first-visit defaults."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config.config import AudioSourceEntry
from anki_miner.languages._spaced import availability
from anki_miner.languages._spaced.audio import spaced_audio_candidates, spaced_speakable
from anki_miner.languages._spaced.fields import POS_FIELD, spaced_card_fields, spaced_scoped_defaults
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages.profile import AudioDefaults
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS


def test_the_ladder_asks_for_the_card_front_only():
    assert spaced_audio_candidates(SimpleNamespace(mined_form="go", surface="went")) == [("go", "go")]
    assert spaced_audio_candidates(SimpleNamespace(mined_form="")) == []


def test_speakable_is_the_reading_or_the_term():
    assert spaced_speakable("go", "") == "go"
    assert spaced_speakable("go", "goʊ") == "goʊ"
    assert spaced_speakable("", "") is None


@pytest.fixture
def probe(monkeypatch):
    present: set[str] = set()
    monkeypatch.setattr(availability, "find_spec", lambda name: object() if name in present else None)
    monkeypatch.setattr(availability, "_pack_component_present", lambda code, name: False)
    return present


def test_nothing_missing_is_none(probe):
    probe.update({"spacy", "xx_core_news_sm"})
    assert availability.spaced_missing_reason("xx", "Xish", "xx_core_news_sm")() is None


def test_a_missing_engine_names_the_extra_and_the_pack(probe):
    reason = availability.spaced_missing_reason("xx", "Xish", "xx_core_news_sm")()
    assert 'pip install "anki-miner[xx]"' in reason and "Settings -> Mining Language" in reason


def test_a_missing_model_names_the_download(probe):
    probe.add("spacy")
    reason = availability.spaced_missing_reason("xx", "Xish", "xx_core_news_sm")()
    assert "xx_core_news_sm" in reason and "pip" not in reason


def test_a_frozen_build_names_the_pack_only(probe, monkeypatch):
    monkeypatch.setattr(availability.sys, "frozen", True, raising=False)
    assert availability.spaced_missing_reason("xx", "Xish", "xx_core_news_sm")() == (
        "Xish mining needs the Xish language pack. Download it in Settings -> Mining Language."
    )


def test_a_pack_install_satisfies_both(monkeypatch):
    monkeypatch.setattr(availability, "find_spec", lambda name: None)
    monkeypatch.setattr(
        availability,
        "_pack_component_present",
        lambda code, name: (code, name) in {("_spacy", "spacy"), ("xx", "xx_core_news_sm")},
    )
    assert availability.spaced_missing_reason("xx", "Xish", "xx_core_news_sm")() is None


def test_scoped_defaults_cover_every_scoped_field_with_the_latin_filter_on():
    audio = AudioDefaults(
        gtts_lang="xx", cache_stem_prefix="googletts_xx", sentence_cache_stem_prefix="sentencetts_xx",
        custom_fetcher_language="xx", default_chain=(AudioSourceEntry(kind="googletts"),),
    )  # fmt: skip
    cards = spaced_card_fields((POS_FIELD,))
    defaults = spaced_scoped_defaults(
        subtitle_langs="xx", audio=audio, allowed_pos=("NOUN",), excluded_subtypes=("NNP",), card_fields=cards
    )
    assert set(defaults) == set(LANGUAGE_SCOPED_FIELDS)
    assert defaults["downloader_subtitle_langs"] == "xx"
    assert defaults["expression_audio_chain"] == audio.default_chain
    assert defaults["allowed_pos"] == ("NOUN",) and defaults["excluded_subtypes"] == ("NNP",)
    assert defaults["anki_fields"] == dict(cards) and defaults["anki_fields"] is not cards
    assert defaults["anki_deck_name"] == "Anki Miner" and defaults["anki_note_type"] == ""
    assert defaults["script_variant"] == "" and defaults["reading_tone_color"] is False
    assert defaults["use_subtitle_regex_filter"] is True
    assert defaults["subtitle_regex_filter"] == LATIN_SUBTITLE_REGEX
    assert defaults["subtitle_regex_replacement"] == ""
    assert defaults["known_words_match_kana_variants"] is False


def test_a_language_passes_its_own_caption_regex_and_overrides_keys():
    audio = AudioDefaults(
        gtts_lang="xx", cache_stem_prefix="g", sentence_cache_stem_prefix="s", custom_fetcher_language="xx"
    )
    defaults = spaced_scoped_defaults(
        subtitle_langs="xx",
        audio=audio,
        allowed_pos=("NOUN",),
        excluded_subtypes=(),
        card_fields={},
        subtitle_regex=r"^[A-Z]+ :\s*",
    )
    assert defaults["subtitle_regex_filter"] == r"^[A-Z]+ :\s*"
    defaults["script_variant"] = "br"  # a fresh dict: the caller owns it
    again = spaced_scoped_defaults(
        subtitle_langs="xx", audio=audio, allowed_pos=("NOUN",), excluded_subtypes=(), card_fields={}
    )
    assert again["script_variant"] == "" and again["subtitle_regex_filter"] == LATIN_SUBTITLE_REGEX
