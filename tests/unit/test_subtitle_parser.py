"""Tests for subtitle_parser module."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils import generate_furigana, generate_reading
from anki_miner.utils.text_utils import wrap_target_furigana

# --- Helpers for building mock MeCab tokens ---


def _make_token(surface, pos1, pos2=None, lemma=None, kana=None, orth_base=None):
    """Build a mock fugashi word token with feature attributes.

    ``orthBase`` defaults to the lemma (real UniDic tokens usually agree);
    it must always be set explicitly — an auto-created MagicMock attribute
    is truthy and would leak into ``mined_form``.
    """
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    token.feature.orthBase = orth_base if orth_base is not None else token.feature.lemma
    return token


def _make_token_no_feature(surface):
    """Build a mock token that raises AttributeError on feature access."""
    token = MagicMock()
    token.surface = surface
    token.feature = MagicMock(
        spec=[],  # empty spec → attribute access raises AttributeError
    )
    type(token.feature).pos1 = PropertyMock(side_effect=AttributeError)
    type(token.feature).pos2 = PropertyMock(side_effect=AttributeError)
    type(token.feature).lemma = PropertyMock(side_effect=AttributeError)
    type(token.feature).kana = PropertyMock(side_effect=AttributeError)
    type(token.feature).orthBase = PropertyMock(side_effect=AttributeError)
    return token


class CountingSpy:
    """Callable tagger wrapper that records every text argument it receives.

    Delegates each call to the real tagger so results are identical to
    production.  Used by T2 call-count tests to assert that each subtitle
    line triggers exactly one ``tagger(text)`` call.
    """

    def __init__(self, real_tagger):
        self.real_tagger = real_tagger
        self.calls: list[str] = []

    def __call__(self, text: str):
        self.calls.append(text)
        return self.real_tagger(text)


class TestParseSubtitleFile:
    """Tests for parse_subtitle_file method."""

    def test_file_not_found_raises_subtitle_parse_error(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            service = SubtitleParserService(test_config)

        with pytest.raises(SubtitleParseError, match="not found"):
            service.parse_subtitle_file(Path("/nonexistent/file.ass"))

    def test_parse_failure_raises_subtitle_parse_error(self, test_config, tmp_path):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            service = SubtitleParserService(test_config)

        bad_file = tmp_path / "bad.ass"
        bad_file.write_text("not valid subtitle data!!!", encoding="utf-8")

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                side_effect=Exception("parse error"),
            ),
            pytest.raises(SubtitleParseError, match="Failed to parse"),
        ):
            service.parse_subtitle_file(bad_file)

    def test_parses_words_from_lines(self, test_config, tmp_path):
        """Should extract TokenizedWord objects from subtitle lines."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        # Build mock subtitle lines
        mock_line = MagicMock()
        mock_line.text = "食べる"
        mock_line.start = 1000  # 1 second in ms
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        word_token = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [word_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1
        assert words[0].lemma == "食べる"
        assert words[0].reading == "タベル"
        assert words[0].start_time == 1.0
        assert words[0].end_time == 3.0
        assert words[0].duration == 2.0
        assert words[0].expression_furigana != ""
        assert words[0].sentence_furigana != ""

    def test_applies_subtitle_offset(self, tmp_path):
        """Subtitle offset should shift timing."""
        config = AnkiMinerConfig(
            subtitle_offset=5.0,
            media_temp_folder=tmp_path / "media",
        )
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "勉強する"
        mock_line.start = 2000
        mock_line.end = 4000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        word_token = _make_token("勉強", "名詞", lemma="勉強", kana="ベンキョウ")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [word_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1
        assert words[0].start_time == pytest.approx(7.0)  # 2.0 + 5.0
        assert words[0].end_time == pytest.approx(9.0)  # 4.0 + 5.0

    def test_deduplicates_by_lemma(self, test_config, tmp_path):
        """Same lemma appearing twice should only produce one word."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = "食べる"
        line1.start = 1000
        line1.end = 3000

        line2 = MagicMock()
        line2.text = "食べた"
        line2.start = 4000
        line2.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        # Both tokens have same lemma
        token1 = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        token2 = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ")

        mock_tagger = MagicMock()
        # T2: sentence-level furigana/reading use raw_tokens (no extra tagger
        # calls). Per-line: 1 tokenize. Per emitted word: expression_furigana
        # + expression_reading. Line 1 emits a word → 3 tagger calls. Line 2
        # dedup-skips after tokenize → 1 tagger call. Total: 4.
        mock_tagger.side_effect = [
            [token1],  # line 1: _iter_parsed_lines tokenize
            [token1],  # line 1: expression_furigana (mined)
            [token1],  # line 1: expression_reading (mined)
            [token2],  # line 2: _iter_parsed_lines tokenize (then dedup skip)
        ]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1

    def test_distinct_lemmas_not_deduped_when_surface_equal(self, test_config, tmp_path):
        """Lemma-only dedup: same surface with different lemmas stays as two entries.

        After the Issue #19 cleanup, dedup is keyed on lemma alone — shared
        surface no longer collapses entries with distinct dictionary forms.
        """
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = "学生です"
        line1.start = 1000
        line1.end = 3000

        line2 = MagicMock()
        line2.text = "学生だ"
        line2.start = 4000
        line2.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        token1 = _make_token("学生", "名詞", lemma="学生", kana="ガクセイ")
        token2 = _make_token("学生", "名詞", lemma="学生X", kana="ガクセイ")

        mock_tagger = MagicMock()
        # T2: sentence-level calls use raw_tokens. Each line: 1 tokenize + 2
        # expression-level = 3 tagger calls. Two lines → 6.
        mock_tagger.side_effect = [
            [token1],  # line 1: tokenize
            [token1],  # line 1: expression_furigana (mined)
            [token1],  # line 1: expression_reading (mined)
            [token2],  # line 2: tokenize
            [token2],  # line 2: expression_furigana (mined)
            [token2],  # line 2: expression_reading (mined)
        ]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 2
        assert {w.lemma for w in words} == {"学生", "学生X"}

    def test_skips_empty_cleaned_text(self, test_config, tmp_path):
        """Lines that clean to empty should be skipped."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "{\\an8}  "
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        mock_tagger = MagicMock()

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.clean_subtitle_text", return_value=""),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 0
        mock_tagger.assert_not_called()

    def test_sentence_furigana_computed_once_per_line(self, test_config, tmp_path):
        """Regression: sentence_furigana / sentence_reading are line-level, not word-level.

        T2: sentence-level annotation uses generate_furigana_from_tokens /
        generate_reading_from_tokens (no extra tagger calls). The tagger is
        called exactly ONCE per non-empty line (the _iter_parsed_lines call)
        regardless of how many words are emitted from that line. Per-word
        generate_furigana(mined) calls pass a single-word string, not the full
        sentence text. Guards against re-introducing per-word redundant
        MeCab passes.
        """
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "猫と犬と鳥"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        token1 = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        token2 = _make_token("犬", "名詞", lemma="犬", kana="イヌ")
        token3 = _make_token("鳥", "名詞", lemma="鳥", kana="トリ")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [token1, token2, token3]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 3
        # T2: tagger called only for tokenize (1 per line) + expression-level
        # (1 per emitted word × 2 for furigana+reading). 1 + 3×2 = 7 total.
        # Critically, the tagger is called only ONCE with the full sentence
        # text (the _iter_parsed_lines tokenize call).
        full_line_calls = [c for c in mock_tagger.call_args_list if c.args and c.args[0] == "猫と犬と鳥"]
        assert len(full_line_calls) == 1, f"Expected exactly 1 full-sentence tagger call; got {len(full_line_calls)}"


class TestExpressionFuriganaSource:
    """ExpressionFurigana source is POS-aware (mirrors TokenizedWord.mined_form).

    Nouns: surface (Issue #5 — unidic 豪腕 → 剛腕 mis-lemma).
    Verbs/adjectives: lemma (Issue #19 — 破れ → 破れる).
    """

    def _run_parse(self, test_config, tmp_path, line_text: str, token):
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = line_text
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch(
                "anki_miner.services.subtitle_parser.generate_furigana",
                return_value="stub",
            ) as mock_furigana,
        ):
            service = SubtitleParserService(test_config)
            service.parse_subtitle_file(sub_file)
        return mock_furigana

    def test_noun_furigana_uses_surface(self, test_config, tmp_path):
        """Noun token: expression furigana generated from surface."""
        token = _make_token("豪腕", "名詞", lemma="剛腕", kana="ゴウワン")
        mock_furigana = self._run_parse(test_config, tmp_path, "彼は豪腕の投手だ", token)
        # T2: sentence-level uses generate_furigana_from_tokens, not generate_furigana.
        # generate_furigana is called once for the expression only.
        called_texts = [c.args[0] for c in mock_furigana.call_args_list]
        assert "豪腕" in called_texts  # expression uses surface
        assert "剛腕" not in called_texts  # not the mis-lemma

    def test_verb_furigana_uses_lemma(self, test_config, tmp_path):
        """Verb token: expression furigana generated from lemma."""
        token = _make_token("破れ", "動詞", lemma="破れる", kana="ヤブレ")
        mock_furigana = self._run_parse(test_config, tmp_path, "胸のとこ破れそう", token)
        called_texts = [c.args[0] for c in mock_furigana.call_args_list]
        assert "破れる" in called_texts  # expression uses lemma
        assert "破れ" not in called_texts  # not the surface form

    def test_verb_furigana_uses_orth_base_not_normalized_lemma(self, test_config, tmp_path):
        """Kanji-variant verb: expression furigana comes from orthBase (乞う), not
        unidic's normalized lemma (請う) — the card must keep the source kanji."""
        token = _make_token("乞わ", "動詞", lemma="請う", kana="コワ", orth_base="乞う")
        mock_furigana = self._run_parse(test_config, tmp_path, "神に祈りを乞われて", token)
        called_texts = [c.args[0] for c in mock_furigana.call_args_list]
        assert "乞う" in called_texts  # expression uses source-orthography dictionary form
        assert "請う" not in called_texts  # not the normalized lemma

    def test_adjective_furigana_uses_orth_base_not_normalized_lemma(self, test_config, tmp_path):
        """Kanji-variant adjective: 淋しい stays 淋しい even when lemma is 寂しい."""
        token = _make_token("淋しかっ", "形容詞", lemma="寂しい", kana="サビシカッ", orth_base="淋しい")
        mock_furigana = self._run_parse(test_config, tmp_path, "淋しかった", token)
        called_texts = [c.args[0] for c in mock_furigana.call_args_list]
        assert "淋しい" in called_texts
        assert "寂しい" not in called_texts


class TestLemmaReading:
    """lemma_reading carries the lemma's OWN reading for the JPod101 audio retry.

    Surface-mined nouns whose surface ≠ lemma must store the lemma reading
    (探す→さがす), not the surface reading (探し→さがし); verbs reuse the
    expression reading because mined_form already IS the lemma.
    """

    def _parse_one(self, test_config, tmp_path, line_text, token, reading_map):
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = line_text
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="stub"),
            patch(
                "anki_miner.services.subtitle_parser.generate_reading",
                side_effect=lambda s, _tagger: reading_map.get(s, s),
            ),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)
        return words[0]

    def test_surface_mined_noun_stores_lemma_reading(self, test_config, tmp_path):
        token = _make_token("探し", "名詞", lemma="探す", kana="サガシ")
        word = self._parse_one(test_config, tmp_path, "鍵を探し", token, {"探し": "さがし", "探す": "さがす"})
        assert word.expression_reading == "さがし"  # surface reading
        assert word.lemma_reading == "さがす"  # lemma's own reading

    def test_verb_reuses_expression_reading(self, test_config, tmp_path):
        token = _make_token("破れ", "動詞", lemma="破れる", kana="ヤブレ")
        word = self._parse_one(test_config, tmp_path, "胸破れそう", token, {"破れる": "やぶれる"})
        # mined_form == lemma for verbs ⇒ lemma_reading reuses expression_reading.
        assert word.expression_reading == "やぶれる"
        assert word.lemma_reading == "やぶれる"

    def test_variant_verb_mines_orth_base_and_keeps_lemma_reading(self, test_config, tmp_path):
        """Kanji-variant verb (orthBase ≠ lemma): Expression fields follow the
        source spelling 乞う while lemma_reading is recomputed from the
        normalized lemma 請う for the JPod101 retry ladder."""
        token = _make_token("乞わ", "動詞", lemma="請う", kana="コワ", orth_base="乞う")
        word = self._parse_one(
            test_config,
            tmp_path,
            "神に祈りを乞われて",
            token,
            {"乞う": "こう-from-orth", "請う": "こう-from-lemma"},
        )
        assert word.mined_form == "乞う"
        assert word.lemma == "請う"
        assert word.expression_reading == "こう-from-orth"
        assert word.lemma_reading == "こう-from-lemma"


