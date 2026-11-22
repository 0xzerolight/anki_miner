"""Session K1121, lane CHAINS: where a mined word's highlight ends (the chain-end resolver)."""

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


def _service(terms: frozenset[str] = frozenset()) -> SubtitleParserService:
    if not terms:
        return SubtitleParserService(AnkiMinerConfig())
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda forms: {form for form in forms if form in terms},
        kana_attest_lookup=lambda forms: {form: form in terms for form in forms},
    )


def _spans(text: str, terms: frozenset[str] = frozenset()) -> dict[str, str]:
    """Each card front of the line with the text its highlight covers."""
    words, _index, _counts = _service(terms).parse_text_units(
        [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
    )
    return {word.mined_form: text[word.surface_start : word.bold_end] for word in words}


class TestSentenceFinalYo:
    """A sentence-final よ is not the よ of an imperative the speaker never said."""

    @pytest.mark.parametrize(
        ("text", "front", "highlighted"),
        [
            ("みんなで食べてよ", "食べる", "食べて"),  # not the imperative of 食べてる
            ("教えてくれよ", "教える", "教えてくれ"),  # not the imperative of 教えてくれる
            ("ちゃんと見ててよ", "見る", "見てて"),
            ("もう寝てよ", "寝る", "寝て"),
        ],
    )
    def test_the_chain_ends_before_the_particle(self, text, front, highlighted):
        assert _spans(text)[front] == highlighted

    @pytest.mark.parametrize(
        ("text", "front", "highlighted"),
        [
            ("一緒に捕まえよ", "捕まえる", "捕まえよ"),  # the verb's own よ form
            ("今すぐ会わせよ", "会う", "会わせよ"),
            ("早く食べろよ", "食べる", "食べろ"),
            ("こっちに来いよ", "来る", "来い"),
            ("遊んでやがんな", "遊ぶ", "遊んでやがんな"),  # the prohibitive な stays inside
            ("朝ごはん作ろっか", "作る", "作ろっか"),
            ("覚えてろよ", "覚える", "覚えてろ"),
            ("ちょっと待ってねぇ", "待つ", "待ってねぇ"),  # ねぇ is not gated: mostly the negative
            ("何も聞いてねえ", "聞く", "聞いてねえ"),
            ("話を聞いてねえよ", "聞く", "聞いてねえ"),
            ("俺は知らねえ", "知る", "知らねえ"),
            ("行かないよ", "行く", "行かない"),
        ],
    )
    def test_other_chains_end_where_they_did(self, text, front, highlighted):
        assert _spans(text)[front] == highlighted

    def test_the_end_the_front_is_resolved_from_keeps_the_particle(self):
        # _find_highlight_end is today's window: the interjection test and the
        # front resolver read it, and neither may start to depend on the gate.
        service = _service()
        text = "みんなで食べてよ"
        _stored, raw_tokens, merged_tokens, *_ = service._build_line_state(text, 0.0, 0.0)
        token = next(token for token in merged_tokens if token.surface == "食べ")
        start = text.index("食べ")
        assert service._find_highlight_end(text, raw_tokens, start, start + 2, token) == len(text)
        assert service._emission_highlight_end(text, raw_tokens, start, start + 2, token) == len(text) - 1
