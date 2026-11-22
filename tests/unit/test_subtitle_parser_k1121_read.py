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
    "通り": {"とおり", "どおり"},
    "頃": {"ころ", "ごろ"},
    "度": {"たび", "ど"},
    "何時": {"いつ", "なんじ", "なんどき"},
    "側": {"がわ", "そば", "かわ"},
    "主": {"ぬし", "あるじ", "しゅ", "おも"},
    "留まる": {"とどまる", "とまる"},
    "家": {"いえ", "うち", "け", "や"},
    "術": {"すべ", "じゅつ"},
    "敵": {"てき", "かたき"},
    "宴": {"うたげ", "えん"},
    "下": {"した", "もと", "しも"},
    "空く": {"すく", "あく"},
    "開く": {"あく", "ひらく"},
    "体": {"からだ", "たい", "てい"},
}


class TestReadingTable:
    """The table itself: every new reading is one the dictionary lists."""

    def test_every_new_reading_is_a_dictionary_reading(self) -> None:
        assert morphology._READING_CONTEXT
        for row in morphology._READING_CONTEXT:
            new = katakana_to_hiragana(row.new)
            assert any(reading.startswith(new) for reading in _LISTED_READINGS[row.word]), row

    def test_every_row_names_a_part_of_speech_and_two_readings(self) -> None:
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


@needs_tagger
class TestVoicedAfterANoun:
    """通り and 頃 voice directly after a noun; 度 after a verb is たび."""

    @pytest.mark.parametrize(
        ("sentence", "surface", "kana"),
        [
            ("命令通りに動け", "通り", "ドオリ"),
            ("銀座通りを歩く", "通り", "ドオリ"),
            ("今まで通りに暮らす", "通り", "ドオリ"),
            ("いつも通りだ", "通り", "ドオリ"),
            ("思い通りになった", "通り", "ドオリ"),
            ("言った通りにしろ", "通り", "トオリ"),
            ("その通りだ", "通り", "トオリ"),
            ("二通りある", "通り", "トオリ"),
            ("通りを渡る", "通り", "トオリ"),
            ("車の通りが多い", "通り", "トオリ"),
            ("3時頃に来て", "頃", "ゴロ"),
            ("来週頃に届く", "頃", "ゴロ"),
            ("子供の頃の話", "頃", "コロ"),
            ("若い頃は強かった", "頃", "コロ"),
            ("描く度に人が変わる", "度", "タビ"),
            ("3度目だ", "度", "ド"),
        ],
    )
    def test_the_token_reading(self, sentence: str, surface: str, kana: str) -> None:
        assert _kana(sentence, surface) == [kana]

    def test_a_whole_word_takes_the_reading_in_every_kana_field(self) -> None:
        token = next(t for t in _reread("命令通りに動け")[1] if t.surface == "通り")
        assert {getattr(token.feature, name) for name in morphology._CONTEXT_KANA_FIELDS} == {"ドオリ"}

    @pytest.mark.parametrize(
        ("sentence", "ruby"),
        [("命令通りに動け", "通[どお]り"), ("3時頃に来て", "頃[ごろ]"), ("描く度に人が変わる", "度[たび]")],
    )
    def test_the_sentence_ruby(self, sentence: str, ruby: str) -> None:
        words = _words(sentence)
        assert words
        assert all(ruby in word.sentence_furigana for word in words.values())


