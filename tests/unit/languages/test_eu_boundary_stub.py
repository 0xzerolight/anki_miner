"""The profile boundary, proved by a language that shares nothing with Japanese.

``eu_stub.py`` builds a complete ``LanguageProfile`` by hand — space-delimited,
Latin-script, no reading layer, no bundled resources. These tests drive it
through the real mining seams (registry, switch, parser, splitter, note builder,
dictionary key folding) and record, at the bottom, exactly which central lists a
REAL fourth language would still have to edit.

The stub is never added to the shipped contract matrix: ``PROBE``,
``CAPABILITY_VOCABULARY`` and ``EXTRA_HOOK_FIELDS`` in
``test_language_contract.py`` stay closed, and ``eu`` joins none of them.
"""

from __future__ import annotations

import collections
import dataclasses
from collections.abc import Callable, Mapping

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import AVAILABLE_LANGUAGES, registry, tagger_provider
from anki_miner.languages.profile import (
    AudioDefaults,
    CaptionLangs,
    CardFieldSpec,
    ContentTextStyle,
    LanguageProfile,
    PosDefaults,
    ResourceSpec,
    SentenceRules,
)
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS, switch_language
from anki_miner.models import CardPayload, MediaData, TokenizedWord
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS, build_note
from anki_miner.services.reading.sentence_splitter import split_sentences
from tests.unit.languages import eu_stub
from tests.unit.languages.eu_stub import EU_CODE, WhitespaceTagger, build_profile

SENTENCE = "The cat sat on the mat."


@pytest.fixture
def eu_profile(monkeypatch):
    """Register the hand-built profile and admit its code, for one test.

    TWO patches, and both are load-bearing:

    * the registry entries — ``_BUILDERS`` so ``config_language`` stops
      degrading ``eu`` to ``ja``, ``_CACHE`` so ``get_profile`` never calls a
      builder under the registry's non-reentrant lock;
    * ``config.config._LANGUAGE_CODES`` — ``AnkiMinerConfig.__post_init__``
      folds an unlisted code to ``"ja"`` (config.py:753) and ``switch_language``
      ends in a ``dataclasses.replace`` that re-runs it (switching.py:132), so
      without this the switch silently lands back on Japanese.

    ``monkeypatch`` undoes both, so ``available_languages()`` is unchanged for
    every other test in the run.
    """
    profile = build_profile()
    monkeypatch.setitem(registry._BUILDERS, EU_CODE, lambda: profile)
    monkeypatch.setitem(registry._CACHE, EU_CODE, profile)
    monkeypatch.setattr("anki_miner.config.config._LANGUAGE_CODES", ("ja", "ko", "zh", EU_CODE))
    return profile


@pytest.fixture
def eu_parser(eu_profile, monkeypatch):
    """A live ``SubtitleParserService`` for the stub, over the whitespace tagger."""
    monkeypatch.setitem(tagger_provider._TAGGERS, EU_CODE, WhitespaceTagger())
    config = switch_language(AnkiMinerConfig(), EU_CODE)
    assert config.language == EU_CODE
    return eu_profile.create_parser(config)


# ---------------------------------------------------------------------------
# Contract mirror: every LanguageProfile field, present and typed
# ---------------------------------------------------------------------------


def _duck(*names: str) -> Callable[[object], bool]:
    return lambda value: all(callable(getattr(value, name, None)) for name in names)


def _optional(check: Callable[[object], bool]) -> Callable[[object], bool]:
    return lambda value: value is None or check(value)


def _tuple_of(kind: type) -> Callable[[object], bool]:
    return lambda value: isinstance(value, tuple) and all(isinstance(item, kind) for item in value)


