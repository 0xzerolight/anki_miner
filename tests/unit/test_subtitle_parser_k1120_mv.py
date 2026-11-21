"""Verb and adjective forms, and readings (session K1120, lane MV).

The parser tests run the real parser wired to the benchmark's fixture
dictionary; the helper tests need no tagger.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anki_miner.services.deinflection import resolve_dictionary_form
from anki_miner.services.morphology import rejoin_adjective_stem_cries, resolve_reading_override_in_line

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


needs_tagger = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")


def _words(sentence: str) -> dict:
    """Every word mined from *sentence*, by card front."""
    from anki_miner.models.reading import ReadingUnit
    from scripts.parse_benchmark import _get_anchor_service

    unit = ReadingUnit(text=sentence, index=0, location_label="test")
    words, _index, _counts = _get_anchor_service().parse_text_units([unit], want_line_index=False)
    return {w.mined_form: w for w in words}


class TestDigitFirstDayReading:
    """１日 reads いちにち unless a month stands directly before it."""

    def test_the_row_applies_away_from_a_month(self) -> None:
        assert resolve_reading_override_in_line("１日", "ついたち", "今日も") == "いちにち"
        assert resolve_reading_override_in_line("１日", "ついたち", "") == "いちにち"

    def test_the_row_is_skipped_after_a_month(self) -> None:
        assert resolve_reading_override_in_line("１日", "ついたち", "３月") is None
        assert resolve_reading_override_in_line("１日", "ついたち", "毎月") is None

    def test_other_rows_ignore_the_line(self) -> None:
        assert resolve_reading_override_in_line("一日", "ついたち", "三月") == "いちにち"
        assert resolve_reading_override_in_line("仏", "ふつ", "月") == "ほとけ"
        assert resolve_reading_override_in_line("猫", "ねこ", "") is None

    @needs_tagger
    @pytest.mark.parametrize("sentence", ["今日も１日 頑張ろう", "あと１日しかない", "１日２回飲む"])
    def test_one_day_cards_and_sentence_read_ichinichi(self, sentence: str) -> None:
        word = _words(sentence)["１日"]
        assert word.expression_reading == "いちにち"
        assert word.expression_furigana == "１日[いちにち]"
        assert "１日[いちにち]" in word.sentence_furigana

    @needs_tagger
    def test_a_date_keeps_tsuitachi(self) -> None:
        word = _words("３月１日に会おう")["１日"]
        assert word.expression_reading == "ついたち"
        assert "１日[ついたち]" in word.sentence_furigana


def _adjectives(*listed: str):
    """A form lookup whose dictionary lists *listed* as i-adjectives and 嫌い, 楽 as other words."""

    def lookup(terms: list[str]) -> dict[str, list[tuple[str, str]]]:
        rows = {term: [("", "adj-i")] for term in terms if term in listed}
        rows.update({term: [("", "n adj-na")] for term in terms if term in ("嫌い", "楽")})
        return rows

    return lookup


_LISTED = ("熱い", "寒い", "早い", "重い", "痛い", "高い", "遅い", "臭い", "全い")


@needs_tagger
class TestAdjectiveStemCries:
    """A kanji + っ cry cards the adjective, not the one-kanji noun or a rare verb."""

    @staticmethod
    def _retag(sentence: str, *listed: str) -> list:
        import fugashi

        tagger = fugashi.Tagger()
        return rejoin_adjective_stem_cries(list(tagger(sentence)), tagger, _adjectives(*(listed or _LISTED)))

    @pytest.mark.parametrize(
        ("sentence", "surface", "base", "kana"),
        [
            ("熱っ！", "熱", "熱い", "アツ"),
            ("寒っ", "寒", "寒い", "サム"),
            ("熱ッ 何これ", "熱", "熱い", "アツ"),
            ("痛っ！", "痛", "痛い", "イタ"),
            ("高っ…", "高", "高い", "タカ"),
            ("重っ、何これ", "重っ", "重い", "オモッ"),
            ("早っ", "早っ", "早い", "ハヤッ"),
            ("臭っ", "臭っ", "臭い", "クサッ"),
            ("痛ぇな", "痛", "痛い", "イタ"),
            ("遅ぇよ", "遅", "遅い", "オソ"),
        ],
    )
    def test_the_cry_becomes_the_adjective_stem(self, sentence: str, surface: str, base: str, kana: str) -> None:
        token = self._retag(sentence)[0]
        feature = token.feature
        assert (token.surface, feature.pos1, feature.orthBase, feature.cForm) == (surface, "形容詞", base, "語幹-一般")
        assert (feature.kana, feature.kanaBase) == (kana, kana.rstrip("ッ") + "イ")

    def test_the_text_is_not_recut(self) -> None:
        tokens = self._retag("熱っ 熱っ！")
        assert "".join(t.surface for t in tokens) == "熱っ熱っ！"
        assert tokens[2].white_space == " "
        assert tokens[2].feature.orthBase == "熱い"

    @pytest.mark.parametrize(
        "sentence",
        [
            "何っ！",  # no adjective 何い
            "嘘っ",
            "嫌っ！",  # 嫌い is listed, but not as an i-adjective
            "楽っ！",  # an adjective to the tagger only
            "全っ然 違う",  # the mark is inside a word: no pause after it
            "言っ…",  # a verb cut off: 言い is no adjective
            "熱っぽい",
            "熱がある",
        ],
    )
    def test_other_shapes_pass_through(self, sentence: str) -> None:
        import fugashi

        tagger = fugashi.Tagger()
        tokens = list(tagger(sentence))
        assert rejoin_adjective_stem_cries(tokens, tagger, _adjectives(*_LISTED)) == tokens

    def test_no_dictionary_no_change(self) -> None:
        import fugashi

        tagger = fugashi.Tagger()
        tokens = list(tagger("熱っ！"))
        assert rejoin_adjective_stem_cries(tokens, tagger, None) is tokens

    @pytest.mark.parametrize(
        ("sentence", "front", "reading"),
        [("熱っ！", "熱い", "あつい"), ("早っ", "早い", "はやい"), ("痛ぇな", "痛い", "いたい")],
    )
    def test_the_card_is_the_adjective(self, sentence: str, front: str, reading: str) -> None:
        from anki_miner.config.config import AnkiMinerConfig
        from anki_miner.models.reading import ReadingUnit
        from anki_miner.services.subtitle_parser import SubtitleParserService

        parser = SubtitleParserService(AnkiMinerConfig(), form_lookup=_adjectives(*_LISTED))
        unit = ReadingUnit(text=sentence, index=0, location_label="test")
        words, _index, _counts = parser.parse_text_units([unit], want_line_index=False)
        assert [(w.mined_form, w.expression_reading) for w in words] == [(front, reading)]
        assert words[0].sentence_furigana.startswith(f"{front[0]}[{reading[:-1]}]")


class TestAdjectiveGaruVerb:
    """A kana adjective + がる span cards the がる verb the dictionary lists as a reading."""

    @staticmethod
    def _resolve(
        surface: str, orth_base: str, terms: set[str], common: set[str], readings: frozenset[str] = frozenset()
    ):
        return resolve_dictionary_form(
            surface,
            orth_base,
            lambda candidates: {text for text, _conditions in candidates if text in terms},
            lambda found: {term: term in common for term in found},
            kana_lookup=lambda fronts: {front: front in readings for front in fronts},
        )

    def test_a_kana_garu_verb_is_attested_by_its_reading(self) -> None:
        readings = frozenset({"かわいがる"})
        assert self._resolve("かわいがって", "かわいい", {"かわいい"}, {"かわいい"}, readings) == "かわいがる"

    def test_no_dictionary_verb_keeps_the_adjective(self) -> None:
        assert self._resolve("うらやましがられる", "うらやましい", {"うらやましい"}, {"うらやましい"}) == "うらやましい"
        # A kanji spelling is never taken on a reading alone.
        readings = frozenset({"怪しがる"})
        assert self._resolve("怪しがられて", "怪しい", {"怪しい"}, {"怪しい"}, readings) == "怪しい"

    def test_the_commonness_filter_still_prunes_a_rare_twin(self) -> None:
        assert self._resolve("呼ばれた", "呼ぶ", {"呼ぶ", "呼ばる"}, {"呼ぶ"}) == "呼ぶ"
        # A span that is not the がる verb adds nothing to the pool.
        assert (
            self._resolve("かわいくて", "かわいい", {"かわいい"}, {"かわいい"}, frozenset({"かわいがる"})) == "かわいい"
        )
