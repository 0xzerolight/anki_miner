"""The import decode ladder is the caller's, and the wrong one is observable."""

import pytest

from anki_miner.services.known_words_import import KnownWordsImportError, parse_known_words_file

JA_LADDER = ("utf-8-sig", "cp932", "euc_jp")
KO_LADDER = ("utf-8-sig", "cp949")


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
    assert str(exc.value) == "unreadable"


@pytest.mark.parametrize("encoding", ["utf-8", "cp932"])
def test_japanese_lists_are_unaffected_by_the_added_leg(tmp_path, encoding):
    path = _write(tmp_path, "猫\n犬\n", encoding)
    assert parse_known_words_file(path).words == frozenset({"猫", "犬"})
    assert parse_known_words_file(path, encodings=JA_LADDER).words == frozenset({"猫", "犬"})
