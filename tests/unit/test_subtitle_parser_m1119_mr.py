"""Lane MR: doubled katakana mimetic words (ドキドキ), a noun + って the tagger reads as a rare verb (恋って),
and capital-letter words the dictionary has (ＣＤ)."""

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

_MIMETICS = ("ドキドキ", "ワクワク", "ニコニコ", "ウロウロ", "ペラペラ")


def _service(headwords=()) -> SubtitleParserService:
    spelled = set(headwords)
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestAttestedKatakanaMimetics:
    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("胸がドキドキする", "ドキドキ"),
            ("明日が楽しみでワクワクする", "ワクワク"),
            ("彼女はニコニコ笑った", "ニコニコ"),
            ("駅の前をウロウロ歩く", "ウロウロ"),
            ("英語をペラペラ話す", "ペラペラ"),
        ],
    )
    def test_a_doubled_mimetic_the_dictionary_has_is_mined(self, text: str, front: str) -> None:
        assert front in _fronts(_service(_MIMETICS), text)

    @pytest.mark.parametrize("text", ["胸がドキドキする", "駅の前をウロウロ歩く"])
    def test_one_the_dictionary_lacks_stays_out(self, text: str) -> None:
        assert not set(_fronts(_service(), text)) & set(_MIMETICS)

    def test_nothing_changes_without_a_dictionary(self) -> None:
        assert "ドキドキ" not in _fronts(SubtitleParserService(AnkiMinerConfig()), "胸がドキドキする")

    @pytest.mark.parametrize(
        ("text", "sound"),
        [
            ("ドンと音がした", "ドン"),
            ("フッと笑った", "フッ"),
            ("パッと消えた", "パッ"),
            ("ハァハァ言っている", "ハァハァ"),
            ("風がゴーゴー鳴る", "ゴーゴー"),
            ("ピーピー鳴る", "ピーピー"),
            ("オイオイ 何してるんだ", "オイオイ"),
        ],
    )
    def test_a_sound_effect_shape_stays_out_even_when_listed(self, text: str, sound: str) -> None:
        assert sound not in _fronts(_service([sound]), text)

    def test_the_count_gate_agrees_with_mining(self) -> None:
        service = _service(_MIMETICS)
        token = next(tok for tok in service._tagger()("胸がドキドキする") if tok.surface == "ドキドキ")
        assert service._should_include_word(token) is True


class TestNounBeforeQuotingTte:
    """恋って, 番って and 敵って are the noun and the particle って, not the て form of 恋う, 番う and 敵う."""

    @pytest.mark.parametrize(
        ("text", "noun", "verb"),
        [
            ("恋ってすごいですね", "恋", "恋う"),
            ("次は私の番ってことでいいよね", "番", "番う"),
            ("敵って誰のことですか", "敵", "敵う"),
            ("この味って何だろう", "味", "味う"),
            ("この曲っていいよね", "曲", "曲る"),
            ("あの空ってきれいだね", "空", "空る"),
            ("この氷って溶けないの", "氷", "氷る"),
            ("この逆って何だろう", "逆", "逆う"),
            ("この隣って誰の席", "隣", "隣る"),
            ("この縁って大事だね", "縁", "縁る"),
            ("森の主って誰のこと", "主", "主る"),
        ],
    )
    def test_the_noun_is_mined_and_the_verb_is_not(self, text: str, noun: str, verb: str) -> None:
        fronts = _fronts(SubtitleParserService(AnkiMinerConfig()), text)
        assert noun in fronts
        assert verb not in fronts

    def test_the_card_highlights_the_noun_alone(self) -> None:
        service = SubtitleParserService(AnkiMinerConfig())
        words, _index, _counts = service.parse_text_units(
            [ReadingUnit(text="恋ってすごいですね", index=0, location_label="t")], want_line_index=False
        )
        word = next(word for word in words if word.mined_form == "恋")
        assert word.sentence[word.surface_start : word.highlight_end] == "恋"
        assert word.sentence == "恋ってすごいですね"

    @pytest.mark.parametrize(
        ("text", "verb"),
        [
            ("彼には敵わない", "敵う"),
            ("大きな声で歌って", "歌う"),
            ("料理を味わって食べる", "味わう"),
            ("角を曲がって進む", "曲がる"),
            ("親に逆らってばかり", "逆らう"),
        ],
    )
    def test_the_verbs_other_forms_are_untouched(self, text: str, verb: str) -> None:
        assert verb in _fronts(SubtitleParserService(AnkiMinerConfig()), text)


class TestAttestedLetterWords:
    _LETTER_WORDS = ("ＣＤ", "ＤＶＤ", "ＮＧ", "ＯＫ")

    @pytest.mark.parametrize(
        ("text", "front"),
        [
            ("新しいＣＤを買った", "ＣＤ"),
            ("ＤＶＤで映画を見る", "ＤＶＤ"),
            ("その言葉はＮＧだ", "ＮＧ"),
            ("これでＯＫです", "ＯＫ"),
        ],
    )
    def test_a_capital_letter_word_the_dictionary_has_is_mined(self, text: str, front: str) -> None:
        assert front in _fronts(_service(self._LETTER_WORDS), text)

    @pytest.mark.parametrize("text", ["新しいＣＤを買った", "これでＯＫです"])
    def test_one_the_dictionary_lacks_stays_out(self, text: str) -> None:
        assert not set(_fronts(_service(), text)) & set(self._LETTER_WORDS)

    def test_nothing_changes_without_a_dictionary(self) -> None:
        assert "ＣＤ" not in _fronts(SubtitleParserService(AnkiMinerConfig()), "新しいＣＤを買った")

    @pytest.mark.parametrize(
        ("text", "letters"),
        [
            ("ｙｏｕは誰だ", "ｙｏｕ"),
            ("答えはＡだ", "Ａ"),
            ("Ｎｏと言った", "Ｎｏ"),
        ],
    )
    def test_lower_case_and_single_letters_stay_out_even_when_listed(self, text: str, letters: str) -> None:
        assert letters not in _fronts(_service([letters]), text)
