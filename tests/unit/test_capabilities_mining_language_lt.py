"""S28: the mining-language capability is searchable by Lithuanian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_lithuanian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("lithuanian") and capability in search("Lietuvių")
    assert capability in search("LIETUVIŲ")
    assert {"lithuanian", "lietuvių", "lt"} <= set(capability.keywords)
