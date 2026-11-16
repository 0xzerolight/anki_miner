"""Tagger parts of speech the token post-pass corrects (morphology.retag_mistagged_tokens)."""

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


class TestNounReadAsAPrefixAcrossASpace:
    @pytest.mark.parametrize(("line", "front"), [("今 駅にいる", "今"), ("次 頑張ります", "次")])
    def test_mines_the_noun(self, test_config, line, front):
        assert front in _mine(test_config, line)

    def test_reads_the_noun(self, test_config):
        service = SubtitleParserService(test_config, kana_attest_lookup=lambda forms: dict.fromkeys(forms, True))
        units = [ReadingUnit(text="今 駅にいる", index=0, location_label="t")]
        words, _index, _counts = service.parse_text_units(units, want_line_index=False)
        assert {word.mined_form: word.reading for word in words}["今"] == "イマ"

    @pytest.mark.parametrize("line", ["今大会で優勝した", "次年度の予算"])
    def test_the_prefix_stays_a_prefix(self, test_config, line):
        fronts = _mine(test_config, line)
        assert "今" not in fronts
        assert "次" not in fronts


class TestUnknownKatakanaNoun:
    _ROWS = {
        "スマホ": [("smartphone", "n abbr"), ("", "forms")],
        "ワーオ": [("wow", "int"), ("", "forms")],
        "ピーポー": [("nee-naw", "n on-mim"), ("", "forms")],
    }

    def _mine_with_rows(self, test_config, line):
        service = SubtitleParserService(
            test_config,
            term_lookup=lambda terms: set(),
            form_lookup=lambda terms: {term: self._ROWS[term] for term in terms if term in self._ROWS},
        )
        words, _index, _counts = service.parse_text_units(
            [ReadingUnit(text=line, index=0, location_label="t")], want_line_index=False
        )
        return {word.mined_form: word.reading for word in words}

    def test_mines_a_loanword_the_tagger_does_not_know(self, test_config):
        assert self._mine_with_rows(test_config, "スマホを見て")["スマホ"] == "スマホ"

    @pytest.mark.parametrize(("line", "front"), [("ワーオ！すごい", "ワーオ"), ("ピーポーと鳴る", "ピーポー")])
    def test_a_sound_or_cry_the_dictionary_lists_stays_unmined(self, test_config, line, front):
        assert front not in self._mine_with_rows(test_config, line)

    def test_needs_a_dictionary(self, test_config):
        assert "スマホ" not in _mine(test_config, "スマホを見て")


class TestJibunchiResplit:
    @pytest.mark.parametrize("line", ["自分ちに帰る", "自分ちで食う"])
    def test_mines_jibun_not_its_pieces(self, test_config, line):
        fronts = _mine(test_config, line)
        assert "自分" in fronts
        assert not {"自", "分ち"} & set(fronts)
