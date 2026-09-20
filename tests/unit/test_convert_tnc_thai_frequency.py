"""The TNC/TTC converter emits a Yomitan frequency dictionary."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "convert_tnc_thai_frequency.py"
_spec = importlib.util.spec_from_file_location("convert_tnc_thai_frequency", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ctf = importlib.util.module_from_spec(_spec)
sys.modules["convert_tnc_thai_frequency"] = ctf
_spec.loader.exec_module(ctf)

build_zip = ctf.build_zip
parse_rows = ctf.parse_rows


def test_rows_parse_as_term_and_count():
    text = "ที่\t1234567\nการ\t1000000\nกังหัน\t42\n"
    assert parse_rows(text) == [("ที่", 1234567), ("การ", 1000000), ("กังหัน", 42)]


def test_blank_and_malformed_lines_are_dropped():
    assert parse_rows("\nที่\t1\nbroken\nการ\tnotanumber\n") == [("ที่", 1)]


def test_the_zip_is_a_yomitan_frequency_dictionary(tmp_path):
    payload = build_zip([("ที่", 5), ("การ", 3)], title="TNC Thai", revision="2026-09-20")
    out = tmp_path / "tnc-th.zip"
    out.write_bytes(payload)
    with zipfile.ZipFile(out) as archive:
        index = json.loads(archive.read("index.json"))
        bank = json.loads(archive.read("term_meta_bank_1.json"))
    assert index["format"] == 3
    assert index["title"] == "TNC Thai"
    assert index["frequencyMode"] == "occurrence-based"
    assert index["sourceLanguage"] == "th"
    assert bank[0] == ["ที่", "freq", 5]
    assert len(bank) == 2


def test_the_declared_mode_is_the_importer_own_constant():
    from anki_miner.services.frequency.mode_probe import OCCURRENCE_BASED

    payload = build_zip([("ที่", 5)], title="TNC Thai", revision="r")
    with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
        index = json.loads(archive.read("index.json"))
    assert index["frequencyMode"] == OCCURRENCE_BASED


def test_the_importer_ranks_the_biggest_count_first(tmp_path):
    """judge-r1 B3: the declared mode must make the importer RANK the counts.

    ``mode_probe.OCCURRENCE_BASED`` is the literal ``"occurrence-based"``;
    ``source_importer`` takes ``index.json``'s declared mode as-is and never
    validates it, so a typo'd mode would store an 818,364 count as a rank in an
    immutable published asset. This test is the guard: the top term stores
    rank 1, the rarest one stores rank 3.
    """
    from anki_miner.services.frequency.source_importer import import_frequency_source

    src = tmp_path / "tnc-th.zip"
    src.write_bytes(build_zip([("ที่", 5), ("การ", 3), ("กังหัน", 1)], title="TNC Thai", revision="r"))
    dest = tmp_path / "freqs"
    import_frequency_source(src, dest, source_id="tnc-th", language="th")
    conn = sqlite3.connect(dest / "tnc-th" / "index.sqlite")
    try:
        rows = conn.execute("SELECT term, rank FROM entries ORDER BY rank").fetchall()
    finally:
        conn.close()
    assert rows == [("ที่", 1), ("การ", 2), ("กังหัน", 3)]
