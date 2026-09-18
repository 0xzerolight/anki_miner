"""S28: the mining-language capability is searchable by Swedish's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_swedish_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("swedish") and capability in search("Svenska")
    assert {"swedish", "svenska", "sv"} <= set(capability.keywords)
