"""The import decode ladder is the caller's, and the wrong one is observable."""

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.known_words_import import KnownWordsImportError, parse_known_words_file

JA_LADDER = ("utf-8-sig", "cp932", "euc_jp")
KO_LADDER = ("utf-8-sig", "cp949")
ZH_LADDER = get_profile("zh").import_encodings

#: A real Taiwanese export, not a handful of words: the Big5 guard judges the
#: private-use-area share of the whole file, and a few characters sit below the
#: noise floor it documents as unjudgeable.
TRADITIONAL_WORDS = (
    "蘋果", "學習", "電腦", "網路", "開會", "時間", "國家", "經濟",
    "發展", "關係", "應該", "這樣", "為什麼", "沒關係", "謝謝",
    "對不起", "醫生", "圖書館", "飛機", "車站", "臺灣", "銀行",
    "護照", "聽說", "點鐘", "會議", "櫃檯", "臺北", "機場", "藥局",
    "學校", "體育", "廚房", "鐵路",
)  # fmt: skip


def _write(tmp_path, text, encoding):
    path = tmp_path / "words.txt"
    path.write_bytes(text.encode(encoding))
    return path


def test_korean_ladder_reads_a_cp949_list(tmp_path):
    path = _write(tmp_path, "사과\n학교\n", "cp949")
    result = parse_known_words_file(path, encodings=KO_LADDER)
    assert result.words == frozenset({"사과", "학교"})


def test_japanese_ladder_mangles_the_same_list(tmp_path):
    """cp949 hangul fails cp932 but decodes under euc_jp into kanji, so the ja
    ladder imports mojibake rather than the words — which is exactly why the
    ladder has to come from the profile instead of being hard-coded."""
    path = _write(tmp_path, "사과\n학교\n", "cp949")
    result = parse_known_words_file(path, encodings=JA_LADDER)
    assert result.words == frozenset({"紫引", "俳嘘"})


def test_an_explicit_empty_ladder_decodes_nothing(tmp_path):
    """`None` is the "use the default" sentinel, never `()`.

    Truthiness silently turned an empty ladder into the Japanese default, which
    is the failure the contract's is-None rule exists to stop: a profile that
    ships no import encodings would have decoded every list as Japanese.
    """
    path = _write(tmp_path, "猫\n犬\n", "utf-8")
    with pytest.raises(KnownWordsImportError) as exc:
        parse_known_words_file(path, encodings=())
    assert str(exc.value) == "undecodable"


@pytest.mark.parametrize("encoding", ["utf-8", "cp932"])
def test_japanese_lists_are_unaffected_by_the_added_leg(tmp_path, encoding):
    path = _write(tmp_path, "猫\n犬\n", encoding)
    assert parse_known_words_file(path).words == frozenset({"猫", "犬"})
    assert parse_known_words_file(path, encodings=JA_LADDER).words == frozenset({"猫", "犬"})


def test_chinese_ladder_reads_a_big5_list(tmp_path):
    """gb18030 decodes every valid Big5 sequence into PUA mojibake without raising.

    A first-success ladder therefore never reached its big5 leg, and a
    Taiwanese export imported as unmatchable garbage words.
    """
    path = _write(tmp_path, "\n".join(TRADITIONAL_WORDS) + "\n", "big5")
    result = parse_known_words_file(path, encodings=ZH_LADDER)
    assert result.words == frozenset(TRADITIONAL_WORDS)


def test_chinese_ladder_reads_a_gb18030_list(tmp_path):
    """The mainland majority still wins gb18030: the guard is signature-driven."""
    words = ("苹果", "学习", "电脑", "网络", "开会", "时间", "国家", "经济")
    path = _write(tmp_path, "\n".join(words) + "\n", "gb18030")
    result = parse_known_words_file(path, encodings=ZH_LADDER)
    assert result.words == frozenset(words)


def test_the_japanese_ladder_keeps_losing_euc_jp_to_cp932(tmp_path):
    """The Big5 guard is inert for a ladder naming no Big5-family codec.

    EUC-JP hiragana decodes without error as cp932, so first-success stops at
    the mojibake — the same shape the guard rescues Chinese from, and Japanese
    must still get the old answer rather than a new sniff.
    """
    path = _write(tmp_path, "かな\n", "euc_jp")
    assert parse_known_words_file(path, encodings=JA_LADDER).words == frozenset({"､ｫ､ﾊ"})
    assert parse_known_words_file(path).words == frozenset({"､ｫ､ﾊ"})


def test_both_import_paths_share_one_decoder():
    """One decoder, one Big5 rule.

    A second copy of the ladder walk is how Manage Known Words came to mojibake
    a Big5 list that the word-list path read as words.
    """
    from anki_miner.services import known_words_import, word_list_service
    from anki_miner.services.reading._util import decode_with_ladder

    assert known_words_import.decode_with_ladder is decode_with_ladder
    assert word_list_service.decode_with_ladder is decode_with_ladder
