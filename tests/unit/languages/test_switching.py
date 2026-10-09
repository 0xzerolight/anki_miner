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
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, blank_scoped_defaults, switch_language

#: Registered with a stub builder in `_register`; no real zh profile exists yet.
STUB_CODE = "zh"


def _sentinel_for(value: object) -> object:
    """A value of the same shape as `value` that can never equal the ja one."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
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


#: The frequency band: its ranks index the language's own frequency_chain, so it is scoped with it.
BAND = ("min_frequency_rank", "max_frequency_rank", "frequency_keep_unranked")


def _band(config: AnkiMinerConfig) -> tuple[object, ...]:
    return tuple(getattr(config, name) for name in BAND)


def test_a_first_visit_opens_the_frequency_band_and_the_ja_band_comes_back():
    """A ja band ("skip the 3,000 commonest") never reaches a first de visit, where it would
    drop German's commonest words; it is parked with ja and restored on the way back."""
    ja = dataclasses.replace(
        AnkiMinerConfig(), min_frequency_rank=3000, max_frequency_rank=10000, frequency_keep_unranked=True
    )

    de = switch_language(ja, "de")

    assert _band(de) == (0, 0, False)
    assert _band(switch_language(de, "ja")) == (3000, 10000, True)


def test_the_blank_band_is_an_open_band():
    blank = blank_scoped_defaults()
    assert [(blank[name], type(blank[name])) for name in BAND] == [(0, int), (0, int), (False, bool)]


def test_the_first_switch_after_the_band_became_scoped_gives_old_snapshots_the_shared_band():
    """Snapshots parked before the band was scoped already carry the Stage S regex trio but no band.
    The band was global, so the first switch completes each of them with the live band once, even
    though another formerly global name is already present; a later change reaches no one else."""
    old = {name: getattr(AnkiMinerConfig(), name) for name in LANGUAGE_SCOPED_FIELDS if name not in BAND}
    on_ja = dataclasses.replace(AnkiMinerConfig(), min_frequency_rank=3000, language_stash={"de": old, "fr": old})

    on_de = switch_language(on_ja, "de")
    assert on_de.min_frequency_rank == 3000

    on_fr = switch_language(dataclasses.replace(on_de, min_frequency_rank=500), "fr")
    assert on_fr.min_frequency_rank == 3000
    assert switch_language(on_fr, "it").min_frequency_rank == 0  # a first visit still opens the band


#: The note-type answers Anki Miner Note made per-language: one language may use it, another Senren.
NOTE_TYPE_ANSWERS = ("pitch_category_format", "card_type_marker_fields")


def test_a_first_visit_keeps_the_config_default_markers_and_pitch_format():
    """Neither has a legal type-blank ("" is not a pitch format; every reader wants all four
    marker keys), so a first visit gets the config default every language shared before."""
    blank = AnkiMinerConfig()

    de = switch_language(blank, "de")

    assert de.pitch_category_format == blank.pitch_category_format
    assert dict(de.card_type_marker_fields) == dict(blank.card_type_marker_fields)


def test_the_first_switch_after_the_markers_became_scoped_gives_old_snapshots_the_shared_markers():
    """Snapshots parked before the two were scoped carry neither, so the first switch completes them
    with the live values once; a change made after that reaches no other language."""
    senren = {"word_and_sentence": "", "click": "", "sentence": "sentenceCard", "audio": "audioCard"}
    old = {name: getattr(AnkiMinerConfig(), name) for name in LANGUAGE_SCOPED_FIELDS if name not in NOTE_TYPE_ANSWERS}
    on_ja = dataclasses.replace(
        AnkiMinerConfig(),
        pitch_category_format="romaji",
        card_type_marker_fields=senren,
        language_stash={"de": old, "fr": old},
    )

    on_de = switch_language(on_ja, "de")
    assert (on_de.pitch_category_format, dict(on_de.card_type_marker_fields)) == ("romaji", senren)

    edited = dataclasses.replace(on_de, pitch_category_format="jp", card_type_marker_fields=dict.fromkeys(senren, ""))
    on_fr = switch_language(edited, "fr")
    assert (on_fr.pitch_category_format, dict(on_fr.card_type_marker_fields)) == ("romaji", senren)
    back_on_ja = switch_language(on_fr, "ja")
    assert (back_on_ja.pitch_category_format, dict(back_on_ja.card_type_marker_fields)) == ("romaji", senren)


def test_a_first_visit_lifts_the_character_cap_and_ja_gets_it_back():
    """A ja cap of 30 characters is a whole Japanese line but a German fragment, so it never
    reaches a first de visit; it is parked with ja and restored on the way back."""
    on_de = switch_language(dataclasses.replace(AnkiMinerConfig(), max_sentence_chars=30), "de")
    assert on_de.max_sentence_chars == 0
    assert switch_language(on_de, "ja").max_sentence_chars == 30


def test_the_first_switch_after_the_cap_became_scoped_gives_old_snapshots_the_shared_cap():
    """Snapshots parked while the cap was global carry no key for it, so the first switch
    completes them with the cap every language shared."""
    old = {n: getattr(AnkiMinerConfig(), n) for n in LANGUAGE_SCOPED_FIELDS if n != "max_sentence_chars"}
    on_ja = dataclasses.replace(AnkiMinerConfig(), max_sentence_chars=30, language_stash={"de": old})
    assert switch_language(on_ja, "de").max_sentence_chars == 30
