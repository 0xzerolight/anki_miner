"""S28: the mining-language capability is searchable by Danish's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_danish_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("danish") and capability in search("Dansk")
    assert {"danish", "dansk", "da"} <= set(capability.keywords)
