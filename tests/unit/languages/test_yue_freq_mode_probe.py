"""yue's mode-probe terms, checked against the ranks in the published asset."""

from __future__ import annotations

import json
from pathlib import Path

from anki_miner.services.frequency import mode_probe

FIXTURE = Path(__file__).parents[2] / "fixtures" / "yue" / "mode_probe.json"
RANKS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_both_tables_carry_ten_yue_terms():
    assert len(mode_probe.MORE_COMMON_TERMS["yue"]) == 10
    assert len(mode_probe.LESS_COMMON_TERMS["yue"]) == 10


def test_every_probe_term_is_present_in_the_built_list():
    assert set(mode_probe.MORE_COMMON_TERMS["yue"]) == set(RANKS["more_common"])
    assert set(mode_probe.LESS_COMMON_TERMS["yue"]) == set(RANKS["less_common"])


def test_the_common_terms_really_are_the_commoner_ones():
    assert max(RANKS["more_common"].values()) < min(RANKS["less_common"].values())


def test_the_probe_votes_rank_based_on_those_ranks():
    # probe_direction(lookup, source_language) with
    # lookup: Callable[[str], Sequence[int]] (mode_probe.py:400-403).
    # The declared frequencyMode and the statistical fallback therefore agree
    # instead of racing.
    values = {**RANKS["more_common"], **RANKS["less_common"]}
    direction = mode_probe.probe_direction(lambda term: [values[term]] if term in values else [], "yue")
    assert direction == mode_probe.ASCENDING