@needs_tagger
class TestSenseReadings:
    """お家, 開く, 空く, 術, 敵, 宴 and 下 take the reading of the sense the line shows."""

    @pytest.mark.parametrize(
        ("sentence", "surface", "kana"),
        [
            ("お家に帰ろう", "家", "ウチ"),
            ("家に帰る", "家", "イエ"),
            ("お家騒動だ", "家", "イエ"),
            ("徳川のお家のため", "家", "イエ"),
            ("お家を守るのが務め", "家", "イエ"),
            ("ドアが開いた", "開い", "アイ"),
            ("穴が開いてる", "開い", "アイ"),
            ("目が開いた", "開い", "アイ"),
            ("本を開いた", "開い", "ヒライ"),
            ("傷口が開いた", "開い", "ヒライ"),
            ("目が開かれた", "開か", "ヒラカ"),
            ("お家ご安泰でございます", "家", "イエ"),
            ("ある術を使った", "術", "ジュツ"),
            ("煽る術にはまるな", "術", "ジュツ"),
            ("為す術なし", "術", "スベ"),
            ("宴も たけなわですが", "宴", "エン"),
            ("口を開くな", "開く", "ヒラク"),
            ("お腹が空いた", "空い", "スイ"),
            ("お腹空いた", "空い", "スイ"),
            ("席が空いた", "空い", "アイ"),
            ("知る術がない", "術", "スベ"),
            ("逃げる術はない", "術", "スベ"),
            ("為す術もなく負けた", "術", "スベ"),
            ("影分身の術だ", "術", "ジュツ"),
            ("術を使うな", "術", "ジュツ"),
            ("生きるための術だ", "術", "スベ"),
            ("親の敵だ", "敵", "カタキ"),
            ("僕たちの敵を取ってくれ", "敵", "カタキ"),
            ("父の敵を討つ", "敵", "カタキ"),
            ("敵が来たぞ", "敵", "テキ"),
            ("敵を取り囲め", "敵", "テキ"),
            ("敵を討ち取った", "敵", "テキ"),
            ("ジェットで敵をうつ", "敵", "テキ"),
            ("お家のことはわしに任せよ", "家", "イエ"),
            ("宴の準備をしろ", "宴", "ウタゲ"),
            ("宴もたけなわですが", "宴", "エン"),
            ("法の下に平等だ", "下", "モト"),
            ("机の下にある", "下", "シタ"),
        ],
    )
    def test_the_token_reading(self, sentence: str, surface: str, kana: str) -> None:
        assert _kana(sentence, surface) == [kana]

    @pytest.mark.parametrize(
        ("sentence", "ruby"),
        [("お腹が空いた", "空[す]いた"), ("ドアが開いた", "開[あ]いた"), ("知る術がない", "術[すべ]")],
    )
    def test_the_sentence_ruby(self, sentence: str, ruby: str) -> None:
        words = _words(sentence)
        assert words
        assert all(ruby in word.sentence_furigana for word in words.values())


@needs_tagger
class TestHumbleWatakushi:
    """私 keeps わたくし in a humble or ladylike line."""

    @pytest.mark.parametrize("sentence", ["私でございます", "私が参ります", "私ですわ", "私、田中と申します"])
    def test_watakushi_on_the_card_and_in_the_sentence(self, sentence: str) -> None:
        word = _words(sentence)["私"]
        assert word.expression_reading == "わたくし"
        assert "私[わたくし]" in word.sentence_furigana

    @pytest.mark.parametrize(
        "sentence",
        ["私は学生だ", "私も行くよ", "私からは以上です ありがとうございました", "私に言ってくださいましたね"],
    )
    def test_watashi_elsewhere(self, sentence: str) -> None:
        word = _words(sentence)["私"]
        assert word.expression_reading == "わたし"
        assert "私[わたし]" in word.sentence_furigana


@needs_tagger
class TestBesideAndMaster:
    """側 beside a person is そば, the 主 of a house is あるじ, and to stay is 留まる とどまる."""

    @pytest.mark.parametrize(
        ("sentence", "surface", "kana"),
        [
            ("ずっと側にいてくれ", "側", "ソバ"),
            ("側を離れるな", "側", "ソバ"),
            ("俺の側にいろ", "側", "ソバ"),
            ("敵の側につく", "側", "ガワ"),
            ("正義の側にいる", "側", "ガワ"),
            ("知ってる側だ", "側", "ガワ"),
            ("この家の主だ", "主", "アルジ"),
            ("森の主が現れた", "主", "ヌシ"),
            ("ここに留まる", "留まる", "トドマル"),
            ("目に留まった", "留まっ", "トマッ"),
            ("鳥が留まっている", "留まっ", "トマッ"),
        ],
    )
    def test_the_token_reading(self, sentence: str, surface: str, kana: str) -> None:
        assert _kana(sentence, surface) == [kana]


@needs_tagger
class TestWhatTime:
    """何時 as one token is なんじ; なんどき belongs to いつ何時."""

    @pytest.mark.parametrize("sentence", ["今何時ですか", "今 何時？"])
    def test_nanji_on_the_card_and_in_the_sentence(self, sentence: str) -> None:
        word = _words(sentence)["何時"]
        assert word.expression_reading == "なんじ"
        assert "何時[なんじ]" in word.sentence_furigana

    def test_itsu_is_untouched(self) -> None:
        assert _kana("何時から始まる", "何時") == ["イツ"]
