"""Cross-language contract suite. Stage 1A ran it for ja; Stage 2A adds zh.

The matrix is the registry itself, so a newly registered language joins every
parametrised case with no edit here. Growth is additive: the Stage 1A cases
stay exactly as written and later stages only append.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

import anki_miner
from anki_miner.config.config import AnkiMinerConfig
from anki_miner.languages.profile import LanguageProfile, ScriptFilterOption
from anki_miner.languages.registry import available_languages, get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS, REQUIRED_FIELD_KEYS
from anki_miner.services.resource_catalog import RESOURCE_KINDS

CODES = sorted(available_languages())

#: Query word per language for the lookup-shape cases. A registered language
#: with no entry here fails loudly rather than silently skipping.
PROBE = {"ja": "食べた", "zh": "银行", "ko": "먹었다"}

#: Every capability name any profile is allowed to declare. A typo'd flag is a
#: silently-off feature everywhere it is gated, so the vocabulary is closed.
CAPABILITY_VOCABULARY = frozenset(
    {
        "pitch",
        "furigana",
        "kana_filters",
        "name_wordsets",
        "deinflection",
        "hangul_filters",
        "hanja",
        "pinyin",
        "tone_color",
        "script_variants",
        "measure_word",
    }
)

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


@pytest.mark.parametrize("code", CODES)
def test_render_hooks_take_the_config_keyword_only(code):
    """A hook is the only thing that can honour a hook-only scoped setting.

    ``reading_tone_color`` shipped structurally unreachable because ``render``
    took no config; the keyword-only spelling is what stops the next one from
    binding to ``word`` instead.
    """
    for hook in get_profile(code).render_hooks:
        parameter = inspect.signature(hook.render).parameters["config"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, (code, type(hook).__name__)


def test_available_languages_contains_ja():
    assert "ja" in available_languages()


@pytest.mark.parametrize("code", CODES)
def test_language_package_exports_build_profile(code):
    """The registry's auto-discovery loop calls ``<package>.build_profile()``
    lazily; every registered code's package must actually export it."""
    module = importlib.import_module(f"anki_miner.languages.{code}")
    assert callable(module.build_profile)


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


# --------------------------------------------------------------------------
# Stage 2A: assertions the ja-only matrix never needed. Appended, not folded
# into the cases above — those stay as Stage 1A wrote them.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_profile_shape_is_complete(code):
    profile = get_profile(code)
    assert profile.code == code
    assert profile.display_name
    assert callable(profile.create_parser) and callable(profile.normalize)
    assert profile.capabilities <= CAPABILITY_VOCABULARY
    assert profile.audio_track_codes and profile.import_encodings
    assert profile.asr_language and profile.captions.primary
    assert profile.captions.primary in profile.captions.codes
    assert {spec.kind for spec in profile.catalog} <= RESOURCE_KINDS
    assert len({spec.id for spec in profile.catalog}) == len(profile.catalog)


@pytest.mark.parametrize("code", CODES)
def test_lookup_returns_deduped_pairs_for_the_language_probe(code):
    """Same three positional args, same pair shape, per-language query word."""
    assert code in PROBE, f"add a PROBE word for {code!r}"
    word = PROBE[code]
    result = get_profile(code).lookup.candidates(word, "", None)
    assert isinstance(result, list)
    assert all(isinstance(text, str) and isinstance(conditions, int) for text, conditions in result)
    texts = [text for text, _ in result]
    assert word not in texts
    assert len(texts) == len(set(texts))


@pytest.mark.parametrize("code", CODES)
def test_switch_language_activates_every_registered_language(code):
    config = switch_language(AnkiMinerConfig(), code)
    assert config.language == code


@pytest.mark.parametrize("code", CODES)
def test_card_fields_and_hooks_agree(code):
    """Hook keys are the profile's OWN logical keys, not the ja dataclass's.

    ``OPTIONAL_FIELD_KEYS`` is frozen legacy: it carries the ja/ko/zh keys
    because they predate ``LanguageProfile.extra_card_fields``, and it never
    grows again. A later language's key satisfies this by being DECLARED on its
    own profile — which is what ``AnkiService`` threads into ``build_note`` as
    ``extra_optional_keys`` — so the union is the right right-hand side. Every
    language shipped today keeps passing on the central set alone; the union
    only matters for a profile-declared key (see
    ``tests/unit/languages/test_eu_boundary_stub.py``).
    """
    profile = get_profile(code)
    assert set(profile.card_field_defaults) >= REQUIRED_FIELD_KEYS  # ruff SIM300: no Yoda side
    hook_keys = {name for hook in profile.render_hooks for name in hook.field_names()}
    assert hook_keys <= set(profile.card_field_defaults)
    assert hook_keys <= (OPTIONAL_FIELD_KEYS | {spec.key for spec in profile.extra_card_fields})


@pytest.mark.parametrize("code", CODES)
def test_script_filter_options_are_labelled(code):
    for option in get_profile(code).script.filter_options():
        assert option.option_id and option.label


@pytest.mark.parametrize("code", CODES)
def test_no_import_time_gui_edge_for_any_language(code):
    """Building ANY profile must not drag anki_miner.gui into the process."""
    src = (
        "import sys, anki_miner.languages.registry as r;"
        f"r.get_profile({code!r});"
        "print([m for m in sys.modules if m.startswith('anki_miner.gui')])"
    )
    out = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(Path(anki_miner.__file__).resolve().parents[1])},
    )
    assert out.stdout.strip() == "[]", out.stdout


def _clear_zh_converter_caches() -> None:
    """Drop the memoised OpenCC converters (``lru_cache`` on both helpers)."""
    from anki_miner.languages.zh import variants

    for name in ("_converter", "_converters"):
        getattr(getattr(variants, name), "cache_clear", lambda: None)()


def test_zh_lookup_yields_the_traditional_variant_with_opencc():
    pytest.importorskip("opencc")
    _clear_zh_converter_caches()
    assert ("銀行", 0) in get_profile("zh").lookup.candidates(PROBE["zh"], "", None)


@pytest.mark.parametrize("code", CODES)
def test_english_name_is_nonempty_ascii(code):
    name = get_profile(code).english_name
    assert name and name.isascii()


@pytest.mark.parametrize("code", CODES)
def test_smoke_sentence_is_nonempty(code):
    assert get_profile(code).smoke_sentence


@pytest.mark.parametrize("code", CODES)
def test_extra_card_fields_match_the_render_hooks_exactly(code):
    profile = get_profile(code)
    spec_keys = {spec.key for spec in profile.extra_card_fields}
    hook_keys = {name for hook in profile.render_hooks for name in hook.field_names()}
    assert spec_keys <= EXTRA_HOOK_FIELDS
    assert spec_keys == hook_keys


@pytest.mark.parametrize("code", CODES)
def test_extra_card_field_capabilities_are_declared(code):
    profile = get_profile(code)
    for spec in profile.extra_card_fields:
        assert spec.capability in CAPABILITY_VOCABULARY
        assert spec.capability in profile.capabilities


def test_zh_lookup_stays_contract_shaped_without_opencc(monkeypatch):
    """The other side of the OpenCC branch: no variants, same contract.

    ``sys.modules["opencc"] = None`` is what an uninstalled extra looks like
    from inside ``variants._converter``. The caches are cleared on the way out
    as well as in, so no later test inherits a converter-less cache.
    """
    monkeypatch.setitem(sys.modules, "opencc", None)
    _clear_zh_converter_caches()
    try:
        result = get_profile("zh").lookup.candidates(PROBE["zh"], "", None)
    finally:
        _clear_zh_converter_caches()
    assert result == []
