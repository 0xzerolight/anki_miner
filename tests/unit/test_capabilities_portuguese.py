"""S28: the mining-language capability is searchable by Portuguese."""

from __future__ import annotations

from anki_miner.gui.capabilities import CAPABILITIES, CapabilityTarget, search


def test_portuguese_reaches_the_mining_language_capability():
    capability = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert {"portuguese", "brazilian", "pt"} <= set(capability.keywords)
    assert capability in search("portuguese")
    assert capability in search("brazilian")


def test_regional_variety_opens_the_mining_language_page():
    """The Open button on the variety entry must land on its real row (T10)."""
    capability = next(c for c in CAPABILITIES if c.id == "regional-variety")
    assert capability.target == CapabilityTarget("settings", "mining_language")