class TestFuriganaMemoization:
    """Per-parse memoization of generate_furigana / generate_reading / wrap_target_furigana.

    Task 4: identical input strings within one parse pass must be tagged at most once.
    Cache must reset between separate parse_subtitle_file calls.
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_subs_single_line(self, text: str, start: int = 1000, end: int = 3000):
        mock_line = MagicMock()
        mock_line.text = text
        mock_line.start = start
        mock_line.end = end
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        return mock_subs

    def _make_subs_two_lines(self, text1: str, text2: str):
        def _line(text, start, end):
            m = MagicMock()
            m.text = text
            m.start = start
            m.end = end
            return m

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([_line(text1, 1000, 3000), _line(text2, 4000, 6000)]))
        return mock_subs

    # ------------------------------------------------------------------
    # 1. Repeated mined form across lines → generate_furigana called once
    # ------------------------------------------------------------------

    def test_repeated_expression_furigana_memoized(self, test_config, tmp_path):
        """Same mined form on two lines → generate_furigana called once for that string.

        Both lines have the same verb with lemma 食べる (mined form = lemma for verbs).
        The global lemma dedup means only line-1's word is emitted into all_words,
        but without caching, line-2's word would still trigger generate_furigana("食べる")
        before the dedup check discards it.  With the cache the call is served from
        _fg_cache and generate_furigana itself is not invoked a second time.

        We patch both generate_furigana AND generate_reading (and the tagger, to
        avoid StopIteration from the mock) so the only actual calls observed in
        mock_fg.call_args_list come from the parser's own invocations — not from
        nested tagger usage inside the real util functions.
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        # Two lines; both tokenize to the same verb with lemma 食べる.
        # Distinct sentence text ensures sentence-level calls don't accidentally
        # match "食べる" and inflate the expression-level count.
        taberux2_subs = self._make_subs_two_lines("食べる", "また食べる")
        token_taberu = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        mock_tagger = MagicMock()
        # All tagger calls return [token_taberu]; we only care about generate_furigana calls.
        mock_tagger.return_value = [token_taberu]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=taberux2_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="食べる[たべる]") as mock_fg,
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="たべる"),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        # The mined form for a verb is its lemma: "食べる".
        # With the cache, generate_furigana("食べる", ...) must be called at most once.
        expression_calls = [c for c in mock_fg.call_args_list if c.args[0] == "食べる"]
        assert (
            len(expression_calls) <= 1
        ), f"generate_furigana('食べる') called {len(expression_calls)} times; expected ≤ 1 (memoized)"
        # Sanity: at least one word should have been emitted.
        assert len(words) >= 1

    # ------------------------------------------------------------------
    # 2. Cache reset between two separate parse_subtitle_file calls
    # ------------------------------------------------------------------

    def test_cache_reset_between_parse_calls(self, test_config, tmp_path):
        """generate_furigana must be re-invoked on a second parse_subtitle_file call.

        The per-parse cache must be cleared at the start of each call so a
        second parse (possibly with a different file) is not served stale
        entries from the first parse.
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        token = _make_token("猫", "名詞", lemma="猫", kana="ネコ")

        def make_mock_subs():
            mock_line = MagicMock()
            mock_line.text = "猫"
            mock_line.start = 1000
            mock_line.end = 3000
            ms = MagicMock()
            ms.__iter__ = MagicMock(return_value=iter([mock_line]))
            return ms

        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]

        fg_call_counts: list[int] = []

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", side_effect=lambda _: make_mock_subs()),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="stub") as mock_fg,
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="stub"),
        ):
            service = SubtitleParserService(test_config)

            # First parse
            service.parse_subtitle_file(sub_file)
            fg_call_counts.append(mock_fg.call_count)

            # Second parse — cache must be reset, so generate_furigana is called again
            service.parse_subtitle_file(sub_file)
            fg_call_counts.append(mock_fg.call_count)

        calls_first = fg_call_counts[0]
        calls_second = fg_call_counts[1] - fg_call_counts[0]

        # Each parse must produce at least one generate_furigana call (sentence + expression level).
        assert calls_first >= 1, "First parse did not call generate_furigana"
        assert calls_second >= 1, "Second parse did not call generate_furigana — cache was NOT reset"

    # ------------------------------------------------------------------
    # 3. Same assertions for parse_subtitle_file_with_index
    # ------------------------------------------------------------------

    def test_repeated_expression_furigana_memoized_with_index(self, test_config, tmp_path):
        """Same mined form on two lines → generate_furigana called once (with_index path)."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        taberux2_subs = self._make_subs_two_lines("食べる", "また食べる")
        token_taberu = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token_taberu]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=taberux2_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="食べる[たべる]") as mock_fg,
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="たべる"),
        ):
            service = SubtitleParserService(test_config)
            words, index = service.parse_subtitle_file_with_index(sub_file)

        expression_calls = [c for c in mock_fg.call_args_list if c.args[0] == "食べる"]
        assert (
            len(expression_calls) <= 1
        ), f"generate_furigana('食べる') called {len(expression_calls)} times; expected ≤ 1"
        assert len(words) >= 1

    def test_cache_reset_between_parse_with_index_calls(self, test_config, tmp_path):
        """Cache reset between two parse_subtitle_file_with_index calls."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        token = _make_token("猫", "名詞", lemma="猫", kana="ネコ")

        def make_mock_subs():
            mock_line = MagicMock()
            mock_line.text = "猫"
            mock_line.start = 1000
            mock_line.end = 3000
            ms = MagicMock()
            ms.__iter__ = MagicMock(return_value=iter([mock_line]))
            return ms

        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]

        fg_call_counts: list[int] = []

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", side_effect=lambda _: make_mock_subs()),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="stub") as mock_fg,
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="stub"),
        ):
            service = SubtitleParserService(test_config)

            service.parse_subtitle_file_with_index(sub_file)
            fg_call_counts.append(mock_fg.call_count)

            service.parse_subtitle_file_with_index(sub_file)
            fg_call_counts.append(mock_fg.call_count)

        calls_first = fg_call_counts[0]
        calls_second = fg_call_counts[1] - fg_call_counts[0]

        assert calls_first >= 1, "First parse (with_index) did not call generate_furigana"
        assert calls_second >= 1, "Second parse (with_index) did not call generate_furigana — cache not reset"

    # Note: the bold-furigana memoization test (_bold_cache) was removed when the
    # tokenize-once merge replaced the re-tokenizing _bold() path with the
    # token-based wrap_target_furigana_from_tokens (zero extra MeCab passes), so
    # there is no longer a _bold_cache to exercise.


class TestShouldIncludeWord:
    """Tests for _should_include_word method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    def test_excludes_empty_surface(self, service):
        token = _make_token("", "名詞")
        assert service._should_include_word(token) is False

    def test_excludes_whitespace_surface(self, service):
        token = _make_token("  ", "名詞")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["助詞", "助動詞", "記号", "補助記号"])
    def test_excludes_non_content_pos(self, service, pos1):
        token = _make_token("から", pos1, lemma="から")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["感動詞", "フィラー"])
    def test_excludes_interjections_and_fillers(self, service, pos1):
        token = _make_token("ええ", pos1, lemma="ええ")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("pos1", ["名詞", "動詞", "形容詞", "副詞", "形状詞"])
    def test_includes_content_pos_with_kanji(self, service, pos1):
        token = _make_token("勉強", pos1, lemma="勉強")
        assert service._should_include_word(token) is True

    @pytest.mark.parametrize(
        "pos2",
        ["非自立", "数詞", "接尾", "助動詞", "接頭", "固有名詞"],
    )
    def test_excludes_filtered_subtypes(self, service, pos2):
        token = _make_token("物事", "名詞", pos2=pos2, lemma="物事")
        assert service._should_include_word(token) is False

    @pytest.mark.parametrize("surface", ["彼", "誰", "何", "我々", "貴様"])
    def test_includes_pronouns_by_default(self, service, surface):
        """Pronouns (pos1=代名詞) like 彼/誰/何/我々/貴様 must be mined."""
        token = _make_token(surface, "代名詞", pos2="*", lemma=surface)
        assert service._should_include_word(token) is True

    @pytest.mark.parametrize("surface", ["これ", "それ", "ここ", "あれ"])
    def test_excludes_hiragana_pronouns(self, service, surface):
        """Hiragana-only pronouns must still be filtered as noise."""
        token = _make_token(surface, "代名詞", pos2="*", lemma=surface)
        assert service._should_include_word(token) is False

    def test_excludes_no_lemma(self, service):
        token = _make_token("何か", "名詞")
        token.feature.lemma = None
        assert service._should_include_word(token) is False

    def test_excludes_no_feature(self, service):
        token = _make_token_no_feature("何か")
        assert service._should_include_word(token) is False

    def test_includes_single_kanji_by_default(self, service):
        """Single kanji content words are always admitted."""
        token = _make_token("皿", "名詞", lemma="皿")
        assert service._should_include_word(token) is True

    def test_excludes_single_katakana(self, service):
        """Single katakana characters are filtered as noise."""
        token = _make_token("ア", "名詞", lemma="ア")
        assert service._should_include_word(token) is False

    def test_excludes_single_hiragana(self, service):
        """Single hiragana characters are filtered as noise."""
        token = _make_token("あ", "名詞", lemma="あ")
        assert service._should_include_word(token) is False

    def test_includes_kanji_compound(self, service):
        token = _make_token("勉強", "名詞", lemma="勉強")
        assert service._should_include_word(token) is True

    def test_includes_kanji_with_okurigana(self, service):
        token = _make_token("食べる", "動詞", lemma="食べる")
        assert service._should_include_word(token) is True

    def test_excludes_katakana_onomatopoeia(self, service):
        """Short katakana with repeated chars (likely onomatopoeia)."""
        token = _make_token("ドキドキ", "副詞", lemma="ドキドキ")
        # 4 chars, stripped unique = {ド,キ} = 2, len<=4 → excluded
        assert service._should_include_word(token) is False

    def test_excludes_katakana_ending_small_tsu(self, service):
        """Short katakana ending in ッ (likely sound effect)."""
        token = _make_token("バッ", "副詞", lemma="バッ")
        assert service._should_include_word(token) is False

    def test_excludes_single_char_katakana(self, service):
        """Single katakana character is rejected by the katakana <2 floor."""
        token = _make_token("ア", "名詞", lemma="ア")
        assert service._should_include_word(token) is False

    def test_includes_long_katakana(self, service):
        """Real katakana loanwords should pass."""
        token = _make_token("コンピューター", "名詞", lemma="コンピューター")
        assert service._should_include_word(token) is True

    def test_excludes_pos_not_in_allowed(self, service):
        """POS types not in allowed list should be excluded."""
        token = _make_token("接続詞", "接続詞", lemma="接続詞")
        assert service._should_include_word(token) is False

    def test_excludes_hiragana_only_word(self, service):
        """Hiragana-only words (no kanji, not katakana) should return False."""
        token = _make_token("ところ", "名詞", lemma="ところ")
        assert service._should_include_word(token) is False

    def test_excludes_three_char_katakana_ending_tsu(self, service):
        """Three-char katakana ending in ッ should be excluded as sound effect."""
        token = _make_token("ガッ", "副詞", lemma="ガッ")
        assert service._should_include_word(token) is False
        token2 = _make_token("ドンッ", "副詞", lemma="ドンッ")
        assert service._should_include_word(token2) is False

    # OVH-029 — POS-gated onomatopoeia filter: 2-char katakana NOUNs must survive
    @pytest.mark.parametrize("surface", ["ビル", "バス", "ドア", "パン", "キス", "ジム", "メモ"])
    def test_includes_2char_katakana_noun_loanwords(self, service, surface):
        """2-char katakana nouns (loanwords) must not be rejected by the onomatopoeia heuristic.

        The unique-char/length gate (≤2 unique, ≤4 chars) was previously POS-blind,
        blocking ビル/バス/ドア/パン/キス/ジム/メモ.  After OVH-029 the gate only
        fires on 副詞 (adverb) tokens so these nouns fall through to the ≥2-char
        acceptance floor.
        """
        token = _make_token(surface, "名詞", lemma=surface)
        assert (
            service._should_include_word(token) is True
        ), f"2-char katakana noun '{surface}' must be included (not caught by onomatopoeia heuristic)"

    def test_excludes_2char_katakana_adverb_onomatopoeia(self, service):
        """2-char katakana 副詞 with ≤2 unique chars is still onomatopoeia → excluded."""
        # ドキ is a 2-char adverb with 2 unique chars (ド, キ) → excluded
        token = _make_token("ドキ", "副詞", lemma="ドキ")
        assert service._should_include_word(token) is False

    def test_excludes_dokidoki_adverb(self, service):
        """ドキドキ (副詞) must still be excluded by the POS-gated heuristic."""
        token = _make_token("ドキドキ", "副詞", lemma="ドキドキ")
        assert service._should_include_word(token) is False


