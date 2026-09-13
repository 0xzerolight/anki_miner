"""Stage S contract cases, auto-grown over every registered profile.

One section per seam; later Stage S tasks append their own section below the
last one rather than editing an earlier one.
"""

from __future__ import annotations

import re

import pytest

from anki_miner.config.config import _SCRIPT_VARIANT_IDS, AnkiMinerConfig
from anki_miner.languages import SCRIPT_VARIANT_IDS
from anki_miner.languages.registry import available_languages, get_profile

CODES = sorted(available_languages())


# --- S27: script_variant ids and the resolved gTTS code ------------------------


def test_config_variant_literal_matches_the_languages_tuple():
    """config must not import the languages package; this keeps the copies in sync."""
    assert _SCRIPT_VARIANT_IDS == SCRIPT_VARIANT_IDS


@pytest.mark.parametrize("value", ["br", "pt"])
def test_portuguese_variant_ids_are_accepted(value):
    assert AnkiMinerConfig(script_variant=value).script_variant == value


def test_no_two_profiles_share_a_variant_id():
    defaults = [get_profile(code).scoped_defaults["script_variant"] for code in CODES]
    declared = [variant for variant in defaults if variant]
    assert set(defaults) <= set(SCRIPT_VARIANT_IDS)
    assert len(declared) == len(set(declared))


@pytest.mark.parametrize("code", CODES)
def test_gtts_language_resolves_to_a_string(code):
    assert isinstance(get_profile(code).audio.resolved_gtts_lang(AnkiMinerConfig()), str)


# --- R28: wiktionary_code ------------------------------------------------------

_WIKTIONARY_CODE = re.compile(r"^[a-z]{2,3}$")


@pytest.mark.parametrize("code", CODES)
def test_wiktionary_code_is_empty_or_a_bare_language_code(code):
    value = get_profile(code).wiktionary_code
    assert value == "" or _WIKTIONARY_CODE.fullmatch(value)
