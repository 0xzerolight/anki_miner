"""S28: the mining-language capability is searchable by Cantonese's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_cantonese_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("cantonese") and capability in search("廣東話")
    assert {"cantonese", "粵語", "廣東話", "jyutping", "yue"} <= set(capability.keywords)
