"""K1121 lane JOIN: the dictionary matcher's gates (the kana gate for entries of content words)."""

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

# The fake dictionary: headword -> (entry tags, readings). As in JMdict, an entry
# that is usually written in kana is still headed by its kanji spelling.
_ENTRIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "旨く行く": ("exp v5k-s", ("うまくいく",)),
    "仰る通り": ("exp", ("おっしゃるとおり",)),
    "あの頃": ("exp", ("あのころ",)),
    "そんな物": ("exp n", ("そんなもの",)),
    "ある時": ("exp adv", ("あるとき",)),
    "事はない": ("exp adj-i", ("ことはない",)),
    "物がある": ("exp v5r-i", ("ものがある",)),
    "訳ない": ("adj-i", ("わけない",)),
    "どうでも良い": ("exp adj-ix", ("どうでもいい", "どうでもよい")),
    "やって見る": ("exp v1", ("やってみる",)),
    "見た所": ("exp", ("みたところ",)),
    "美味しそう": ("adj-na", ("おいしそう",)),
    "間もなく": ("adv", ("まもなく",)),
    "間もない": ("adj-i", ("まもない",)),
    "筈がない": ("exp adj-i", ("はずがない",)),
    "方がいい": ("exp adj-ix", ("ほうがいい",)),
    "ろくでもない": ("exp adj-i", ("ろくでもない",)),
    "風邪をひく": ("exp v5k", ("かぜをひく",)),
    "無駄にする": ("exp vs-i", ("むだにする",)),
    # Entries that must stay unjoined.
    "そうする": ("exp vs-i", ("そうする",)),
    "どうかする": ("vs-i", ("どうかする",)),
    "そうにない": ("exp adj-i", ("そうにない",)),
    "してもいい": ("exp", ("してもいい",)),
    "もういい": ("int", ("もういい",)),
    "無くなる": ("v5r vi", ("なくなる",)),
    "それで良い": ("exp adj-ix", ("それでいい",)),
    "あんたら": ("pn", ("あんたら",)),
    "すぐそこ": ("adv adj-no", ("すぐそこ",)),
    "お浚い": ("n vs", ("おさらい",)),
}

# What the kana probe (term or reading) answers yes to.
_KANA = frozenset(reading for _tags, readings in _ENTRIES.values() for reading in readings) | {
    "うまい",
    "いく",
    "こと",
    "ない",
    "いい",
    "ころ",
    "とおり",
    "おっしゃる",
    "どう",
    "そう",
    "もう",
    "はず",
    "ほう",
    "すぐ",
    "そこ",
    "なる",
}


def _fronts(sentence: str, *, readings: bool = True) -> list[str]:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in _ENTRIES},
        kana_attest_lookup=lambda forms: {form: form in _KANA or form in _ENTRIES for form in forms},
        form_lookup=lambda asked: {term: [("", _ENTRIES[term][0])] for term in asked if term in _ENTRIES},
        reading_lookup=(
            (lambda asked: {term: list(_ENTRIES[term][1]) for term in asked if term in _ENTRIES}) if readings else None
        ),
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


# --- J-1: an attested all-kana span of content words passes the kana gate -------------

_KANA_ENTRIES = [
    ("計画はうまくいかなかった。", "うまくいく", ("うまい", "いく")),
    ("勝ち負けはどうでもいいよ。", "どうでもいい", ("どう", "いい")),
    ("部長のおっしゃるとおりに直します。", "おっしゃるとおり", ("おっしゃる", "とおり")),
    ("そんなわけないだろう。", "わけない", ("わけ", "ない")),
    ("もうどうでもよくなった。", "どうでもよい", ("どう", "よい", "よくなる")),
    ("彼が負けるはずがない。", "はずがない", ("はず", "ない")),
    ("ろくでもない男だった。", "ろくでもない", ("ろく", "ない")),
]


@pytest.mark.parametrize(("sentence", "entry", "pieces"), _KANA_ENTRIES)
def test_a_kana_entry_of_content_words_is_one_card(sentence: str, entry: str, pieces: tuple[str, ...]) -> None:
    fronts = _fronts(sentence)
    assert entry in fronts
    assert not set(pieces) & set(fronts)


