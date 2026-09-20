"""S28: the mining-language capability is searchable by Hebrew's names and its own vocabulary."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, search


def test_hebrew_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert capability in search("hebrew") and capability in search("Hebrew")
    assert capability in search("עברית")
    # The two words a Hebrew learner is likeliest to type when looking for this.
    assert capability in search("niqqud")
    assert capability in search("rtl")
    assert {"hebrew", "עברית", "niqqud", "rtl", "he"} <= set(capability.keywords)
