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
        "いい",
        "よい",
        "あんま",
        "ちっと",
        "ちと",
        "なんせ",
        "しばし",
        "ちっとも",
        "やる",
        "いかなる",
        "とある",
        "さらなる",
        "ひょんな",
        "なんたる",
        "かの",
        "わが",
        "ぞっと",
        "はっと",
        "じーっと",
        "そーっと",
        "ホッと",
        "ボーッと",
        "フッ",
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
    | {"いっしょ", "がってん"}
)


def _fronts(text: str, terms: frozenset[str] = frozenset()) -> list[str]:
    """Card fronts of ``text`` with only ``_ATTESTED`` in the dictionary (``terms`` as headwords), so a window can miss."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in terms},
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

    @pytest.mark.parametrize(
        ("text", "front"),
        [("そんなわけないっしょ", "いっしょ"), ("もう いいっしょ", "いっしょ"), ("何を 残念がってんの？", "がってん")],
    )
    def test_a_piece_of_a_contraction_is_no_card(self, text, front):
        # The tagger cuts っしょ (でしょ) after an い-ending word as い|いっしょ and
        # 〜がってる as た|がってん: a listed row would card the piece.
        assert front not in _fronts(text)
        assert not is_listed(front, "名詞", merged=False)

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


class TestToAdverbs:
    @pytest.mark.parametrize(("text", "front"), [("ホッとした", "ホッと"), ("ボーッとしていた", "ボーッと")])
    def test_mines_a_katakana_to_adverb_the_dictionary_has_as_written(self, text, front):
        assert front in _fronts(text, frozenset({"ホッと", "ボーッと"}))

    @pytest.mark.parametrize(("text", "front"), [("ホッとした", "ホッと"), ("フッ", "フッ")])
    def test_a_spelling_the_dictionary_lacks_or_a_bare_sound_stays_out(self, text, front):
        assert front not in _fronts(text, frozenset({"フッ"}))

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ぞっとした", "ぞっと"),
            ("じーっと見る", "じーっと"),
            ("そーっと歩く", "そーっと"),
        ],
    )
    def test_mines_a_short_or_drawn_out_to_adverb_by_its_list_row(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "副詞", merged=False)

    def test_a_short_hiragana_to_adverb_cut_from_a_particle_stays_out(self):
        assert "はっと" not in _fronts("戸締まりはっと")


class TestPreNounRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("いかなる時も諦めない", "いかなる"),
            ("とある町で会った", "とある"),
            ("さらなる努力が要る", "さらなる"),
            ("ひょんなことから知り合った", "ひょんな"),
            ("なんたる失態だ", "なんたる"),
        ],
    )
    def test_mines_a_kana_pre_noun_adjectival(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "連体詞", merged=False)

    @pytest.mark.parametrize(("text", "front"), [("かの有名な画家だ", "かの"), ("わが手にある", "わが")])
    def test_a_two_kana_pre_noun_word_stays_out(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize("text", ["誰かの声がする", "何かの間違いだ"])
    def test_ka_no_after_an_indefinite_is_no_card(self, text):
        assert "かの" not in _fronts(text)


class TestRequestNoun:
    @pytest.mark.parametrize("text", ["水をちょうだい", "お茶ちょうだい", "それ、ちょうだいよ", "すべてちょうだい"])
    def test_mines_choudai_as_the_lines_own_word(self, text):
        assert "ちょうだい" in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        [
            "少し待ってちょうだい",
            "ここで待っててちょうだい",
            "まだ飲んでちょうだい",
            "行かせて…ちょうだい",
            "持ってってちょうだい",
        ],
    )
    def test_choudai_after_te_is_grammar(self, text):
        assert "ちょうだい" not in _fronts(text)


class TestStretchedCry:
    @pytest.mark.parametrize("text", ["やったぁ！", "やったぁー", "わあ、やったぁ"])
    def test_a_stretched_yatta_is_a_cry(self, text):
        assert "やる" not in _fronts(text)

    @pytest.mark.parametrize("text", ["やったことない", "宿題はやったぁ？"])
    def test_the_verb_after_a_word_is_kept(self, text):
        assert "やる" in _fronts(text)


class TestShortAdverbRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("あんま食べない", "あんま"),
            ("ちっとは考えろ", "ちっと"),
            ("なんせ古い家だ", "なんせ"),
            ("しばし待たれよ", "しばし"),
        ],
    )
    def test_mines_a_short_spoken_or_period_adverb(self, text, front):
        assert front in _fronts(text)
        assert is_listed(front, "副詞", merged=False)

    def test_chittomo_is_its_own_word(self):
        fronts = _fronts("ちっとも分からない")
        assert "ちっとも" in fronts
        assert "ちっと" not in fronts


class TestWordsRefusedByPosition:
    @pytest.mark.parametrize("text", ["おらは行くだ", "おらの村だ", "おらたちの村だ"])
    def test_mines_ora_the_pronoun_before_a_particle_or_a_plural(self, text):
        assert "おら" in _fronts(text)

    @pytest.mark.parametrize("text", ["おらよ", "立てよ、おら", "おら、どけ"])
    def test_ora_the_shout_is_no_card(self, text):
        assert "おら" not in _fronts(text)

    def test_mines_yoroshuu_the_adverb(self):
        assert "よろしゅう" in _fronts("よろしゅう頼みます")

    def test_yoroshuu_before_gozaimasu_is_the_adjective(self):
        assert "よろしゅう" not in _fronts("よろしゅうございますか")

    @pytest.mark.parametrize("text", ["ちと尋ねたい", "お城で ちと話がある", "子供にはちと重い"])
    def test_mines_chito_after_a_pause_or_a_particle(self, text):
        assert "ちと" in _fronts(text)

    @pytest.mark.parametrize("text", ["俺っちと来い", "俺んちと隣の家だ"])
    def test_chito_glued_to_the_word_before_it_is_a_piece(self, text):
        assert "ちと" not in _fronts(text)


class TestGoodAfterConditional:
    @pytest.mark.parametrize("text", ["本を読めばいい", "先生に聞いたらいい", "どうすりゃいい", "早く行きゃいいんだ"])
    def test_ii_after_a_conditional_is_the_lines_own_word(self, text):
        assert "いい" in _fronts(text)

    @pytest.mark.parametrize("text", ["食べてもいい", "見ていい", "帰っていいよ"])
    def test_ii_after_te_stays_grammar(self, text):
        assert "いい" not in _fronts(text)