class TestExtractLemma:
    """Tests for _extract_lemma method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    def test_returns_lemma(self, service):
        token = _make_token("食べた", "動詞", lemma="食べる")
        assert service._extract_lemma(token) == "食べる"

    def test_falls_back_to_surface(self, service):
        token = _make_token_no_feature("食べた")
        assert service._extract_lemma(token) == "食べた"

    def test_strips_english_after_hyphen(self, service):
        token = _make_token("スクランブル", "名詞", lemma="スクランブル-scramble")
        assert service._extract_lemma(token) == "スクランブル"

    def test_keeps_japanese_after_hyphen(self, service):
        """A Japanese tail after a hyphen (compound names) must NOT be stripped."""
        token = _make_token("メル", "名詞", lemma="メル-ビル")
        assert service._extract_lemma(token) == "メル-ビル"


class TestExtractOrthBase:
    """Tests for _extract_orth_base method (source-orthography dictionary form)."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    def test_returns_orth_base(self, service):
        """orthBase keeps the source kanji variant that lemma normalizes away."""
        token = _make_token("乞わ", "動詞", lemma="請う", orth_base="乞う")
        assert service._extract_orth_base(token) == "乞う"

    def test_none_falls_back_to_lemma(self, service):
        """fugashi maps unidic's ``*`` placeholder to None → fall back to lemma."""
        token = _make_token("食べた", "動詞", lemma="食べる")
        token.feature.orthBase = None
        assert service._extract_orth_base(token) == "食べる"

    def test_fallback_branch_keeps_gloss_stripping(self, service):
        """The lemma fallback inherits extract_lemma's ASCII-gloss strip."""
        token = _make_token("スクランブル", "名詞", lemma="スクランブル-scramble")
        token.feature.orthBase = None
        assert service._extract_orth_base(token) == "スクランブル"

    def test_missing_attribute_falls_back(self, service):
        """Synthetic merged-compound tokens have no orthBase attribute
        (_SyntheticToken's SimpleNamespace feature) — must not crash."""
        token = _make_token_no_feature("食べた")
        assert service._extract_orth_base(token) == "食べた"


class TestExtractReading:
    """Tests for _extract_reading method."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    def test_returns_kana(self, service):
        token = _make_token("食べる", "動詞", kana="タベル")
        assert service._extract_reading(token) == "タベル"

    def test_falls_back_to_surface(self, service):
        token = _make_token_no_feature("食べる")
        assert service._extract_reading(token) == "食べる"


class TestParseRawEntries:
    """Tests for parse_raw_entries method."""

    def test_returns_tuples_of_start_end_text(self, test_config, tmp_path):
        """Should return list of (start, end, text) tuples."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "こんにちは"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(test_config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        start, end, text = entries[0]
        assert start == pytest.approx(1.0)
        assert end == pytest.approx(3.0)
        assert text == "こんにちは"

    def test_skips_empty_lines(self, test_config, tmp_path):
        """Should skip subtitle lines with empty text."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = ""
        line1.start = 0
        line1.end = 1000

        line2 = MagicMock()
        line2.text = "テスト"
        line2.start = 2000
        line2.end = 4000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(test_config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        assert entries[0][2] == "テスト"

    def test_file_not_found_raises_error(self, test_config):
        """Should raise SubtitleParseError for missing file."""
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            service = SubtitleParserService(test_config)

        with pytest.raises(SubtitleParseError, match="not found"):
            service.parse_raw_entries(Path("/nonexistent/file.ass"))

    def test_applies_subtitle_offset(self, tmp_path):
        """Should apply config subtitle_offset to timing."""

        config = AnkiMinerConfig(subtitle_offset=2.0)

        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "テスト"
        mock_line.start = 1000  # 1.0s
        mock_line.end = 3000  # 3.0s

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(config)
            entries = service.parse_raw_entries(sub_file)

        assert len(entries) == 1
        start, end, _ = entries[0]
        assert start == pytest.approx(3.0)  # 1.0 + 2.0 offset
        assert end == pytest.approx(5.0)  # 3.0 + 2.0 offset


class TestCompoundReassembly:
    """Tests for _merge_compound_suffixes — 名詞+接尾辞 reassembly."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "head_surface,head_pos2,suffix_surface,suffix_pos2,expected",
        [
            ("刑務", "普通名詞", "所", "名詞的", "刑務所"),
            ("爆発", "普通名詞", "的", "形状詞的", "爆発的"),
            ("死傷", "普通名詞", "者", "名詞的", "死傷者"),
            ("入院", "普通名詞", "中", "名詞的", "入院中"),
        ],
    )
    def test_merges_noun_plus_nominal_suffix(
        self, service, head_surface, head_pos2, suffix_surface, suffix_pos2, expected
    ):
        head = _make_token(head_surface, "名詞", pos2=head_pos2, lemma=head_surface)
        suffix = _make_token(suffix_surface, "接尾辞", pos2=suffix_pos2, lemma=suffix_surface)
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        assert merged.feature.pos1 == "名詞"
        assert merged.feature.pos2 == head_pos2

    def test_suffix_at_line_start_unchanged(self, service):
        """Bare suffix with no preceding 名詞 head must not be merged."""
        suffix = _make_token("所", "接尾辞", pos2="名詞的", lemma="所")
        result = service._merge_compound_suffixes([suffix])
        assert len(result) == 1
        assert result[0] is suffix

    def test_noun_plus_noun_no_merge(self, service):
        """Two adjacent 名詞 tokens (no suffix) emit independently."""
        a = _make_token("学校", "名詞", pos2="普通名詞", lemma="学校")
        b = _make_token("生活", "名詞", pos2="普通名詞", lemma="生活")
        result = service._merge_compound_suffixes([a, b])
        assert len(result) == 2
        assert result[0] is a
        assert result[1] is b

    def test_chain_merge_noun_two_suffixes(self, service):
        """Chain: 入院(名詞) + 中(接尾辞,名詞的) + 的(接尾辞,形状詞的) → 入院中的."""
        head = _make_token("入院", "名詞", pos2="普通名詞", lemma="入院")
        suf1 = _make_token("中", "接尾辞", pos2="名詞的", lemma="中")
        suf2 = _make_token("的", "接尾辞", pos2="形状詞的", lemma="的")
        result = service._merge_compound_suffixes([head, suf1, suf2])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "入院中的"
        assert merged.feature.lemma == "入院中的"
        assert merged.feature.pos1 == "名詞"

    def test_non_nominal_suffix_pos2_not_merged(self, service):
        """Suffix with pos2 outside _NOMINAL_SUFFIX_POS2 (e.g. 動詞的) is not merged."""
        head = _make_token("勉強", "名詞", pos2="普通名詞", lemma="勉強")
        suffix = _make_token("する", "接尾辞", pos2="動詞的", lemma="する")
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 2
        assert result[0] is head
        assert result[1] is suffix

    def test_empty_token_list(self, service):
        """Empty input must return an empty list (no IndexError, no merge)."""
        assert service._merge_compound_suffixes([]) == []

    def test_token_without_feature_passes_through(self, service):
        """A token whose feature.pos1 raises AttributeError must pass through unchanged."""
        bad = _make_token_no_feature("???")
        result = service._merge_compound_suffixes([bad])
        assert len(result) == 1
        assert result[0] is bad

    def test_propagates_proper_noun_pos2(self, service):
        """A 固有名詞 head + nominal suffix must keep pos2=固有名詞 on the synthetic.

        This matters because the include filter drops 固有名詞 via
        config.excluded_subtypes, so the synthetic must carry the head's
        pos2 to be filtered out correctly.
        """
        head = _make_token("田中", "名詞", pos2="固有名詞", lemma="田中")
        suffix = _make_token("様", "接尾辞", pos2="名詞的", lemma="様")
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "田中様"
        assert merged.feature.pos2 == "固有名詞"

    def test_lemma_reconstructed_from_base_lemmas(self, service):
        """Synthetic lemma concatenates component feature.lemmas, not surfaces.

        Distinct head-surface vs head-lemma (rare in nouns but possible with
        unidic's English-gloss stripping fallback) is preserved in the
        synthetic so dictionary lookups can hit the headword.
        """
        head = _make_token("入院", "名詞", pos2="普通名詞", lemma="入院LEMMA")
        suffix = _make_token("中", "接尾辞", pos2="名詞的", lemma="中LEMMA")
        result = service._merge_compound_suffixes([head, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "入院中"
        assert merged.feature.lemma == "入院LEMMA中LEMMA"


class TestPrefixCompounds:
    """Tests for _merge_prefix_compounds — 接頭辞 + 名詞/形状詞 merging."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "prefix_surface,root_surface,root_pos1,expected",
        [
            ("不", "可能", "形状詞", "不可能"),
            ("無", "関心", "名詞", "無関心"),
            ("非", "常識", "名詞", "非常識"),
            ("反", "社会", "名詞", "反社会"),
            ("超", "能力", "名詞", "超能力"),
        ],
    )
    def test_merges_whitelisted_prefix_plus_nominal(self, service, prefix_surface, root_surface, root_pos1, expected):
        """Whitelisted prefix + 名詞/形状詞 root → single synthetic emitted as 名詞.

        pos1 is normalized to 名詞 regardless of root_pos1 so that downstream
        noun-suffix merge can chain (e.g. 不+可能+性 → 不可能 → 不可能性).
        """
        prefix = _make_token(prefix_surface, "接頭辞", pos2="*", lemma=prefix_surface)
        root_pos2 = "一般" if root_pos1 == "形状詞" else "普通名詞"
        root = _make_token(root_surface, root_pos1, pos2=root_pos2, lemma=root_surface)
        result = service._merge_compound_suffixes([prefix, root])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        # pos1 is normalized to 名詞 to allow chaining with noun-suffix merge.
        assert merged.feature.pos1 == "名詞"
        # pos2 inherits from root (with "*" coerced to 普通名詞).
        assert merged.feature.pos2 == root_pos2

    def test_non_whitelisted_prefix_not_merged(self, service):
        """Prefix not in _PREFIX_WHITELIST (e.g. お) must not merge."""
        prefix = _make_token("お", "接頭辞", pos2="*", lemma="お")
        root = _make_token("金", "名詞", pos2="普通名詞", lemma="金")
        result = service._merge_compound_suffixes([prefix, root])
        # Both pass through; お is dropped later by allowed_pos filter, not here.
        assert len(result) == 2
        assert result[0] is prefix
        assert result[1] is root

    def test_prefix_followed_by_verb_not_merged(self, service):
        """Whitelisted prefix followed by a 動詞 (not 名詞/形状詞) must not merge."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        verb = _make_token("食べる", "動詞", pos2="一般", lemma="食べる")
        result = service._merge_compound_suffixes([prefix, verb])
        assert len(result) == 2
        assert result[0] is prefix
        assert result[1] is verb

    def test_prefix_at_line_end_not_merged(self, service):
        """A trailing 接頭辞 with no following root must pass through."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        result = service._merge_compound_suffixes([prefix])
        assert len(result) == 1
        assert result[0] is prefix

    def test_prefix_chain_into_noun_suffix(self, service):
        """接頭辞 + 名詞 + 接尾辞(名詞的) chain: prefix-merge then noun-suffix-merge.

        Empirically 可能 is 形状詞 in unidic, but the prefix-merge synthetic
        always emits pos1=名詞 so the suffix-chain can fire. Tested here with
        a 名詞 root (関心) for clarity; the 形状詞 case is covered separately.
        """
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        root = _make_token("関心", "名詞", pos2="普通名詞", lemma="関心")
        suffix = _make_token("性", "接尾辞", pos2="名詞的", lemma="性")
        result = service._merge_compound_suffixes([prefix, root, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "不関心性"
        assert merged.feature.lemma == "不関心性"
        assert merged.feature.pos1 == "名詞"

    def test_prefix_chain_with_keijoushi_root_into_noun_suffix(self, service):
        """不 + 可能(形状詞) + 性 → 不可能性 (chains because synthetic emits pos1=名詞)."""
        prefix = _make_token("不", "接頭辞", pos2="*", lemma="不")
        root = _make_token("可能", "形状詞", pos2="一般", lemma="可能")
        suffix = _make_token("性", "接尾辞", pos2="名詞的", lemma="性")
        result = service._merge_compound_suffixes([prefix, root, suffix])
        assert len(result) == 1
        merged = result[0]
        assert merged.surface == "不可能性"
        assert merged.feature.lemma == "不可能性"
        assert merged.feature.pos1 == "名詞"


class TestVerbNominalizers:
    """Tests for _merge_verb_nominalizers — 動詞(連用形) + nominalizer suffix."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    @pytest.mark.parametrize(
        "verb_surface,verb_lemma,suffix_surface,expected",
        [
            ("言い", "言う", "方", "言い方"),
            ("読み", "読む", "方", "読み方"),
            ("生き", "生きる", "方", "生き方"),
            ("やり", "遣る", "方", "やり方"),
        ],
    )
    def test_merges_verb_stem_plus_nominalizer(self, service, verb_surface, verb_lemma, suffix_surface, expected):
        """Verb 連用形 + nominalizer (方/手/様) → synthetic with surface=lemma=連用形+suffix."""
        verb = _make_token(verb_surface, "動詞", pos2="一般", lemma=verb_lemma)
        suffix = _make_token(suffix_surface, "接尾辞", pos2="名詞的", lemma=suffix_surface)
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 1
        merged = result[0]
        # CRITICAL: surface uses verb's CONJUGATED form (連用形), not lemma.
        # Lemma == surface for the merged form (NOT 言う方).
        assert merged.surface == expected
        assert merged.feature.lemma == expected
        assert merged.feature.pos1 == "名詞"
        assert merged.feature.pos2 == "普通名詞"

    def test_non_whitelisted_verb_suffix_not_merged(self, service):
        """Verb + 接尾辞(名詞的) where suffix not in _VERB_NOMINALIZER_SUFFIXES is not merged.

        Example: 話し + 者 — 者 is not a productive verb-stem nominalizer in
        the same way 方/手/様 are, so we don't merge it here.
        """
        verb = _make_token("話し", "動詞", pos2="一般", lemma="話す")
        suffix = _make_token("者", "接尾辞", pos2="名詞的", lemma="者")
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 2
        assert result[0] is verb
        assert result[1] is suffix

    def test_verb_at_line_end_not_merged(self, service):
        """A 動詞 with no following suffix must pass through unchanged."""
        verb = _make_token("言い", "動詞", pos2="一般", lemma="言う")
        result = service._merge_compound_suffixes([verb])
        assert len(result) == 1
        assert result[0] is verb

    def test_verb_plus_non_nominal_suffix_not_merged(self, service):
        """動詞 + 接尾辞(動詞的) (e.g. する) is not a nominalizer — no merge here."""
        verb = _make_token("勉強し", "動詞", pos2="一般", lemma="勉強する")
        suffix = _make_token("する", "接尾辞", pos2="動詞的", lemma="する")
        result = service._merge_compound_suffixes([verb, suffix])
        assert len(result) == 2
        assert result[0] is verb
        assert result[1] is suffix


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_target_words(tmp_path):
    """Real fugashi pipeline must mine the FMA-style targets after the fixes."""
    srt_file = tmp_path / "fma_ep1.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で爆発的な事件を起こし、死傷者が出た\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    expected = {"彼", "刑務所", "爆発的", "事件", "死傷者"}
    missing = expected - surfaces
    assert not missing, f"missing target surfaces: {missing}; got: {surfaces}"

    # Verify the 刑務所 merged synthetic carries the correct lemma and reading.
    by_surface = {w.surface: w for w in words}
    keimusho = by_surface["刑務所"]
    assert keimusho.lemma == "刑務所"
    # unidic emits katakana readings; concatenated stems give ケイムショ.
    assert keimusho.reading == "ケイムショ"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_prefix_compound(tmp_path):
    """Real fugashi pipeline must mine 不可能 from 不+可能 prefix-merge."""
    srt_file = tmp_path / "prefix.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "不可能な事を諦めた\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    assert "不可能" in surfaces, f"expected 不可能 in mined surfaces; got: {surfaces}"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_mines_verb_nominalizer(tmp_path):
    """Real fugashi pipeline must mine 生き方 from 生き(動詞) + 方(接尾辞,名詞的)."""
    srt_file = tmp_path / "verb_nom.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "生き方を考える\n",
        encoding="utf-8",
    )

    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    surfaces = {w.surface for w in words}
    assert "生き方" in surfaces, f"expected 生き方 in mined surfaces; got: {surfaces}"
    # Lemma must be the merged surface, NOT 生きる方.
    by_surface = {w.surface: w for w in words}
    assert by_surface["生き方"].lemma == "生き方"


