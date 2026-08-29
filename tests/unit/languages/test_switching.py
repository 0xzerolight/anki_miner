"""`switch_language`: the language-scoped stash-swap (spec sections 4 and 6).

The incoming language is stubbed rather than built for real — Stage 1A has no
zh profile — by registering a `dataclasses.replace` clone of the ja profile
under the zh code. The code has to be a REAL member of
`config._LANGUAGE_CODES`: `AnkiMinerConfig.__post_init__` resets any unknown
`language` value to "ja", so a made-up "xx" would never survive the swap.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.languages import registry
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language

#: Registered with a stub builder in `_register`; no real zh profile exists yet.
STUB_CODE = "zh"


def _sentinel_for(value: object) -> object:
    """A value of the same shape as `value` that can never equal the ja one."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return "__stub__"
    if isinstance(value, tuple):
        return ("__stub__",)
    if isinstance(value, Mapping):
        return {"word": "__stub__"}
    if value is None:
        return Path("/stub/list.txt")
    raise AssertionError(f"no sentinel rule for {type(value).__name__}")


#: Scoped fields whose value space `AnkiMinerConfig.__post_init__` validates, so
#: a merely type-shaped sentinel would raise instead of differing: task 2A.11's
#: `script_variant` is constrained to {"", "simplified", "traditional"}. Each
#: value here still differs from the ja one, which is what the tests assert.
_VALIDATED_SENTINELS: Mapping[str, object] = {"script_variant": "traditional"}


def _stub_defaults() -> dict[str, object]:
    ja = AnkiMinerConfig()
    return {name: _VALIDATED_SENTINELS.get(name, _sentinel_for(getattr(ja, name))) for name in LANGUAGE_SCOPED_FIELDS}


def _stub(code, defaults):
    return dataclasses.replace(get_profile("ja"), code=code, scoped_defaults=defaults)


def _register(monkeypatch, profile):
    """Register `profile` for the length of one test.

    `_CACHE` is populated with `setitem`, not cleared with `delitem`: a
    `delitem(..., raising=False)` of an absent key records no undo, so the stub
    would leak into the process-wide cache for the rest of the worker session.
    """
    monkeypatch.setitem(registry._BUILDERS, profile.code, lambda: profile)
    monkeypatch.setitem(registry._CACHE, profile.code, profile)


def test_first_visit_uses_the_profile_defaults_and_no_ja_value(monkeypatch):
    """Spec 4: a first zh visit produces no JA-shaped value in any scoped field."""
    ja = AnkiMinerConfig()
    defaults = _stub_defaults()
    _register(monkeypatch, _stub(STUB_CODE, defaults))

    out = switch_language(ja, STUB_CODE)

    for name in LANGUAGE_SCOPED_FIELDS:
        assert getattr(out, name) == defaults[name], name
        assert getattr(out, name) != getattr(ja, name), name
    assert out.language == STUB_CODE


def test_the_outgoing_language_is_parked_whole(monkeypatch):
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))
    ja = AnkiMinerConfig()

    out = switch_language(ja, STUB_CODE)

    assert set(out.language_stash) == {"ja"}
    assert set(out.language_stash["ja"]) == set(LANGUAGE_SCOPED_FIELDS)
    for name in LANGUAGE_SCOPED_FIELDS:
        assert out.language_stash["ja"][name] == getattr(ja, name), name


def test_round_trip_restores_every_scoped_ja_value(monkeypatch):
    ja = AnkiMinerConfig()
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))

    back = switch_language(switch_language(ja, STUB_CODE), "ja")

    assert back.language == "ja"
    for name in LANGUAGE_SCOPED_FIELDS:
        assert getattr(back, name) == getattr(ja, name), name
    assert dict(back.language_stash) != {}  # the zh side is parked, not lost


def test_second_visit_restores_the_stash_not_the_defaults(monkeypatch):
    """An edit made while on zh survives a switch away and back."""
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))
    ja = AnkiMinerConfig()

    on_zh = dataclasses.replace(switch_language(ja, STUB_CODE), anki_deck_name="Edited on zh")
    again = switch_language(switch_language(on_zh, "ja"), STUB_CODE)

    assert again.language == STUB_CODE
    assert again.anki_deck_name == "Edited on zh"
    assert again.anki_deck_name != _stub_defaults()["anki_deck_name"]


def test_the_active_language_is_never_left_in_the_stash(monkeypatch):
    """Arrival state a settings IMPORT can produce: `language` is portable and
    comes from the file, `language_stash` is machine-specific and is stripped
    from it, so the local stash can still hold a snapshot for the language the
    import just made active. Switching away must not keep that stale entry."""
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))
    arrived = dataclasses.replace(
        AnkiMinerConfig(),
        language=STUB_CODE,
        anki_deck_name="Live zh deck",
        language_stash={STUB_CODE: dict.fromkeys(LANGUAGE_SCOPED_FIELDS, "__stale__")},
    )

    out = switch_language(arrived, "ja")

    assert out.language == "ja"
    assert out.language_stash[STUB_CODE]["anki_deck_name"] == "Live zh deck"
    assert "__stale__" not in dict(out.language_stash[STUB_CODE]).values()


