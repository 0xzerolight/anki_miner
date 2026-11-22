"""Kana words session K1121 brought back: する as the line's own verb, the auxiliary-capable verb class."""

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
    {"する", "びっくり", "ゆっくり", "ごろごろ", "どう", "せい", "くせ", "きれい"}
    | {"あげる", "すぎる", "かける", "みせる", "つける", "つく", "なれる", "おれる", "いける", "もらえる", "つづく"}
    | {"いう", "かわいそう", "やる", "とる", "すき", "かかわる", "ない"}
    | {"あり", "ある", "だす", "きる", "きれる", "つくす"}
    | {"とき", "きのう", "しだい", "もよう", "われ", "たとき", "ときどき", "その"}
    # Windows a dictionary has as the reading of a word written otherwise.
    | {"という", "といい", "かわいそうに", "ですき", "とって", "にもかかわらず", "かかわらず"}
    | {"ぼける", "とぼけ", "とぼける"}
)

# Dictionary entry tags by headword: a word with no row here has no entry at all.
_TAGS = {
    "勉強": "n vs vt",
    "びっくり": "adv vs",
    "宿題": "n",
    "手術": "n vs vt",
    "どう": "adv",
    "せい": "n",
    "何": "pn",
    "掃除": "n vs vt",
    "毎日": "n adv",
    "さっき": "n adv",
}