#: One predicate per ``LanguageProfile`` field, spelled from the declared type.
#: The set equality below is the mirror: a field added to the dataclass fails
#: here until the stub answers it, which is the whole point of a boundary stub.
FIELD_CHECKS: dict[str, Callable[[object], bool]] = {
    "code": lambda v: isinstance(v, str) and bool(v),
    "display_name": lambda v: isinstance(v, str) and bool(v),
    "create_parser": callable,
    "mined_form": _duck("mined_form"),
    "lookup": _duck("candidates"),
    "reading": _optional(_duck("word_reading")),
    "sentence_annotator": _optional(_duck("annotate_sentence")),
    "script": _duck("filter_options", "matches", "contains_target_script"),
    "audio_track_codes": lambda v: isinstance(v, frozenset) and all(isinstance(c, str) for c in v),
    "import_encodings": _tuple_of(str),
    "scoped_defaults": lambda v: isinstance(v, Mapping),
    "sentence_rules": lambda v: isinstance(v, SentenceRules),
    "normalize": callable,
    "dict_keys": _duck("fold_term", "fold_reading", "homograph_keep_mask"),
    "audio": lambda v: isinstance(v, AudioDefaults),
    "asr_language": lambda v: isinstance(v, str) and bool(v),
    "captions": lambda v: isinstance(v, CaptionLangs),
    "pos_defaults": lambda v: isinstance(v, PosDefaults),
    "catalog": _tuple_of(ResourceSpec),
    "capabilities": lambda v: isinstance(v, frozenset) and all(isinstance(c, str) for c in v),
    "card_field_defaults": lambda v: isinstance(v, Mapping) and all(isinstance(x, str) for x in v.values()),
    "render_hooks": lambda v: isinstance(v, tuple) and all(_duck("field_names", "render")(h) for h in v),
    "content_style": lambda v: isinstance(v, ContentTextStyle),
    "unavailable_reason": _optional(callable),
    "extra_card_fields": _tuple_of(CardFieldSpec),
    "smoke_sentence": lambda v: isinstance(v, str) and bool(v),
    "english_name": lambda v: isinstance(v, str) and v.isascii() and bool(v),
}


def test_the_mirror_covers_every_profile_field():
    assert set(FIELD_CHECKS) == {f.name for f in dataclasses.fields(LanguageProfile)}


@pytest.mark.parametrize("field_name", sorted(FIELD_CHECKS))
def test_hand_built_profile_answers_every_field(field_name):
    profile = build_profile()
    assert FIELD_CHECKS[field_name](getattr(profile, field_name)), field_name


def test_the_profile_is_constructed_not_replaced_off_ja():
    """Nothing the stub carries is a ja object; the two share no component.

    ``dataclasses.replace(ja_profile, ...)`` would pass most of the mirror above
    while inheriting twenty Japanese answers, so identity against ja is the
    assertion that makes the mirror mean something.
    """
    ja = registry.get_profile("ja")
    stub = build_profile()
    shared = [
        f.name
        for f in dataclasses.fields(LanguageProfile)
        # Empty containers and None are interned/singleton, not shared ja
        # answers, so identity says nothing about them.
        if getattr(stub, f.name) is getattr(ja, f.name) and getattr(stub, f.name) not in (None, (), frozenset(), "")
    ]
    assert shared == []


def test_render_hook_takes_the_config_keyword_only():
    import inspect

    hook = build_profile().render_hooks[0]
    assert inspect.signature(hook.render).parameters["config"].kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# switch_language round trip: ja -> eu -> ja
# ---------------------------------------------------------------------------


def _scoped(config: AnkiMinerConfig) -> dict[str, object]:
    return {name: getattr(config, name) for name in LANGUAGE_SCOPED_FIELDS}


def _comparable(value: object) -> object:
    return dict(value) if isinstance(value, Mapping) else value


def test_switch_to_the_stub_parks_ja_and_installs_the_stub_defaults(eu_profile):
    ja_config = AnkiMinerConfig()
    eu_config = switch_language(ja_config, EU_CODE)

    assert eu_config.language == EU_CODE
    # Every scoped field is the stub's own first-visit value: no JA-shaped
    # dataclass default reaches the eu visit.
    for name in LANGUAGE_SCOPED_FIELDS:
        assert _comparable(getattr(eu_config, name)) == _comparable(eu_profile.scoped_defaults[name]), name
    # The parked ja snapshot is the live ja values, verbatim.
    assert {k: _comparable(v) for k, v in eu_config.language_stash["ja"].items()} == {
        k: _comparable(v) for k, v in _scoped(ja_config).items()
    }