# ---------------------------------------------------------------------------
# Pre-tokenization Japanese normalization (ja_normalize wired into
# clean_subtitle_text) — end-to-end through the real fugashi pipeline.
# ---------------------------------------------------------------------------


def _mine_line(tmp_path, text):
    srt_file = tmp_path / "norm.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" + text + "\n",
        encoding="utf-8",
    )
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    return SubtitleParserService(config).parse_subtitle_file(srt_file)


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_halfwidth_katakana_mines_fullwidth(tmp_path):
    """Halfwidth ﾊﾟｿｺﾝ must normalize to パソコン and be mined."""
    words = _mine_line(tmp_path, "ﾊﾟｿｺﾝを使う")

    surfaces = {w.surface for w in words}
    assert "パソコン" in surfaces, f"got: {surfaces}"
    # No halfwidth katakana survives into the mined data.
    for w in words:
        assert "ﾊ" not in w.sentence and "ﾟ" not in w.sentence


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_offset_invariant_on_halfwidth_line(tmp_path):
    """Because normalization precedes tokenization, the stored sentence *is* the
    normalized text: every mined word's surface is findable in its sentence at
    the recorded offsets (Issue #20 invariant preserved by construction)."""
    words = _mine_line(tmp_path, "ﾊﾟｿｺﾝを使う")

    assert words
    for w in words:
        assert w.surface in w.sentence
        assert w.sentence[w.surface_start : w.surface_end] == w.surface


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_nfd_kana_tokenizes_like_precomposed(tmp_path):
    """NFD-decomposed dakuten kana (か + U+3099) must tokenize identically to the
    precomposed line — the whole point of the NFC step."""
    import unicodedata

    precomposed = "ゲームが好きだ"
    decomposed = unicodedata.normalize("NFD", precomposed)
    assert decomposed != precomposed  # sanity: the input really is decomposed

    surfaces_pre = {w.surface for w in _mine_line(tmp_path, precomposed)}
    surfaces_nfd = {w.surface for w in _mine_line(tmp_path, decomposed)}
    assert surfaces_pre == surfaces_nfd
    assert surfaces_pre  # and something was actually mined


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_kangxi_radical_mines_kanji_word(tmp_path):
    """OCR Kangxi radical ⼭ (U+2F2D) must fold to 山 (U+5C71) and mine 山-words."""
    words = _mine_line(tmp_path, "高い⼭に登る")

    surfaces = {w.surface for w in words}
    assert "山" in surfaces, f"got: {surfaces}"
    assert "登る" in surfaces, f"got: {surfaces}"
    for w in words:
        assert "⼭" not in w.sentence  # radical folded away


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_kanji_variant_mines_standard_form(tmp_path):
    """Astral 𠮟 (U+20B9F) must standardize to 叱 (U+53F1) and mine lemma 叱る."""
    words = _mine_line(tmp_path, "母に𠮟られた")

    lemmas = {w.lemma for w in words}
    assert "叱る" in lemmas, f"got: {lemmas}"
    for w in words:
        assert "𠮟" not in w.sentence  # variant standardized away


# ---------------------------------------------------------------------------
# parse_subtitle_file_with_index — i+1 filter foundation
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestParseSubtitleFileWithIndex:
    """Tests for parse_subtitle_file_with_index — the i+1 filter's line-index path.

    Uses the real fugashi pipeline (same approach as the existing
    test_real_fugashi_* end-to-end tests) because the line-index method emits
    sentence_furigana / sentence_reading that depend on real tokenization, and
    we want to verify post-compound-merge lemmas with real unidic output.
    """

    def _write_srt(self, path: Path, lines: list[tuple[str, str, str]]) -> Path:
        """Write a minimal .srt file. Each line tuple is (start, end, text)."""
        chunks = []
        for i, (start, end, text) in enumerate(lines, start=1):
            chunks.append(f"{i}\n{start} --> {end}\n{text}\n")
        path.write_text("\n".join(chunks), encoding="utf-8")
        return path

    def test_returns_tuple_of_words_and_index(self, tmp_path):
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [("00:00:01,000", "00:00:03,000", "学校で勉強する")],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        result = service.parse_subtitle_file_with_index(srt)

        assert isinstance(result, tuple)
        assert len(result) == 2
        words, index = result
        assert isinstance(words, list)
        assert isinstance(index, list)
        assert all(isinstance(w, TokenizedWord) for w in words)
        assert all(isinstance(line, LineLemmas) for line in index)

    def test_words_match_legacy_parse(self, tmp_path):
        """Regression guard: both methods must produce identical TokenizedWord lists.

        Same input → identical dedup-by-(lemma|surface), first-wins ordering.
        """
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [
                ("00:00:01,000", "00:00:03,000", "彼は刑務所で爆発的な事件を起こした"),
                ("00:00:04,000", "00:00:06,000", "学校で勉強する"),
                ("00:00:07,000", "00:00:09,000", "また勉強した"),  # 勉強 dup
            ],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        legacy = service.parse_subtitle_file(srt)
        new_words, _ = service.parse_subtitle_file_with_index(srt)

        # Same length, same lemma sequence (ordering matters — first-wins).
        assert [w.lemma for w in legacy] == [w.lemma for w in new_words]
        assert [w.surface for w in legacy] == [w.surface for w in new_words]
        assert [w.sentence for w in legacy] == [w.sentence for w in new_words]
        assert [w.start_time for w in legacy] == [w.start_time for w in new_words]
        # Per-line sentence furigana/reading should match (same generator,
        # same input text — just computed once per line in the new path).
        assert [w.sentence_furigana for w in legacy] == [w.sentence_furigana for w in new_words]
        assert [w.sentence_reading for w in legacy] == [w.sentence_reading for w in new_words]

    def test_line_index_includes_all_occurrences(self, tmp_path):
        """A lemma appearing on two lines must show up in both LineLemmas entries.

        The line index intentionally does NOT dedup against seen_words — the
        i+1 filter needs per-line lemma sets to count unknowns.
        """
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [
                ("00:00:01,000", "00:00:03,000", "勉強する"),
                ("00:00:04,000", "00:00:06,000", "また勉強する"),
            ],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        _, index = service.parse_subtitle_file_with_index(srt)

        # 勉強 appears in both line entries — no dedup across lines.
        assert len(index) == 2
        assert "勉強" in index[0].lemmas
        assert "勉強" in index[1].lemmas

    def test_line_index_excludes_non_content_words(self, tmp_path):
        """A line made only of particles + punctuation must not appear in the index."""
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [
                ("00:00:01,000", "00:00:03,000", "は、を。"),  # particles + punctuation
                ("00:00:04,000", "00:00:06,000", "勉強する"),
            ],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        _, index = service.parse_subtitle_file_with_index(srt)

        # Particle-only line is skipped from the index entirely.
        assert len(index) == 1
        assert "勉強" in index[0].lemmas

    def test_line_index_respects_should_include_word(self, tmp_path):
        """Tokens excluded by _should_include_word (e.g. 固有名詞) must not appear in lemmas.

        Uses mocked tokenization to deterministically inject a 固有名詞 (proper
        noun) — excluded_subtypes includes 固有名詞 by default.
        """
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "田中は勉強する"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        proper = _make_token("田中", "名詞", pos2="固有名詞", lemma="田中")
        content = _make_token("勉強", "名詞", pos2="普通名詞", lemma="勉強")

        mock_tagger = MagicMock()
        # T2: sentence-level furigana/reading use raw_tokens (no extra tagger
        # calls). 1 tokenize + 2 expression-level for the emitted 勉強 word.
        mock_tagger.side_effect = [
            [proper, content],  # tokenize
            [content],  # generate_furigana(surface='勉強')
            [content],  # generate_reading(surface='勉強')
        ]

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(config)
            _, index = service.parse_subtitle_file_with_index(sub_file)

        assert len(index) == 1
        # 固有名詞 田中 must be filtered out by _should_include_word.
        assert "田中" not in index[0].lemmas
        assert "勉強" in index[0].lemmas

    def test_line_index_lemmas_match_post_compound_merge(self, tmp_path):
        """Compound merge runs BEFORE lemma collection — 刑務所 not 刑務+所."""
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [("00:00:01,000", "00:00:03,000", "彼は刑務所にいる")],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        _, index = service.parse_subtitle_file_with_index(srt)

        assert len(index) == 1
        assert "刑務所" in index[0].lemmas
        # The individual constituents must NOT appear — they were consumed by
        # the merge pass.
        assert "刑務" not in index[0].lemmas
        assert "所" not in index[0].lemmas

    def test_line_index_skips_empty_lines(self, tmp_path):
        """Lines that clean to empty text must produce no index entry."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        empty_line = MagicMock()
        empty_line.text = "{\\an8}  "  # All formatting — clean strips to ""
        empty_line.start = 1000
        empty_line.end = 3000

        good_line = MagicMock()
        good_line.text = "勉強する"
        good_line.start = 4000
        good_line.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([empty_line, good_line]))

        content = _make_token("勉強", "名詞", pos2="普通名詞", lemma="勉強")
        mock_tagger = MagicMock()
        # T2: sentence-level uses raw_tokens. 1 tokenize + 2 expression-level.
        mock_tagger.side_effect = [
            [content],  # tokenize good_line
            [content],  # generate_furigana(surface)
            [content],  # generate_reading(surface)
        ]

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(config)
            _, index = service.parse_subtitle_file_with_index(sub_file)

        # Empty-text line skipped; only the 勉強 line is indexed.
        assert len(index) == 1
        assert index[0].line_text == "勉強する"

    def test_line_index_sentence_furigana_computed_once_per_line(self, tmp_path):
        """Perf invariant: the tagger is called exactly once per non-empty line.

        T2: sentence-level annotation uses generate_furigana_from_tokens /
        generate_reading_from_tokens with the already-parsed raw_tokens, so no
        extra tagger calls are made for sentence-level work. The tagger is
        called exactly ONCE per non-empty line (the _iter_parsed_lines tokenize)
        regardless of how many words are emitted or whether i+1 index is built.
        """
        srt = self._write_srt(
            tmp_path / "ep.srt",
            [
                ("00:00:01,000", "00:00:03,000", "学校で勉強する事件"),  # 3 content words
                ("00:00:04,000", "00:00:06,000", "また勉強する"),  # 1 content word (勉強 dup)
            ],
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        # Wrap the real tagger so call counts are recorded but real tokens
        # come out — the test asserts on call patterns, not return values.
        real_tagger = service.tagger

        class SpyTagger:
            def __init__(self):
                self.calls: list[str] = []

            def __call__(self, text: str):
                self.calls.append(text)
                return real_tagger(text)

        spy = SpyTagger()
        service.tagger = spy

        service.parse_subtitle_file_with_index(srt)

        line_texts = {"学校で勉強する事件", "また勉強する"}
        # Each full-sentence tagger call is exactly 1 per non-empty line
        # (the _iter_parsed_lines tokenize). Per-word expression calls pass
        # a single mined form, not the full sentence text.
        full_sentence_calls = [t for t in spy.calls if t in line_texts]
        assert len(full_sentence_calls) == 2, (
            f"Expected 1 tagger call per non-empty line (2 total); "
            f"got {len(full_sentence_calls)} full-sentence calls out of {len(spy.calls)} total"
        )

    def test_line_index_with_subtitle_regex_filter(self, tmp_path):
        """Issue #8 interaction: line_text and lemmas must reflect POST-filter text."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_line = MagicMock()
        mock_line.text = "(田中) 勉強する"
        mock_line.start = 1000
        mock_line.end = 3000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        # After regex strip "(田中) " is removed; only "勉強する" reaches tokenize.
        content = _make_token("勉強", "名詞", pos2="普通名詞", lemma="勉強")
        mock_tagger = MagicMock()
        # T2: sentence-level uses raw_tokens. 1 tokenize + 2 expression-level.
        mock_tagger.side_effect = [
            [content],  # tokenize post-filter "勉強する"
            [content],  # generate_furigana(surface)
            [content],  # generate_reading(surface)
        ]

        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\([^)]*\)",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(config)
            _, index = service.parse_subtitle_file_with_index(sub_file)

        assert len(index) == 1
        # line_text reflects post-filter (no "(田中)" speaker tag).
        assert index[0].line_text == "勉強する"
        # 田中 never enters lemmas (filtered out before tokenization).
        assert "田中" not in index[0].lemmas
        assert "勉強" in index[0].lemmas


