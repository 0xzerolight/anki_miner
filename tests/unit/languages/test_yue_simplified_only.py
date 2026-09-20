"""The simplified-only character set (spec section 9: the data ships, the predicate does not)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import anki_miner.languages.yue as yue_package

PACKAGE = Path(yue_package.__file__).parent
DATA = PACKAGE / "data" / "simplified_only.txt"
PROBE = json.loads((Path(__file__).parents[2] / "fixtures" / "yue" / "simplified_only_probe.json").read_text("utf-8"))
ROOT = Path(__file__).parents[3]


def characters() -> set[str]:
    return {line for line in DATA.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")}


def test_the_set_is_the_derived_size():
    assert len(characters()) == PROBE["total"] == 2160


def test_every_character_is_a_single_codepoint():
    assert all(len(char) == 1 for char in characters())


def test_the_probe_simplified_characters_are_in_the_set():
    assert set(PROBE["simplified_only"]) <= characters()


def test_the_traditional_look_alikes_are_not():
    # 里 后 台 干 只 才 系 面 云 几 松 制 余 are legitimate traditional spellings
    # (后髮座, 七里河區, 三台縣, 明窗淨几); a set that held them would mark a
    # traditional deck as Mandarin.
    assert not (set(PROBE["not_simplified_only"]) & characters())


def test_the_file_records_its_own_derivation():
    text = DATA.read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith("#")
    assert "CC-CEDICT" in text


def test_nothing_in_the_package_reads_it_yet():
    readers = [p.name for p in PACKAGE.rglob("*.py") if "simplified_only" in p.read_text(encoding="utf-8")]
    assert readers == []


def test_the_data_ships_in_the_wheel():
    # MANIFEST.in covers only gui/resources and resources, so a package data
    # file needs its own entry (the fa precedent, pyproject.toml).
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["setuptools"]["package-data"]["anki_miner.languages.yue.data"] == ["*.txt"]


def test_the_bundle_collects_no_yue_data():
    # Nothing reads it at runtime (the S15 predicate is deferred), so unlike
    # fa's tables it is wheel-only and the .spec collects nothing for it.
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"yue", "data"' not in spec