def test_no_ja_shaped_default_survives_the_switch(eu_profile):
    """Spot-checks with teeth: fields whose ja default is conspicuously ja."""
    ja_config = AnkiMinerConfig()
    eu_config = switch_language(ja_config, EU_CODE)

    # "Lapis" is a JP Mining Note layout; the jmdict chain, the ja name
    # wordsets, the unidic POS names and the "ja" subtitle language are all
    # Japanese answers a first eu visit must not inherit.
    assert ja_config.anki_note_type == "Lapis" and eu_config.anki_note_type == ""
    assert ja_config.dictionary_chain and eu_config.dictionary_chain == ()
    assert ja_config.excluded_wordsets and eu_config.excluded_wordsets == ()
    assert eu_config.allowed_pos == ("WORD",)
    assert eu_config.downloader_subtitle_langs == "en"
    assert dict(eu_config.anki_fields)["stub_extra"] == ""
    # Deck name is the one the blank-by-type loop gets WRONG rather than merely
    # empty ("" is not a deck AnkiConnect accepts), so the stub overrides it to
    # the generic default - which the ja default happens to share.
    assert eu_config.anki_deck_name == "Anki Miner"


def test_round_trip_restores_every_parked_ja_value(eu_profile):
    ja_config = dataclasses.replace(AnkiMinerConfig(), anki_deck_name="My JP Deck", anki_tags="mined jp")
    before = {k: _comparable(v) for k, v in _scoped(ja_config).items()}

    eu_config = switch_language(ja_config, EU_CODE)
    back = switch_language(eu_config, "ja")

    assert back.language == "ja"
    assert {k: _comparable(v) for k, v in _scoped(back).items()} == before
    # A non-scoped field is untouched throughout, and the outgoing language's
    # own stash entry is dropped rather than kept stale.
    assert back.anki_tags == "mined jp"
    assert "ja" not in back.language_stash
    assert EU_CODE in back.language_stash


def test_second_visit_serves_the_parked_stub_values_not_the_defaults(eu_profile):
    eu_config = switch_language(AnkiMinerConfig(), EU_CODE)
    edited = dataclasses.replace(eu_config, anki_deck_name="Stub Deck")
    again = switch_language(switch_language(edited, "ja"), EU_CODE)

    assert again.anki_deck_name == "Stub Deck"


def test_switch_raises_when_a_scoped_field_has_no_default(eu_profile, monkeypatch):
    """The completeness raise is itself under test (switching.py:109-111)."""
    incomplete = dataclasses.replace(
        eu_profile,
        scoped_defaults={k: v for k, v in eu_profile.scoped_defaults.items() if k != "anki_fields"},
    )
    monkeypatch.setitem(registry._CACHE, EU_CODE, incomplete)

    with pytest.raises(ValueError, match="anki_fields"):
        switch_language(AnkiMinerConfig(), EU_CODE)


def test_the_language_codes_gate_is_what_makes_the_switch_stick():
    """Without the _LANGUAGE_CODES patch the switch folds straight back to ja.

    Deliberately does NOT take the ``eu_profile`` fixture's config patch: this
    is the gate, and naming it is half the boundary proof.
    """
    profile = build_profile()
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(registry._BUILDERS, EU_CODE, lambda: profile)
        mp.setitem(registry._CACHE, EU_CODE, profile)
        assert switch_language(AnkiMinerConfig(), EU_CODE).language == "ja"


# ---------------------------------------------------------------------------
# Mining pipeline: tokenize -> parse -> count
# ---------------------------------------------------------------------------


def test_the_whitespace_tagger_emits_locatable_duck_tokens():
    tokens = WhitespaceTagger()(SENTENCE)

    assert [t.surface for t in tokens] == ["The", "cat", "sat", "on", "the", "mat."]
    assert all(t.surface in SENTENCE for t in tokens)
    assert [t.feature.lemma for t in tokens] == ["the", "cat", "sat", "on", "the", "mat"]
    assert {t.feature.pos1 for t in tokens} == {"WORD"}
    assert {t.feature.kana for t in tokens} == {""}


def test_the_parser_resolves_the_stub_tagger_through_the_provider(eu_parser):
    assert isinstance(eu_parser.tagger, WhitespaceTagger)


def test_parse_text_units_mines_casefolded_words(eu_parser):
    units = [ReadingUnit(text=SENTENCE, index=0, location_label="p.1")]

    words, line_index, counts = eu_parser.parse_text_units(units, want_line_index=True)

    assert words
    fronts = [w.mined_form for w in words]
    # Card fronts are the folded lemma: no capital, no trailing period, and
    # "The"/"the" collapse to one card (mined_form-keyed dedup).
    assert fronts == ["the", "cat", "sat", "on", "mat"]
    assert all(front == front.casefold() for front in fronts)
    assert all(w.sentence == SENTENCE for w in words)
    assert line_index is not None and len(line_index) == 1
    assert counts == collections.Counter({"the": 2, "cat": 1, "sat": 1, "on": 1, "mat": 1})


