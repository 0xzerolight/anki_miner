"""The two committed Persian data files are well-formed and attributed.

Shape only: the rows themselves are exercised by the tokenizer tests. What
breaks silently is a stray empty column, a BOM, a CRLF line ending or a missing
provenance header, so those are what this pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[3] / "anki_miner" / "languages" / "fa" / "data"
LIGHT_VERB_COUNT_FLOOR = 700
COLLOQUIAL_COUNT_FLOOR = 300


def _rows(name: str, width: int) -> list[tuple[str, ...]]:
    lines = DATA.joinpath(name).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#"), "the first line records provenance"
    rows = [tuple(line.split("\t")) for line in lines if line and not line.startswith("#")]
    assert all(len(row) == width for row in rows), f"{name}: every row has {width} columns"
    assert all(all(cell for cell in row) for row in rows), f"{name}: no empty column"
    return rows


def _header(name: str) -> str:
    lines = DATA.joinpath(name).read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if line.startswith("#"))


@pytest.mark.parametrize(
    ("name", "licence", "source"),
    [
        ("compound_verbs.tsv", "CC BY-SA 4.0", "wty-fa-en"),
        ("colloquial.tsv", "MIT", "shekar"),
    ],
)
def test_the_header_records_the_licence_and_the_source(name, licence, source):
    header = _header(name)
    assert licence in header
    assert source in header


def test_compound_verbs_are_noun_plus_light_verb():
    rows = _rows("compound_verbs.tsv", 2)
    assert len(rows) >= LIGHT_VERB_COUNT_FLOOR
    assert len({row[0] for row in rows}) > 1
    assert all(verb.endswith("\N{ARABIC LETTER NOON}") for _noun, verb in rows)
    assert len(rows) == len(set(rows)), "no duplicate pairs"


def test_colloquial_pairs_differ_and_name_their_source():
    rows = _rows("colloquial.tsv", 3)
    assert len(rows) >= COLLOQUIAL_COUNT_FLOOR
    assert all(informal != formal for informal, formal, _source in rows)
    assert {source for _i, _f, source in rows} <= {"hazm", "shekar"}
    assert len({row[0] for row in rows}) == len(rows), "one formal reading per informal key"


@pytest.mark.parametrize("name", ["compound_verbs.tsv", "colloquial.tsv"])
def test_the_file_is_plain_utf8_without_a_bom(name):
    raw = DATA.joinpath(name).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw


def test_the_tables_are_importable_package_data():
    # The tokenizer reads them through importlib.resources, which needs
    # anki_miner.languages.fa to be a real package on both build paths.
    from importlib.resources import files

    root = files("anki_miner.languages.fa") / "data"
    assert (root / "compound_verbs.tsv").is_file()
    assert (root / "colloquial.tsv").is_file()


@pytest.mark.parametrize("package", ["", "data"])
def test_the_package_carries_an_init(package):
    # setuptools.packages.find is NOT the namespace finder: a directory with no
    # __init__.py is never discovered, so the package-data glob would name a
    # package the wheel does not contain and both tables would go missing.
    assert DATA.parent.joinpath(package, "__init__.py").is_file()
