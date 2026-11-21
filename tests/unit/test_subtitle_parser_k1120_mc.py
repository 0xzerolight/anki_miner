"""Session K1120, lane MC: windows, joins and expressions on the streaming-caption corpus."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.compound_matcher import CompoundDictionaryMatcher
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# What the dictionary spells this way, and kana that are only the reading of a
# word it writes otherwise (戯け, 邦画, 後で, 田植え, 多聞, ご挨拶).
_TERMS = frozenset({"わけ", "ほう", "あと", "うえ", "もん", "あいさつ", "もんか"})
_READINGS = frozenset({"たわけ", "ほうが", "あとで", "たうえ", "たもん", "ごあいさつ"})


def _words(text: str, terms=_TERMS, readings=_READINGS, config: AnkiMinerConfig | None = None):
    service = SubtitleParserService(
        config or AnkiMinerConfig(),
        term_lookup=lambda forms: {form for form in forms if form in terms},
        kana_attest_lookup=lambda forms: {form: form in terms or form in readings for form in forms},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return words


def _fronts(text: str, terms=_TERMS, readings=_READINGS, config: AnkiMinerConfig | None = None) -> list[str]:
    return [word.mined_form for word in _words(text, terms, readings, config)]


class TestListedSpellingWindow:
    """A listed kana noun is not taken by a homophone that crosses a word boundary."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("行ったわけじゃない", "わけ"),  # た|わけ reads 戯け
            ("こっちのほうがいい", "ほう"),  # ほう|が reads 邦画
            ("あとで帰る", "あと"),  # あと|で is the reading of 後で
            ("考えたうえで決める", "うえ"),  # た|うえ reads 田植え
            ("それは見たもんだ", "もん"),  # た|もん reads 多聞
        ],
    )
    def test_a_reading_only_window_does_not_take_the_noun(self, text, front):
        assert front in _fronts(text)

    def test_a_window_the_dictionary_spells_in_kana_takes_it(self):
        assert "もん" not in _fronts("知るもんか")

    def test_a_window_opened_by_a_prefix_counts_by_reading(self):
        # ご|あいさつ: the tail of ご挨拶, which the dictionary writes in kanji only.
        assert "あいさつ" not in _fronts("ごあいさつに来ました")

    def test_the_noun_alone_is_mined(self):
        assert "あいさつ" in _fronts("あいさつに来ました")


_SPOKEN = frozenset(
    {"やだ", "あかん", "すんません", "すんまへん", "ほっとく", "ほっておく", "ほる", "いや", "かんぱい"}
)


class TestSpokenWordsTheTaggerCuts:
    """や|だ, あか|ん, すん|ませ|ん and ほっ|とく are one listed word each."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("もうやだ", "やだ"),
            ("それはあかんわ", "あかん"),
            ("あかん", "あかん"),  # alone the tagger reads あ|かん
            ("すんません", "すんません"),
            ("すんまへん", "すんまへん"),
            ("ほっといて", "ほっとく"),
            ("ほっとけ", "ほっとく"),
            ("ほっておいて", "ほっておく"),
            ("ほっておけない", "ほっておく"),
        ],
    )
    def test_mines_the_spoken_word(self, text, front):
        fronts = _fronts(text, terms=_SPOKEN, readings=frozenset())
        assert front in fronts
        assert "ほる" not in fronts  # 放る 'dig', the tagger's reading of ほっ

    @pytest.mark.parametrize(
        ("text", "front"), [("いやだ", "いや"), ("あ、かんぱい", "かんぱい"), ("ほったらどうなる", "ほる")]
    )
    def test_the_other_words_stay(self, text, front):
        fronts = _fronts(text, terms=_SPOKEN, readings=frozenset())
        assert front in fronts
        assert not {"やだ", "あかん", "ほっとく"} & set(fronts)


_NEGATIVES = frozenset(
    {
        "ふざけんな",
        "わからん",
        "いらん",
        "かなわない",
        "かまわない",
        "つまらん",
        "つまらぬ",
        "くだらん",
        "くだらぬ",
        "たまらん",
        "わかる",
        "ふざける",
        "かまう",
        "つまる",
        "くだる",
        "たまる",
    }
)


class TestKanaVerbWithItsOwnNegative:
    """A contraction the dictionary lists is the card, not the verb the tagger cut it from."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("ふざけんなよ", "ふざけんな"),
            ("わからん", "わからん"),
            ("そんなものいらんわ", "いらん"),
            ("かなわない", "かなわない"),
            ("かまわないよ", "かまわない"),
            ("つまらん", "つまらん"),
            ("つまらぬ", "つまらぬ"),
            ("くだらん", "くだらん"),
            ("くだらぬ", "くだらぬ"),
            ("たまらん", "たまらん"),
        ],
    )
    def test_mines_the_listed_negative(self, text, front):
        fronts = _fronts(text, terms=_NEGATIVES, readings=frozenset())
        assert front in fronts
        assert not {"つまる", "くだる", "たまる"} & set(fronts)

    @pytest.mark.parametrize(
        ("text", "front"),
        [("わからない", "わかる"), ("わかんなかった", "わかる"), ("ふざけるな", "ふざける"), ("かまわん", "かまう")],
    )
    def test_the_plain_verb_stays(self, text, front):
        assert front in _fronts(text, terms=_NEGATIVES, readings=frozenset())


