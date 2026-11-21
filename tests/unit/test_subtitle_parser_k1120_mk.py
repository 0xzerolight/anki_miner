"""Kana words the K1120 corpus session brought back: long kana spellings of kanji-written words."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.kana_words import is_kanji_spelled, is_listed
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
    {
        "ぬいぐるみ",
        "なる",
        "からだ",
        "ちから",
        "いのち",
        "おかみ",
        "とどめ",
        "ささい",
        "せがれ",
        "ひいき",
        "ありか",
        "まこと",
        "そなた",
        "おぬし",
        "あっし",
        "おいら",
        "それがし",
        "こやつ",
        "こちとら",
        "おら",
        "わし",
        "まさしく",
        "さぞかし",
        "どのみち",
        "ちゃっかり",
        "やっぱし",
        "とんでも",
        "おうおう",
        "はあはあ",
        "よろしゅう",
        "じゃーん",
        "ないしょ",
        "しきたり",
        "かくれんぼ",
        "ひとたまり",
        "めっそう",
        "ちきしょう",
        "まんじゅう",
        "べっぴん",
    }
    | {"いやいや", "ちょうだい", "ごらん", "せんかい", "もったい", "ひかり", "かけがえ", "こんなん", "じょじょ"}
)


def _fronts(text: str) -> list[str]:
    """Card fronts of ``text`` with only ``_ATTESTED`` in the dictionary, so a window can miss."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: set(),
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestLongKanaSpellings:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("妹のぬいぐるみを洗った", "ぬいぐるみ"),
            ("村のしきたりを守る", "しきたり"),
            ("公園でかくれんぼをした", "かくれんぼ"),
        ],
    )
    def test_mines_a_long_kana_spelling_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ひとたまりもない", "ひとたまり"),
            ("めっそうもない", "めっそう"),
            ("かけがえのない人だ", "かけがえ"),
            ("こんなんじゃ足りない", "こんなん"),
            ("少し待ってちょうだい", "ちょうだい"),
            ("ほら、見てごらん", "ごらん"),
            ("もったいぶるな", "もったい"),
        ],
    )
    def test_a_word_left_out_by_name_stays_out(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ないしょの話を聞いた", "ないしょ"),
            ("ちきしょう、負けた", "ちきしょう"),
            ("まんじゅうを食べた", "まんじゅう"),
            ("べっぴんさんだな", "べっぴん"),
        ],
    )
    def test_mines_a_sino_japanese_kana_spelling_by_its_list_row(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "名詞", merged=False) and is_kanji_spelled(front, "名詞")

    @pytest.mark.parametrize(("text", "front"), [("早うせんかい", "せんかい"), ("じょじょじょ、上手だね", "じょじょ")])
    def test_an_unlisted_sino_japanese_reading_is_no_card(self, text, front):
        assert front not in _fronts(text)

    def test_a_three_kana_noun_stays_by_name(self):
        assert "ひかり" not in _fronts("ひかりが見えた")


class TestLongKanaAdverbs:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("まさしく本物だ", "まさしく"),
            ("さぞかし驚いた", "さぞかし"),
            ("どのみち同じ結果だ", "どのみち"),
            ("ちゃっかり席を取った", "ちゃっかり"),
            ("やっぱし無理だ", "やっぱし"),
        ],
    )
    def test_mines_a_long_kana_adverb_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("とんでもねえ", "とんでも"),
            ("よろしゅうございます", "よろしゅう"),
            ("おうおう、兄ちゃん", "おうおう"),
            ("はあはあ", "はあはあ"),
            ("じゃーん", "じゃーん"),
        ],
    )
    def test_a_piece_or_a_doubled_sound_stays_out(self, text, front):
        assert front not in _fronts(text)


class TestPeriodPronouns:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("そなたの名を聞こう", "そなた"),
            ("おぬしは誰だ", "おぬし"),
            ("あっしに任せてくれ", "あっし"),
            ("おいらの勝ちだ", "おいら"),
            ("それがしが参ります", "それがし"),
            ("こやつを捕らえろ", "こやつ"),
            ("こちとら忙しいんだ", "こちとら"),
        ],
    )
    def test_mines_a_pronoun_of_period_or_rough_speech(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "代名詞", merged=False)

    def test_the_shout_ora_is_no_card(self):
        assert "おら" not in _fronts("おらおら、どけ")


class TestShortKanaNouns:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ちからを貸してくれ", "ちから"),
            ("いのちは大切だ", "いのち"),
            ("旅館のおかみに聞いた", "おかみ"),
            ("とどめの一撃だ", "とどめ"),
            ("ささいな事だ", "ささい"),
            ("うちのせがれです", "せがれ"),
        ],
    )
    def test_mines_a_named_three_kana_spelling_of_an_everyday_noun(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "名詞", merged=False) and is_kanji_spelled(front, "名詞")

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ひかりちゃんが来た", "ひかり"),
            ("えこひいきだ", "ひいき"),
            ("まことに残念だ", "まこと"),
            ("そんなのありかよ", "ありか"),
            ("からだが大きい", "からだ"),
        ],
    )
    def test_a_name_a_piece_or_a_homograph_of_two_words_stays_out(self, text, front):
        assert front not in _fronts(text)


class TestHonorificNaru:
    @pytest.mark.parametrize(
        "text",
        [
            "先生がお尋ねになりました",
            "社長がお帰りになる",
            "先生がお読みになった本です",
            "どうぞお休みになってください",
        ],
    )
    def test_naru_after_an_honorific_stem_is_grammar(self, text):
        assert "なる" not in _fronts(text)

    @pytest.mark.parametrize(
        "text", ["医者になりました", "お金になる仕事", "もうお昼になる", "邪魔になるぞ", "お金持ちになりたい"]
    )
    def test_naru_after_a_noun_is_the_lines_own_word(self, text):
        assert "なる" in _fronts(text)
