"""Korean tokenizer fixtures on real kiwi output: dictionary form, noisy coda, hanja."""

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.models.reading import ReadingUnit

pytest.importorskip("kiwipiepy")


@pytest.fixture
def ko_parser():
    # switch_language, not replace(language="ko"): the POS gate reads
    # config.allowed_pos, and only the switch swaps in the profile's Sejong
    # scoped_defaults. A bare replace leaves the JA tags and mines nothing.
    return get_profile("ko").create_parser(switch_language(AnkiMinerConfig(), "ko"))


def _words(parser, line: str):
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="fixture")], False)
    return words


def _forms(parser, line: str) -> list[str]:
    return [w.mined_form for w in _words(parser, line)]


def test_verbs_and_adjectives_mine_as_stem_plus_da(ko_parser) -> None:
    forms = _forms(ko_parser, "학생이 밥을 먹었어요. 날씨가 참 좋았다.")
    assert "먹다" in forms
    assert "좋다" in forms
    # Nouns keep their surface; no 다 is ever appended to them.
    assert "학생" in forms
    assert "학생다" not in forms


def test_z_coda_line_keeps_source_findable_surfaces(ko_parser) -> None:
    # kiwi's default z_coda repair rewrites 에성 -> 에서 + Z_CODA. A parser that
    # copied Token.form into .surface would emit a string absent from the line
    # and iter_token_spans would drop the token; 3.3 carries the raw
    # text[start:end] slice instead, which is what this pins.
    line = "우리집에성 먹었어욥"
    words = _words(ko_parser, line)
    assert words, "the noisy line still yields mineable words"
    for word in words:
        assert word.surface in line or word.mined_form.endswith("다")
    assert "먹다" in [w.mined_form for w in words]


def test_hanja_token_is_mined_and_not_split(ko_parser) -> None:
    forms = _forms(ko_parser, "그 韓國 사람은 學生이다.")
    assert "韓國" in forms
    assert "學生" in forms


def test_duck_tokens_carry_no_japanese_only_attributes() -> None:
    tokens = get_tagger("ko")("학생이 공부한다.")
    assert tokens
    # Contract part 5: the duck token exposes surface + feature only; every
    # ja-only attribute is absent by design and probed with getattr defaults.
    for name in ("orthBase", "pron", "cType", "cForm", "kanaBase"):
        assert not hasattr(tokens[0], name)
        assert not hasattr(tokens[0].feature, name)
