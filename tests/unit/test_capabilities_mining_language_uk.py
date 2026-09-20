"""S28: the mining-language capability is searchable by Ukrainian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_ukrainian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("ukrainian") and capability in search("Українська")
    assert {"ukrainian", "українська", "uk"} <= set(capability.keywords)