# ---------------------------------------------------------------------------
# Subtitle regex filter (Issue #8)
# ---------------------------------------------------------------------------


class TestSubtitleRegexFilter:
    """Tests for the optional regex filter applied before tokenization."""

    def _build_raw_service(self, config):
        """Construct a service with a stub tagger (raw-entries tests don't tokenize)."""
        return SubtitleParserService(config)

    def _patch_subs(self, tmp_path, lines):
        """Return a (sub_file, mock_subs) pair for the given iterable of lines."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")
        mock_line_objs = []
        for text, start_ms, end_ms in lines:
            ml = MagicMock()
            ml.text = text
            ml.start = start_ms
            ml.end = end_ms
            mock_line_objs.append(ml)
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter(mock_line_objs))
        return sub_file, mock_subs

    def test_disabled_filter_passes_text_through(self, tmp_path):
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\([^)]*\)",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=False,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("(田中) 今日はいい天気", 0, 2000)])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        # Filter disabled: full text survives.
        assert entries[0][2] == "(田中) 今日はいい天気"

    def test_strips_parens_from_raw_entries(self, tmp_path):
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\([^)]*\)",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("(田中) 今日はいい天気", 0, 2000)])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        # Speaker tag stripped; whitespace renormalized.
        assert entries[0][2] == "今日はいい天気"

    def test_strips_brackets_and_drops_line_when_empty(self, tmp_path):
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\[[^\]]*\]",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(
            tmp_path,
            [
                ("[ドアが閉まる音]", 0, 1000),  # filter empties this line — dropped
                ("[足音] お疲れ様", 2000, 4000),  # filter strips brackets; remainder survives
            ],
        )
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        # Only the partially-stripped line survives; whitespace-only result is dropped.
        assert len(entries) == 1
        assert entries[0][2] == "お疲れ様"

    def test_replacement_with_backreference(self, tmp_path):
        # Capture group + Python-style \1 backref: prove the substitution path
        # accepts capture references, distinct from asbplayer's $1 syntax.
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\((.*?)\)",
            subtitle_regex_replacement=r"\1",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("(田中) こんにちは", 0, 2000)])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        # Parens dropped, inner content kept.
        assert entries[0][2] == "田中 こんにちは"

    def test_invalid_regex_disables_filter_without_crashing(self, tmp_path, caplog):
        # Unbalanced paren is a re.error. Parser must construct cleanly and the
        # filter must no-op so a mining run is not lost to bad config.
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter="(unclosed",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("テスト", 0, 1000)])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            caplog.at_level("WARNING"),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        assert service._filter_pattern is None
        assert entries[0][2] == "テスト"
        assert any("Invalid subtitle_regex_filter" in rec.message for rec in caplog.records)

    def test_mining_path_applies_same_filter(self, tmp_path):
        # parse_subtitle_file must honor the filter identically to parse_raw_entries:
        # if we strip the only content character, MeCab sees an empty line and
        # produces no words.
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\([^)]*\)",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("(全部消える)", 0, 2000)])
        mock_tagger = MagicMock(return_value=[])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = SubtitleParserService(config)
            words = service.parse_subtitle_file(sub_file)

        # Stripped-to-empty line is dropped before tokenization → no words and
        # tagger is never called on the post-filter empty string.
        assert words == []
        assert mock_tagger.call_count == 0

    def test_alternation_strips_multiple_pattern_types(self, tmp_path):
        # Verify the `|`-combined preset model: one pattern with multiple
        # alternations handles parens AND brackets AND music notes in one pass.
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            subtitle_regex_filter=r"\([^)]*\)|\[[^\]]*\]|[♪♬]+",
            subtitle_regex_replacement="",
            use_subtitle_regex_filter=True,
        )
        sub_file, mock_subs = self._patch_subs(tmp_path, [("(田中) [足音] ♪歌う♪ こんにちは", 0, 2000)])
        with (
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
        ):
            service = self._build_raw_service(config)
            entries = service.parse_raw_entries(sub_file)

        assert entries[0][2] == "歌う こんにちは"


# ---------------------------------------------------------------------------
# Surface offsets + bold precomputation (Issue #20)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestSurfaceOffsetsAndBolding:
    """Parser must emit char offsets for each mined morpheme and, when the
    bold_target_in_sentence flag is on, precompute the bolded sentence
    + sentence_furigana fields on the TokenizedWord."""

    def test_emits_surface_offsets_matching_sentence_slice(self, tmp_path):
        srt_file = tmp_path / "offset.srt"
        srt_file.write_text(
            "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で爆発的な事件を起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)

        for word in words:
            assert word.surface_start >= 0, f"missing offset on {word.surface}"
            assert word.surface_end > word.surface_start
            assert word.sentence[word.surface_start : word.surface_end] == word.surface

    def test_offsets_span_compound_merged_token(self, tmp_path):
        srt_file = tmp_path / "compound.srt"
        srt_file.write_text(
            "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で爆発的な事件を起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)
        by_surface = {w.surface: w for w in words}

        # 刑務所 is a compound-merge synthetic. Offsets must cover all three chars.
        keimusho = by_surface["刑務所"]
        assert keimusho.surface_end - keimusho.surface_start == len("刑務所")
        assert keimusho.sentence[keimusho.surface_start : keimusho.surface_end] == "刑務所"

    def test_no_bolded_fields_when_flag_off(self, tmp_path):
        srt_file = tmp_path / "no_bold.srt"
        srt_file.write_text(
            "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で事件を起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)

        for word in words:
            assert word.sentence_bolded == ""
            assert word.sentence_furigana_bolded == ""

    def test_bolded_fields_populated_when_flag_on(self, tmp_path):
        srt_file = tmp_path / "bold.srt"
        srt_file.write_text(
            "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で事件を起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            bold_target_in_sentence=True,
        )
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)
        by_surface = {w.surface: w for w in words}

        keimusho = by_surface["刑務所"]
        # Plain bolded form wraps exactly the morpheme.
        assert "<b>刑務所</b>" in keimusho.sentence_bolded
        # Furigana bolded form keeps furigana annotations and bolds the target.
        assert "<b>" in keimusho.sentence_furigana_bolded
        assert "</b>" in keimusho.sentence_furigana_bolded
        # Within the <b>...</b> run, the kanji of the merged compound must
        # all be present (the wrap helper re-tokenizes via the raw tagger,
        # so a compound-merge synthetic may split into per-morpheme rubies
        # like "刑務[けいむ] 所[しょ]" — both halves should still be bolded).
        between = keimusho.sentence_furigana_bolded.split("<b>", 1)[1].split("</b>", 1)[0]
        for ch in "刑務所":
            assert ch in between

    def test_with_index_emits_lemma_spans(self, tmp_path):
        srt_file = tmp_path / "index.srt"
        srt_file.write_text(
            "1\n" "00:00:01,000 --> 00:00:05,000\n" "彼は刑務所で事件を起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        _words, line_index = service.parse_subtitle_file_with_index(srt_file)

        assert len(line_index) == 1
        ll = line_index[0]
        assert ll.lemma_spans, "expected lemma_spans populated for the line"
        by_lemma = {entry[0]: entry for entry in ll.lemma_spans}
        keimusho_entry = by_lemma["刑務所"]
        _, surface, span_start, span_end, span_highlight_end = keimusho_entry
        assert surface == "刑務所"
        assert ll.line_text[span_start:span_end] == "刑務所"
        # Nouns never extend: highlight_end == span_end.
        assert span_highlight_end == span_end
        # The verb 起こす extends over its auxiliary: 起こした.
        okosu_entry = by_lemma["起こす"]
        _, _, okosu_start, okosu_end, okosu_highlight_end = okosu_entry
        assert okosu_highlight_end >= okosu_end
        assert ll.line_text[okosu_start:okosu_highlight_end] == "起こした"

    def test_offsets_survive_internal_spaces(self, tmp_path):
        """Regression for Issue #20 and Issue #31: MeCab elides whitespace
        from the token stream. Cursor arithmetic by token-surface length
        drifts left by the number of preceding spaces, so bolded spans
        land on the wrong chars — both in the plain Sentence field
        (#20) and in the SentenceFurigana field (#31)."""
        import re

        srt_file = tmp_path / "spaces.srt"
        # Lines lifted from the user's exported reproducer (Issue #20 apkg).
        # Each has internal spaces and a target morpheme that previously got
        # bolded one or more characters too early.
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\nなんで 素直に 好きって 言えないんだろう。\n"
            "\n"
            "2\n00:00:06,000 --> 00:00:10,000\nごめんね 通して。 あっ 押さないで。\n"
            "\n"
            "3\n00:00:11,000 --> 00:00:15,000\n何？ 女の子に そんな 顔 真っ赤にして！\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            bold_target_in_sentence=True,
        )
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)
        by_lemma = {w.lemma: w for w in words}

        # Every mined word's stored offsets must round-trip the surface.
        for word in words:
            assert word.surface_start >= 0, f"missing offset on {word.surface}"
            assert word.sentence[word.surface_start : word.surface_end] == word.surface, (
                f"offset drift on {word.surface!r} in {word.sentence!r}: "
                f"slice={word.sentence[word.surface_start : word.surface_end]!r}"
            )

        # The bolded plain field must wrap the exact morpheme.
        # 素直: was bolding " 素" before the fix.
        sunao = by_lemma["素直"]
        assert "<b>素直</b>" in sunao.sentence_bolded, sunao.sentence_bolded
        # 通す: was bolding " 通" before the #20 fix; the full inflected
        # form 通して is bolded since the deinflection-span fix (the
        # following 。 is 補助記号 and stops the window).
        toosu = by_lemma["通す"]
        assert "<b>通して</b>" in toosu.sentence_bolded, toosu.sentence_bolded
        # 真っ赤: was bolding "な 顔" before the fix.
        makka = by_lemma["真っ赤"]
        assert "<b>真っ赤</b>" in makka.sentence_bolded, makka.sentence_bolded

        # The bolded furigana field must wrap the exact morpheme's
        # ``surface[reading]`` chunk — not the preceding/following token.
        # Pre-#31 fix, the <b> tag drifted left by the count of preceding
        # spaces and engulfed the next morpheme too. We don't hardcode
        # readings here because they come from unidic-lite and could
        # legitimately differ across versions; we assert structurally.
        def _assert_furigana_bold(word, surface_head: str, must_not_contain: str):
            field = word.sentence_furigana_bolded
            m = re.search(r"<b>([^<]+)</b>", field)
            assert m, f"no <b>...</b> in {field!r}"
            body = m.group(1)
            assert body.startswith(
                surface_head
            ), f"bold body {body!r} does not start with {surface_head!r} in {field!r}"
            assert (
                must_not_contain not in body
            ), f"bold body {body!r} bled into adjacent morpheme {must_not_contain!r} in {field!r}"

        _assert_furigana_bold(sunao, "素直", "に")
        _assert_furigana_bold(toosu, "通", "。")
        _assert_furigana_bold(makka, "真", "顔")

    def test_with_index_offsets_survive_internal_spaces(self, tmp_path):
        """The lemma_spans table (used by the i+1 swap to rebuild bold fields
        against a different example line) must also use raw-text offsets."""
        srt_file = tmp_path / "index_spaces.srt"
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\n彼は 刑務所で 事件を 起こした\n",
            encoding="utf-8",
        )

        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        _words, line_index = service.parse_subtitle_file_with_index(srt_file)

        assert len(line_index) == 1
        ll = line_index[0]
        for lemma_key, surface, span_start, span_end, span_highlight_end in ll.lemma_spans:
            assert ll.line_text[span_start:span_end] == surface, (
                f"lemma_spans drift on {lemma_key!r}: "
                f"slice={ll.line_text[span_start:span_end]!r}, surface={surface!r}"
            )
            assert span_highlight_end >= span_end

    # ------------------------------------------------------------------
    # Full-inflected-form bolding (Yomitan deinflection span). Expected
    # spans are pinned per vector — verified against the ported engine
    # AND the real upstream engine at the pinned commit, not intuition.
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("sentence", "lemma", "expected_bold"),
        [
            # Unambiguous single-auxiliary cases.
            ("種を蒔いた", "蒔く", "<b>蒔いた</b>"),
            ("昨日食べた", "食べる", "<b>食べた</b>"),
            ("犬が死んだ", "死ぬ", "<b>死んだ</b>"),
            ("値段が高かった", "高い", "<b>高かった</b>"),
            # Auxiliary chains (user-confirmed full-Yomitan behavior).
            ("海で泳いでいた", "泳ぐ", "<b>泳いでいた</b>"),
            # Non-rule stops: upstream has no benefactive/てみる/ていく
            # rules, so the span ends at the last valid chain point.
            ("本を買ってくれた", "買う", "<b>買って</b>"),
            ("食べていく", "食べる", "<b>食べて</b>"),
        ],
    )
    def test_bolds_full_inflected_form(self, tmp_path, sentence, lemma, expected_bold):
        srt_file = tmp_path / "inflection.srt"
        srt_file.write_text(
            f"1\n00:00:01,000 --> 00:00:05,000\n{sentence}\n",
            encoding="utf-8",
        )
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            bold_target_in_sentence=True,
        )
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)
        by_lemma = {w.lemma: w for w in words}
        assert lemma in by_lemma, f"expected {lemma!r} mined from {sentence!r}: {sorted(by_lemma)}"
        word = by_lemma[lemma]

        # Plain bolded sentence covers the full inflected form.
        assert expected_bold in word.sentence_bolded, word.sentence_bolded
        # Offsets: surface invariant intact, highlight covers the bold text.
        assert word.sentence[word.surface_start : word.surface_end] == word.surface
        assert word.highlight_end >= word.surface_end
        bold_text = expected_bold.removeprefix("<b>").removesuffix("</b>")
        assert word.sentence[word.surface_start : word.bold_end] == bold_text
        # Furigana-bolded body starts at the kanji head and spans the same
        # source text (structural — readings come from unidic-lite).
        import re as _re

        m = _re.search(r"<b>([^<]+)</b>", word.sentence_furigana_bolded)
        assert m, word.sentence_furigana_bolded
        assert m.group(1).startswith(word.surface[0])

    def test_hiragana_benefactive_not_mined_separately(self, tmp_path):
        """くれ (呉れる) has a pure-hiragana surface: the pre-existing
        has_kanji gate drops it, so 買ってくれた mines only 買う (and 本)."""
        srt_file = tmp_path / "benefactive.srt"
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\n本を買ってくれた\n",
            encoding="utf-8",
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt_file)
        lemmas = {w.lemma for w in words}
        assert "買う" in lemmas
        assert not any("くれ" in lemma for lemma in lemmas)


