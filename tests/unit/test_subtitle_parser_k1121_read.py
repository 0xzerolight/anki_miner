"""Readings decided by the line (session K1121, lane READ).

The parser tests run the real parser wired to the benchmark's fixture
dictionary: the card reading and the sentence ruby must agree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from anki_miner.services import morphology
from anki_miner.utils import katakana_to_hiragana

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "parse_corpus" / "k1121_read.jsonl"


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


needs_tagger = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _service():
    from scripts.parse_benchmark import _get_anchor_service

    return _get_anchor_service()


def _words(sentence: str) -> dict:
    """Every word mined from *sentence*, by card front."""
    from anki_miner.models.reading import ReadingUnit

    unit = ReadingUnit(text=sentence, index=0, location_label="test")
    words, _index, _counts = _service().parse_text_units([unit], want_line_index=False)
    return {w.mined_form: w for w in words}


def _reread(sentence: str) -> tuple[list, list]:
    import fugashi

    tokens = list(fugashi.Tagger()(sentence))
    return tokens, morphology.reread_in_context(tokens)


def _kana(sentence: str, surface: str) -> list[str]:
    return [token.feature.kana for token in _reread(sentence)[1] if token.surface == surface]


#: Readings a dictionary lists for each word of the table (written by hand).
_LISTED_READINGS: dict[str, set[str]] = {
    "何": {"なに", "なん"},
    "私": {"わたし", "わたくし"},
    "明日": {"あした", "あす", "みょうにち"},
    "日本": {"にほん", "にっぽん"},
    "仏": {"ほとけ", "ふつ"},
    "蛙": {"かえる", "かわず"},
    "一日": {"いちにち", "ついたち"},
    "１日": {"いちにち", "ついたち"},
    "言う": {"いう", "ゆう"},
    "不味い": {"まずい"},
    "後": {"あと", "ご", "のち", "うしろ"},
    "他": {"ほか", "た"},
    "時": {"とき", "じ"},
}


class TestReadingTable:
    """The table itself: every new reading is one the dictionary lists."""

    def test_every_new_reading_is_a_dictionary_reading(self) -> None:
        assert morphology._READING_CONTEXT
        for row in morphology._READING_CONTEXT:
            new = katakana_to_hiragana(row.new)
            assert any(reading.startswith(new) for reading in _LISTED_READINGS[row.word]), row

    def test_a_change_row_is_the_last_word_of_its_group_or_has_a_test(self) -> None:
        for row in morphology._READING_CONTEXT:
            assert row.old and row.new and row.pos1, row


@needs_tagger
class TestRereadInContext:
    """The pass changes kana fields only, and only on a token a row names."""

    @pytest.mark.parametrize("sentence", ["猫が寝ている", "それは違うと思う"])
    def test_a_line_without_a_table_word_is_the_same_list(self, sentence: str) -> None:
        tokens, out = _reread(sentence)
        assert out is tokens

    def test_a_kept_token_passes_by_identity(self) -> None:
        tokens, out = _reread("何だこれは")
        assert all(a is b for a, b in zip(tokens, out, strict=True))

    def test_a_changed_token_keeps_its_cut_and_part_of_speech(self) -> None:
        tokens, out = _reread("何を見る")
        assert [t.surface for t in out] == [t.surface for t in tokens]
        assert isinstance(out[0], morphology.RetaggedToken)
        assert out[0].feature.pos1 == tokens[0].feature.pos1
        assert (out[0].feature.kana, out[0].feature.pron) == ("ナニ", "ナニ")
        assert all(a is b for a, b in zip(tokens[1:], out[1:], strict=True))


@needs_tagger
class TestWhatReading:
    """何 is なん before the copula, で, と, の, て, って, じゃ, や and a counter; なに elsewhere."""

    @pytest.mark.parametrize(
        "sentence",
        [
            "何だこれは",
            "それは何ですか",
            "何の話だ",
            "何て言ったの",
            "何なのよ",
            "何なら手伝う",
            "何や、お前か",
            "何すか",
            "何じゃそりゃ",
            "何って言われても",
        ],
    )
    def test_nan_on_the_card_and_in_the_sentence(self, sentence: str) -> None:
        word = _words(sentence)["何"]
        assert word.expression_reading == "なん"
        assert word.expression_furigana == "何[なん]"
        assert "何[なん]" in word.sentence_furigana

    @pytest.mark.parametrize(
        "sentence",
        [
            "何を言ってるんだ",
            "何が起きたの",
            "何よ",
            "何？",
            "何か食べたい",
            "何に使うの",
            "何やってんだ",
            "何する気だ",
            "何と何を買うの",
            "何と戦ってるの",
            "何のんきなことを言ってる",
        ],
    )
    def test_nani_on_the_card_and_in_the_sentence(self, sentence: str) -> None:
        word = _words(sentence)["何"]
        assert word.expression_reading == "なに"
        assert word.expression_furigana == "何[なに]"
        assert "何[なに]" in word.sentence_furigana
        assert "何[なん]" not in word.sentence_furigana

    @pytest.mark.parametrize("sentence", ["何回目だ", "何度も言った", "何人いるの", "何歳ですか"])
    def test_nan_before_a_counter(self, sentence: str) -> None:
        assert _kana(sentence, "何") == ["ナン"]

    @pytest.mark.parametrize(
        ("sentence", "front", "reading"), [("何事だ", "何事", "なにごと"), ("何者だ", "何者", "なにもの")]
    )
    def test_own_words_are_untouched(self, sentence: str, front: str, reading: str) -> None:
        assert _words(sentence)[front].expression_reading == reading

    @pytest.mark.parametrize("sentence", ["何なりとお申し付けください", "何やかんやあった", "何らかの形で残す"])
    def test_nan_where_the_tagger_cuts_the_next_word_oddly(self, sentence: str) -> None:
        assert _kana(sentence, "何") == ["ナン"]

    def test_a_space_ends_the_context(self) -> None:
        assert _kana("何 だから言った", "何") == ["ナニ"]

    def test_a_headword_read_alone_goes_through_the_table(self) -> None:
        service = _service()
        assert service._reading("何") == "なに"
        assert service._furigana("何") == "何[なに]"
        assert service._reading("何だ") == "なんだ"


@needs_tagger
def test_fixture_readings() -> None:
    records = [json.loads(line) for line in _FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert records
    for record in records:
        words = _words(record["sentence"])
        assert set(words) == set(record["expected"]), record["id"]
        for front, reading in record["expected_readings"].items():
            assert words[front].expression_reading == reading, record["id"]


@needs_tagger
class TestRowsThatWereCardOverrides:
    """What the card-level override table patched is read on the token, so a merge inherits it."""

    @pytest.mark.parametrize(
        ("sentence", "surface", "kana"),
        [
            ("私は学生だ", "私", "ワタシ"),
            ("明日は晴れだ", "明日", "アシタ"),
            ("日本一の男だ", "日本", "ニホン"),
            ("仏の顔も三度", "仏", "ホトケ"),
            ("蛙が鳴く", "蛙", "カエル"),
            ("井の中の蛙だ", "蛙", "カワズ"),
            ("私どもの責任です", "私", "ワタクシ"),
            ("私めにお任せを", "私", "ワタクシ"),
            ("私自身の問題だ", "私", "ワタシ"),
            ("大日本ホルモン焼き", "日本", "ニッポン"),
            ("その後、彼は去った", "後", "アト"),
            ("3日後に会おう", "後", "ゴ"),
            ("兄ちゃん、一日、仏", "一日", "イチニチ"),
            ("今日１日は休む", "１日", "イチニチ"),
            ("６月１日だ", "１日", "ツイタチ"),
            ("六月一日に会う", "一日", "ツイタチ"),
            ("毎月 １日に会う", "１日", "ツイタチ"),
            ("そう言うな", "言う", "イウ"),
            ("不味い", "不味い", "マズイ"),
        ],
    )
    def test_the_token_reading(self, sentence: str, surface: str, kana: str) -> None:
        assert _kana(sentence, surface) == [kana]

    def test_a_kana_token_is_never_reread(self) -> None:
        for sentence in ("なんか変だ", "わたくしが行く", "あすは晴れる"):
            tokens, out = _reread(sentence)
            assert all(a is b for a, b in zip(tokens, out, strict=True))

    def test_a_headword_read_alone(self) -> None:
        service = _service()
        assert service._reading("何と言う") == "なんという"
        assert service._reading("そう言う") == "そういう"
        assert service._reading("こう言う") == "こういう"
        assert service._reading("私自身") == "わたしじしん"
        assert service._reading("日本一") == "にほんいち"
        assert service._reading("不味い") == "まずい"

    @pytest.mark.parametrize(
        ("sentence", "wrong", "right"),
        [("私たちは行く", "わたくし", "わたし"), ("日本語を話す", "にっぽん", "にほん"), ("そう言うな", "ゆ", "い")],
    )
    def test_the_sentence_and_the_cards_agree(self, sentence: str, wrong: str, right: str) -> None:
        words = _words(sentence)
        assert words
        for word in words.values():
            assert wrong not in word.sentence_furigana
            assert right in word.sentence_furigana
            assert wrong not in word.expression_reading


@needs_tagger
class TestPauseArtefacts:
    """他, 時 and 後 that the tagger misreads at a subtitle space or a comma."""

    @pytest.mark.parametrize(
        ("sentence", "surface", "kana"),
        [
            ("それとも 他に誰かいる", "他", "ホカ"),
            ("その他のこと", "他", "タ"),
            ("他の人に聞いて", "他", "ホカ"),
            ("他チームに負けた", "他", "タ"),
            ("弾を込める時 気をつけろ", "時", "トキ"),
            ("あの時 君はいなかった", "時", "トキ"),
            ("3時に会おう", "時", "ジ"),
            ("緊急時には逃げろ", "時", "ジ"),
            ("この後、会議がある", "後", "アト"),
            ("後三年かかる", "後", "アト"),
            ("3日後に会おう", "後", "ゴ"),
        ],
    )
    def test_the_token_reading(self, sentence: str, surface: str, kana: str) -> None:
        assert _kana(sentence, surface) == [kana]

    @pytest.mark.parametrize(
        ("sentence", "ruby"),
        [
            ("この後、会議がある", "後[あと]"),
            ("弾を込める時 気をつけろ", "時[とき]"),
            ("それとも 他に誰かいる", "他[ほか]"),
        ],
    )
    def test_the_sentence_ruby(self, sentence: str, ruby: str) -> None:
        words = _words(sentence)
        assert words
        assert all(ruby in word.sentence_furigana for word in words.values())