def test_a_stale_active_stash_entry_is_dropped_even_by_a_same_code_call(monkeypatch):
    """Same arrival state, but the user re-picks the language already active."""
    arrived = dataclasses.replace(
        AnkiMinerConfig(),
        language=STUB_CODE,
        anki_deck_name="Live zh deck",
        language_stash={
            STUB_CODE: dict.fromkeys(LANGUAGE_SCOPED_FIELDS, "__stale__"),
            "ko": {"anki_deck_name": "KO"},
        },
    )

    out = switch_language(arrived, STUB_CODE)

    assert out.language == STUB_CODE
    assert STUB_CODE not in out.language_stash
    assert out.language_stash["ko"]["anki_deck_name"] == "KO"
    assert out.anki_deck_name == "Live zh deck"  # live fields untouched


def test_the_incoming_code_is_normalized_like_the_stash_keys(monkeypatch):
    """`__post_init__` lower-strips both `language` and every stash key, so an
    unnormalized argument must be folded BEFORE the stash is keyed off it —
    otherwise the pop misses and the incoming snapshot stays parked under the
    now-active language."""
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))
    parked = dataclasses.replace(
        AnkiMinerConfig(),
        language_stash={
            STUB_CODE: {name: getattr(AnkiMinerConfig(), name) for name in LANGUAGE_SCOPED_FIELDS}
            | {"anki_deck_name": "Parked zh deck"}
        },
    )

    out = switch_language(parked, " ZH ")

    assert out.language == STUB_CODE
    assert STUB_CODE not in out.language_stash
    assert out.anki_deck_name == "Parked zh deck"


def test_a_partial_stash_falls_back_to_the_defaults_for_absent_fields(monkeypatch):
    """Every pre-2A.11 stash is partial by construction (2A.11 appends two names
    to LANGUAGE_SCOPED_FIELDS with no schema bump), so an absent key must take
    the incoming profile's default — never the OUTGOING language's live value."""
    defaults = _stub_defaults()
    _register(monkeypatch, _stub(STUB_CODE, defaults))
    kept, dropped = LANGUAGE_SCOPED_FIELDS[0], "anki_deck_name"
    ja = dataclasses.replace(
        AnkiMinerConfig(),
        anki_deck_name="Live ja deck",
        language_stash={STUB_CODE: {kept: defaults[kept]}},
    )

    out = switch_language(ja, STUB_CODE)

    assert getattr(out, kept) == defaults[kept]
    assert out.anki_deck_name == defaults[dropped]
    assert out.anki_deck_name != "Live ja deck"


def test_a_bogus_stash_key_is_dropped_instead_of_raising(monkeypatch):
    """A hand-edited gui_config.json can park a name that is not a config field;
    `dataclasses.replace` would raise TypeError on it."""
    defaults = _stub_defaults()
    _register(monkeypatch, _stub(STUB_CODE, defaults))
    ja = dataclasses.replace(
        AnkiMinerConfig(),
        language_stash={STUB_CODE: dict(defaults) | {"not_a_config_field": "boom"}},
    )

    out = switch_language(ja, STUB_CODE)

    assert out.language == STUB_CODE
    assert not hasattr(out, "not_a_config_field")
    for name in LANGUAGE_SCOPED_FIELDS:
        assert getattr(out, name) == defaults[name], name


def test_missing_scoped_default_raises_valueerror_naming_the_field(monkeypatch):
    partial = {n: getattr(AnkiMinerConfig(), n) for n in LANGUAGE_SCOPED_FIELDS[:-1]}
    _register(monkeypatch, _stub(STUB_CODE, partial))
    with pytest.raises(ValueError, match=LANGUAGE_SCOPED_FIELDS[-1]):
        switch_language(AnkiMinerConfig(), STUB_CODE)


def test_never_mutates_the_input(monkeypatch):
    ja = AnkiMinerConfig()
    before = {n: getattr(ja, n) for n in LANGUAGE_SCOPED_FIELDS}
    _register(monkeypatch, _stub(STUB_CODE, _stub_defaults()))

    out = switch_language(ja, STUB_CODE)

    assert out is not ja
    assert {n: getattr(ja, n) for n in LANGUAGE_SCOPED_FIELDS} == before
    assert ja.language == "ja" and ja.language_stash == {}


def test_the_profiles_own_defaults_are_not_mutated(monkeypatch):
    """`scoped_defaults` hands back the profile's own objects (they are the
    config's default objects); the swap copies, never edits in place."""
    defaults = _stub_defaults()
    snapshot = dict(defaults)
    _register(monkeypatch, _stub(STUB_CODE, defaults))

    switch_language(AnkiMinerConfig(), STUB_CODE)

    assert defaults == snapshot


def test_same_code_is_a_no_op(monkeypatch):
    ja = AnkiMinerConfig()
    assert switch_language(ja, "ja") is ja


def test_unknown_code_raises_valueerror():
    with pytest.raises(ValueError):
        switch_language(AnkiMinerConfig(), "qq")
