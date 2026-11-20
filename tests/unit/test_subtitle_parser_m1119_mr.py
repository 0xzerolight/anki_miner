"""Doubled katakana mimetic words (ドキドキ) are mined when the dictionary has them; sound effects stay out."""

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
