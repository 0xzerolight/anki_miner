import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.wordset_service import WORDSET_IDS

_ALL_WORDSETS = ("surnames", "given-names", "place-names", "org-product")


def test_excluded_wordsets_defaults_all_on():
    """Default-ON (junk-reduction r3): every bundled name set ships enabled."""
    assert AnkiMinerConfig().excluded_wordsets == _ALL_WORDSETS


def test_config_default_matches_wordset_ids():
    """The config literal must stay byte-identical to WORDSET_IDS.

    config must not import services, so the default is a hand-duplicated
    literal; this assertion is the guard that keeps the two in sync.
    """
    assert AnkiMinerConfig().excluded_wordsets == WORDSET_IDS


def test_excluded_wordsets_replace():
    cfg = dataclasses.replace(AnkiMinerConfig(), excluded_wordsets=("surnames", "place-names"))
    assert cfg.excluded_wordsets == ("surnames", "place-names")
