import dataclasses

from anki_miner.config import AnkiMinerConfig


def test_excluded_wordsets_defaults_empty():
    assert AnkiMinerConfig().excluded_wordsets == ()


def test_excluded_wordsets_replace():
    cfg = dataclasses.replace(AnkiMinerConfig(), excluded_wordsets=("surnames", "place-names"))
    assert cfg.excluded_wordsets == ("surnames", "place-names")
