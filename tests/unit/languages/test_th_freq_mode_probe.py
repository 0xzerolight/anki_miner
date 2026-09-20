"""th frequency mode probe: the two term tables resolve on a real Thai list."""

from anki_miner.services.frequency.mode_probe import LESS_COMMON_TERMS, MORE_COMMON_TERMS


def test_th_has_both_probe_tables():
    assert len(MORE_COMMON_TERMS["th"]) == 10
    assert len(LESS_COMMON_TERMS["th"]) == 10
    assert not set(MORE_COMMON_TERMS["th"]) & set(LESS_COMMON_TERMS["th"])
