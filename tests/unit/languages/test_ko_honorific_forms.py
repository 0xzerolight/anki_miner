"""Honorific -세요 forms kiwi misreads on its own: 계세요, 주무세요, 드세요.

Real-engine test: kiwipiepy is installed for this session and a skip here would
hide a broken tokenizer at the gate. The tagger is built ONCE per module because
tests/conftest.py clears the shared tagger cache per test and the model is 88 MB.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.ko.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit

pytest.importorskip("kiwipiepy")

#: line, the verb the line uses, the unrelated word bare kiwi mined instead.
HONORIFIC_LINES = [
    ("안녕히 계세요.", "계시다", "계"),
    ("아버지는 회사에 계세요.", "계시다", "계"),
    ("사장님 계세요?", "계시다", "계"),
    ("여기 계세요", "계시다", "계세"),
    ("할머니께서는 벌써 주무세요.", "주무시다", "주무"),
    ("안녕히 주무세요.", "주무시다", "주무"),
    ("식사 맛있게 드세요.", "드시다", "드세다"),
    ("하루에 세 번 식후에 드세요.", "드시다", "드세다"),
    ("많이 드세요.", "드시다", "드세다"),
]


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


@pytest.mark.parametrize(("line", "verb", "wrong"), HONORIFIC_LINES)
def test_honorific_seyo_form_tokenizes_as_its_verb(tagger, line, verb, wrong):
    tokens = tagger(line)
    predicates = [t for t in tokens if t.feature.pos1 == "VV"]
    assert [t.feature.lemma for t in predicates] == [verb]
    assert predicates[0].surface in line
    assert wrong not in [t.feature.lemma for t in tokens]


@pytest.mark.parametrize(
    ("line", "verb"),
    [("계셨어요", "계시다"), ("주무시고", "주무시다"), ("드셨어요", "드시다"), ("가세요", "가다")],
)
def test_other_forms_keep_kiwis_own_analysis(tagger, line, verb):
    assert [t.feature.lemma for t in tagger(line) if t.feature.pos1 == "VV"] == [verb]


def test_parser_mines_the_honorific_verb_not_the_unrelated_noun(tagger, monkeypatch):
    # subtitle_parser does `from ...tagger_provider import get_tagger` at module
    # scope, so the patch target is the name it bound.
    monkeypatch.setattr("anki_miner.services.subtitle_parser.get_tagger", lambda language: tagger)
    parser = get_profile("ko").create_parser(switch_language(AnkiMinerConfig(), "ko"))
    for line, verb, wrong in HONORIFIC_LINES:
        words, _index, _counts = parser.parse_text_units(
            [ReadingUnit(text=line, index=0, location_label="fixture")], False
        )
        forms = [w.mined_form for w in words]
        assert verb in forms, line
        assert wrong not in forms, line
