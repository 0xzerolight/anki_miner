"""Arabic normalisation (P3), the key fold, the script gate, sentence rules and the SDH regex (spec C.1)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from anki_miner.languages.ar.script import (
    AR_SENTENCE_RULES,
    AR_SUBTITLE_REGEX,
    ArabicDictKeys,
    ArabicScript,
    ar_fold,
    ar_normalize,
)
from anki_miner.services.reading.sentence_splitter import split_sentences

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "ar"
NORMALIZE = [json.loads(line) for line in (FIXTURES / "normalize.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("row", NORMALIZE, ids=[row["id"] for row in NORMALIZE])
def test_normalize_and_fold_match_the_fixture(row):
    assert ar_normalize(row["text"]) == row["normalized"]
    assert ar_fold(row["text"]) == row["folded"]
    assert ar_fold(ar_fold(row["text"])) == row["folded"]


def test_the_key_fold_is_symmetric_over_tashkeel():
    keys = ArabicDictKeys()
    assert (
        keys.fold_term("\u0643\u0650\u062a\u064e\u0627\u0628\u064c")
        == keys.fold_term("\u0643\u062a\u0627\u0628")
        == "\u0643\u062a\u0627\u0628"
    )  # kitaabun / kitaab
    assert (
        keys.fold_reading("\u0643\u0650\u062a\u064e\u0627\u0628") == "\u0643\u0650\u062a\u064e\u0627\u0628"
    )  # readings keep their tashkeel
    assert keys.fold_term("\u0623\u0646") != keys.fold_term("\u0625\u0646")  # hamza seats are never folded


def test_rule_a_keeps_only_exact_term_rows_and_their_shared_content():
    rows = [("\u0643\u062a\u0627\u0628", "book"), ("\u0643\u062a\u0628", "books"), ("\u0643\u062a\u0628", "book")]
    assert ArabicDictKeys().homograph_keep_mask("\u0643\u062a\u0627\u0628", rows) == [True, False, True]
    assert ArabicDictKeys().homograph_keep_mask("\u0642\u0644\u0645", rows) == [True, True, True]


@pytest.mark.parametrize(
    ("text", "arabic"),
    [
        ("\u0643\u062a\u0627\u0628", True),  # kitaab
        ("\u06a9\u062a\u0627\u0628", True),  # Persian kaf: S15, the first-switch deck checklist's job
        ("123 \u0663\u0664 \u061f \u060c", False),  # digits and Arabic punctuation are not letters
        ("Netflix", False),
    ],
)
def test_the_script_gate_is_has_an_arabic_letter(text, arabic):
    script = ArabicScript()
    assert script.contains_target_script(text) is arabic
    assert script.filter_options() == ()


def test_book_sentences_split_on_the_arabic_question_mark():
    text = "\u0647\u0644 \u0643\u062a\u0628\u062a \u0627\u0644\u0631\u0633\u0627\u0644\u0629\u061f \u0646\u0639\u0645\u060c \u0643\u062a\u0628\u062a\u0647\u0627 \u0623\u0645\u0633."  # hal katabta ar-risaala? na'am, katabtuhaa ams.
    assert split_sentences(text, rules=AR_SENTENCE_RULES) == [
        "\u0647\u0644 \u0643\u062a\u0628\u062a \u0627\u0644\u0631\u0633\u0627\u0644\u0629\u061f",
        "\u0646\u0639\u0645\u060c \u0643\u062a\u0628\u062a\u0647\u0627 \u0623\u0645\u0633.",
    ]


@pytest.mark.parametrize(
    ("cue", "cleaned"),
    [
        (
            "- \u0647\u0644 \u0643\u062a\u0628\u062a \u0627\u0644\u0631\u0633\u0627\u0644\u0629\u061f",
            "\u0647\u0644 \u0643\u062a\u0628\u062a \u0627\u0644\u0631\u0633\u0627\u0644\u0629\u061f",
        ),  # dialogue dash at the cue start
        (
            "\u0646\u0639\u0645\u061f - \u0644\u0627.",
            "\u0646\u0639\u0645\u061f \u0644\u0627.",
        ),  # a dash after the Arabic question mark opens a turn
        (
            "[\u0645\u0648\u0633\u064a\u0642\u0649] \u0645\u0631\u062d\u0628\u0627\u064b",
            " \u0645\u0631\u062d\u0628\u0627\u064b",
        ),  # [muusiiqaa] marhaban
        (
            "(\u064a\u0636\u062d\u0643) \u062d\u0633\u0646\u0627\u064b",
            " \u062d\u0633\u0646\u0627\u064b",
        ),  # (yadhak) hasanan
        ("♪ \u0644\u0627 \u0644\u0627 ♪", " \u0644\u0627 \u0644\u0627 "),
    ],
)
def test_the_sdh_default_strips_labels_music_and_dialogue_dashes(cue, cleaned):
    assert re.sub(AR_SUBTITLE_REGEX, "", cue) == cleaned
