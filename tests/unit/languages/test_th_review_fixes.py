"""th mai yamok lookup rungs, and the split pass for newmm compounds no dictionary holds.

Real-engine tests: the pass re-tags through the newmm tokenizer. The tagger is
built ONCE per module because tests/conftest.py clears the shared tagger cache
per test. The dictionary is a set: ``attest`` answers which of its probes it holds.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.th.parser import ThaiDecompoundPass
from anki_miner.languages.th.pos import TH_ALLOWED_POS
from anki_miner.languages.th.support import ThaiLookupStrategy
from anki_miner.languages.th.tokenizer import build_tagger
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.morphology import iter_token_spans


@pytest.mark.parametrize("word", ["จริงๆ", "ช้าๆ", "บ่อยๆ", "ค่อยๆ", "ง่ายๆ", "เก่าๆ"])
def test_a_mai_yamok_token_looks_up_the_spaced_headword_then_its_base(word):
    # newmm emits จริงๆ as one token; wty-th-en spells the headword with the
    # Royal Institute space (จริง ๆ), and its base จริง is a headword too.
    base = word[:-1]
    assert ThaiLookupStrategy().candidates(word, word, None) == [(base + " ๆ", 0), (base, 0)]


def test_a_spaced_query_is_not_spaced_twice():
    assert ThaiLookupStrategy().candidates("จริง ๆ", "", None) == [("จริง", 0)]


def test_a_bare_mai_yamok_has_no_rung():
    assert ThaiLookupStrategy().candidates("ๆ", "", None) == []


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def dictionary(*words: str):
    held = set(words)
    return lambda probes: {probe for probe in probes if probe in held}


def split(tagger, line: str, *words: str):
    return ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(tagger.parse(line), dictionary(*words), None)


def test_a_miss_splits_into_its_two_attested_words(tagger):
    assert [t.surface for t in tagger.parse("ผมขับรถไปทำงาน")][1] == "ขับรถ"

    tokens = split(tagger, "ผมขับรถไปทำงาน", "ผม", "ขับ", "รถ", "ไป", "ทำงาน")

    assert [t.surface for t in tokens] == ["ผม", "ขับ", "รถ", "ไป", "ทำงาน"]
    tags = {t.surface: (t.feature.pos1, t.feature.pos2) for t in tokens}
    assert tags["ขับ"][0] in TH_ALLOWED_POS and tags["รถ"][0] in TH_ALLOWED_POS
    # The re-tag keeps the tiers: ผม is still a stopword.
    assert tags["ผม"] == ("PRON", "stopword")


def test_the_cut_is_at_the_longest_attested_prefix(tagger):
    tokens = split(tagger, "ฉันคิดถึงบ้านมาก", "คิด", "ถึงบ้าน", "คิดถึง", "บ้าน")

    assert [t.surface for t in tokens][1:3] == ["คิดถึง", "บ้าน"]


def test_the_parts_are_verbatim_slices_of_the_line(tagger):
    line = "เขาไปสถานีรถไฟ"
    tokens = split(tagger, line, "สถานี", "รถไฟ")

    assert [line[start:end] for _token, start, end in iter_token_spans(line, tokens)][-2:] == ["สถานี", "รถไฟ"]


def test_the_split_line_is_retagged_in_one_call(tagger):
    calls = []

    def spy(text, **kwargs):
        calls.append(text)
        return tagger(text, **kwargs)

    raw = tagger.parse("ผมขับรถไปทำงาน")
    tokens = ThaiDecompoundPass(spy, TH_ALLOWED_POS)(raw, dictionary("ขับ", "รถ"), None)

    assert len(calls) == 1
    assert [t.surface for t in tokens] == ["ผม", "ขับ", "รถ", "ไป", "ทำงาน"]


def test_nothing_splits_unless_both_parts_are_attested(tagger):
    raw = tagger.parse("ผมขับรถไปทำงาน")
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, dictionary("ขับ"), None) is raw


def test_an_attested_token_stays_whole(tagger):
    raw = tagger.parse("ผมขับรถไปทำงาน")
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, dictionary("ขับรถ", "ขับ", "รถ"), None) is raw


def test_a_token_the_lookup_ladder_defines_stays_whole(tagger):
    # จริงๆ is found as the spaced headword จริง ๆ: it is no miss, so it never splits.
    raw = tagger.parse("ฉันเสียใจจริงๆ")
    assert raw[-1].surface == "จริงๆ"
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, dictionary("จริง ๆ", "จริง", "ๆ"), None) is raw


def test_a_token_outside_the_allowed_pos_is_never_split(tagger):
    raw = tagger.parse("ไม่ต้องห่วง")
    assert (raw[0].surface, raw[0].feature.pos1) == ("ไม่ต้อง", "AUX")
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, dictionary("ไม่", "ต้อง", "ห่วง"), None) is raw


def test_a_proper_noun_is_never_split_even_when_ticked(tagger):
    raw = tagger.parse("ไปกรุงเทพมหานคร")
    assert (raw[1].surface, raw[1].feature.pos1) == ("กรุงเทพมหานคร", "PROPN")
    pos = (*TH_ALLOWED_POS, "PROPN")
    assert ThaiDecompoundPass(tagger, pos)(raw, dictionary("กรุงเทพ", "มหานคร"), None) is raw


def test_a_part_must_be_a_word_of_the_segmenter_not_a_letter(tagger):
    # wty-th-en files every letter and vowel sign as a headword; ขับ + ร + ถ is no cut.
    raw = tagger.parse("ผมขับรถไปทำงาน")
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, dictionary("ขับร", "ถ", "ข", "ับรถ"), None) is raw


def test_a_tiered_token_is_never_split(tagger):
    raw = tagger.parse("ไม่เป็นไรหรือเปล่า")
    assert (raw[-1].surface, raw[-1].feature.pos2) == ("หรือเปล่า", "stopword")
    pos = (*TH_ALLOWED_POS, "PROPN")
    assert ThaiDecompoundPass(tagger, pos)(raw, dictionary("ไม่เป็นไร", "หรือ", "เปล่า"), None) is raw


def test_without_a_dictionary_the_pass_is_inert(tagger):
    raw = tagger.parse("ผมขับรถไปทำงาน")
    assert ThaiDecompoundPass(tagger, TH_ALLOWED_POS)(raw, None, None) is raw


def test_the_parser_mines_the_words_inside_a_compound_miss():
    profile = get_profile("th")
    config = switch_language(AnkiMinerConfig(), "th")
    parser = profile.create_parser(config, term_lookup=dictionary("ผม", "ขับ", "รถ", "ไป", "ทำงาน"))

    units = [ReadingUnit(text="ผมขับรถไปทำงาน", index=0, location_label="t")]
    words, _index, _counts = parser.parse_text_units(units, False)

    assert {"ขับ", "รถ"} <= {word.mined_form for word in words}
    assert "ขับรถ" not in {word.mined_form for word in words}