def _fronts(text: str, terms: frozenset[str] = frozenset()) -> list[str]:
    """Card fronts of ``text`` with ``_ATTESTED`` as the kana dictionary, ``terms`` as headwords and ``_TAGS`` as entry tags."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in terms},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestSuruAsTheLinesOwnVerb:
    @pytest.mark.parametrize(
        "text",
        [
            "宿題をする。",
            "どうするの？",
            "何してるの？",
            "手術までするとは。",
            "雨のせいにするな。",
            "毎日してるよ。",
            "さっきしたばかりだ。",
        ],
    )
    def test_mines_suru_where_it_is_the_main_verb(self, text):
        assert "する" in _fronts(text)

    @pytest.mark.parametrize("text", ["勉強する。", "びっくりした。", "手術した。", "三回した。"])
    def test_suru_after_a_noun_that_takes_it_is_that_nouns_verb(self, text):
        assert "する" not in _fronts(text)

    def test_suru_after_a_noun_with_no_entry_is_no_card(self):
        assert "する" not in _fronts("合体ごろごろする。")

    @pytest.mark.parametrize(
        "text", ["行こうとした。", "見たりした。", "掃除とかしなくていいよ。", "行くかしないか決めて。"]
    )
    def test_suru_after_a_quoting_or_listing_particle_is_grammar(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize("text", ["彼にしては上出来だ。", "子供にしても重い。"])
    def test_ni_shite_wa_is_grammar(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize("text", ["きれいにしておいて。", "早くしろ。", "食べてしまった。"])
    def test_what_the_auxiliary_check_refuses_stays_refused(self, text):
        assert "する" not in _fronts(text)

    def test_the_particle_shi_after_a_contracted_adjective_is_no_suru(self):
        # The tagger cuts くせぇ as くせ|ぇ and reads the し after it as する.
        assert "する" not in _fronts("この部屋くせぇし、もう帰ろう。")

    @pytest.mark.parametrize(
        "text",
        ["よぉし 始めるか。", "ふ・し・ぎな話だ。", "それをさ 見せてよ。", "ウレしい知らせだ。", "どっせい！"],
    )
    def test_a_stray_kana_the_tagger_reads_as_suru_is_no_card(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        [
            "約束をし 家に帰った。",
            "準備をし、出発した。",
            "いいかげんにせい！",
            "どれにしょうかな。",
            "何をしに来たの？",
        ],
    )
    def test_suru_in_its_own_frame_is_mined(self, text):
        assert "する" in _fronts(text)


class TestAuxiliaryCapableVerbClass:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("本をあげる。", "あげる"),
            ("時間がすぎるのは早い。", "すぎる"),
            ("お手数をおかけします。", "かける"),
            ("写真をみせる。", "みせる"),
            ("ドアにかぎをかけた。", "かける"),
            ("かたをつけてやる。", "つける"),
            ("ちゃんとみせて。", "みせる"),
        ],
    )
    def test_mines_an_auxiliary_capable_kana_verb_as_the_main_verb(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("読んであげる。", "あげる"),
            ("食べすぎた。", "すぎる"),
            ("若すぎるのう。", "すぎる"),
            ("雨がふりつづく。", "つづく"),
        ],
    )
    def test_after_a_verb_or_an_adjective_it_is_grammar(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize("text", ["バカすぎる。", "静かすぎる。", "突然すぎて驚いた。"])
    def test_sugiru_glued_to_the_word_before_it_is_that_words_tail(self, text):
        assert "すぎる" not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("騎士になれる。", "なれる"),
            ("家におれんようになった。", "おれる"),
            ("それはいけない。", "いける"),
            ("それ、もらえる？", "もらえる"),
        ],
    )
    def test_the_potential_of_a_listed_or_denied_word_is_no_card(self, text, front):
        assert front not in _fronts(text)

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("命あらば助けよう。", "あり"),
            ("ええ人だすなあ。", "だす"),
            ("きっちゃんが来た。", "きる"),
            ("つくしちゃんに会った。", "つくす"),
        ],
    )
    def test_a_denied_verb_is_no_card(self, text, front):
        assert front not in _fronts(text)

    def test_a_bare_stem_before_a_shout_is_no_card(self):
        assert "つける" not in _fronts("ウソつけ！")

    def test_a_voiced_tail_is_no_card(self):
        assert "つける" not in _fronts("元気づける言葉だ。")

    def test_suru_after_a_class_verb_stays_grammar(self):
        assert "する" not in _fronts("お手数をおかけします。")


class TestKanaWindowChoice:
    def test_iu_after_the_quoting_to_is_its_own_word(self):
        # という is only the reading of と言う, the word itself with one particle.
        assert "いう" in _fronts("行かないという人がいます。", frozenset({"と言う"}))

    def test_a_kana_adjectival_noun_with_its_particle_is_its_own_word(self):
        assert "かわいそう" in _fronts("かわいそうに思った。", frozenset({"可哀想に"}))

    def test_a_reading_that_is_no_headword_in_the_words_own_kanji_still_wins(self):
        assert "いう" not in _fronts("行かないという人がいます。")
        assert "すき" not in _fronts("本気ですきだと言った。", frozenset({"出好き"}))

    @pytest.mark.parametrize(
        ("text", "front", "terms"),
        [
            ("彼は田中といいます。", "いう", {"といい", "と言う"}),
            ("弟にやってもらう。", "やる", {"にやっ"}),
            ("変な音がした。", "する", {"がし"}),
        ],
    )
    def test_a_window_that_ends_inside_the_words_own_inflection_is_no_window(self, text, front, terms):
        assert front in _fronts(text, frozenset(terms))

    def test_a_cut_whose_dictionary_form_is_a_longer_verb_still_wins(self):
        # と|ぼけ|ない: とぼけ is a reading, and とぼける the verb the tagger split.
        assert "ぼける" not in _fronts("とぼけないでよ。")

    def test_an_expression_that_swallows_two_words_still_wins(self):
        assert "かかわる" not in _fronts("雨にもかかわらず出かけた。", frozenset({"にも関わらず"}))

    def test_the_words_own_te_form_homophone_still_wins(self):
        assert "とる" not in _fronts("彼にとって大事だ。", frozenset({"取って"}))

    @pytest.mark.parametrize(("text", "window"), [("行くしかない。", "しかない"), ("そうやないですよ。", "やない")])
    def test_a_window_spelled_in_kana_still_wins(self, text, window):
        assert "ない" not in _fronts(text, frozenset({window}))


class TestShortKanaNounRows:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("そのときは笑った。", "とき"),
            ("雨がやんだときに出かけた。", "とき"),
            ("きのうは使えた。", "きのう"),
            ("捜査を開始したもようです。", "もよう"),
            ("天気しだいで決める。", "しだい"),
            ("われは王なり。", "われ"),
        ],
    )
    def test_mines_a_short_kana_noun_by_its_list_row(self, text, front):
        assert front in _fronts(text)

    @pytest.mark.parametrize(
        ("form", "pos1"),
        [("とき", "名詞"), ("きのう", "名詞"), ("しだい", "名詞"), ("もよう", "名詞"), ("われ", "代名詞")],
    )
    def test_the_rows_are_kanji_spellings_so_they_take_the_window_check(self, form, pos1):
        assert is_listed(form, pos1, merged=False) and is_kanji_spelled(form, pos1)

    def test_toki_after_a_verbs_ta_is_not_hidden_by_a_window_on_that_ta(self):
        # たとき is a kana headword in some dictionaries; the た is the verb's own tail.
        assert "とき" in _fronts("決めたときのことを話す。", frozenset({"たとき"}))

    @pytest.mark.parametrize("text", ["もう少し待っときや。", "それはやめときな。"])
    def test_toki_glued_to_a_verb_stem_is_the_contracted_te_oki(self, text):
        assert "とき" not in _fronts(text)

    def test_toki_inside_a_longer_listed_word_is_no_second_card(self):
        fronts = _fronts("ときどき来る。")
        assert "とき" not in fronts and "ときどき" in fronts
