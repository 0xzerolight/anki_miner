"""split_sentences takes its character policy from SentenceRules."""

import pytest

from anki_miner.languages.ar.script import AR_SENTENCE_RULES
from anki_miner.languages.fa.script import FA_SENTENCE_RULES
from anki_miner.languages.he.script import HE_SENTENCE_RULES
from anki_miner.languages.profile import SentenceRules
from anki_miner.languages.registry import get_profile
from anki_miner.services.reading.sentence_splitter import split_sentences

KO_RULES = SentenceRules(
    terminators=frozenset("。｡！？!?‼⁉⁇⁈."),
    ellipses=frozenset("…‥"),
    openers=frozenset("「｢『（〔［｛〈《【([{｟〝"),
    closers=frozenset("」｣』）〕］｝〉》】)]}｠〟"),
    space_aware=True,
)


def test_korean_rules_split_on_the_ascii_period():
    assert split_sentences("안녕하세요. 반갑습니다.", rules=KO_RULES) == [
        "안녕하세요.",
        "반갑습니다.",
    ]


def test_japanese_rules_leave_the_ascii_period_alone():
    assert split_sentences("안녕하세요. 반갑습니다.") == ["안녕하세요. 반갑습니다."]


def test_space_aware_keeps_a_decimal_intact():
    assert split_sentences("값은 3.14 입니다.", rules=KO_RULES) == ["값은 3.14 입니다."]


def test_korean_rules_still_gate_on_brackets():
    assert split_sentences("그는 「안녕. 반가워」라고 했다.", rules=KO_RULES) == ["그는 「안녕. 반가워」라고 했다."]


def test_default_path_is_unchanged():
    assert split_sentences("猫だ。犬だ。") == ["猫だ。", "犬だ。"]
    assert split_sentences("えっ……。そう。") == ["えっ……。", "そう。"]


@pytest.mark.parametrize(
    ("code", "text"),
    [
        ("hu", "2003. szeptember 1-jén Anna elkezdte az egyetemet."),
        ("hu", "Nyáron egy kávézóban dolgozott a VIII. kerületben."),
        ("hu", "– Mi szeretnél lenni? – kérdezte egyszer az apja."),
        ("hr", "Rođen je 12. svibnja 1990. u Splitu."),
        ("sl", "V 19. stoletju je mesto raslo."),
        ("nb", "Den 6. mars 2014 var det kaldt."),
        ("es", "—¿Vienes? —preguntó ella."),
        ("pt", "— Onde estiveste? — perguntou a mãe da cozinha."),
        ("ca", "—Què fas? —va preguntar la seva mare."),
        ("pl", "— Gdzie byłeś? — zapytała matka."),
        ("lt", "– Kur buvai? – paklausė mama."),
        ("tr", "— Nereye? diye sordu."),
        ("ru", "-- Почему? -- спросила Полина."),  # a plain-text book's "--" em dash
    ],
)
def test_a_lowercase_continuation_does_not_end_the_sentence(code, text):
    """A cased sentence never starts lowercase: an ordinal dot or a ?/! before a lowercase word or speech tag."""
    assert split_sentences(text, rules=get_profile(code).sentence_rules) == [text]


@pytest.mark.parametrize(
    ("code", "text", "sentences"),
    [
        ("pl", "— Tak! — zawołała Ania. — Byłam tam!", ["— Tak! — zawołała Ania.", "— Byłam tam!"]),
        ("hu", "A 3. emeleten lakom. Szép a kilátás.", ["A 3. emeleten lakom.", "Szép a kilátás."]),
        ("es", "—¿Vienes? —Sí, ahora.", ["—¿Vienes?", "—Sí, ahora."]),
    ],
)
def test_a_capital_after_the_terminator_still_ends_the_sentence(code, text, sentences):
    assert split_sentences(text, rules=get_profile(code).sentence_rules) == sentences


@pytest.mark.parametrize(
    "text",
    [
        "Am 3. Oktober ist Feiertag.",
        "Das war im 19. Jahrhundert.",
        "Der Klub spielt in der 5. Liga.",
        "Kroatien trat am 1. Januar 2023 der Eurozone bei.",
    ],
)
def test_a_german_ordinal_after_its_lead_word_does_not_end_the_sentence(text):
    assert split_sentences(text, rules=get_profile("de").sentence_rules) == [text]


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Er ist 30. Sie ist 25.", ["Er ist 30.", "Sie ist 25."]),
        ("Wir treffen uns um 8. Nein, um 9.", ["Wir treffen uns um 8.", "Nein, um 9."]),
    ],
)
def test_a_german_number_after_any_other_word_still_ends_the_sentence(text, sentences):
    assert split_sentences(text, rules=get_profile("de").sentence_rules) == sentences


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("เด็ก ๆ ในหมู่บ้านวิ่งเล่น แม่เรียกลูก", ["เด็ก ๆ ในหมู่บ้านวิ่งเล่น", "แม่เรียกลูก"]),
        ("มะลิอายุ 12 ปี เธอเรียนเก่ง", ["มะลิอายุ 12 ปี", "เธอเรียนเก่ง"]),
        ("เขาใช้ iPhone ทุกวัน ฝนตก", ["เขาใช้ iPhone ทุกวัน", "ฝนตก"]),
        ("ไปกรุงเทพฯ ในวันเสาร์ ฝนตก", ["ไปกรุงเทพฯ ในวันเสาร์", "ฝนตก"]),
    ],
)
def test_a_thai_space_beside_a_joiner_is_not_a_boundary(text, sentences):
    """Royal Institute spacing puts a space around ๆ, numerals and Latin words: none of them ends a clause."""
    assert split_sentences(text, rules=get_profile("th").sentence_rules) == sentences


def test_the_new_rule_data_defaults_empty():
    rules = SentenceRules(terminators=frozenset("."), ellipses=frozenset(), openers=frozenset(), closers=frozenset())
    assert rules.ordinal_leads == frozenset() and rules.whitespace_joiners == frozenset()


def test_a_hebrew_apostrophe_geresh_does_not_glue_sentences_up_to_a_stray_closer():
    text = "קניתי ג'ינס חדש. זה עלה מאה שקל. איזה כיף :)"
    assert split_sentences(text, rules=HE_SENTENCE_RULES) == [
        "קניתי ג'ינס חדש.",
        "זה עלה מאה שקל.",
        "איזה כיף :)",
    ]


@pytest.mark.parametrize("rules", [AR_SENTENCE_RULES, FA_SENTENCE_RULES], ids=["ar", "fa"])
def test_an_ascii_double_quote_does_not_glue_sentences_up_to_a_stray_closer(rules):
    text = 'قال "مرحبا". ثم ذهب. :)'
    assert split_sentences(text, rules=rules) == ['قال "مرحبا".', "ثم ذهب.", ":)"]


def test_no_symmetric_ascii_quote_is_both_opener_and_closer():
    for code in ("he", "ar", "fa", "th"):
        rules = get_profile(code).sentence_rules
        assert not (rules.openers & rules.closers & {"'", '"'}), code
