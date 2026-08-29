"""Cross-language contract suite. Stage 1A runs it for ja only."""

from __future__ import annotations

import dataclasses

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.languages.profile import LanguageProfile, ScriptFilterOption
from anki_miner.languages.registry import available_languages, get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS

CODES = ["ja"]

#: Logical card-field keys later languages' render hooks may add on top of the
#: config's own ``anki_fields`` keys. Spelled exactly as the hook tasks emit
#: them — "expression_pinyin", never a bare "pinyin".
EXTRA_HOOK_FIELDS = {"measure_word", "expression_traditional", "expression_pinyin", "hanja"}


@pytest.mark.parametrize("code", CODES)
def test_profile_is_the_frozen_type(code):
    assert isinstance(get_profile(code), LanguageProfile)


@pytest.mark.parametrize("code", CODES)
def test_registry_is_cached(code):
    assert get_profile(code) is get_profile(code)


@pytest.mark.parametrize("code", CODES)
def test_scoped_defaults_cover_exactly_the_scoped_fields(code):
    assert set(get_profile(code).scoped_defaults) == set(LANGUAGE_SCOPED_FIELDS)


def test_every_scoped_field_exists_on_the_config():
    names = {f.name for f in dataclasses.fields(AnkiMinerConfig)}
    assert set(LANGUAGE_SCOPED_FIELDS) <= names


@pytest.mark.parametrize("code", CODES)
def test_lookup_takes_three_args_for_every_language(code):
    result = get_profile(code).lookup.candidates("食べた", "", None)
    assert all(isinstance(text, str) and isinstance(cond, int) for text, cond in result)
    assert "食べた" not in [text for text, _ in result]


@pytest.mark.parametrize("code", CODES)
def test_script_options_declare_real_config_fields(code):
    names = {f.name for f in dataclasses.fields(AnkiMinerConfig)}
    for opt in get_profile(code).script.filter_options():
        assert isinstance(opt, ScriptFilterOption)
        assert opt.config_field == "" or opt.config_field in names


@pytest.mark.parametrize("code", CODES)
def test_render_hook_field_names_are_logical_keys(code):
    logical = set(AnkiMinerConfig().anki_fields)
    for hook in get_profile(code).render_hooks:
        assert set(hook.field_names()) <= logical | EXTRA_HOOK_FIELDS


def test_available_languages_contains_ja():
    assert "ja" in available_languages()


def test_languages_package_carries_no_import_time_gui_edge():
    import subprocess
    import sys

    src = (
        "import sys, anki_miner.languages.registry as r;"
        "r.get_profile('ja');"
        "print([m for m in sys.modules if m.startswith('anki_miner.gui')])"
    )
    out = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", out.stdout
