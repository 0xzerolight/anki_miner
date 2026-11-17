"""Kana words the parser used to turn away by tag alone, mined again where the line uses them as words."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _service(attested) -> SubtitleParserService:
    wanted = set(attested)
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: set(),
        kana_attest_lookup=lambda forms: {form: form in wanted for form in forms},
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


_EXPRESSIONS = ("という", "として", "について", "ことがある", "かもしれない", "ような", "ように", "どのように")


class TestGrammarExpressions:
    @pytest.mark.parametrize(
        ("text", "front", "pieces"),
        [
            ("猫という動物", "という", set()),
            ("先生として働く", "として", set()),
            ("会ったことがあった", "ことがある", {"こと"}),
            ("雨かもしれません", "かもしれない", set()),
            ("夢のような話", "ような", set()),
        ],
    )
    def test_mines_the_expression_whole(self, text, front, pieces):
        fronts = _fronts(_service(_EXPRESSIONS), text)
        assert front in fronts
        assert not pieces & set(fronts)

    def test_a_joined_expression_is_not_lost_to_its_own_inflection(self):
        # かもしれません is an expression too: a join is a listed word, not judged by its window.
        fronts = _fronts(_service((*_EXPRESSIONS, "かもしれません")), "雨かもしれません")
        assert "かもしれない" in fronts

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ダメだというだろ", "という"),  # a quote and the verb 言う
            ("起こそうとしていない", "として"),  # trying to: volitional + とする
            ("あんなことがあって", "ことがある"),  # a thing happened, not 'have done'
        ],
    )
    def test_the_same_tokens_after_another_word_stay_apart(self, text, front):
        assert front not in _fronts(_service(_EXPRESSIONS), text)

    def test_a_longer_expression_keeps_its_question_word(self):
        fronts = _fronts(_service(_EXPRESSIONS), "どのように作る")
        assert "どのように" in fronts
        assert "ように" not in fronts
