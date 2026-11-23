"""K1121 lane PHRASE: join rows for the polite negative of the copula, and rows with a context."""

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

# What the fake dictionary lists, as a term or as a reading.
_ATTESTED = frozenset(
    {
        # P-1
        "じゃないか",
        "じゃない",
        "ではない",
        "かもしれない",
    }
)

_BENCH_POS = (*AnkiMinerConfig().allowed_pos, "接続詞", "連体詞", "感動詞")


def _fronts(
    sentence: str, *, allowed: tuple[str, ...] = _BENCH_POS, grammar_points: bool = True, interjections: bool = False
) -> list[str]:
    def lookup(terms: list[str]) -> dict[str, bool]:
        return {term: term in _ATTESTED for term in terms}

    service = SubtitleParserService(
        AnkiMinerConfig(allowed_pos=allowed, mine_grammar_points=grammar_points, mine_interjections=interjections),
        kana_attest_lookup=lookup,
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# --- P-1: the polite negative of the copula row ----------------------------------------------


def test_ja_nai_without_ka_keeps_its_own_card() -> None:
    fronts = _fronts("この煮物、結構うまいじゃねえ")
    assert "じゃない" in fronts
    assert "じゃないか" not in fronts


def test_a_verbs_own_negative_before_ka_is_no_row() -> None:
    fronts = _fronts("今夜は一杯飲まねえか")
    assert "じゃないか" not in fronts
    assert "飲む" in fronts


def test_ja_nai_ka_before_mo_leaves_ka_to_kamoshirenai() -> None:
    fronts = _fronts("あの噂は嘘じゃねえかもしれない")
    assert "じゃないか" not in fronts
    assert "じゃない" in fronts


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("父は医者じゃありません", "じゃない"),
        ("父は医者じゃありませんでした", "じゃない"),
    ],
)
def test_the_polite_negative_of_the_copula_is_the_rows_card(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)
    assert front not in _fronts(sentence, grammar_points=False)
