"""One fold for both sides of the alignment (services/book_sync/normalize.py)."""

from __future__ import annotations

import pytest

from anki_miner.services.book_sync.normalize import normalize_for_alignment as norm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("こんにちは、世界！", "こんにちわ世界"),  # punctuation dropped, は→わ
        ("カタカナ", "かたかな"),  # katakana → hiragana
        ("ｶﾀｶﾅ", "かたかな"),  # halfwidth katakana via NFKC, then folded
        ("ＡＢＣ abc", "abcabc"),  # fullwidth ASCII via NFKC, casefold, space dropped
        ("三十二人", "32人"),  # numerals: 三→3 十 dropped 二→2
        ("〇時と零時", "0時と0時"),
        ("スーパー", "すぱ"),  # ー dropped, then パ-sequence stays (no run)
        ("ああ、そうそう", "あそうそう"),  # runs collapsed: ああ→あ; そうそう has no ADJACENT duplicate, so it stays
        ("人々", "人"),  # 々 dropped
        ("駅へ行く", "駅え行く"),  # へ→え
        ("本を読む", "本お読む"),  # を→お
        ("「引用」…‥・", "引用"),
        ("", ""),
        ("！？。、", ""),
    ],
)
def test_normalize(raw, expected):
    assert norm(raw) == expected


def test_normalize_is_idempotent():
    for raw in ("こんにちは、世界！", "ＡＢＣ", "三十二人", "スーパー"):
        once = norm(raw)
        assert norm(once) == once


def test_korean_and_chinese_pass_through_letters_only():
    assert norm("안녕하세요, 세계!") == "안녕하세요세계"
    assert norm("你好，世界。") == "你好世界"
