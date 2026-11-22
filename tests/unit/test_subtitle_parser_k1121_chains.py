"""Session K1121, lane CHAINS: where a mined word's highlight ends (the chain-end resolver)."""

from __future__ import annotations

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.deinflection import (
    condition_flags_from_rules,
    conditions_match,
    find_highlight_end_with_trace,
    get_chain_end_deinflector,
    get_highlight_deinflector,
    resolve_dictionary_form,
)
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


def _chain_text(text: str, surface: str) -> str:
    """The text the chain-end resolver covers for the raw token ``surface`` of the line."""
    _stored, raw_tokens, _merged, *_ = _service()._build_line_state(text, 0.0, 0.0)
    token = next(token for token in raw_tokens if token.surface == surface)
    start = text.index(surface)
    end, _chain = find_highlight_end_with_trace(text, raw_tokens, start, start + len(surface), token, chain_end=True)
    return text[start:end]


def _reaches(deinflector, inflected: str, base: str) -> bool:
    return any(result.text == base for result in deinflector.transform(inflected))


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


class TestKansaiChainEnds:
    """Forms the benchmark's own table puts inside the unit; ours knows them for the END only."""

    @pytest.mark.parametrize(
        ("text", "surface", "highlighted"),
        [
            ("あいつは動いとんのか", "動い", "動いとん"),
            ("全然おもろない", "おもろ", "おもろない"),
            ("先に行きゃがった", "行きゃ", "行きゃがった"),
            ("取るんだ", "取る", "取る"),
            ("銭の絡まん話だ", "絡ま", "絡まん"),
        ],
    )
    def test_where_the_chain_ends(self, text, surface, highlighted):
        assert _chain_text(text, surface) == highlighted

    def test_the_card_highlight_follows(self):
        assert _spans("あいつは動いとんのか")["動く"] == "動いとん"
        assert _spans("銭の絡まん話だ")["絡む"] == "絡まん"

    @pytest.mark.parametrize(
        ("inflected", "base"),
        [
            ("おもろない", "おもろい"),
            ("こよ", "くる"),
            ("しよ", "する"),
            ("行きゃがる", "行く"),
        ],
    )
    def test_the_supplements_are_in_the_end_table_only(self, inflected, base):
        assert _reaches(get_chain_end_deinflector(), inflected, base)
        assert not _reaches(get_highlight_deinflector(), inflected, base)

    @pytest.mark.parametrize(("surface", "base", "other"), [("絡まん", "絡む", "絡まる"), ("下さん", "下す", "下さる")])
    def test_a_front_is_never_resolved_through_them(self, surface, base, other):
        # Both verbs are godan entries; the front table reaches the other one
        # only as an ichidan verb (its own negative ん), which the entry refuses.
        godan = condition_flags_from_rules("v5")

        def rules_lookup(candidates):
            return {text for text, mask in candidates if text in (base, other) and conditions_match(mask, godan)}

        assert resolve_dictionary_form(surface, base, rules_lookup) == base