# ---------------------------------------------------------------------------
# count_lemmas — raw in-corpus occurrence counts
# ---------------------------------------------------------------------------


class TestCountLemmas:
    """Tests for SubtitleParserService.count_lemmas.

    Uses mocked tokenization (same style as the rest of this file) so the
    tests are hermetic and fast — no MeCab process required.
    """

    def _make_mock_subs(self, lines):
        """Build a mock pysubs2 subtitle container from a list of mock-line dicts.

        Each dict must have keys: text, start, end (milliseconds).
        """
        mock_lines = []
        for spec in lines:
            ml = MagicMock()
            ml.text = spec["text"]
            ml.start = spec["start"]
            ml.end = spec["end"]
            mock_lines.append(ml)
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter(mock_lines))
        return mock_subs

    # ------------------------------------------------------------------
    # 1. Repeats counted (no dedup)
    # ------------------------------------------------------------------

    def test_counts_repeats_within_single_line(self, test_config, tmp_path):
        """The same lemma tokenized twice on one line must be counted twice."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = self._make_mock_subs([{"text": "食べる食べる", "start": 1000, "end": 3000}])

        token = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        mock_tagger = MagicMock()
        # _iter_parsed_lines calls tagger once per line
        mock_tagger.return_value = [token, token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert counts["食べる"] == 2

    def test_counts_repeats_across_lines(self, test_config, tmp_path):
        """A lemma appearing on two separate lines must have count = 2."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = self._make_mock_subs(
            [
                {"text": "食べる", "start": 1000, "end": 3000},
                {"text": "食べた", "start": 4000, "end": 6000},
            ]
        )

        token1 = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        token2 = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ")

        mock_tagger = MagicMock()
        mock_tagger.side_effect = [[token1], [token2]]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        # Both surface forms share the same lemma — must add up, not dedup.
        assert counts["食べる"] == 2

    def test_counts_multiple_distinct_lemmas(self, test_config, tmp_path):
        """Multiple distinct content lemmas on one line each get their own count."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = self._make_mock_subs([{"text": "猫と犬", "start": 1000, "end": 3000}])

        neko = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        inu = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [neko, inu]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert counts["猫"] == 1
        assert counts["犬"] == 1

    # ------------------------------------------------------------------
    # 2. POS filtering: excluded tokens are NOT counted
    # ------------------------------------------------------------------

    def test_excludes_particles_same_as_mining(self, test_config, tmp_path):
        """Particles (助詞) must not appear in the returned Counter."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = self._make_mock_subs([{"text": "猫が走る", "start": 1000, "end": 3000}])

        neko = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        ga = _make_token("が", "助詞", lemma="が", kana="ガ")
        hashiru = _make_token("走る", "動詞", lemma="走る", kana="ハシル")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [neko, ga, hashiru]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert "が" not in counts
        assert counts["猫"] == 1
        assert counts["走る"] == 1

    def test_count_lemma_keys_match_parse_subtitle_file_lemmas(self, test_config, tmp_path):
        """count_lemmas keys must be a superset of parse_subtitle_file lemmas.

        parse_subtitle_file deduplicates, so its lemma set is a subset of
        count_lemmas keys (same inclusion filter, just without counting repeats).
        For a file with no repeated lemmas the two sets must be identical.

        With the shared-tagger singleton, service_a and service_b share one
        tagger instance.  The same mock is reused for both phases; the
        behavioral assertions are unchanged.
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        line_spec = [{"text": "事件を調べる", "start": 1000, "end": 3000}]
        jiken = _make_token("事件", "名詞", lemma="事件", kana="ジケン")
        wo = _make_token("を", "助詞", lemma="を", kana="ヲ")
        shiraberu = _make_token("調べる", "動詞", lemma="調べる", kana="シラベル")

        # Single shared tagger mock — both service instances get it via the singleton.
        mock_tagger = MagicMock()
        mock_tagger.return_value = [jiken, wo, shiraberu]

        # Run count_lemmas
        mock_subs_a = self._make_mock_subs(line_spec)
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs_a),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service_a = SubtitleParserService(test_config)
            counts = service_a.count_lemmas(sub_file)

        # Run parse_subtitle_file
        mock_subs_b = self._make_mock_subs(line_spec)
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs_b),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service_b = SubtitleParserService(test_config)
            words = service_b.parse_subtitle_file(sub_file)

        mined_lemmas = {w.lemma for w in words}
        # All mined lemmas must appear as keys in the counter.
        assert mined_lemmas.issubset(set(counts.keys()))
        # Grammar tokens must not be keys in either.
        assert "を" not in counts
        assert "を" not in mined_lemmas

    def test_whitespace_spanning_compound_count_matches_mine(self, test_config, tmp_path):
        """Regression for T-38: a merged compound whose components were separated
        by whitespace in the source line is dropped from mining (its space-free
        concatenated surface is not str.find-able in the spaced text). count_lemmas
        must drop it identically so the count and mine lemma sets agree — the
        count-vs-mine divergence behind the Deck Builder preview bug.
        """
        # Source text has a SPACE between 可能 and 性; the noun-suffix merge
        # concatenates them into the synthetic surface "可能性" (no space), which
        # str.find("可能性") cannot locate in "可能 性".
        line_spec = [{"text": "可能 性", "start": 1000, "end": 3000}]
        kanou = _make_token("可能", "名詞", pos2="普通名詞", lemma="可能", kana="カノウ")
        sei = _make_token("性", "接尾辞", pos2="名詞的", lemma="性", kana="セイ")

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [kanou, sei]

        # Separate service instances → separate per-file caches, each tokenizes
        # its own fresh mock_subs (matches the sibling symmetry test above).
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=self._make_mock_subs(line_spec)),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            counts = SubtitleParserService(test_config).count_lemmas(sub_file)

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=self._make_mock_subs(line_spec)),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            words = SubtitleParserService(test_config).parse_subtitle_file(sub_file)

        mined_lemmas = {w.lemma for w in words}
        # The dropped compound must not be counted while it is un-mined.
        assert "可能性" not in mined_lemmas  # str.find fails on the spaced surface
        assert "可能性" not in counts  # count must mirror the same drop
        # Both paths agree (here: both empty for this single-compound line).
        assert set(counts.keys()) == mined_lemmas

    # ------------------------------------------------------------------
    # 3. Empty / content-free file → empty Counter
    # ------------------------------------------------------------------

    def test_empty_subtitle_file_returns_empty_counter(self, test_config, tmp_path):
        """A subtitle file with no lines yields an empty Counter."""
        sub_file = tmp_path / "empty.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([]))

        mock_tagger = MagicMock()

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert counts == {}
        assert isinstance(counts, dict)  # Counter is a dict subclass

    def test_content_free_lines_return_empty_counter(self, test_config, tmp_path):
        """Lines that clean to empty text (e.g. ASS formatting-only) yield empty Counter."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        mock_subs = self._make_mock_subs([{"text": "{\\an8}", "start": 1000, "end": 3000}])
        mock_tagger = MagicMock()

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.clean_subtitle_text", return_value=""),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert counts == {}
        mock_tagger.assert_not_called()

    def test_file_not_found_raises_subtitle_parse_error(self, test_config):
        """Should propagate SubtitleParseError from _load_subs for missing file."""
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            service = SubtitleParserService(test_config)

        with pytest.raises(SubtitleParseError, match="not found"):
            service.count_lemmas(Path("/nonexistent/file.srt"))

    # ------------------------------------------------------------------
    # 4. Real fugashi end-to-end smoke test
    # ------------------------------------------------------------------

    @pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
    def test_real_fugashi_counts_repeats(self, tmp_path):
        """Integration: real MeCab pipeline counts repeated lemmas without dedup."""
        srt_file = tmp_path / "repeat.srt"
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\n勉強する\n\n" "2\n00:00:04,000 --> 00:00:06,000\n また勉強した\n",
            encoding="utf-8",
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        counts = service.count_lemmas(srt_file)

        # 勉強 appears on both lines — must be counted twice.
        assert counts["勉強"] >= 2


