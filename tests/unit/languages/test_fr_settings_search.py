"""S28: the mining-language capability is searchable by French's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_french_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("french") and capability in search("Français")
    assert {"french", "français", "fr"} <= set(capability.keywords)
