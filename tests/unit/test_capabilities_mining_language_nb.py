"""S28: the mining-language capability is searchable by Norwegian Bokmål's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_norwegian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("norwegian") and capability in search("Norsk bokmål") and capability in search("Bokmål")
    assert {"norwegian", "norsk bokmål", "bokmål", "nb"} <= set(capability.keywords)