@pytest.mark.parametrize(
    ("sentence", "entry"),
    [
        ("昨日カゼをひいた。", "風邪をひく"),
        ("それをムダにするな。", "無駄にする"),
    ],
)
def test_a_katakana_noun_idiom_joins_under_its_plain_spelling(sentence: str, entry: str) -> None:
    assert entry in _fronts(sentence)


@pytest.mark.parametrize(
    ("sentence", "entry"),
    [
        # A pro-form + a light verb is the line's own predicate.
        ("そうして暮らしてきた。", "そうする"),
        ("どうかしたの。", "どうかする"),
        # The auxiliary そう is no content word.
        ("間に合いそうにない。", "そうにない"),
        # する and what follows it is grammar.
        ("こうしてもいいですか。", "してもいい"),
        # An interjection entry is the interjection switch's.
        ("もういいよ。", "もういい"),
        # After a predicate, なく|なる is the negative and なる.
        ("楽しくなくなった。", "なくなる"),
        # それで has a row: what starts with a row's tokens is the row's.
        ("それでいいと思う。", "それでいい"),
        # A pre-noun demonstrative and its noun are two words.
        ("あのころの写真を見つけた。", "あのころ"),
        ("そんなものはいらない。", "そんなもの"),
        # A light verb at the head is the predicate of what stands before it.
        ("時間があるときに来て。", "あるとき"),
        # こと and もの belong to the clause before them.
        ("君が謝ることはないよ。", "ことはない"),
        ("大事なものがあるんだ。", "ものがある"),
        # After a verb's て the tail is the auxiliary any verb takes, and a te-auxiliary starts no entry.
        ("一度やってみたい。", "やってみる"),
        ("調べてみたところ違った。", "みたところ"),
        # The auxiliary そう is no content word at the tail either.
        ("とてもおいしそうだ。", "おいしそう"),
        # A pronoun or an adverb of two kana tokens is no entry card.
        ("あんたらには関係ない。", "あんたら"),
        ("すぐそこにある。", "すぐそこ"),
        # A する noun is no verb class.
        ("明日おさらいをする。", "おさらい"),
        # 方がいい reads ほうがいい: かた|が|いい is not that entry.
        ("あのかたがいい。", "かたがいい"),
    ],
)
def test_what_the_kana_gate_still_refuses(sentence: str, entry: str) -> None:
    fronts = _fronts(sentence)
    assert entry not in fronts
    assert not any(front in _ENTRIES for front in fronts)


def test_a_twin_needs_the_dictionary_reading() -> None:
    assert "うまくいく" not in _fronts("計画はうまくいかなかった。", readings=False)


def test_the_bold_runs_through_the_inflected_tail() -> None:
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in _ENTRIES},
        kana_attest_lookup=lambda forms: {form: form in _KANA or form in _ENTRIES for form in forms},
        form_lookup=lambda asked: {term: [("", _ENTRIES[term][0])] for term in asked if term in _ENTRIES},
        reading_lookup=lambda asked: {term: list(_ENTRIES[term][1]) for term in asked if term in _ENTRIES},
    )
    sentence = "計画はうまくいかなかった。"
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=sentence, index=0, location_label="t")], want_line_index=False
    )
    word = next(word for word in words if word.mined_form == "うまくいく")
    assert sentence[word.surface_start : word.highlight_end] == "うまくいかなかった"


def test_an_adverb_listed_in_the_ku_form_is_that_adverb() -> None:
    fronts = _fronts("まもなく電車が来る。")
    assert "まもなく" in fronts
    assert "まもない" not in fronts


def test_a_grammar_row_still_follows_a_kana_entry() -> None:
    service = SubtitleParserService(
        AnkiMinerConfig(mine_grammar_points=True),
        term_lookup=lambda asked: {term for term in asked if term in _ENTRIES},
        kana_attest_lookup=lambda forms: {form: form in _KANA or form in _ENTRIES or form == "んだ" for form in forms},
        form_lookup=lambda asked: {term: [("", _ENTRIES[term][0])] for term in asked if term in _ENTRIES},
        reading_lookup=lambda asked: {term: list(_ENTRIES[term][1]) for term in asked if term in _ENTRIES},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text="勝ち負けはどうでもいいんだよ。", index=0, location_label="t")], want_line_index=False
    )
    assert {"どうでもいい", "んだ"} <= {word.mined_form for word in words}
