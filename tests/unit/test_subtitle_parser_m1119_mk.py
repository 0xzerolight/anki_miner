"""Kana words mined by a list row of their own: spoken spellings, short nouns and joins."""

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


_JOINED = (
    ("なんか変だ", "なんか"),
    ("なんで来たの", "なんで"),
    ("なんでも食べる", "なんでも"),
    ("なんとか間に合った", "なんとか"),
    ("なんだか眠い", "なんだか"),
    ("なんにも見えない", "なんにも"),
    ("そっか、分かった", "そっか"),
    ("どっかで見た", "どっか"),
    ("うちらの番だ", "うちら"),
    ("耳がぴんと立った", "ぴんと"),
    ("まともに見られない", "まともに"),
    ("めったに来ない", "めったに"),
    ("はるかに高い", "はるかに"),
    ("むやみに触るな", "むやみに"),
    ("わずかに動いた", "わずかに"),
    ("たしかに見た", "たしかに"),
    ("にわかに曇った", "にわかに"),
    ("あまりに遠い", "あまりに"),
    ("ほんとに行くの", "ほんとに"),
    ("いっぺんに食べた", "いっぺんに"),
    ("いっしょに帰ろう", "いっしょに"),
    ("べつに構わない", "べつに"),
    ("かってに決めるな", "かってに"),
    ("なんでもなかった", "なんでもない"),
    ("あまりにも遠い", "あまりにも"),
)

_ATTESTED = frozenset(
    {"ったく", "うむ", "おおきに", "てめぇ", "とやかく", "まんま"}
    | {"もん", "ようす", "しっぽ", "きげん", "ぜんぶ"}
    | {front for _text, front in _JOINED}
    | {"おやすみ", "おやすみなさい", "おかえり", "おかえりなさい", "おめでとう", "ごちそうさま", "おつかれさま"}
    | {"ごめんなさい", "ごめん", "いただきます", "かしこまりました", "すまない", "すまん"}
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


class TestWordRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ったく、遅いな", "ったく"),
            ("うむ、分かった", "うむ"),
            ("おおきに", "おおきに"),
            ("てめぇの番だ", "てめぇ"),
            ("とやかく言うな", "とやかく"),
            ("昔のまんまだ", "まんま"),
        ],
    )
    def test_mines_a_spoken_word_jmdict_gives_no_priority_tag(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("立派なもんだ", "もん"),
            ("ようすが変だ", "ようす"),
            ("犬のしっぽ", "しっぽ"),
            ("きげんが悪い", "きげん"),
            ("ぜんぶ食べた", "ぜんぶ"),
        ],
    )
    def test_mines_a_named_short_kana_spelling_of_a_kanji_written_noun(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize("front", ["もん", "ようす", "しっぽ", "きげん", "ぜんぶ"])
    def test_a_short_spelling_row_still_takes_the_window_check(self, front):
        assert is_listed(front, "名詞", merged=False)
        assert is_kanji_spelled(front, "名詞")


class TestJoinRows:
    @pytest.mark.parametrize(("text", "front"), _JOINED)
    def test_joins_a_kana_word_the_tagger_cuts_apart(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "piece"),
        [
            ("なんで来たの", "なん"),
            ("どっかで見た", "どっ"),
            ("まともに見られない", "まとも"),
            ("ほんとに行くの", "ほんと"),
        ],
    )
    def test_the_piece_before_the_join_is_no_card(self, text, piece):
        assert piece not in _fronts(text)

    def test_the_longer_row_wins(self):
        fronts = _fronts("なんでも食べる")
        assert "なんでも" in fronts
        assert "なんで" not in fronts
        assert _fronts("なんでもないよ") == ["なんでもない"]

    @pytest.mark.parametrize("text", ["照れてなんかない", "犬なんか嫌いだ"])
    def test_the_particle_nanka_is_not_joined(self, text):
        assert "なんか" not in _fronts(text)


class TestGreetings:
    @pytest.mark.parametrize(
        ("text", "fronts"),
        [
            ("おやすみ", ["おやすみ"]),
            ("おやすみなさい", ["おやすみなさい"]),
            ("おかえり", ["おかえり"]),
            ("おかえりなさい", ["おかえりなさい"]),
            ("おめでとう", ["おめでとう"]),
            ("ごちそうさまでした", ["ごちそうさま"]),
            ("おつかれさま", ["おつかれさま"]),
            ("ごめんなさい", ["ごめんなさい"]),
            ("いただきます", ["いただきます"]),
            ("かしこまりました", ["かしこまりました"]),
            ("すまない", ["すまない"]),
            ("すまなかった", ["すまない"]),
            ("すまん、遅れた", ["すまん", "遅れる"]),
        ],
    )
    def test_joins_a_greeting_the_tagger_cuts_apart(self, text, fronts):
        assert _fronts(text) == fronts

    def test_the_humble_auxiliary_is_no_greeting(self):
        assert "いただきます" not in _fronts("見ていただきます")
