"""S28: the mining-language capability is searchable by Spanish's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_spanish_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("spanish")
    assert capability in search("español")
    assert {"spanish", "español", "es"} <= set(capability.keywords)
