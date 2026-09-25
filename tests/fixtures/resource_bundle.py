"""A small but real resource setup for the resource-bundle tests.

One of each exportable resource, built through the real importers, so the
bundle round trip exercises the same slots a user's install holds.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.frequency.source_importer import import_frequency_source
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.pitch_accent.source_importer import import_pitch_source
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip

FREQ_ID = "test-freq"
PITCH_ID = "test-pitch"


def build_resource_setup(root: Path, base: AnkiMinerConfig) -> AnkiMinerConfig:
    """Install a dictionary, frequency and pitch list, ignore list and blacklist under ``root``."""
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    dict_id = import_yomitan_zip(build_yomitan_zip(inputs / "dict.zip", title="Test Dict"), root / "dicts").dict_id
    freq_csv = inputs / "freq.csv"
    freq_csv.write_text("term,rank\n猫,5\n", encoding="utf-8")
    import_frequency_source(freq_csv, root / "freqs", source_id=FREQ_ID, source_name="Test Freq")
    pitch_csv = inputs / "pitch.csv"
    pitch_csv.write_text("ねこ,猫,1\n", encoding="utf-8")
    import_pitch_source(pitch_csv, root / "pitch", source_id=PITCH_ID, source_name="Test Pitch")
    known_words_db = root / "known_words.db"
    db = KnownWordDB(known_words_db)
    db.initialize()
    db.add_words({"猫"}, source="user")
    db.add_words({"犬"}, source="anki")
    blacklist = inputs / "blacklist.txt"
    blacklist.write_text("猫\n", encoding="utf-8")
    return replace(
        base,
        dicts_root=root / "dicts",
        freqs_root=root / "freqs",
        pitch_root=root / "pitch",
        known_words_db_path=known_words_db,
        dictionary_chain=(ChainEntry(kind="indexed", dict_id=dict_id), ChainEntry(kind="jisho", enabled=False)),
        frequency_chain=(FreqEntry(FREQ_ID),),
        pitch_chain=(PitchSourceEntry(PITCH_ID),),
        blacklist_path=blacklist,
        use_blacklist=True,
    )


def empty_receiver(root: Path, base: AnkiMinerConfig) -> AnkiMinerConfig:
    """A fresh install's resource state under ``root``: no slots, nothing chained."""
    return replace(
        base,
        dicts_root=root / "dicts",
        freqs_root=root / "freqs",
        pitch_root=root / "pitch",
        known_words_db_path=root / "known_words.db",
        dictionary_chain=(ChainEntry(kind="jisho", enabled=False),),
        frequency_chain=(),
        pitch_chain=(),
        blacklist_path=None,
        use_blacklist=False,
        whitelist_path=None,
        use_whitelist=False,
    )
