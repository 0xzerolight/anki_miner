"""The edgetts voice is profile data, and D14 holds for every registered language.

D14 (spec E.10): a language Google Translate has no voice for (resolved
``gtts_lang == ""``) takes the Edge read-aloud leg as its synthetic source,
so its profile names a voice and its default chain carries an enabled
``edgetts`` entry. New languages are checked by iterating the registry;
nobody edits this file to add one.
"""

from __future__ import annotations

import re

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.profile import AudioDefaults
from anki_miner.languages.registry import get_profile

_VOICE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-[A-Za-z]+Neural$")

#: The 21 languages that shipped before the edgetts seam. The seam is inert for
#: them: none names a voice or defaults to the Edge leg.
_SHIPPED_BEFORE_EDGETTS = (
    "ja",
    "ko",
    "zh",
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
)


def _has_edge_default(audio: AudioDefaults) -> bool:
    return any(entry.kind == "edgetts" and entry.enabled for entry in audio.default_chain)


def test_the_voice_defaults_to_none():
    audio = AudioDefaults(
        gtts_lang="ja",
        cache_stem_prefix="g",
        sentence_cache_stem_prefix="s",
        custom_fetcher_language="ja",
    )
    assert audio.edge_voice == ""


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_a_voice_is_blank_or_a_short_voice_name(code):
    voice = get_profile(code).audio.edge_voice
    assert voice == "" or _VOICE.fullmatch(voice), voice


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_an_edge_default_leg_names_a_voice(code):
    audio = get_profile(code).audio
    assert not _has_edge_default(audio) or audio.edge_voice


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_no_google_voice_means_the_edge_leg_is_the_default(code):
    audio = get_profile(code).audio
    if audio.resolved_gtts_lang(AnkiMinerConfig(language=code)):
        return
    assert audio.edge_voice and _has_edge_default(audio), code


@pytest.mark.parametrize("code", _SHIPPED_BEFORE_EDGETTS)
def test_the_seam_is_inert_for_the_shipped_languages(code):
    audio = get_profile(code).audio
    assert audio.edge_voice == ""
    assert not any(entry.kind == "edgetts" for entry in audio.default_chain)
