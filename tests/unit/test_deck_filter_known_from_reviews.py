"""F-01 must not reach Deck Filter: there the known set is also the duplicate gate.

A reviewed-only set would copy every word whose note is still new into
the filtered deck (REPORT, F-01 judge round 2). FakeAnkiService has no
get_reviewed_vocabulary, so a call to it would raise AttributeError.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.services.deck_filter import scan_deck_filter
from tests.unit.test_deck_filter import FakeAnkiService, _note, _options, _services


def test_deck_filter_keeps_every_card_with_the_box_on(test_config):
    config = replace(test_config, known_from_reviewed_cards=True)
    anki = FakeAnkiService(
        notes={
            1: _note(1, "Core", {"Expression": "頷く"}),
            2: _note(2, "Core", {"Expression": "食べる"}),
        },
        vocab={"食べる"},
    )

    plan = scan_deck_filter(anki, config, _services(config), _options())

    assert [kept.expression for kept in plan.kept] == ["頷く"]
    assert dict(plan.drops) == {"known": 1}
    assert anki.vocab_queries == ["Premade"]
