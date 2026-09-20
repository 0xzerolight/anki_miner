"""S28: the mining-language capability is searchable by Turkish's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_turkish_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("turkish") and capability in search("Türkçe")
    assert {"turkish", "türkçe", "tr"} <= set(capability.keywords)
