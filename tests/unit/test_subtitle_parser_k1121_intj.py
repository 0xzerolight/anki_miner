"""Session K1121 INTJ: the interjection switch, and predicate frames the auxiliary test refused."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.kana_words import kana_words
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# Kana the dictionary has as a headword or a reading.
_ATTESTED = frozenset({"いえ", "うん", "ねえさん", "ありがとう", "そんな", "ああいう", "あの", "いただく", "ある"})

# Dictionary entry tags by headword: a word with no row here has no entry at all.
_TAGS = {
    "あっ": "int",
    "えっと": "int",
    "ねえ": "int",
    "あはは": "int",
    "ははは": "int",
    "あ": "int",
    "いえ": "int",
    "まあ": "adv int",
    "うー": "int",
    "えへん": "int",
    "まー": "int",
    "鍵": "n",
    "あのね": "int",
    "しまった": "int",
    "んん": "int",
}


def _service(*, interjections: bool) -> SubtitleParserService:
    """A parser with ``_ATTESTED`` as the kana dictionary and ``_TAGS`` as entry tags; the switch set as the kit sets it."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in _TAGS},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED or form in _TAGS for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )
    if interjections:
        service._mine_interjections = True
        assert service._lexical_joiner is not None
        service._lexical_joiner._interjections = True
    return service


def _fronts(text: str, *, interjections: bool = True) -> list[str]:
    words, _index, _counts = _service(interjections=interjections).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


_SWITCHED = [
    ("あっ、鍵を忘れた。", "あっ"),
    ("えっと、鍵はどこだっけ。", "えっと"),
    ("ねえ、聞いて。", "ねえ"),
    ("あはは、面白いね。", "あはは"),
    ("ははは、面白いね。", "ははは"),
    ("あ 雨だ。", "あ"),
    ("いえ、結構です。", "いえ"),
]


class TestInterjectionSwitch:
    def test_the_switch_is_off_by_default(self):
        assert SubtitleParserService(AnkiMinerConfig())._mine_interjections is False

    @pytest.mark.parametrize(("text", "front"), _SWITCHED)
    def test_a_listed_interjection_between_pauses_is_mined_with_the_switch_on(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(("text", "front"), _SWITCHED)
    def test_nothing_is_mined_with_the_switch_off(self, text, front):
        assert front not in _fronts(text, interjections=False)

    @pytest.mark.parametrize(("text", "front"), _SWITCHED)
    def test_count_equals_mine(self, text, front):
        words, _index, counts = _service(interjections=True).parse_text_units(
            [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
        )
        assert sum(counts.values()) == len(words)

    @pytest.mark.parametrize("text", ["あ、ありがとう。", "ありがとう、あ、あっ。"])
    def test_a_stammer_is_no_interjection(self, text):
        assert "あ" not in _fronts(text)

    def test_a_cry_with_no_interjection_entry_stays_out(self):
        assert "わあ" not in _fronts("わあ！鍵を忘れた。")

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            # Glued to a word on either side: a piece of that word, a name or a stretch.
            ("そんなのありえへん。", "えへん"),
            ("いただきまーす。", "まー"),
            ("あのー うーちゃんは鍵を忘れた。", "うー"),
            ("まあさんは鍵を忘れた。", "まあ"),
            ("昨日 ねえさんに会った。", "ねえ"),
        ],
    )
    def test_a_token_glued_to_a_word_is_no_interjection(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize("text", ["見てねえで手伝え。", "そんなの知らねえよ。", "ねえ、聞いて。"])
    def test_one_card_at_most_on_one_nee(self, text):
        assert _fronts(text).count("ねえ") <= 1

    @pytest.mark.parametrize("text", ["ああいう人だ。", "あの店に行く。"])
    def test_a_pre_noun_word_is_unchanged(self, text):
        assert _fronts(text) == _fronts(text, interjections=False)


_ROWS = [
    ("あのね、聞いて。", "あのね"),
    ("しまった！鍵を忘れた。", "しまった"),
    ("んん、違う。", "んん"),
]


class TestInterjectionRows:
    def test_the_rows_are_marked_in_the_list(self):
        joins = kana_words().joins
        assert (
            joins["あの", "ね"].interjection and joins["しまっ", "た"].interjection and joins["ん", "ん"].interjection
        )
        assert not any(row.interjection and row.grammar for row in joins.values())

    @pytest.mark.parametrize(("text", "front"), _ROWS)
    def test_an_interjection_row_is_joined_with_the_switch_on(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(("text", "front"), _ROWS)
    def test_an_interjection_row_is_not_joined_with_the_switch_off(self, text, front):
        assert front not in _fronts(text, interjections=False)

    def test_the_verb_put_away_is_no_interjection(self):
        assert "しまった" not in _fronts("箱にしまった。")

    def test_a_pre_noun_ano_before_a_noun_is_no_row(self):
        assert "あのね" not in _fronts("あの猫はどこ？")