_OBLIGATION = frozenset(
    {"ならない", "いけない", "いけません", "いかん", "ならん", "なりません", "なる", "いく", "いける"}
)


class TestObligationAfterAConditional:
    """ならない / いけない after a conditional is the unit; the verb chain before it keeps its card."""

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("行かなきゃいけない", "いけない"),
            ("見てはいけない", "いけない"),
            ("行かないといけない", "いけない"),
            ("言っちゃいけなかった", "いけない"),
            ("行ったらいけません", "いけません"),
            ("食べちゃいかん", "いかん"),
            ("行かなければならない", "ならない"),
            ("行かなくちゃならない", "ならない"),
            ("行かねばならん", "ならん"),
            ("触ってはなりません", "なりません"),
        ],
    )
    def test_mines_the_unit_after_the_conditional(self, text, front):
        fronts = _fronts(text, terms=_OBLIGATION, readings=frozenset())
        assert fronts[0] in {"行く", "見る", "言う", "食べる", "触る"}
        assert fronts[1:] == [front]

    @pytest.mark.parametrize("text", ["医者にならない", "それはいけない", "今日はいけない", "見るといけない"])
    def test_no_unit_without_a_conditional(self, text):
        fronts = _fronts(text, terms=_OBLIGATION, readings=frozenset())
        assert not {"ならない", "いけない"} & set(fronts)

    def test_the_unit_starts_after_the_verb_chain(self):
        text = "行かなければならない"
        verb, unit = _words(text, terms=_OBLIGATION, readings=frozenset())
        assert text[verb.surface_start : verb.highlight_end] == "行かなければ"
        assert text[unit.surface_start : unit.highlight_end] == "ならない"

    def test_no_unit_with_grammar_points_off(self):
        config = AnkiMinerConfig(mine_grammar_points=False)
        fronts = _fronts("行かなきゃいけない", terms=_OBLIGATION, readings=frozenset(), config=config)
        assert "いけない" not in fronts


class TestPreNounWordIsNotInflected:
    """そう言う is the pre-noun 'such' (adj-pn): it has no て-form, so そう言って is そう + 言う."""

    @staticmethod
    def _fronts(text: str) -> list[str]:
        terms = {"そう言う", "言う", "そう", "笑う", "人"}
        service = SubtitleParserService(
            AnkiMinerConfig(),
            term_lookup=lambda forms: {form for form in forms if form in terms},
            kana_attest_lookup=lambda forms: {form: form in terms for form in forms},
        )
        matcher = next(value for value in vars(service).values() if isinstance(value, CompoundDictionaryMatcher))
        matcher._entry_tags = lambda heads: {
            head: {"adj-pn", "uk"} if head == "そう言う" else {"v5u"} for head in heads
        }
        words, _index, _counts = service.parse_text_units(
            [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
        )
        return [word.mined_form for word in words]

    @pytest.mark.parametrize("text", ["そう言って笑った", "そう言えばいい"])
    def test_an_inflected_tail_is_the_verb(self, text):
        fronts = self._fronts(text)
        assert "言う" in fronts
        assert "そう言う" not in fronts

    def test_the_base_form_before_a_noun_stays_joined(self):
        assert "そう言う" in self._fronts("そう言う人もいる")


_EXPRESSIONS = frozenset(
    {
        "そういえば",
        "そういや",
        "なんとなく",
        "もしも",
        "どうにか",
        "わがはい",
        "そう",
        "いう",
        "もし",
        "どう",
        "はい",
        "もしもし",
    }
)


class TestKanaExpressionsTheTaggerCuts:
    @pytest.mark.parametrize(
        ("text", "front", "pieces"),
        [
            ("そういえば昨日会った", "そういえば", {"そう", "いう"}),
            ("そういや忘れてた", "そういや", {"そう"}),
            ("なんとなくわかる", "なんとなく", set()),
            ("もしも雨なら", "もしも", {"もし"}),
            ("どうにかする", "どうにか", {"どう"}),
            ("わがはいは猫である", "わがはい", {"はい"}),
        ],
    )
    def test_mines_the_expression(self, text, front, pieces):
        fronts = _fronts(text, terms=_EXPRESSIONS, readings=frozenset())
        assert front in fronts
        assert not pieces & set(fronts)

    def test_the_connective_only_opens_a_clause(self):
        fronts = _fronts("彼がそういえば", terms=_EXPRESSIONS, readings=frozenset())
        assert "そういえば" not in fronts

    def test_a_doubled_word_is_untouched(self):
        assert _fronts("もしもし", terms=_EXPRESSIONS, readings=frozenset()) == ["もしもし"]
