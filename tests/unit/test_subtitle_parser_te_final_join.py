"""An auxiliary after a joined word ending in the て of する/やる (どうして, として, どうやって)."""

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

_ATTESTED = frozenset(
    {"どうして", "として", "どうやって", "について", "いる", "いい", "くれる", "ほしい", "できる", "なる"}
)


def _fronts(text: str) -> list[str]:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: _ATTESTED.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


@pytest.mark.parametrize(
    ("line", "aux"),
    [
        ("あの人は今どうしているの", "いる"),
        ("どうしていいかわからない", "いい"),
        ("どうしてくれるのよ", "くれる"),
        ("どうしてほしいんだ", "ほしい"),
        ("それはどうやっているの", "いる"),
        ("医者としていた頃", "いる"),
    ],
)
def test_auxiliary_after_a_te_final_join_is_not_mined(line: str, aux: str) -> None:
    assert aux not in _fronts(line)


@pytest.mark.parametrize(
    ("line", "word"),
    [
        ("どうしてできないの", "できる"),
        ("どうしてこうなったの", "なる"),
        ("人としてできること", "できる"),
        ("これについていい案がある", "いい"),
    ],
)
def test_main_word_after_a_te_final_join_is_mined(line: str, word: str) -> None:
    assert word in _fronts(line)
