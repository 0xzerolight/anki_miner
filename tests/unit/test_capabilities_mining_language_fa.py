"""S28: the mining-language capability is searchable by Persian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_persian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    # Both English names: the language is filed under either in the wild.
    assert capability in search("persian") and capability in search("Farsi")
    assert capability in search("فارسی")
    assert {"persian", "farsi", "فارسی", "fa"} <= set(capability.keywords)
