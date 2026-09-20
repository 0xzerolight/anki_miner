"""S28: the mining-language capability is searchable by Arabic's names."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_arabic_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("arabic") and capability in search("\u0627\u0644\u0639\u0631\u0628\u064a\u0629")
    assert {"arabic", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629", "ar"} <= set(capability.keywords)