def test_the_script_gate_drops_a_number_but_keeps_a_word(eu_parser):
    units = [ReadingUnit(text="Room 101 opened.", index=0, location_label="p.1")]

    words, _index, counts = eu_parser.parse_text_units(units, want_line_index=False)

    assert [w.mined_form for w in words] == ["room", "opened"]
    assert "101" not in counts


def test_count_lemmas_runs_the_same_gate_over_a_subtitle_file(eu_parser, tmp_path):
    srt = tmp_path / "stub.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nThe cat sat on the mat.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nThe dog barked!\n",
        encoding="utf-8",
    )

    counts = eu_parser.count_lemmas(srt)

    assert counts == collections.Counter({"the": 3, "cat": 1, "sat": 1, "on": 1, "mat": 1, "dog": 1, "barked": 1})


def test_extract_lemma_still_truncates_a_hyphenated_compound(eu_parser):
    """A residual ja-ism, pinned rather than papered over.

    ``morphology.extract_lemma`` strips a hyphen tail containing an ASCII letter
    — unidic's ``スクランブル-scramble`` disambiguator. On Latin script that also
    truncates a genuine compound: "well-known" mines as "well". It is not one of
    the injectable seams, so a real Latin-script language would need this gated
    (on the token type, or on the profile) rather than assumed harmless.
    """
    units = [ReadingUnit(text="A well-known author.", index=0, location_label="p.1")]

    words, _index, _counts = eu_parser.parse_text_units(units, want_line_index=False)

    assert "well" in [w.mined_form for w in words]
    assert "well-known" not in [w.mined_form for w in words]


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------


def test_space_aware_rules_split_ascii_sentences_and_keep_the_decimal():
    """What the splitter ACTUALLY does for space-delimited text: 3, not 2.

    ``space_aware`` requires a terminator run to be followed by whitespace or
    end-of-text, which is exactly enough to keep "3.14" whole. It is NOT an
    abbreviation model: "Dr." is a period followed by a space, so it ends a
    sentence. The splitter has no abbreviation list and none is added here —
    the gap is real, would need a per-language abbreviation set to close, and
    affects any language whose terminator set contains ".".
    """
    rules = build_profile().sentence_rules

    assert split_sentences("Dr. Smith paid 3.14 today. He left!", rules=rules) == [
        "Dr.",
        "Smith paid 3.14 today.",
        "He left!",
    ]


def test_japanese_rules_would_not_split_the_same_text_at_all():
    assert split_sentences("Dr. Smith paid 3.14 today. He left!") == ["Dr. Smith paid 3.14 today. He left!"]


def test_stub_rules_still_gate_on_brackets():
    rules = build_profile().sentence_rules

    assert split_sentences("He said (wait. really) and left.", rules=rules) == ["He said (wait. really) and left."]


# ---------------------------------------------------------------------------
# Dictionary key folding
# ---------------------------------------------------------------------------


def test_case_variants_fold_to_one_dictionary_key():
    keys = build_profile().dict_keys

    assert keys.fold_term("Word") == keys.fold_term("word") == keys.fold_term("WORD") == "word"
    # NFD input composes, so a decomposed accent is the same key as a composed
    # one - and casefolding is applied to both sides symmetrically.
    nfd, nfc = "Cafe\u0301", "Caf\u00e9"
    assert nfd != nfc
    assert keys.fold_term(nfd) == keys.fold_term(nfc) == "caf\u00e9"
    # Readings compose but do NOT casefold: the stub has no reading layer, so
    # this is the ko arrangement (NFC-or-None), not zh's pinyin lowering.
    assert keys.fold_reading(None) is None
    assert keys.fold_reading(nfd) == nfc


def test_homograph_mask_is_rule_a_only():
    keys = build_profile().dict_keys
    rows = [("bank", "financial institution"), ("bank", "river edge"), ("banking", "the trade")]

    assert keys.homograph_keep_mask("bank", rows) == [True, True, False]
    # No term-exact row: nothing is dropped.
    assert keys.homograph_keep_mask("river", rows) == [True, True, True]


# ---------------------------------------------------------------------------
# Card fields: a key neither frozen central set carries
# ---------------------------------------------------------------------------


