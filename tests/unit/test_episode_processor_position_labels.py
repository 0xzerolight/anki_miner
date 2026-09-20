"""The one position formula behind the curator's Position column and the card's Source field.

A video run has no unit labels and reads as a HH:MM:SS timestamp; a reading run
stamps the unit index as a dummy start_time and carries its own human labels,
which win. Both sites call the same helper so the column and the card cannot
disagree (Issue #129).
"""

from __future__ import annotations

import pytest

from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import _position_label


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "00:00:00"), (61.4, "00:01:01"), (1867.0, "00:31:07"), (4364.9, "01:12:44"), (-5.0, "00:00:00")],
)
def test_no_unit_labels_reads_as_a_timestamp(seconds, expected):
    assert _position_label(seconds, None) == expected


def test_a_unit_label_wins_over_the_timestamp():
    """A reading run's start_time is a dummy unit index, so the label is the only truth."""
    assert _position_label(42.0, {42: "p.42"}) == "p.42"


def test_a_missing_unit_label_falls_back_to_the_timestamp():
    """Never a KeyError: a synthetic or rounded start_time still prints something."""
    assert _position_label(7.0, {42: "p.42"}) == "00:00:07"


def test_an_empty_unit_label_falls_back_to_the_timestamp():
    assert _position_label(42.0, {42: ""}) == "00:00:42"


def test_the_field_defaults_to_unstamped():
    word = TokenizedWord(
        surface="食べる",
        lemma="食べる",
        reading="",
        sentence="猫が魚を食べた",
        start_time=1867.0,
        end_time=1870.0,
        duration=3.0,
    )

    assert word.position_label == ""
