"""S28: the mining-language capability is searchable by Vietnamese's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_vietnamese_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("vietnamese") and capability in search("Tiếng Việt")
    assert capability in search("TIẾNG VIỆT")
    assert {"vietnamese", "tiếng việt", "vi"} <= set(capability.keywords)
