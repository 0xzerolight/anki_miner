"""S28: the mining-language capability is searchable by Italian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_italian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("italian") and capability in search("italiano")
    assert {"italian", "italiano", "it"} <= set(capability.keywords)
