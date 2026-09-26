"""Characterization of ``AnkiMinerConfig.__post_init__`` (services-04).

Pins what every guard accepts, what it coerces to and what it raises, plus
which error wins when several fields are bad at once. Expectations key on each
field's declared annotation and on literal values, never on the method's own
tables, so this suite passed against the hand-written branches before the
table-driven rewrite and passes unchanged after it.

CPython's own error texts differ across the supported versions, so builtin
errors are compared by type, or against the text the same bad value raises on
its own; only the two messages this module writes are pinned verbatim.
"""

from __future__ import annotations

import dataclasses
import itertools
import types
from pathlib import Path
from typing import Mapping

import pytest

from anki_miner.config import AnkiMinerConfig

_FIELDS = dataclasses.fields(AnkiMinerConfig)


def _annotated(annotation: object) -> list[str]:
    return [f.name for f in _FIELDS if f.type == annotation]


PATH_FIELDS = _annotated(Path)
OPTIONAL_PATH_FIELDS = _annotated(Path | None)
TUPLE_FIELDS = _annotated(tuple[str, ...])
MAPPING_FIELDS = _annotated(Mapping[str, str])

_WORKERS_MESSAGE = "max_parallel_workers must be an integer from 1 to 20"
_SCRIPT_VARIANT_MESSAGE = "script_variant must be one of '', 'simplified', 'traditional', 'br', 'pt'"

# field -> (accepted values, reset default)
_ENUM_RESETS = {
    "deck_builder_mode": (("all", "top_n", "coverage_pct"), "all"),
    "youtube_subtitle_source": (("auto", "transcribe", "captions"), "auto"),
    "asr_model": (("large-v3", "small"), "large-v3"),
    "asr_device": (("auto", "cuda", "cpu", "vulkan"), "auto"),
}

# Fields __post_init__ validates, clamps or normalises by name.
_BY_NAME = {
    "max_parallel_workers",
    "script_variant",
    "language_stash",
    "ui_zoom",
    "deck_builder_top_n",
    "deck_builder_coverage_pct",
    "ui_language",
    "language",
    *_ENUM_RESETS,
}
_UNTOUCHED = [
    f.name
    for f in _FIELDS
    if f.name not in _BY_NAME and f.name not in {*PATH_FIELDS, *OPTIONAL_PATH_FIELDS, *TUPLE_FIELDS, *MAPPING_FIELDS}
]


def _value(name: str, given: object) -> object:
    return getattr(AnkiMinerConfig(**{name: given}), name)


class _Unhashable:
    __hash__ = None  # type: ignore[assignment]


def test_the_annotation_groups_hold_exactly_todays_fields():
    # Exact, so a field whose annotation drifts (say excluded_decks to
    # Sequence[str]) fails here instead of silently losing its coercion.
    assert PATH_FIELDS == [
        "media_temp_folder",
        "jmdict_path",
        "dicts_root",
        "audio_packs_root",
        "pitch_accent_path",
        "pitch_root",
        "freqs_root",
        "known_words_db_path",
        "stats_db_path",
        "log_path",
        "asr_models_root",
        "cuda_libs_root",
        "onnx_pack_root",
        "bin_root",
        "uv_root",
        "themes_root",
    ]
    assert OPTIONAL_PATH_FIELDS == [
        "blacklist_path",
        "whitelist_path",
        "youtube_cookies_file",
        "youtube_ffmpeg_location",
        "ytdlp_location",
        "ffmpeg_location",
        "ffprobe_location",
        "alass_location",
        "mokuro_location",
    ]
    assert TUPLE_FIELDS == [
        "excluded_decks",
        "backfill_field_groups",
        "allowed_pos",
        "excluded_subtypes",
        "excluded_wordsets",
        "theme_favorites",
        "hidden_utilities",
    ]
    assert MAPPING_FIELDS == ["anki_fields", "card_type_marker_fields", "key_bindings"]


# One test per group, looping over its fields: a parametrized case per field
# costs the unit suite's per-test fixture overhead a hundred times over.


def test_path_fields_coerce_a_str_and_nothing_else():
    for name in PATH_FIELDS:
        coerced = _value(name, "some/dir")
        assert isinstance(coerced, Path), name
        assert coerced == Path("some/dir"), name
        assert _value(name, "") == Path(""), name
        for given in (Path("kept"), None, 7, b"raw"):
            assert _value(name, given) is given, (name, given)


