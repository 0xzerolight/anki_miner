"""F-16 (R12): fresh installs try Commons recordings before Google TTS; ja and zh are unchanged."""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig, AudioSourceEntry
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

WIKI = AudioSourceEntry(kind="wikimedia")
GOOGLE = AudioSourceEntry(kind="googletts")
EDGE = AudioSourceEntry(kind="edgetts")

#: Every language whose default chain is Google TTS, except ja (R12) and zh
#: (Commons' Mandarin recordings are pinyin-keyed, unreachable from a Han-titled page).
_COMMONS_FIRST = (
    "ko",
    "en",
    "ca",
    "de",
    "pt",
    "fr",
    "es",
    "it",
    "nl",
    "nb",
    "ro",
    "el",
    "fi",
    "hu",
    "hr",
    "sv",
    "pl",
    "lt",
    "da",
    "tr",
    "id",
    "ru",
    "ar",
    "th",
    "uk",
    "vi",
    "yue",
    "he",
)


@pytest.mark.parametrize("code", _COMMONS_FIRST)
def test_a_google_language_tries_commons_first(code):
    audio = get_profile(code).audio

    assert audio.default_chain == (WIKI, GOOGLE)
    assert audio.wikimedia_prefixes


@pytest.mark.parametrize("code", ["fa", "sl"])
def test_an_edge_language_keeps_its_chain_but_can_add_commons(code):
    audio = get_profile(code).audio

    assert audio.default_chain == (EDGE,)
    assert audio.wikimedia_prefixes


def test_japanese_is_unchanged():
    audio = get_profile("ja").audio

    assert audio.default_chain == AnkiMinerConfig().expression_audio_chain
    assert audio.wikimedia_prefixes == ()


def test_mandarin_has_no_commons_leg():
    audio = get_profile("zh").audio

    assert audio.default_chain == (GOOGLE,)
    assert audio.wikimedia_prefixes == ()


def test_every_language_is_accounted_for():
    assert set(_COMMONS_FIRST) | {"fa", "sl", "ja", "zh"} == set(AVAILABLE_LANGUAGES)


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_every_prefix_is_in_commons_canonical_form(code):
    for prefix in get_profile(code).audio.wikimedia_prefixes:
        assert prefix[:1] == prefix[:1].upper(), (code, prefix)
        assert prefix.count("{term}") <= 1, (code, prefix)


def test_spanish_takes_lingua_libre_first():
    assert get_profile("es").audio.wikimedia_prefixes == ("LL-Q1321 (spa)-", "Es-", "Es-am-lat-")


def test_norwegian_carries_the_bot_template():
    assert "NB - Pronunciation of Norwegian Bokmål «{term}».ogg" in get_profile("nb").audio.wikimedia_prefixes


def test_a_first_switch_to_spanish_lands_on_the_commons_chain():
    assert switch_language(AnkiMinerConfig(), "es").expression_audio_chain == (WIKI, GOOGLE)


def test_the_spanish_default_chain_builds_wikimedia_then_google(tmp_path, monkeypatch):
    from anki_miner.gui.utils import service_factory

    monkeypatch.setattr(service_factory, "ANKI_MINER_HOME", tmp_path)
    chain = service_factory.create_expression_audio_fetcher(switch_language(AnkiMinerConfig(), "es"))

    assert [type(m).__name__ for m in chain._fetchers] == ["WikimediaAudioFetcher", "GoogleTranslateAudioFetcher"]
    assert chain._fetchers[0]._prefixes == get_profile("es").audio.wikimedia_prefixes
    assert chain._fetchers[0]._language == "es"
    chain.close()
