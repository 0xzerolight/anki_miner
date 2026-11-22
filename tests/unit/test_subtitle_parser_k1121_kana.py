"""Kana words session K1121 brought back: する as the line's own verb."""

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

_ATTESTED = frozenset({"する", "びっくり", "ゆっくり", "ごろごろ", "どう", "せい", "くせ", "きれい"})

# Dictionary entry tags by headword: a word with no row here has no entry at all.
_TAGS = {
    "勉強": "n vs vt",
    "びっくり": "adv vs",
    "宿題": "n",
    "手術": "n vs vt",
    "どう": "adv",
    "せい": "n",
    "何": "pn",
    "掃除": "n vs vt",
    "毎日": "n adv",
    "さっき": "n adv",
}


def _fronts(text: str, terms: frozenset[str] = frozenset()) -> list[str]:
    """Card fronts of ``text`` with ``_ATTESTED`` as the kana dictionary, ``terms`` as headwords and ``_TAGS`` as entry tags."""
    service = SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda asked: {term for term in asked if term in terms},
        kana_attest_lookup=lambda forms: {form: form in _ATTESTED for form in forms},
        form_lookup=lambda asked: {term: [("", _TAGS[term])] for term in asked if term in _TAGS},
    )
    words, _index, _counts = service.parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return [word.mined_form for word in words]


class TestSuruAsTheLinesOwnVerb:
    @pytest.mark.parametrize(
        "text",
        [
            "宿題をする。",
            "どうするの？",
            "何してるの？",
            "手術までするとは。",
            "雨のせいにするな。",
            "毎日してるよ。",
            "さっきしたばかりだ。",
        ],
    )
    def test_mines_suru_where_it_is_the_main_verb(self, text):
        assert "する" in _fronts(text)

    @pytest.mark.parametrize("text", ["勉強する。", "びっくりした。", "手術した。", "三回した。"])
    def test_suru_after_a_noun_that_takes_it_is_that_nouns_verb(self, text):
        assert "する" not in _fronts(text)

    def test_suru_after_a_noun_with_no_entry_is_no_card(self):
        assert "する" not in _fronts("合体ごろごろする。")

    @pytest.mark.parametrize(
        "text", ["行こうとした。", "見たりした。", "掃除とかしなくていいよ。", "行くかしないか決めて。"]
    )
    def test_suru_after_a_quoting_or_listing_particle_is_grammar(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize("text", ["彼にしては上出来だ。", "子供にしても重い。"])
    def test_ni_shite_wa_is_grammar(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize("text", ["きれいにしておいて。", "早くしろ。", "食べてしまった。"])
    def test_what_the_auxiliary_check_refuses_stays_refused(self, text):
        assert "する" not in _fronts(text)

    def test_the_particle_shi_after_a_contracted_adjective_is_no_suru(self):
        # The tagger cuts くせぇ as くせ|ぇ and reads the し after it as する.
        assert "する" not in _fronts("この部屋くせぇし、もう帰ろう。")

    @pytest.mark.parametrize(
        "text",
        ["よぉし 始めるか。", "ふ・し・ぎな話だ。", "それをさ 見せてよ。", "ウレしい知らせだ。", "どっせい！"],
    )
    def test_a_stray_kana_the_tagger_reads_as_suru_is_no_card(self, text):
        assert "する" not in _fronts(text)

    @pytest.mark.parametrize(
        "text",
        [
            "約束をし 家に帰った。",
            "準備をし、出発した。",
            "いいかげんにせい！",
            "どれにしょうかな。",
            "何をしに来たの？",
        ],
    )
    def test_suru_in_its_own_frame_is_mined(self, text):
        assert "する" in _fronts(text)
