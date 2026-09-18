"""S28: the mining-language capability is searchable by Finnish's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_finnish_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("finnish") and capability in search("Suomi")
    assert {"finnish", "suomi", "fi"} <= set(capability.keywords)
