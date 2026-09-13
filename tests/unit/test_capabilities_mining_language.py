"""S28: the mining-language capability is searchable by every shipped language's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_english_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("english")
    assert {"english", "en"} <= set(capability.keywords)
    assert capability.title == "Mine another language"