def test_optional_path_fields_coerce_a_str_and_empty_to_none():
    for name in OPTIONAL_PATH_FIELDS:
        coerced = _value(name, "some/file")
        assert isinstance(coerced, Path), name
        assert coerced == Path("some/file"), name
        assert _value(name, "") is None, name
        for given in (Path("kept"), None, 0, b"raw"):
            assert _value(name, given) is given, (name, given)


def test_tuple_fields_coerce_a_list_and_nothing_else():
    for name in TUPLE_FIELDS:
        coerced = _value(name, ["a", "b"])
        assert type(coerced) is tuple, name
        assert coerced == ("a", "b"), name
        assert _value(name, []) == (), name
        for given in (("kept",), "ab", {"a"}, None):
            assert _value(name, given) is given, (name, given)


def test_mapping_fields_are_wrapped_read_only_as_a_copy():
    for name in MAPPING_FIELDS:
        source = {"k": "v"}
        wrapped = _value(name, source)
        assert isinstance(wrapped, types.MappingProxyType), name
        assert dict(wrapped) == {"k": "v"}, name
        source["k"] = "changed"
        assert wrapped["k"] == "v", name
        proxy = types.MappingProxyType({"k": "v"})
        assert _value(name, proxy) is proxy, name
        assert dict(_value(name, [("k", "v")])) == {"k": "v"}, name
        with pytest.raises(TypeError):
            AnkiMinerConfig(**{name: None})
        with pytest.raises(ValueError):
            AnkiMinerConfig(**{name: ["ab", "c"]})


def test_every_other_field_is_stored_as_given():
    rewritten = [
        (name, given) for name in _UNTOUCHED for given in (["a", "list"], "a/str") if _value(name, given) is not given
    ]
    assert rewritten == []


@pytest.mark.parametrize("name", list(_ENUM_RESETS))
def test_enumerated_field_keeps_known_values_and_resets_the_rest(name):
    accepted, default = _ENUM_RESETS[name]
    for value in accepted:
        assert _value(name, value) == value
    for bad in ("bogus", "", None, 0, default.upper(), set()):
        assert _value(name, bad) == default
    for unhashable in ([], {}):
        with pytest.raises(TypeError):
            AnkiMinerConfig(**{name: unhashable})


@pytest.mark.parametrize("workers", [1, 6, 20])
def test_max_parallel_workers_accepts_1_to_20(workers):
    assert AnkiMinerConfig(max_parallel_workers=workers).max_parallel_workers == workers


@pytest.mark.parametrize("workers", [0, 21, -1, True, False, 6.0, "6", None])
def test_max_parallel_workers_rejects_everything_else(workers):
    with pytest.raises(ValueError) as excinfo:
        AnkiMinerConfig(max_parallel_workers=workers)
    assert str(excinfo.value) == _WORKERS_MESSAGE


@pytest.mark.parametrize("variant", ["", "simplified", "traditional", "br", "pt"])
def test_script_variant_accepts_the_known_ids(variant):
    assert AnkiMinerConfig(script_variant=variant).script_variant == variant


@pytest.mark.parametrize("variant", ["bogus", "Simplified", None, []])
def test_script_variant_rejects_everything_else(variant):
    with pytest.raises(ValueError) as excinfo:
        AnkiMinerConfig(script_variant=variant)
    assert str(excinfo.value) == _SCRIPT_VARIANT_MESSAGE


@pytest.mark.parametrize(
    ("name", "given", "expected"),
    [
        ("ui_zoom", 0.1, 0.5),
        ("ui_zoom", 1, 1.0),
        ("ui_zoom", 3, 2.0),
        ("ui_zoom", "1.5", 1.5),
        ("ui_zoom", True, 1.0),
        ("deck_builder_top_n", 0, 1),
        ("deck_builder_top_n", 1000, 1000),
        ("deck_builder_top_n", 100_001, 100_000),
        ("deck_builder_top_n", "5", 5),
        ("deck_builder_top_n", 5.9, 5),
        ("deck_builder_coverage_pct", 0.5, 1.0),
        ("deck_builder_coverage_pct", 90, 90.0),
        ("deck_builder_coverage_pct", 101, 100.0),
        ("deck_builder_coverage_pct", "50", 50.0),
    ],
)
def test_clamped_field_converts_and_clamps(name, given, expected):
    value = _value(name, given)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize(
    ("name", "given", "error"),
    [
        ("ui_zoom", "abc", ValueError),
        ("ui_zoom", None, TypeError),
        ("deck_builder_top_n", "top", ValueError),
        ("deck_builder_top_n", "5.5", ValueError),
        ("deck_builder_top_n", None, TypeError),
        ("deck_builder_coverage_pct", "pct", ValueError),
        ("deck_builder_coverage_pct", None, TypeError),
    ],
)
def test_clamped_field_raises_on_an_unconvertible_value(name, given, error):
    with pytest.raises(error):
        AnkiMinerConfig(**{name: given})


