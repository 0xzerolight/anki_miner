"""split_sentences takes its character policy from SentenceRules."""

import pytest

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
