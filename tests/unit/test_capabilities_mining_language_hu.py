"""S28: the mining-language capability is searchable by Hungarian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_hungarian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("hungarian") and capability in search("Magyar")
    assert {"hungarian", "magyar", "hu"} <= set(capability.keywords)