@pytest.mark.parametrize(
    ("given", "expected"), [(" FR ", "fr"), ("pt_BR", "pt_br"), ("", "en"), ("   ", "en"), (None, "none"), (5, "5")]
)
def test_ui_language_is_stripped_lowered_and_defaults_to_en(given, expected):
    assert AnkiMinerConfig(ui_language=given).ui_language == expected


@pytest.mark.parametrize(("given", "expected"), [(" ZH ", "zh"), ("KO", "ko"), ("xx", "ja"), ("", "ja"), (None, "ja")])
def test_language_is_normalised_and_unknown_codes_reset_to_ja(given, expected):
    assert AnkiMinerConfig(language=given).language == expected


def test_language_stash_is_deep_wrapped_with_normalised_keys():
    stash = AnkiMinerConfig(language_stash={" ZH ": {"a": 1}, 5: [("b", 2)]}).language_stash
    assert isinstance(stash, types.MappingProxyType)
    assert list(stash) == ["zh", "5"]
    assert all(isinstance(inner, types.MappingProxyType) for inner in stash.values())
    assert dict(stash["zh"]) == {"a": 1}
    assert dict(stash["5"]) == {"b": 2}


def test_language_stash_already_wrapped_all_the_way_down_is_kept():
    stash = types.MappingProxyType({"zh": types.MappingProxyType({"a": 1})})
    assert AnkiMinerConfig(language_stash=stash).language_stash is stash


def test_language_stash_with_a_mutable_inner_mapping_is_rewrapped():
    stash = types.MappingProxyType({"zh": {"a": 1}})
    rewrapped = AnkiMinerConfig(language_stash=stash).language_stash
    assert rewrapped is not stash
    assert isinstance(rewrapped["zh"], types.MappingProxyType)


@pytest.mark.parametrize(("given", "error"), [(None, TypeError), ({"zh": 5}, TypeError), ({"zh": ["x"]}, ValueError)])
def test_language_stash_that_cannot_be_wrapped_raises(given, error):
    with pytest.raises(error):
        AnkiMinerConfig(language_stash=given)


# Every guard that can raise, in the order __post_init__ evaluates them today.
_RAISING_STEPS: list[tuple[str, object]] = [
    ("max_parallel_workers", 0),
    ("script_variant", "bogus"),
    ("anki_fields", None),
    ("card_type_marker_fields", 5),
    ("key_bindings", 1.5),
    ("language_stash", {"zh": ["x"]}),
    ("ui_zoom", "abc"),
    ("deck_builder_top_n", "top"),
    ("deck_builder_coverage_pct", "pct"),
    ("deck_builder_mode", []),
    ("youtube_subtitle_source", {}),
    ("asr_model", bytearray()),
    ("asr_device", _Unhashable()),
]


def _error(**bad: object) -> tuple[type, str]:
    with pytest.raises((ValueError, TypeError)) as excinfo:
        AnkiMinerConfig(**bad)
    return type(excinfo.value), str(excinfo.value)


def test_each_raising_guard_fails_distinctly_on_its_own():
    errors = [_error(**{name: bad}) for name, bad in _RAISING_STEPS]
    assert len(set(errors)) == len(errors)
    assert errors[0] == (ValueError, _WORKERS_MESSAGE)
    assert errors[1] == (ValueError, _SCRIPT_VARIANT_MESSAGE)


def test_the_earlier_guard_wins_whenever_two_fields_are_bad():
    wrong_winner = [
        (first_name, later_name)
        for (first_name, first_bad), (later_name, later_bad) in itertools.combinations(_RAISING_STEPS, 2)
        if _error(**{first_name: first_bad, later_name: later_bad}) != _error(**{first_name: first_bad})
    ]
    assert wrong_winner == []
