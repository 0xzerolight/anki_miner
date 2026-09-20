"""The Cantonese POS defaults, checked against the recorded tagger output."""

from __future__ import annotations

import json
from pathlib import Path

from anki_miner.languages.yue.pos import YUE_ALLOWED_POS, YUE_EXCLUDED_SUBTYPES, YUE_POS_LABELS

FIXTURE = Path(__file__).parents[2] / "fixtures" / "yue" / "tokens.jsonl"


def tokens():
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if line:
            yield from json.loads(line)["tokens"]


def mined(token) -> bool:
    return token["pos1"] in YUE_ALLOWED_POS and token["pos2"] not in YUE_EXCLUDED_SUBTYPES


def test_every_tag_the_engine_emits_has_a_label():
    assert {t["pos1"] for t in tokens()} <= set(YUE_POS_LABELS)


def test_the_stopword_tier_is_offered_and_off():
    assert YUE_EXCLUDED_SUBTYPES == ()
    assert "stopword" in YUE_POS_LABELS


def test_particles_pronouns_numerals_and_punctuation_never_mine():
    assert not any(mined(t) for t in tokens() if t["pos1"] in ("PART", "PRON", "NUM", "PUNCT", "ADP", "PROPN"))


def test_the_content_words_of_the_smoke_line_do_mine():
    smoke = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])["tokens"]
    assert {t["lemma"] for t in smoke if mined(t)} == {"今日", "套", "好", "好睇", "睇", "戲"}


def test_the_measured_mis_tags_still_pass_the_gate():
    # 緊飯 NOUN and 靚啦 ADJ are glued mis-segmentations the POS gate cannot
    # catch; the dictionary miss is what stops them (spec F.1 risk row).
    assert {t["lemma"] for t in tokens() if mined(t)} >= {"緊飯", "靚啦"}
