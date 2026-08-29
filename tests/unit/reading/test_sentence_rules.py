"""split_sentences takes its character policy from SentenceRules."""

from anki_miner.languages.profile import SentenceRules
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
