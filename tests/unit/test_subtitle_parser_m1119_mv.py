"""Word spans and verb and adjective forms (Stage M, lane MV).

The spans run through the real parser wired to the benchmark's fixture
dictionary; the helper tests need no tagger.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.services.deinflection import chain_spelling, stretched_highlight_end
from anki_miner.services.subtitle_parser import SubtitleParserService

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


def _highlights(sentence: str) -> set[tuple[str, str]]:
    """``(highlighted text, card front)`` of every word mined from *sentence*."""
    from anki_miner.models.reading import ReadingUnit
    from scripts.parse_benchmark import _get_anchor_service

    unit = ReadingUnit(text=sentence, index=0, location_label="test")
    words, _index, _counts = _get_anchor_service().parse_text_units([unit], want_line_index=False)
    return {(w.sentence[w.surface_start : w.bold_end], w.mined_form) for w in words}


class TestStretchedHighlightEnd:
    """Stretch marks written right after a word belong to its highlight."""

    @pytest.mark.parametrize(
        ("text", "end", "expected"),
        [
            ("潜るー", 2, 3),
            ("勝ったぁーー！", 3, 6),
            ("放てぇっ！", 2, 4),
            ("嘘ッ", 1, 2),
            ("先輩ー！", 2, 3),
            ("すご〜い", 2, 3),
            ("潜る", 2, 2),
            ("潜るぞー", 2, 2),
        ],
    )
    def test_marks_after_the_word_are_covered(self, text: str, end: int, expected: int) -> None:
        assert stretched_highlight_end(text, end) == expected

    def test_sokuon_before_kana_belongs_to_the_next_word(self) -> None:
        assert stretched_highlight_end("潜るって言った", 2) == 2
        assert stretched_highlight_end("潜るーって言った", 2) == 3

    def test_wave_dash_after_kanji_or_a_digit_is_a_range(self) -> None:
        assert stretched_highlight_end("十時〜十二時", 2) == 2

    def test_out_of_range_end_is_returned_as_is(self) -> None:
        assert stretched_highlight_end("潜る", 0) == 0
        assert stretched_highlight_end("潜る", 5) == 5


class TestChainSpelling:
    """The resolver deinflects the span without the marks the table cannot undo."""

    def test_trailing_marks_are_removed(self) -> None:
        assert chain_spelling("感じたぁー", 2) == "感じた"
        assert chain_spelling("潜るー", 2) == "潜る"
        assert chain_spelling("勝ったっ", 2) == "勝った"

    def test_a_contracted_ending_the_table_knows_is_kept(self) -> None:
        assert chain_spelling("感じねぇ", 2) == "感じねぇ"
        assert chain_spelling("感じねぇー", 2) == "感じねぇ"
        assert chain_spelling("放てぇっ", 2) == "放てぇ"

    def test_long_mark_after_an_e_row_ending_reads_as_the_vowel(self) -> None:
        assert chain_spelling("食べねー", 2) == "食べねえ"

    def test_the_token_itself_is_never_cut(self) -> None:
        assert chain_spelling("行っ", 2) == "行っ"
        assert chain_spelling("歩いた", 2) == "歩いた"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestStretchedWordSpans:
    @pytest.mark.parametrize(
        ("sentence", "highlight", "front"),
        [
            ("先輩ー！", "先輩ー", "先輩"),
            ("それは嘘っ！", "嘘っ", "嘘"),
            ("お前ー", "お前ー", "お前"),
            ("本当ぉ？", "本当ぉ", "本当"),
        ],
    )
    def test_stretch_marks_join_the_highlight(self, sentence: str, highlight: str, front: str) -> None:
        assert (highlight, front) in _highlights(sentence)

    def test_sokuon_of_the_next_word_stays_outside(self) -> None:
        assert ("嘘", "嘘") in _highlights("嘘って言った")


def _kana_part_records() -> list[dict]:
    from scripts.parse_benchmark import DEFAULT_CORPUS_DIR, load_corpus

    return [r for r in load_corpus(DEFAULT_CORPUS_DIR) if r["category"] == "kana-part-front"]


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
@pytest.mark.parametrize("record", _kana_part_records(), ids=lambda r: r["id"])
def test_kana_part_front_fixture(record: dict) -> None:
    from scripts.parse_benchmark import mine_lite_anchor

    assert mine_lite_anchor(record["sentence"]) == set(record["expected"])


def _verb_token(surface: str, orth_base: str, lemma: str, reading: str) -> MagicMock:
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = "動詞"
    token.feature.pos2 = "一般"
    token.feature.lemma = lemma
    token.feature.orthBase = orth_base
    token.feature.cType = "下一段-ガ行"
    token.feature.cForm = "連用形-一般"
    token.feature.lForm = reading
    token.feature.kanaBase = reading
    return token


def _service(test_config, attested: set[str]) -> SubtitleParserService:
    def rules_lookup(candidates: list[tuple[str, int]]) -> set[str]:
        return {text for text, _mask in candidates if text in attested}

    with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
        return SubtitleParserService(
            test_config,
            term_lookup=lambda terms: {t for t in terms if t in attested},
            term_rules_lookup=rules_lookup,
        )


class TestKanaPartFront:
    """A front that writes part of the word in kana, and that no dictionary lists, cards as the lemma."""

    def _front(self, service: SubtitleParserService, token: MagicMock) -> str:
        text = token.surface + "た"
        return service._resolve_front(token, token.feature.orthBase, text, 0, len(text))

    def test_unlisted_spelling_cards_as_the_listed_lemma(self, test_config) -> None:
        service = _service(test_config, {"積み上げる"})
        token = _verb_token("つみ上げ", "つみ上げる", "積み上げる", "ツミアゲル")
        assert self._front(service, token) == "積み上げる"

    def test_listed_spelling_stays(self, test_config) -> None:
        service = _service(test_config, {"つみ上げる", "積み上げる"})
        token = _verb_token("つみ上げ", "つみ上げる", "積み上げる", "ツミアゲル")
        assert self._front(service, token) == "つみ上げる"

    def test_unlisted_lemma_is_no_target(self, test_config) -> None:
        service = _service(test_config, set())
        token = _verb_token("つみ上げ", "つみ上げる", "積み上げる", "ツミアゲル")
        assert self._front(service, token) == "つみ上げる"

    def test_okurigana_variant_keeps_the_source_spelling(self, test_config) -> None:
        service = _service(test_config, {"変わる"})
        token = _verb_token("変っ", "変る", "変わる", "カワル")
        token.feature.cType = "五段-ラ行"
        assert self._front(service, token) == "変る"

    def test_lemma_with_other_kanji_is_no_target(self, test_config) -> None:
        service = _service(test_config, {"取り替える"})
        token = _verb_token("とり換え", "とり換える", "取り替える", "トリカエル")
        assert self._front(service, token) == "とり換える"

    def test_katakana_part_keeps_the_source_spelling(self, test_config) -> None:
        service = _service(test_config, {"打っ飛ばす"})
        token = _verb_token("ブッ飛ばし", "ブッ飛ばす", "打っ飛ばす", "ブットバス")
        token.feature.cType = "五段-サ行"
        assert self._front(service, token) == "ブッ飛ばす"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestKatakanaStemSpans:
    """A verb stem written in katakana takes its whole inflected form as the highlight."""

    def test_passive_after_a_katakana_stem(self) -> None:
        assert ("ヤラれた", "やる") in _highlights("あいつにヤラれた")

    def test_mixed_spelling_still_extends(self) -> None:
        assert ("ハマった", "ハマる") in _highlights("罠にハマった")


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestTeRannaiChain:
    """てらんない (ていられない) is part of the verb's chain, and its ない is no word."""

    @pytest.mark.parametrize(
        ("sentence", "highlight", "front"),
        [
            ("もう見てらんないよ", "見てらんない", "見る"),
            ("こんなの食ってらんねえ", "食ってらんねえ", "食う"),
            ("黙ってらんなかった", "黙ってらんなかった", "黙る"),
            ("酒なんか飲んでらんない", "飲んでらんない", "飲む"),
        ],
    )
    def test_chain_runs_through_ran(self, sentence: str, highlight: str, front: str) -> None:
        mined = _highlights(sentence)
        assert (highlight, front) in mined
        assert "ない" not in {front for _highlight, front in mined}


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestContractedAdjectives:
    """An -ee adjective the tagger cuts into particles and sounds is read as the adjective."""

    @pytest.mark.parametrize(
        ("sentence", "highlight", "front"),
        [
            ("こいつはやべー", "やべー", "やばい"),
            ("本当にやべぇな", "やべぇ", "やばい"),
            ("あの家でけーな", "でけー", "でかい"),
            ("この家でっけぇ", "でっけぇ", "でっかい"),
            ("あの人かっけー", "かっけー", "かっこいい"),
            ("やべーー", "やべーー", "やばい"),
        ],
    )
    def test_contracted_adjective_is_mined(self, sentence: str, highlight: str, front: str) -> None:
        assert (highlight, front) in _highlights(sentence)

    def test_plain_tokens_are_left_alone(self) -> None:
        assert not any(front == "うざい" for _highlight, front in _highlights("早く行こうぜー"))