# ---------------------------------------------------------------------------
# T2 perf tests — tokenize-once regression guard
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestT2TokenizeOnce:
    """T2 regression guard: each subtitle line is tokenized exactly once.

    Two tests:
    (a) Output-equivalence: threading raw_tokens through _from_tokens helpers
        must produce byte-identical output to the original text-based calls.
    (b) Call-count proof: the tagger is called exactly once per non-empty line
        for both parse_subtitle_file and parse_subtitle_file_with_index.
    """

    def _write_multi_line_srt(self, path: Path) -> Path:
        path.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\n"
            "彼は刑務所で爆発的な事件を起こした\n"
            "\n"
            "2\n00:00:06,000 --> 00:00:10,000\n"
            "学校で勉強する\n"
            "\n"
            "3\n00:00:11,000 --> 00:00:15,000\n"
            "また勉強した\n",
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------ #
    # (a) Output-equivalence                                               #
    # ------------------------------------------------------------------ #

    def test_output_equivalence_parse_subtitle_file(self, tmp_path):
        """parse_subtitle_file: raw_tokens path produces byte-identical furigana/reading."""
        srt = self._write_multi_line_srt(tmp_path / "equiv.srt")
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            bold_target_in_sentence=True,
        )
        service = SubtitleParserService(config)
        words = service.parse_subtitle_file(srt)

        assert words, "expected at least one mined word from the fixture"
        for w in words:
            expected_furi = generate_furigana(w.sentence, service.tagger)
            expected_read = generate_reading(w.sentence, service.tagger)
            # bold_end (not surface_end): the fixture's 起こした extends over
            # its auxiliary since the deinflection-span fix.
            expected_bold = wrap_target_furigana(w.sentence, service.tagger, w.surface_start, w.bold_end)
            assert w.sentence_furigana == expected_furi, (
                f"sentence_furigana mismatch for {w.surface!r}: " f"{w.sentence_furigana!r} != {expected_furi!r}"
            )
            assert w.sentence_reading == expected_read, (
                f"sentence_reading mismatch for {w.surface!r}: " f"{w.sentence_reading!r} != {expected_read!r}"
            )
            assert w.sentence_furigana_bolded == expected_bold, (
                f"sentence_furigana_bolded mismatch for {w.surface!r}: "
                f"{w.sentence_furigana_bolded!r} != {expected_bold!r}"
            )

    def test_output_equivalence_parse_subtitle_file_with_index(self, tmp_path):
        """parse_subtitle_file_with_index: raw_tokens path produces byte-identical output
        and all_words matches parse_subtitle_file exactly."""
        srt = self._write_multi_line_srt(tmp_path / "equiv2.srt")
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            bold_target_in_sentence=True,
        )
        service = SubtitleParserService(config)
        legacy = service.parse_subtitle_file(srt)
        new_words, _ = service.parse_subtitle_file_with_index(srt)

        # all_words from both methods must be identical.
        assert [w.lemma for w in legacy] == [w.lemma for w in new_words]
        assert [w.sentence_furigana for w in legacy] == [w.sentence_furigana for w in new_words]
        assert [w.sentence_reading for w in legacy] == [w.sentence_reading for w in new_words]
        assert [w.sentence_furigana_bolded for w in legacy] == [w.sentence_furigana_bolded for w in new_words]

        # Each word's fields must also match the reference text-based calls.
        assert new_words, "expected at least one mined word from the fixture"
        for w in new_words:
            expected_furi = generate_furigana(w.sentence, service.tagger)
            expected_read = generate_reading(w.sentence, service.tagger)
            # bold_end covers the full inflected form (起こした in line 1).
            expected_bold = wrap_target_furigana(w.sentence, service.tagger, w.surface_start, w.bold_end)
            assert w.sentence_furigana == expected_furi
            assert w.sentence_reading == expected_read
            assert w.sentence_furigana_bolded == expected_bold

    # ------------------------------------------------------------------ #
    # (b) Call-count proof (fails before T2, passes after)               #
    # ------------------------------------------------------------------ #

    def test_tagger_called_once_per_line_parse_subtitle_file(self, tmp_path):
        """parse_subtitle_file: tagger is called exactly once per non-empty line.

        Before T2 each full-sentence text triggered 3 tagger calls (tokenize +
        sentence_furigana + sentence_reading). After T2 it is exactly 1.
        Per-word expression calls pass a single mined form (never the full
        sentence text), so they don't match the full-line filter.
        """
        srt = self._write_multi_line_srt(tmp_path / "count.srt")
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        real_tagger = service.tagger
        line_texts = [
            "彼は刑務所で爆発的な事件を起こした",
            "学校で勉強する",
            "また勉強した",
        ]

        spy = CountingSpy(real_tagger)
        service.tagger = spy
        service.parse_subtitle_file(srt)

        for line_text in line_texts:
            count = spy.calls.count(line_text)
            assert count == 1, (
                f"Expected exactly 1 tagger call for line {line_text!r}; got {count}. " f"All calls: {spy.calls}"
            )

    def test_tagger_called_once_per_line_parse_subtitle_file_with_index(self, tmp_path):
        """parse_subtitle_file_with_index: tagger is called exactly once per non-empty line."""
        srt = self._write_multi_line_srt(tmp_path / "count2.srt")
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        real_tagger = service.tagger
        line_texts = [
            "彼は刑務所で爆発的な事件を起こした",
            "学校で勉強する",
            "また勉強した",
        ]

        spy = CountingSpy(real_tagger)
        service.tagger = spy
        service.parse_subtitle_file_with_index(srt)

        for line_text in line_texts:
            count = spy.calls.count(line_text)
            assert count == 1, (
                f"Expected exactly 1 tagger call for line {line_text!r}; got {count}. " f"All calls: {spy.calls}"
            )


class TestPerFileLineCache:
    """Tests for the per-file tokenization cache (Task 5).

    The cache must make a second parse of the SAME unchanged file skip MeCab,
    while an mtime change forces a fresh re-tokenization. Output must remain
    byte-identical to an uncached parse.
    """

    @staticmethod
    def _make_mock_subs(lines):
        mock_lines = []
        for spec in lines:
            ml = MagicMock()
            ml.text = spec["text"]
            ml.start = spec["start"]
            ml.end = spec["end"]
            mock_lines.append(ml)
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(side_effect=lambda: iter(mock_lines))
        return mock_subs

    def test_iter_parsed_lines_cached_by_mtime(self, test_config, tmp_path):
        """Second parse of an unchanged file must not re-invoke the tagger.

        After an mtime bump the file is re-tokenized.
        """
        import os

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        line_spec = [{"text": "猫と犬", "start": 1000, "end": 3000}]

        neko = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        inu = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        mock_tagger = MagicMock()
        mock_tagger.return_value = [neko, inu]

        # Stable mtime so the first two parses share a cache key.
        os.utime(sub_file, (1000, 1000))

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                return_value=self._make_mock_subs(line_spec),
            ) as mock_load,
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)

            first = service.count_lemmas(sub_file)
            calls_after_first = mock_tagger.call_count
            load_after_first = mock_load.call_count

            # Second parse, file unchanged: cache HIT, no tagger / load.
            second = service.count_lemmas(sub_file)

        # One tagger call per line (1 line) total across both parses.
        assert calls_after_first == 1
        assert mock_tagger.call_count == 1
        assert mock_load.call_count == load_after_first  # no reload on hit
        # Byte-identical result from the cached pass.
        assert first == second

        # Now bump the mtime -> cache MISS -> re-tokenize.
        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                return_value=self._make_mock_subs(line_spec),
            ) as mock_load2,
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            os.utime(sub_file, (2000, 2000))
            third = service.count_lemmas(sub_file)

        assert mock_tagger.call_count == 2  # re-tokenized
        assert mock_load2.call_count == 1
        assert third == first

    def test_count_lemmas_and_parse_share_cache(self, test_config, tmp_path):
        """count_lemmas then parse_subtitle_file on one instance hits the cache.

        Total tagger calls must equal the number of lines (one tokenize pass),
        NOT 2x lines. The deck-builder double-parse is exactly this pattern.
        """
        import os

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")
        os.utime(sub_file, (1000, 1000))

        line_spec = [
            {"text": "猫", "start": 1000, "end": 3000},
            {"text": "犬", "start": 4000, "end": 6000},
        ]
        neko = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        inu = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        mock_tagger = MagicMock()
        # Two lines -> two distinct tokenize results on the cache-fill pass.
        # generate_furigana/reading/wrap_* also call the tagger; isolate the
        # _iter_parsed_lines tokenize count by stubbing those helpers.
        mock_tagger.side_effect = [[neko], [inu]]

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                return_value=self._make_mock_subs(line_spec),
            ),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="fg"),
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="rd"),
        ):
            service = SubtitleParserService(test_config)

            counts = service.count_lemmas(sub_file)
            words = service.parse_subtitle_file(sub_file)

        # Exactly two tokenize calls (one per line), shared across both methods.
        assert mock_tagger.call_count == 2
        assert counts["猫"] == 1
        assert counts["犬"] == 1
        assert {w.lemma for w in words} == {"猫", "犬"}


class TestAbandonedGeneratorCacheNonCommit:
    """A consumer that abandons ``_iter_parsed_lines`` early must NOT leave a
    truncated per-file cache entry.

    ``_iter_parsed_lines`` yields lazily and only commits the line-state to
    ``_line_cache`` once the generator is fully drained. A consumer that stops
    after a few lines (here via ``itertools.islice``) therefore commits nothing,
    so a later ``count_lemmas`` re-tokenizes the whole file from scratch instead
    of replaying a partial — otherwise the corpus counts would silently drop the
    lines the abandoned pass never reached.
    """

    @staticmethod
    def _make_mock_subs(lines):
        mock_lines = []
        for spec in lines:
            ml = MagicMock()
            ml.text = spec["text"]
            ml.start = spec["start"]
            ml.end = spec["end"]
            mock_lines.append(ml)
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(side_effect=lambda: iter(mock_lines))
        return mock_subs

    def test_islice_abandon_then_count_lemmas_retokenizes(self, test_config, tmp_path):
        import itertools
        import os

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")
        os.utime(sub_file, (1000, 1000))

        line_spec = [
            {"text": "猫", "start": 1000, "end": 3000},
            {"text": "犬", "start": 4000, "end": 6000},
            {"text": "鳥", "start": 7000, "end": 9000},
        ]
        # Text-keyed so re-tokenizing yields the same surfaces (str.find aligns)
        # regardless of how many passes occur — only the call COUNT changes.
        by_text = {
            "猫": [_make_token("猫", "名詞", lemma="猫", kana="ネコ")],
            "犬": [_make_token("犬", "名詞", lemma="犬", kana="イヌ")],
            "鳥": [_make_token("鳥", "名詞", lemma="鳥", kana="トリ")],
        }
        mock_tagger = MagicMock(side_effect=lambda text: list(by_text[text]))

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                return_value=self._make_mock_subs(line_spec),
            ),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value="fg"),
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value="rd"),
        ):
            service = SubtitleParserService(test_config)

            # Abandon after the first line: only one tokenize call, NO commit.
            gen = service._iter_parsed_lines(sub_file)
            partial = list(itertools.islice(gen, 1))
            assert len(partial) == 1
            assert mock_tagger.call_count == 1
            assert service._line_cache == {}, "abandoned generator left a truncated cache entry"

            # count_lemmas must re-tokenize all three lines (cache had nothing).
            counts = service.count_lemmas(sub_file)

        # 1 (abandoned partial) + 3 (full re-tokenize) = 4 tagger calls.
        assert mock_tagger.call_count == 4
        # All three lines counted — none dropped by a stale partial cache.
        assert counts["猫"] == 1
        assert counts["犬"] == 1
        assert counts["鳥"] == 1

    def test_full_drain_does_commit_then_count_lemmas_hits_cache(self, test_config, tmp_path):
        """Control: fully draining the SAME generator DOES commit, so a follow-up
        count_lemmas serves from cache and adds no tagger calls."""
        import os

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")
        os.utime(sub_file, (1000, 1000))

        line_spec = [
            {"text": "猫", "start": 1000, "end": 3000},
            {"text": "犬", "start": 4000, "end": 6000},
        ]
        by_text = {
            "猫": [_make_token("猫", "名詞", lemma="猫", kana="ネコ")],
            "犬": [_make_token("犬", "名詞", lemma="犬", kana="イヌ")],
        }
        mock_tagger = MagicMock(side_effect=lambda text: list(by_text[text]))

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                return_value=self._make_mock_subs(line_spec),
            ),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)

            # Drain fully -> commit.
            drained = list(service._iter_parsed_lines(sub_file))
            assert len(drained) == 2
            assert mock_tagger.call_count == 2
            assert service._line_cache, "full drain failed to commit the cache entry"

            counts = service.count_lemmas(sub_file)

        # No new tokenize calls — count_lemmas replayed the committed cache.
        assert mock_tagger.call_count == 2
        assert counts["猫"] == 1
        assert counts["犬"] == 1


# ---------------------------------------------------------------------------
# OVH-006 — ASS/SSA Comment lines must be skipped
# ---------------------------------------------------------------------------


class TestASSCommentFilter:
    """ASS/SSA Comment events must not be tokenized or returned (OVH-006).

    pysubs2 SSAEvent.is_comment is True for ``Comment:`` lines (karaoke,
    sign TL, staff credits).  SRT/VTT mocks lack the attribute entirely;
    getattr(..., False) must leave them unaffected.
    """

    @staticmethod
    def _make_line(text: str, start: int = 1000, end: int = 3000, *, is_comment: bool = False):
        line = MagicMock()
        line.text = text
        line.start = start
        line.end = end
        line.is_comment = is_comment
        return line

    @staticmethod
    def _make_line_no_attr(text: str, start: int = 1000, end: int = 3000):
        """SRT-style mock: no is_comment attribute."""
        line = MagicMock(spec=["text", "start", "end"])
        line.text = text
        line.start = start
        line.end = end
        return line

    def _make_service_with_tagger(self, test_config, mock_tagger):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger):
            return SubtitleParserService(test_config)

    def test_comment_line_excluded_from_parse_subtitle_file(self, test_config, tmp_path):
        """Comment-only token must not appear in parse_subtitle_file output."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        dialogue_token = _make_token("猫", "名詞", lemma="猫", kana="ネコ")

        dialogue_line = self._make_line("猫", is_comment=False)
        comment_line = self._make_line("カラオケ", is_comment=True)

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([dialogue_line, comment_line]))

        mock_tagger = MagicMock()
        mock_tagger.return_value = [dialogue_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        lemmas = {w.lemma for w in words}
        assert "カラオケ" not in lemmas, "Comment-line token must not appear in mining output"
        assert "猫" in lemmas

    def test_comment_line_excluded_from_count_lemmas(self, test_config, tmp_path):
        """Comment-only token must not contribute to count_lemmas (Deck Builder coverage)."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        dialogue_token = _make_token("猫", "名詞", lemma="猫", kana="ネコ")

        dialogue_line = self._make_line("猫", is_comment=False)
        comment_line = self._make_line("カラオケ", is_comment=True)

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([dialogue_line, comment_line]))

        mock_tagger = MagicMock()
        mock_tagger.return_value = [dialogue_token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts = service.count_lemmas(sub_file)

        assert "カラオケ" not in counts, "Comment-line token must not be counted"
        assert counts["猫"] == 1

    def test_comment_line_excluded_from_parse_raw_entries(self, test_config, tmp_path):
        """Comment lines must not appear in parse_raw_entries output."""
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        dialogue_line = self._make_line("猫が好き", is_comment=False)
        comment_line = self._make_line("Staff: Alice", is_comment=True)

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([dialogue_line, comment_line]))

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger"),
        ):
            service = SubtitleParserService(test_config)
            entries = service.parse_raw_entries(sub_file)

        texts = [e[2] for e in entries]
        assert not any("Staff" in t for t in texts), "Comment-line text must not appear in raw entries"
        assert any("猫が好き" in t for t in texts)

    def test_srt_line_without_is_comment_attr_unaffected(self, test_config, tmp_path):
        """SRT lines without is_comment must still be parsed normally."""
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        srt_line = self._make_line_no_attr("犬")
        token = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([srt_line]))

        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert any(w.lemma == "犬" for w in words), "SRT lines without is_comment must not be skipped"


