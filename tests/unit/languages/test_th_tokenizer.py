"""The th tokenizer over the real newmm engine and the real tag model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.th.tokenizer import ZWSP, build_tagger

TOKENS = Path(__file__).resolve().parents[2] / "fixtures" / "th" / "tokens.jsonl"
ROWS = [json.loads(line) for line in TOKENS.read_text(encoding="utf-8").splitlines() if line.strip()]


# Module scope: conftest resets the per-test tagger cache, and rebuilding the
# newmm Trie for every parametrised row costs a second each time.
@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def _tags(tagger, text):
    return [(t.surface, t.feature.pos1, t.feature.pos2) for t in tagger.parse(text)]


def test_compound_stays_one_token(tagger):
    assert [s for s, _, _ in _tags(tagger, "ผมชอบกินข้าวครับ")] == ["ผม", "ชอบ", "กินข้าว", "ครับ"]


def test_surfaces_are_verbatim_slices_of_the_input(tagger):
    line = "วันนี้อากาศดีมากครับ"
    cursor = 0
    for surface, _, _ in _tags(tagger, line):
        found = line.find(surface, cursor)
        assert found >= 0, surface
        cursor = found + len(surface)


def test_zero_width_space_is_dropped_not_tagged(tagger):
    text = "สวัสดี" + ZWSP + "ครับ"
    assert [s for s, _, _ in _tags(tagger, text)] == ["สวัสดี", "ครับ"]


def test_particle_is_the_stopword_tier_however_the_model_tagged_it(tagger):
    tiers = {s: p2 for s, _, p2 in _tags(tagger, "ไม่รู้ค่ะ นะคะ")}
    assert tiers["ค่ะ"] == "stopword"
    # นะคะ is ONE newmm token and the model calls it a NOUN; the cluster is on the list.
    assert tiers["นะคะ"] == "stopword"


def test_maiyamok_and_paiyannoi_are_the_mark_tier(tagger):
    assert ("ๆ", "PUNCT", "mark") in _tags(tagger, "มากๆ")
    assert ("ฯลฯ", "PUNCT", "mark") in _tags(tagger, "ฯลฯ")


def test_trailing_paiyannoi_stays_on_its_headword(tagger):
    assert [s for s, _, _ in _tags(tagger, "กรุงเทพฯ")] == ["กรุงเทพฯ"]


def test_latin_and_digits_never_take_the_models_content_tag(tagger):
    # perceptron/tud tags Netflix VERB; a Latin run is X and a digit run is NUM.
    assert ("Netflix", "X", "") in _tags(tagger, "ดู Netflix กัน")
    assert ("๒๕๖๗", "NUM", "") in _tags(tagger, "๒๕๖๗")
    assert ("12,000", "NUM", "") in _tags(tagger, "12,000 บาท")


def test_a_personal_name_is_left_to_the_pos_gate_not_a_tier(tagger):
    # D2: there is no name tier. The model calls a recognised name PROPN, and
    # PROPN is outside TH_ALLOWED_POS, so it never mines.
    assert ("จรัญ", "PROPN", "") in _tags(tagger, "คุณจรัญไปโรงเรียน")


def test_an_ordinary_word_that_is_also_a_given_name_keeps_its_content_tag(tagger):
    # น้ำ is in the PyThaiNLP name corpora AND in thai_words() (TNC rank 130).
    assert ("น้ำ", "NOUN", "") in _tags(tagger, "เขาดื่มน้ำทุกวัน")


def test_lemma_is_the_surface_and_kana_is_empty(tagger):
    for token in tagger.parse("วันนี้อากาศดีมากครับ"):
        assert token.feature.lemma == token.surface and token.feature.kana == ""


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["surface"])
def test_token_fixture_rows(tagger, row):
    got = {s: (p1, p2) for s, p1, p2 in _tags(tagger, row["sentence"])}
    assert got[row["surface"]] == (row["pos1"], row["pos2"])
