"""S28: the mining-language capability is searchable by Greek's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_greek_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("greek") and capability in search("Ελληνικά") and capability in search("ΕΛΛΗΝΙΚΆ")
    assert {"greek", "ελληνικά", "el"} <= set(capability.keywords)