def _classical_compound_records() -> list[dict]:
    from scripts.parse_benchmark import DEFAULT_CORPUS_DIR, load_corpus

    return [r for r in load_corpus(DEFAULT_CORPUS_DIR) if r["id"] in ("mv05", "mv06")]


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
@pytest.mark.parametrize("record", _classical_compound_records(), ids=lambda r: r["id"])
def test_classical_adjective_compound_fixture(record: dict) -> None:
    from scripts.parse_benchmark import mine_lite_anchor

    assert mine_lite_anchor(record["sentence"]) == set(record["expected"])


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_classical_adjective_compound_highlights_the_whole_word() -> None:
    assert ("誇り高き", "誇り高い") in _highlights("誇り高き戦士")


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestStretchedVerbSpans:
    @pytest.mark.parametrize(
        ("sentence", "highlight", "front"),
        [
            ("海に潜るー", "潜るー", "潜る"),
            ("矢を放てぇっ！", "放てぇっ", "放つ"),
            ("やっと歩いたぁ", "歩いたぁ", "歩く"),
            ("試合に勝ったぁーー！", "勝ったぁーー", "勝つ"),
            ("返事が遅いー", "遅いー", "遅い"),
            ("痛みを感じたぁ", "感じたぁ", "感じる"),
            ("もう食べねーよ", "食べねー", "食べる"),
        ],
    )
    def test_stretch_marks_join_the_chain(self, sentence: str, highlight: str, front: str) -> None:
        assert (highlight, front) in _highlights(sentence)

    def test_quoting_tte_is_not_a_stretch(self) -> None:
        assert ("潜る", "潜る") in _highlights("海に潜るって言った")

    def test_particle_with_a_long_mark_stays_outside(self) -> None:
        assert ("待って", "待つ") in _highlights("ここで待ってねー")


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_stretched_katakana_stem_chain() -> None:
    assert ("ヤラれたぁ", "やる") in _highlights("あいつにヤラれたぁ")