def _payload(extra_fields: dict[str, str]) -> CardPayload:
    word = TokenizedWord(
        surface="cat",
        lemma="cat",
        reading="",
        sentence=SENTENCE,
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
        orth_base="cat",
        pos="WORD",
    )
    return CardPayload(word=word, media=MediaData(), definition="a small feline", extra_fields=extra_fields)


def test_profile_declared_field_reaches_the_note(eu_profile):
    spec = eu_profile.extra_card_fields[0]
    fields = dict(eu_profile.card_field_defaults) | {spec.key: spec.placeholder}
    config = dataclasses.replace(switch_language(AnkiMinerConfig(), EU_CODE), anki_fields=fields)
    payload = _payload({spec.key: "CAT"})

    note = build_note(
        payload,
        config,
        set(),
        extra_optional_keys=frozenset({s.key for s in eu_profile.extra_card_fields}),
        extra_raw_html_keys=frozenset({s.key for s in eu_profile.extra_card_fields if s.raw_html}),
    ).note

    assert note["fields"]["StubExtra"] == "CAT"
    assert note["fields"]["Expression"] == "cat"
    assert note["deckName"] == "Anki Miner"


def test_the_key_only_arrives_through_the_per_call_kwarg(eu_profile):
    """Without the profile-declared kwarg the field is dropped: the central
    ``OPTIONAL_FIELD_KEYS`` is frozen and does not carry it."""
    spec = eu_profile.extra_card_fields[0]
    assert spec.key not in OPTIONAL_FIELD_KEYS

    fields = dict(eu_profile.card_field_defaults) | {spec.key: spec.placeholder}
    config = dataclasses.replace(switch_language(AnkiMinerConfig(), EU_CODE), anki_fields=fields)

    note = build_note(_payload({spec.key: "CAT"}), config, set()).note

    assert "StubExtra" not in note["fields"]


def test_the_hook_renders_the_declared_key(eu_profile):
    config = switch_language(AnkiMinerConfig(), EU_CODE)
    hook = eu_profile.render_hooks[0]
    word = _payload({}).word

    assert hook.field_names() == ("stub_extra",)
    assert hook.render(word, config=config) == {"stub_extra": "CAT"}


def test_declared_keys_satisfy_the_amended_contract_assertion(eu_profile):
    """The Task 3 relaxation, applied to the profile it exists for.

    ``test_language_contract.test_card_fields_and_hooks_agree`` allows a hook
    key that the profile itself declares, so a language's own card field no
    longer has to grow the frozen central ``OPTIONAL_FIELD_KEYS``.
    """
    hook_keys = {name for hook in eu_profile.render_hooks for name in hook.field_names()}
    declared = {spec.key for spec in eu_profile.extra_card_fields}

    assert hook_keys <= set(eu_profile.card_field_defaults)
    assert hook_keys <= (OPTIONAL_FIELD_KEYS | declared)
    assert not hook_keys <= OPTIONAL_FIELD_KEYS  # the relaxation is load-bearing here


# ---------------------------------------------------------------------------
# What a REAL fourth language would still have to edit
# ---------------------------------------------------------------------------


def test_the_stub_is_absent_from_every_shipped_list():
    """The whole of what admitting a language costs, outside its own profile.

    Two runtime gates and three contract-governance sets. Nothing else: no
    service, no worker, no GUI panel and no importer names a language code.
    """
    from anki_miner.config.config import _LANGUAGE_CODES
    from tests.unit.languages.test_language_contract import (
        CAPABILITY_VOCABULARY,
        EXTRA_HOOK_FIELDS,
        PROBE,
    )

    # Runtime gates.
    assert EU_CODE not in _LANGUAGE_CODES
    assert EU_CODE not in AVAILABLE_LANGUAGES
    assert EU_CODE not in registry.available_languages()
    # Contract governance — closed on purpose; a typo'd capability is a
    # silently-off feature wherever it is gated.
    assert EU_CODE not in PROBE
    assert not build_profile().capabilities <= CAPABILITY_VOCABULARY
    assert not {s.key for s in build_profile().extra_card_fields} <= EXTRA_HOOK_FIELDS


def test_the_stub_module_ships_no_package():
    """``languages/eu/`` does not exist, so ``_discover`` can never register it."""
    import importlib.util
    from pathlib import Path

    import anki_miner.languages as languages_pkg

    assert importlib.util.find_spec("anki_miner.languages.eu") is None
    assert not (Path(languages_pkg.__file__).parent / EU_CODE).exists()
    assert eu_stub.__file__.startswith(str(Path(__file__).parent))
