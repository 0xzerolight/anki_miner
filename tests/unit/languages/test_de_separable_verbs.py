"""German separable verbs: the svp stash, the dictionary headword gate and the particle-less lookup rung.

Measured over 1,516 real svp arcs (de plan D6): 68.5 % reattach to an attested
verb, 22.6 % keep the bare verb, 7.6 % have a non-verb head (no stash) and 1.3 %
are attested only through a noun homograph (``Ruf … an`` → ``anruf``), an
accepted residual of the existence-only AttestLookup, deliberately not pinned.
"""

from __future__ import annotations

import logging

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit


@pytest.fixture
def config():
    return switch_language(AnkiMinerConfig(), "de")


def _mined(parser, sentence: str) -> set[str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=sentence, index=0, location_label="t")], False)
    return {word.mined_form for word in words}


def _parser(config, attested: set[str] | None, calls: list[list[str]] | None = None):
    if attested is None:
        return get_profile("de").create_parser(config)

    def term_lookup(words: list[str]) -> set[str]:
        if calls is not None:
            calls.append(list(words))
        return attested & set(words)

    return get_profile("de").create_parser(config, term_lookup=term_lookup)


def test_without_a_dictionary_the_particle_reattaches(config):
    assert _mined(_parser(config, None), "Er sieht sich den Film an.") == {"ansehen", "Film"}


def test_an_attested_headword_is_the_front(config):
    calls: list[list[str]] = []
    mined = _mined(_parser(config, {"ansehen"}, calls), "Er sieht sich den Film an.")
    assert mined == {"ansehen", "Film"}
    assert any("ansehen" in batch for batch in calls)


def test_an_unattested_particle_verb_keeps_the_bare_verb_and_logs_it(config, caplog):
    with caplog.at_level(logging.DEBUG, logger="anki_miner.languages._spaced.morphology"):
        mined = _mined(_parser(config, set()), "Er sieht sich den Film an.")
    assert mined == {"sehen", "Film"}
    assert "ansehen" in caplog.text


def test_two_particle_verbs_on_one_line_are_gated_separately(config):
    mined = _mined(_parser(config, {"aufstehen"}), "Sie steht auf und macht das Fenster zu.")
    assert {"aufstehen", "machen", "Fenster"} <= mined
    assert not {"zumachen", "auf", "zu"} & mined


def test_the_lemmatiser_builds_one_token_forms_without_the_gate(config):
    mined = _mined(_parser(config, set()), "Ich habe keine Lust, mir den Film anzusehen.")
    assert "ansehen" in mined


def test_a_misparsed_head_never_reattaches(config):
    mined = _mined(_parser(config, None), "Mach bitte die Tür zu.")
    assert "Tür" in mined and not {"zu", "zumach", "zumachen"} & mined


def test_a_missing_particle_verb_falls_back_to_the_base_verb():
    ladder = get_profile("de").lookup
    # (mined_form, surface): the surface rungs come first, then the rung over the mined form (contract items 1-2).
    assert ladder.candidates("herumfahren", "herumgefahren", None) == [
        ("herumgefahren", 0),
        ("fahren", 0),
        ("umfahren", 0),
    ]
    candidates = ladder.candidates("ansehen", "anzusehen", None)
    assert candidates == [("anzusehen", 0), ("sehen", 0)] and ("zusehen", 0) not in candidates
