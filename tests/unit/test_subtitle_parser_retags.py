"""Tagger parts of speech the token post-pass corrects (morphology._retag_mistagged_tokens)."""

import pytest

from anki_miner.models.reading import ReadingUnit
from anki_miner.services.subtitle_parser import SubtitleParserService


def _mine(test_config, *lines):
    """Card fronts of ``lines`` with every front attested, as a full JMdict would."""
    service = SubtitleParserService(test_config, kana_attest_lookup=lambda forms: dict.fromkeys(forms, True))
    units = [ReadingUnit(text=line, index=i, location_label="t") for i, line in enumerate(lines)]
    words, _index, _counts = service.parse_text_units(units, want_line_index=False)
    return [word.mined_form for word in words]


class TestFillerBeforeANoun:
    @pytest.mark.parametrize("line", ["あの女は嫌いだ", "あの橋の下で待つ", "あの子が来た"])
    def test_mines_the_pre_noun_adjective_the_tagger_calls_a_filler(self, test_config, line):
        assert "あの" in _mine(test_config, line)

    @pytest.mark.parametrize("line", ["あのね、聞いて", "あの…すみません", "あの 山田さん", "あのさ、行こう"])
    def test_a_filler_stays_unmined(self, test_config, line):
        assert "あの" not in _mine(test_config, line)

    def test_a_filler_before_a_pronoun_stays_unmined(self, test_config):
        # あの私 is "um, I": a pronoun after the filler is the start of the reply.
        assert "あの" not in _mine(test_config, "あの私は")
