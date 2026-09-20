"""S28: the mining-language capability is searchable by Russian's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_russian_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("russian") and capability in search("Русский")
    assert {"russian", "русский", "ru"} <= set(capability.keywords)