# ---------------------------------------------------------------------------
# OVH-012 — _line_cache must hold per-file entries (multi-file cross-phase)
# ---------------------------------------------------------------------------


class TestLineCacheMultiFile:
    """Per-file line cache must survive across files so Deck Builder Phase-1 →
    Phase-2 reuse covers ALL files, not just the last one (OVH-012).
    """

    @staticmethod
    def _make_file_subs(text: str):
        line = MagicMock()
        line.text = text
        line.start = 1000
        line.end = 3000
        line.is_comment = False
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(side_effect=lambda: iter([line]))
        return mock_subs

    def test_two_files_both_cached(self, test_config, tmp_path):
        """After parsing file A then file B, both must be in the cache."""
        import os

        file_a = tmp_path / "a.srt"
        file_b = tmp_path / "b.srt"
        file_a.write_text("placeholder", encoding="utf-8")
        file_b.write_text("placeholder", encoding="utf-8")
        os.utime(file_a, (1000, 1000))
        os.utime(file_b, (2000, 2000))

        token_a = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        token_b = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        subs_a = self._make_file_subs("猫")
        subs_b = self._make_file_subs("犬")

        mock_tagger = MagicMock()
        mock_tagger.side_effect = [[token_a], [token_b]]

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                side_effect=lambda p: subs_a if "a.srt" in p else subs_b,
            ),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            service.count_lemmas(file_a)
            service.count_lemmas(file_b)

        assert file_a.resolve() in service._line_cache, "file A must remain cached after file B is parsed"
        assert file_b.resolve() in service._line_cache, "file B must also be cached"

    def test_parse_file_a_after_file_b_is_cache_hit(self, test_config, tmp_path):
        """count_lemmas(A) → count_lemmas(B) → count_lemmas(A) must not re-tokenize A."""
        import os

        file_a = tmp_path / "a.srt"
        file_b = tmp_path / "b.srt"
        file_a.write_text("placeholder", encoding="utf-8")
        file_b.write_text("placeholder", encoding="utf-8")
        os.utime(file_a, (1000, 1000))
        os.utime(file_b, (2000, 2000))

        token_a = _make_token("猫", "名詞", lemma="猫", kana="ネコ")
        token_b = _make_token("犬", "名詞", lemma="犬", kana="イヌ")

        subs_a = self._make_file_subs("猫")
        subs_b = self._make_file_subs("犬")

        mock_tagger = MagicMock()
        # Only two real tokenize calls expected: one for A, one for B.
        # The third call (A again) must be a cache hit → no tagger call.
        mock_tagger.side_effect = [[token_a], [token_b]]

        with (
            patch(
                "anki_miner.services.subtitle_parser.pysubs2.load",
                side_effect=lambda p: subs_a if "a.srt" in p else subs_b,
            ),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            counts_a1 = service.count_lemmas(file_a)  # fills cache for A
            assert mock_tagger.call_count == 1

            counts_b = service.count_lemmas(file_b)  # fills cache for B; A must remain
            assert mock_tagger.call_count == 2

            counts_a2 = service.count_lemmas(file_a)  # must be a cache hit (no new tagger call)
            assert (
                mock_tagger.call_count == 2
            ), "Third parse of file A re-tokenized; cache must hold both A and B entries"

        assert counts_a1 == counts_a2
        assert counts_b["犬"] == 1


# --- Dictionary-attested compound matching (services/compound_matcher.py) ---


def _lookup_for(dictionary: set):
    """Fake TermLookup: attests exactly the given headword set."""
    return lambda terms: dictionary & set(terms)


def _write_srt(tmp_path, name, line):
    srt_file = tmp_path / name
    srt_file.write_text(
        "1\n" f"00:00:01,000 --> 00:00:05,000\n" f"{line}\n",
        encoding="utf-8",
    )
    return srt_file


class TestCompoundMatchingParserIntegration:
    """Mock-tagger coverage of the matcher seam in _iter_parsed_lines."""

    def _parse(self, tmp_path, test_config, text, tokens, dictionary, **parser_kwargs):
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("stub", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = text
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        mock_tagger = MagicMock()
        mock_tagger.return_value = tokens
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config, term_lookup=_lookup_for(dictionary), **parser_kwargs)
            return service.parse_subtitle_file(sub_file)

    def _hashiridashita_tokens(self):
        return [
            _make_token("走り", "動詞", "一般", lemma="走る", kana="ハシリ", orth_base="走る"),
            _make_token("出し", "動詞", "非自立可能", lemma="出す", kana="ダシ", orth_base="出す"),
            _make_token("た", "助動詞", "*", lemma="た", kana="タ"),
        ]

    def test_merged_word_offsets_and_component_suppression(self, tmp_path, test_config):
        words = self._parse(tmp_path, test_config, "走り出した", self._hashiridashita_tokens(), {"走り出す"})

        assert [w.surface for w in words] == ["走り出し"]
        word = words[0]
        assert word.lemma == "走り出す"
        assert word.mined_form == "走り出す"
        assert word.surface_start == 0
        assert word.surface_end == 4  # 走り出し
        # No fragment cards for the components.
        assert not any(w.lemma in ("走る", "出す") for w in words)

    def test_internal_whitespace_offsets(self, tmp_path, test_config):
        """Issue #20 regression: MeCab drops whitespace from the token stream,
        so the merged surface must be located via find, not cursor arithmetic."""
        tokens = [_make_token("ねえ", "感動詞", "一般", kana="ネエ")] + self._hashiridashita_tokens()
        words = self._parse(tmp_path, test_config, "ねえ 走り出した", tokens, {"走り出す"})
        assert len(words) == 1
        assert words[0].surface_start == 3
        assert words[0].surface_end == 7

    def test_sentence_bolded_wraps_full_compound(self, tmp_path, test_config):
        from dataclasses import replace as dc_replace

        config = dc_replace(test_config, bold_target_in_sentence=True)
        words = self._parse(tmp_path, config, "走り出した", self._hashiridashita_tokens(), {"走り出す"})
        # highlight extends over the trailing auxiliary chain: 走り出した
        assert "<b>走り出した</b>" in words[0].sentence_bolded

    def test_standalone_component_still_mined_without_compound(self, tmp_path, test_config):
        tokens = [_make_token("出し", "動詞", "非自立可能", lemma="出す", kana="ダシ", orth_base="出す")]
        words = self._parse(tmp_path, test_config, "出した", tokens, {"走り出す"})
        assert [w.lemma for w in words] == ["出す"]

    def test_compound_reading_regenerated_not_concat_kana(self, tmp_path, test_config):
        """word.reading (curation dialog / TSV export) must be the headword's
        regenerated reading, not concatenated component kana."""
        tokens = [
            _make_token("気", "名詞", "普通名詞", lemma="気", kana="キ"),
            _make_token("が", "助詞", "格助詞", lemma="が", kana="ガ"),
            _make_token("し", "動詞", "非自立可能", lemma="為る", kana="シ", orth_base="する"),
            _make_token("た", "助動詞", "*", lemma="た", kana="タ"),
        ]
        words = self._parse(tmp_path, test_config, "気がした", tokens, {"気がする"})
        assert len(words) == 1
        word = words[0]
        assert word.lemma == "気がする"
        # generate_reading runs the real tagger inside the mocked context —
        # here the mock returns our token list for any input, so just assert
        # the concat artifact (particle kana + non-base stem) is NOT used.
        assert word.reading != "キガシ"

    def test_term_lookup_none_byte_identical(self, tmp_path, test_config):
        """No lookup injected → output equals the pre-feature parser exactly."""
        tokens_a = self._hashiridashita_tokens()
        tokens_b = self._hashiridashita_tokens()

        sub_file = tmp_path / "test.srt"
        sub_file.write_text("stub", encoding="utf-8")

        def run(tokens, **kwargs):
            mock_line = MagicMock()
            mock_line.text = "走り出した"
            mock_line.start = 1000
            mock_line.end = 3000
            mock_subs = MagicMock()
            mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
            mock_tagger = MagicMock()
            mock_tagger.return_value = tokens
            with (
                patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
                patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            ):
                return SubtitleParserService(test_config, **kwargs).parse_subtitle_file(sub_file)

        assert run(tokens_a) == run(tokens_b, term_lookup=None)

    def test_toggle_off_disables_matching(self, tmp_path, test_config):
        from dataclasses import replace as dc_replace

        config = dc_replace(test_config, compound_matching=False)
        words = self._parse(tmp_path, config, "走り出した", self._hashiridashita_tokens(), {"走り出す"})
        assert [w.lemma for w in words] == ["走る", "出す"]

    def test_index_and_count_paths_carry_compound(self, tmp_path, test_config):
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("stub", encoding="utf-8")

        def make_ctx():
            mock_line = MagicMock()
            mock_line.text = "走り出した"
            mock_line.start = 1000
            mock_line.end = 3000
            mock_subs = MagicMock()
            mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
            mock_tagger = MagicMock()
            mock_tagger.return_value = self._hashiridashita_tokens()
            return mock_subs, mock_tagger

        mock_subs, mock_tagger = make_ctx()
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config, term_lookup=_lookup_for({"走り出す"}))
            words, lines = service.parse_subtitle_file_with_index(sub_file)
            counts = service.count_lemmas(sub_file)

        # T-38 parity: index, mining and counting all see the compound lemma.
        assert [w.lemma for w in words] == ["走り出す"]
        assert lines[0].lemmas == {"走り出す"}
        assert counts["走り出す"] == 1
        assert "走る" not in counts and "出す" not in counts


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestCompoundMatchingRealFugashi:
    """End-to-end matcher behavior over real unidic tokenization."""

    def _mine(self, tmp_path, line, dictionary, name="compound.srt"):
        srt_file = _write_srt(tmp_path, name, line)
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config, term_lookup=_lookup_for(dictionary))
        return service.parse_subtitle_file(srt_file)

    def test_hashiridashita_mines_hashiridasu(self, tmp_path):
        words = self._mine(tmp_path, "彼は急に走り出した。", {"走り出す"})
        by_lemma = {w.lemma: w for w in words}
        assert "走り出す" in by_lemma
        word = by_lemma["走り出す"]
        assert word.mined_form == "走り出す"
        assert word.surface == "走り出し"
        assert word.expression_furigana  # non-empty, regenerated from headword
        # No fragment cards from the compound's components.
        assert "走る" not in by_lemma
        assert "出す" not in by_lemma

    def test_oukyuushochi_mined_whole(self, tmp_path):
        words = self._mine(tmp_path, "応急処置が必要だ。", {"応急処置"})
        by_lemma = {w.lemma: w for w in words}
        assert "応急処置" in by_lemma
        assert by_lemma["応急処置"].mined_form == "応急処置"
        assert "応急" not in by_lemma
        assert "処置" not in by_lemma

    def test_kigashita_mines_kigasuru_via_orth_base(self, tmp_path):
        """為る-blocker regression: unidic lemma of し is 為る; orthBase する
        must drive the candidate so 気がする is found."""
        words = self._mine(tmp_path, "嫌な気がした。", {"気がする"})
        by_lemma = {w.lemma: w for w in words}
        assert "気がする" in by_lemma
        word = by_lemma["気がする"]
        assert word.mined_form == "気がする"
        assert word.reading == "きがする"  # regenerated, not キガシ

    def test_collocation_swallows_components_by_design(self, tmp_path):
        """D4: an attested object+verb collocation replaces its components."""
        words = self._mine(tmp_path, "結論を出した。", {"結論を出す"})
        by_lemma = {w.lemma: w for w in words}
        assert "結論を出す" in by_lemma
        assert "結論" not in by_lemma
        assert "出す" not in by_lemma

    def test_no_dictionary_hits_keeps_current_behavior(self, tmp_path):
        srt_file = _write_srt(tmp_path, "plain.srt", "彼は急に走り出した。")
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        with_lookup = SubtitleParserService(config, term_lookup=_lookup_for(set()))
        without = SubtitleParserService(config)
        assert with_lookup.parse_subtitle_file(srt_file) == without.parse_subtitle_file(srt_file)
