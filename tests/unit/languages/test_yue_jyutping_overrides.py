"""yue spoken-jyutping overrides: the HKCanCor table word_jyutping consults first (real engine)."""

from __future__ import annotations

import importlib.util
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.yue.reading import YueReadingSupport, word_jyutping
from anki_miner.languages.yue.render import YueJyutpingHook
from anki_miner.utils.ja_normalize import is_cjk_ideograph

ROOT = Path(__file__).parents[3]
SCRIPT = ROOT / "scripts" / "build_yue_jyutping_overrides.py"
TABLE = ROOT / "anki_miner" / "languages" / "yue" / "data" / "jyutping_overrides.txt"


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("build_yue_jyutping_overrides", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows() -> list[list[str]]:
    lines = TABLE.read_text(encoding="utf-8").splitlines()
    return [line.split("\t") for line in lines if line and not line.startswith("#")]


@pytest.mark.parametrize(
    ("word", "spoken"),
    [
        ("聽", "teng1"),
        ("返", "faan1"),
        ("咪", "mai6"),
        ("驚", "geng1"),
        ("行", "haang4"),
        ("平", "peng4"),
        ("請", "ceng2"),
        ("間", "gaan1"),
        ("名", "meng2"),
        ("嗰個", "go2 go3"),
        ("好玩", "hou2 waan2"),
        ("唔見", "m4 gin3"),
    ],
)
def test_a_common_word_reads_as_hong_kong_speakers_say_it(word, spoken):
    assert word_jyutping(word) == spoken


@pytest.mark.parametrize(
    ("word", "engine"),
    [
        ("啱", "ngaam1"),  # not the ng-dropped aam1
        ("來", "loi4"),  # not the reduced lai4
        ("出來", "ceot1 loi4"),
        ("嗯", "ng6"),  # an interjection: HKCanCor's m6 is its own transcription
        ("下", "haa6"),  # the engine's bare 下 is also 下個月's
    ],
)
def test_a_reviewed_out_row_keeps_the_engine_reading(word, engine):
    assert word_jyutping(word) == engine


def test_a_word_outside_the_table_still_reads_from_the_engine():
    assert word_jyutping("食") == "sik6"
    assert word_jyutping("銀行") == "ngan4 hong4"  # the override is per word, not per character


def test_the_reading_field_and_the_jyutping_field_both_carry_the_override():
    token = SimpleNamespace(surface="聽", feature=SimpleNamespace(lemma="聽"))
    assert YueReadingSupport().word_reading(token) == "teng1"
    config = replace(AnkiMinerConfig(), reading_tone_color=False)
    assert YueJyutpingHook().render(SimpleNamespace(mined_form="返"), config=config) == {"expression_jyutping": "faan1"}


def test_every_row_is_a_han_word_with_one_syllable_per_character():
    syllable = re.compile(r"[a-z]+[1-6]")
    assert rows()
    for word, jyutping, count in rows():
        assert all(is_cjk_ideograph(char) for char in word), word
        assert len(jyutping.split()) == len(word) and all(syllable.fullmatch(s) for s in jyutping.split()), word
        read_so, total = map(int, count.split("/"))
        assert total >= 10 and read_so >= 0.8 * total, word


def test_the_hand_review_is_applied(build):
    words = {row[0] for row in rows()}
    assert not words & set(build.REVIEWED_OUT)
    assert not words & {"喇", "囖", "喀", "嗯", "嘍"}  # particles and interjections


def test_the_table_is_the_scripts_output(build):
    text, dropped = build.build()
    assert TABLE.read_text(encoding="utf-8") == text
    # Every hand-review entry still names a row the thresholds admit.
    assert {row[0] for row in dropped} == set(build.REVIEWED_OUT)
