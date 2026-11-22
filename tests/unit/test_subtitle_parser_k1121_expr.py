"""K1121 lane EXPR: join rows blind to the script, replies that stand alone, expression rows, みたい."""

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

# What the fake dictionary lists, as a term or as a reading. The homophone spans
# a reading match would also fit (時期に, 刷ると, この変) are left out on purpose.
_ATTESTED = frozenset(
    {
        "なんとなく",
        "何となく",
        "なんで",
        "何で",
        "かってに",
        "勝手に",
        "このへん",
        "この辺",
        "そういえば",
        "そう言えば",
        "という",
        "と言う",
        "について",
        "に就いて",
        "おもわぬ",
        "思わぬ",
        "じきに",
        "ところが",
        "所が",
        "すると",
        "わからん",
        "分からん",
        "すまない",
        "済まない",
    }
)

_DEFAULT_POS = AnkiMinerConfig().allowed_pos
_WITH_CONJUNCTIONS = (*_DEFAULT_POS, "接続詞", "連体詞")


def _fronts(sentence: str, *, allowed: tuple[str, ...] = _DEFAULT_POS, grammar_points: bool = True) -> list[str]:
    def lookup(terms: list[str]) -> dict[str, bool]:
        return {term: term in _ATTESTED for term in terms}

    service = SubtitleParserService(
        AnkiMinerConfig(allowed_pos=allowed, mine_grammar_points=grammar_points), kana_attest_lookup=lookup
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# --- E-0: a join row matches a kanji-written token by its reading ---------------------


@pytest.mark.parametrize(
    ("sentence", "front"),
    [
        ("何となくわかる", "何となく"),
        ("なんとなくわかる", "なんとなく"),
        ("何で怒るの", "何で"),
        ("勝手に食べた", "勝手に"),
        ("この辺に住む", "この辺"),
    ],
)
def test_row_joins_the_kanji_spelling_the_dictionary_lists(sentence: str, front: str) -> None:
    assert front in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "kept", "homophone"),
    [
        ("この時期に来た", "時期", "時期に"),  # reads じきに, the row of 直に
        ("この変な店", "変", "この変"),  # reads このへん, the row of この辺
    ],
)
def test_homophone_of_a_row_stays_split(sentence: str, kept: str, homophone: str) -> None:
    fronts = _fronts(sentence, allowed=_WITH_CONJUNCTIONS)
    assert kept in fronts
    assert homophone not in fronts


@pytest.mark.parametrize(
    ("sentence", "kept", "row"),
    [
        ("嘘と言う人もいる", "言う", "と言う"),  # 'people who say', not という 'called'
        ("そう言えばいいのに", "言う", "そう言えば"),  # 'if you say so', not the conjunction
        ("任務に就いていた", "就く", "に就いて"),  # 'was on duty', not について 'about'
        ("そうは思わぬ", "思う", "思わぬ"),  # 'do not think', not おもわぬ 'unexpected'
        ("刷るとすぐ乾く", "刷る", "刷ると"),  # reads すると, the conjunction's row
    ],
)
def test_verb_written_in_kanji_is_the_verb(sentence: str, kept: str, row: str) -> None:
    fronts = _fronts(sentence, allowed=_WITH_CONJUNCTIONS)
    assert kept in fronts
    assert row not in fronts


def test_kanji_verb_before_the_negative_is_the_verb() -> None:
    # すま|ない is the apology すまない; 済ま|ない is 済む negated ('not over yet'), though 済まない is listed too.
    assert _fronts("まだ済まない") == ["済む"]
    assert "すまない" in _fronts("すまないね")


@pytest.mark.parametrize("sentence", ["これが何であるか", "何でございますか"])
def test_de_before_the_copula_is_no_row(sentence: str) -> None:
    assert "何で" not in _fronts(sentence)


def test_kana_row_before_aru_is_unchanged() -> None:
    assert "なんで" in _fronts("なんであるの")


def test_kanji_spelling_of_a_row_obeys_allowed_pos() -> None:
    # The kana list overrides allowed_pos only for kana, which the script gate drops:
    # a kanji-written conjunction is a word like any other, so 所|が joins only where conjunctions are mined.
    assert "ところが" in _fronts("ところが来ない")
    assert "所が" not in _fronts("所が来ない")
