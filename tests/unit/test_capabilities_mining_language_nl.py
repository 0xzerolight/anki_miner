"""S28: the mining-language capability is searchable by Dutch's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_dutch_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("dutch") and capability in search("Nederlands")
    assert {"dutch", "nederlands", "nl"} <= set(capability.keywords)
