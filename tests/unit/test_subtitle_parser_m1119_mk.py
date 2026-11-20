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
    | {"まぁ", "そりゃ", "そりゃあ", "こりゃ", "ありゃ", "なんぼ", "ちょいと", "どいつ", "もと", "じき"}
    | {"とっさに", "じきに", "もうじき", "そのかわり", "その", "もう"}
    | {"ねえ", "ねぇ", "こら", "よっしゃ", "よーし", "いざ", "おう", "やあ", "おーい", "ほな", "へえ", "へぇ", "さぁ"}
    | {"ありがと", "なんちゃって", "はいはい", "はい"}
    | {"ごちそう", "おしまい", "おわび", "おしゃべり", "おせっかい", "おかわり", "おつかれ", "でかした"}
    | {"このへん", "そのへん", "どのへん", "こっから", "そっから", "どっから", "どんだけ", "そんだけ", "こんだけ"}
    | {"あんだけ", "そんで", "そんなら", "ほんなら", "この"}
    | {
        "どうしようもない",
        "しようがない",
        "しかたがない",
        "しょうがない",
        "もったいない",
        "とんでもない",
        "しかた",
        "どう",
        "ない",
    }
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


class TestSpokenRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("まぁいいか", "まぁ"),
            ("そりゃ無理だ", "そりゃ"),
            ("そりゃあ無理だ", "そりゃあ"),
            ("こりゃ大変だ", "こりゃ"),
            ("ありゃ嘘だ", "ありゃ"),
            ("なんぼ言っても聞かない", "なんぼ"),
            ("ちょいと待ちな", "ちょいと"),
            ("どいつが犯人だ", "どいつ"),
            ("父のもとで働く", "もと"),
            ("じき春が来る", "じき"),
        ],
    )
    def test_mines_a_spoken_kana_word_by_name(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front", "pieces"),
        [
            ("とっさに隠れた", "とっさに", ("とっさ",)),
            ("じきに治る", "じきに", ("じき",)),
            ("もうじき春だ", "もうじき", ("もう", "じき")),
            ("そのかわり手伝って", "そのかわり", ("その", "かわり")),
        ],
    )
    def test_joins_the_longer_word(self, text, front, pieces):
        fronts = _fronts(text)
        assert front in fronts
        assert not set(pieces) & set(fronts)


class TestMoreInterjections:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ねぇ、聞いて", "ねぇ"),
            ("こら、待て", "こら"),
            ("よっしゃ、勝った", "よっしゃ"),
            ("よーし、行くぞ", "よーし"),
            ("いざ、勝負", "いざ"),
            ("おう、任せろ", "おう"),
            ("やあ、元気か", "やあ"),
            ("おーい、こっちだ", "おーい"),
            ("ほな、帰る", "ほな"),
            ("へえ、知らなかった", "へえ"),
            ("さぁ、始めよう", "さぁ"),
            ("ありがと", "ありがと"),
        ],
    )
    def test_mines_an_interjection_named_on_the_list(self, text, front):
        assert front in _fronts(text)

    def test_joins_a_doubled_hai(self):
        assert _fronts("はいはい、分かった")[0] == "はいはい"

    @pytest.mark.parametrize(("text", "sound"), [("えっと、行きます", "えっと"), ("あっ、行く", "あっ")])
    def test_fillers_stay_out(self, text, sound):
        assert sound not in _fronts(text)


class TestPrefixedAndNegativeJoins:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ごちそうになった", "ごちそう"),
            ("もうおしまいだ", "おしまい"),
            ("おわびに来た", "おわび"),
            ("おしゃべりな人", "おしゃべり"),
            ("おせっかいだな", "おせっかい"),
            ("おかわりを頼む", "おかわり"),
            ("おつかれ", "おつかれ"),
            ("でかした", "でかした"),
        ],
    )
    def test_joins_a_word_cut_at_the_polite_prefix(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("どうしようもない", "どうしようもない"),
            ("どうしようもなかった", "どうしようもない"),
            ("しようがないな", "しようがない"),
            ("しかたがなかった", "しかたがない"),
            ("しょうがなかった", "しょうがない"),
            ("もったいなかった", "もったいない"),
            ("とんでもなかった", "とんでもない"),
        ],
    )
    def test_joins_a_negative_adjective_in_every_inflection(self, text, front):
        assert _fronts(text) == [front]

    def test_the_longer_greeting_still_wins(self):
        assert _fronts("ごちそうさまでした") == ["ごちそうさま"]
        assert _fronts("おつかれさま") == ["おつかれさま"]


class TestClippedDemonstratives:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("このへんに住む", "このへん"),
            ("そのへんにある", "そのへん"),
            ("どのへんが痛い", "どのへん"),
            ("こっから行く", "こっから"),
            ("そっから見る", "そっから"),
            ("どっから来た", "どっから"),
            ("どんだけ待つの", "どんだけ"),
            ("そんだけか", "そんだけ"),
            ("こんだけある", "こんだけ"),
            ("あんだけ言った", "あんだけ"),
            ("そんで帰った", "そんで"),
            ("そんなら行く", "そんなら"),
            ("ほんなら行く", "ほんなら"),
        ],
    )
    def test_joins_a_word_cut_after_a_clipped_demonstrative(self, text, front):
        assert _fronts(text)[0] == front

    def test_kono_is_not_left_beside_konohen(self):
        assert "この" not in _fronts("このへんに住む")