class TestSpokenChainEnds:
    """The END table's own rows: ませぬ / まへん, かない, どる, りゃあ / きゃあ."""

    @pytest.mark.parametrize(
        ("text", "front", "highlighted"),
        [
            ("敵を恐れませぬ", "恐れる", "恐れませぬ"),
            ("言いたかないけど", "言う", "言いたかない"),
            ("大阪に住んどった", "住む", "住んどった"),
            ("酒を飲んどる", "飲む", "飲んどる"),
            ("本を読んどるんや", "読む", "読んどる"),
            ("見てりゃあいいんだ", "見る", "見てりゃあ"),
            ("そりゃあ見りゃあわかる", "見る", "見りゃあ"),  # the あ follows the mined token itself
            ("別に悪かない", "悪い", "悪かない"),
        ],
    )
    def test_the_chain_runs_through_the_form(self, text, front, highlighted):
        assert _spans(text)[front] == highlighted

    @pytest.mark.parametrize(
        ("text", "surface", "highlighted"),
        [
            ("行かないよ", "行か", "行かない"),
            ("はかない夢だ", "はかない", "はかない"),
            ("おっかない顔", "おっかない", "おっかない"),
            ("あいつはもう帰ったかねえ", "帰っ", "帰った"),  # a past and a question
            ("もう食べたかねえ", "食べ", "食べた"),  # the same particles to the tagger: its reading stands
        ],
    )
    def test_where_other_chains_end(self, text, surface, highlighted):
        assert _chain_text(text, surface) == highlighted

    def test_an_end_that_would_change_the_front_is_not_taken(self):
        # 存じ resolves the front 存じる. The front table cannot undo ませぬ, so
        # from 存じませぬ it would fall back to 存ずる: the card keeps its front
        # and, for that, its shorter highlight.
        text = "存じませぬ"
        assert _chain_text(text, "存じ") == text
        service = SubtitleParserService(
            AnkiMinerConfig(),
            term_lookup=lambda forms: {form for form in forms if form == "存じる"},
            term_rules_lookup=lambda candidates: {form for form, _mask in candidates if form == "存じる"},
        )
        words, _index, _counts = service.parse_text_units(
            [ReadingUnit(text=text, index=0, location_label="t")], want_line_index=False
        )
        assert [(word.mined_form, text[word.surface_start : word.bold_end]) for word in words] == [("存じる", "存じ")]


class TestPotentialImperativeYo:
    """行けよ is 行け and the particle, though the tagger reads one word of the potential verb."""

    @pytest.mark.parametrize(
        ("text", "front", "highlighted"),
        [
            ("早く行けよ", "行く", "行け"),
            ("ここで待てよ", "待つ", "待て"),
            ("黙れよ", "黙る", "黙れ"),
            ("だったら死ねよ", "死ぬ", "死ね"),
            ("はっきり言えよ", "言う", "言え"),  # to the tagger a volitional of 言える
        ],
    )
    def test_the_verb_ends_before_the_particle(self, text, front, highlighted):
        assert _spans(text)[front] == highlighted

    def test_the_token_is_cut_on_the_raw_stream(self):
        _stored, raw_tokens, _merged, *_ = _service()._build_line_state("早く行けよ", 0.0, 0.0)
        assert [token.surface for token in raw_tokens] == ["早く", "行け", "よ"]
        verb, particle = raw_tokens[1].feature, raw_tokens[2].feature
        assert (verb.cType, verb.cForm, verb.orthBase) == ("五段-カ行", "命令形", "行く")
        assert (verb.kana, verb.kanaBase, verb.lemma) == ("イケ", "イク", "行く")
        assert (particle.pos1, particle.pos2, particle.kana) == ("助詞", "終助詞", "ヨ")

    def test_the_sentence_reads_as_before(self):
        words, _index, _counts = _service().parse_text_units(
            [ReadingUnit(text="早く行けよ", index=0, location_label="t")], want_line_index=False
        )
        word = next(word for word in words if word.mined_form == "行く")
        assert (word.surface, word.sentence_furigana, word.sentence_reading) == (
            "行け",
            "早[はや]く 行[い]けよ",
            "はやくいけよ",
        )

    @pytest.mark.parametrize(
        ("text", "front", "highlighted"),
        [
            ("答えよ", "答える", "答えよ"),
            ("見よ", "見る", "見よ"),
            ("勉強を続けよ", "続ける", "続けよ"),
            ("早く開けよ", "開ける", "開けよ"),  # to the tagger the ichidan 開ける, as 続けよ is
            ("全部忘れよ", "忘れる", "忘れよ"),
            ("今すぐ会わせよ", "会う", "会わせよ"),  # せよ: a tail token in 命令形, the causative's own form
        ],
    )
    def test_an_ichidan_yo_form_stays_whole(self, text, front, highlighted):
        assert _spans(text)[front] == highlighted

    def test_a_tail_token_in_yo_is_read_as_the_particle_is(self):
        assert _chain_text("もうやめとけよ", "やめ") == "やめとけ"
