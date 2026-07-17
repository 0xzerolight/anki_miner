"""Tests for subtitle_parser module."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SubtitleParseError
from anki_miner.models import LineLemmas, TokenizedWord
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.compound_matcher import CompoundSyntheticToken
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils import generate_furigana, generate_reading
from anki_miner.utils.text_utils import wrap_target_furigana

# --- Helpers for building mock MeCab tokens ---


def _make_token(
    surface,
    pos1,
    pos2=None,
    lemma=None,
    kana=None,
    orth_base=None,
    l_form=None,
    kana_base=None,
):
    """Build a mock fugashi word token with feature attributes.

    ``orthBase`` defaults to the lemma (real UniDic tokens usually agree);
    it must always be set explicitly — an auto-created MagicMock attribute
    is truthy and would leak into ``mined_form``. ``lForm``/``kanaBase``
    (lemma/orthBase readings — mining_base's fold trigger) are pinned to
    the given values (default None) for the same reason: an auto-created
    Mock attribute would be truthy but non-str, silently exercising
    mining_base's isinstance guard in every test. Set both explicitly when
    testing the fold.
    """
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    token.feature.orthBase = orth_base if orth_base is not None else token.feature.lemma
    token.feature.lForm = l_form
    token.feature.kanaBase = kana_base
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

    def test_parses_cp932_shift_jis_encoded_subtitle(self, test_config, tmp_path):
        """A cp932/Shift-JIS-encoded subtitle loads via the encoding fallback (Bug J5).

        pysubs2 defaults to UTF-8, so a cp932 file used to raise
        UnicodeDecodeError and fail the whole episode. ``_load_subs`` now retries
        cp932 (the dominant non-UTF-8 input) before consulting the detector, so
        real Shift-JIS subtitles parse instead of aborting the run.
        """
        srt = "1\r\n00:00:01,000 --> 00:00:03,000\r\n本を読む\r\n\r\n"
        sub_file = tmp_path / "cp932.srt"
        sub_file.write_bytes(srt.encode("cp932"))

        service = SubtitleParserService(test_config)
        words = service.parse_subtitle_file(sub_file)

        lemmas = {w.lemma for w in words}
        assert "本" in lemmas
        assert "読む" in lemmas

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
        # Sentence-level furigana/reading reuse raw_tokens, and a dict-form verb
        # (surface == orthBase) takes its expression reading from the token
        # itself (Task 1.2), so each line is a single tokenize call. Line 2
        # dedup-skips after tokenize. Total: 2.
        mock_tagger.side_effect = [
            [token1],  # line 1: _iter_parsed_lines tokenize
            [token2],  # line 2: _iter_parsed_lines tokenize (then dedup skip)
        ]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1

    def test_same_surface_nouns_collapse_on_mined_form(self, test_config, tmp_path):
        """mined_form dedup: same-surface nouns with distinct lemmas collapse (Bug J3).

        For nouns ``mined_form == surface``, so two 学生 tokens are one card
        front — identical definition/frequency/audio identity — regardless of
        their UniDic lemma; emitting both would be a duplicate card. (The inverse
        — same lemma, distinct surface — stays distinct; see the kanji-variant
        homograph test.)
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
        # Both lines are a single tokenize call: line 2's token dedup-skips on
        # mined_form (学生) before any reading lookup. Total: 2.
        mock_tagger.side_effect = [
            [token1],  # line 1: tokenize
            [token2],  # line 2: tokenize (then mined_form dedup skip)
        ]

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 1
        assert words[0].mined_form == "学生"

    def test_kanji_variant_homographs_sharing_lemma_both_emit(self, test_config, tmp_path):
        """Distinct-surface homographs that share a lemma both mine (Bug J3).

        UniDic collapses kanji variants onto one canonical lemma (賭ける →
        掛ける), so lemma-keyed dedup dropped the second variant even though it
        is a distinct card front. Dedup now keys on ``mined_form`` (orthBase for
        verbs), matching the card-identity used for definitions/audio/known-words.
        """
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")

        line1 = MagicMock()
        line1.text = "賭ける"
        line1.start = 1000
        line1.end = 3000

        line2 = MagicMock()
        line2.text = "掛ける"
        line2.start = 4000
        line2.end = 6000

        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([line1, line2]))

        # Same canonical lemma (掛ける), different source orthography → different
        # mined_form (orthBase). Verbs mine as orthBase.
        token1 = _make_token("賭ける", "動詞", lemma="掛ける", kana="カケル", orth_base="賭ける")
        token2 = _make_token("掛ける", "動詞", lemma="掛ける", kana="カケル", orth_base="掛ける")

        mapping = {"賭ける": [token1], "掛ける": [token2]}
        mock_tagger = MagicMock(side_effect=lambda text: mapping.get(text, [token2]))

        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)

        assert len(words) == 2
        assert {w.mined_form for w in words} == {"賭ける", "掛ける"}

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
        """Noun token: expression furigana/reading come from the surface token.

        Task 1.2: surface-mined POS take their reading from the in-sentence
        token itself (generate_furigana_from_tokens on that token), not from a
        re-tokenized isolated pass — so the mis-lemma 剛腕 never leaks in.
        """
        sub_file = tmp_path / "test.ass"
        sub_file.write_text("placeholder", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = "彼は豪腕の投手だ"
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        token = _make_token("豪腕", "名詞", lemma="剛腕", kana="ゴウワン")
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)
        word = words[0]
        assert word.expression_furigana == "豪腕[ごうわん]"  # from surface + token kana
        assert word.expression_reading == "ごうわん"
        assert "剛腕" not in word.expression_furigana  # not the mis-lemma

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


class TestTargetReadingSingleSource:
    """Task 1.2: the card's target reading is the in-sentence token's own kana.

    For surface-mined POS (``mined_form == surface``, non-compound) the
    ExpressionReading/ExpressionFurigana flow from the context-disambiguated
    MeCab token reading, NOT from re-tokenizing the surface in isolation — so a
    polyphonic noun (方 かた/ほう) reads the way the learner heard it, and the
    JPod101/audio-pack identity pair (mined_form + expression_reading) matches.
    """

    def _parse_one(self, test_config, tmp_path, line_text, token, *, wrong_furigana, wrong_reading):
        """Parse a single line whose only token is ``token``.

        The isolated-re-tokenization helpers ``generate_furigana`` /
        ``generate_reading`` are stubbed to deliberately WRONG values, so an
        assertion on the emitted reading proves whether the surface-mined path
        consulted them (verb/compound path) or the token's own kana (noun path).
        """
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
            patch("anki_miner.services.subtitle_parser.generate_furigana", return_value=wrong_furigana),
            patch("anki_miner.services.subtitle_parser.generate_reading", return_value=wrong_reading),
        ):
            service = SubtitleParserService(test_config)
            words = service.parse_subtitle_file(sub_file)
        return words[0]

    def test_context_noun_reading_from_token_not_isolated(self, test_config, tmp_path):
        """方 read ほう in context: emitted reading tracks the token, not re-tokenization."""
        token = _make_token("方", "名詞", lemma="方", kana="ホウ")
        word = self._parse_one(
            test_config,
            tmp_path,
            "こっちの方がいい",
            token,
            wrong_furigana="方[かた]",
            wrong_reading="かた",
        )
        assert word.expression_reading == "ほう"
        assert word.expression_furigana == "方[ほう]"

    def test_context_noun_other_reading_tracks_token(self, test_config, tmp_path):
        """Same surface with the other contextual kana → the other reading."""
        token = _make_token("方", "名詞", lemma="方", kana="カタ")
        word = self._parse_one(
            test_config,
            tmp_path,
            "あの方",
            token,
            wrong_furigana="方[ほう]",
            wrong_reading="ほう",
        )
        assert word.expression_reading == "かた"
        assert word.expression_furigana == "方[かた]"

    def test_katakana_noun_folds_to_hiragana(self, test_config, tmp_path):
        """Katakana loanword surface: reading folds to hiragana, no furigana."""
        token = _make_token("コーヒー", "名詞", lemma="コーヒー", kana="コーヒー")
        word = self._parse_one(
            test_config,
            tmp_path,
            "コーヒーを飲む",
            token,
            wrong_furigana="WRONG",
            wrong_reading="わるい",
        )
        assert word.expression_reading == "こーひー"
        assert word.expression_furigana == "コーヒー"

    def test_verb_orthbase_reading_unchanged(self, test_config, tmp_path):
        """Regression: a conjugated verb keeps the isolated orthBase reading.

        mined orthBase 蒔く ≠ surface 蒔い, so the reading must come from
        ``generate_reading(mined)`` (stubbed まく), never the surface kana マイ.
        """
        token = _make_token("蒔い", "動詞", lemma="蒔く", kana="マイ", orth_base="蒔く")
        word = self._parse_one(
            test_config,
            tmp_path,
            "種を蒔いた",
            token,
            wrong_furigana="蒔く[まく]",
            wrong_reading="まく",
        )
        assert word.mined_form == "蒔く"
        assert word.expression_reading == "まく"
        assert word.expression_furigana == "蒔く[まく]"
        assert word.expression_reading != "まい"  # not the surface token kana

    def test_dict_form_verb_matches_token_reading(self, test_config, tmp_path):
        """A dictionary-form verb (surface == orthBase) also reads from its token.

        ``mined == surface`` here, so the surface-mined path applies; the token
        kana タベル and the isolated reading agree, so the result is stable.
        """
        token = _make_token("食べる", "動詞", lemma="食べる", kana="タベル")
        word = self._parse_one(
            test_config,
            tmp_path,
            "パンを食べる",
            token,
            wrong_furigana="WRONG",
            wrong_reading="WRONG",
        )
        assert word.expression_reading == "たべる"
        assert word.expression_furigana == "食[た]べる"

    def test_compound_synthetic_keeps_headword_reading(self, test_config, tmp_path):
        """Regression: a compound synthetic keeps the headword-regenerated reading.

        Its concatenated component kana (キガシ) is wrong, so the reading must
        come from ``self._reading`` (stubbed きがする), not ``extract_reading``.
        """
        token = CompoundSyntheticToken(
            surface="気がする",
            pos1="動詞",
            pos2="非自立可能",
            lemma="気がする",
            kana="キガシ",
        )
        word = self._parse_one(
            test_config,
            tmp_path,
            "彼は気がする",
            token,
            wrong_furigana="気がする[きがする]",
            wrong_reading="きがする",
        )
        assert word.expression_reading == "きがする"
        assert word.expression_reading != "きがし"  # not the concatenated component kana


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

        Both lines carry the same conjugated verb (surface 食べた, orthBase mined
        form 食べる), so the isolated ``generate_furigana(食べる)`` path runs
        rather than the surface-token path nouns take (Task 1.2). The global
        lemma dedup means only line-1's word is emitted, but without caching
        line-2's word would still trigger generate_furigana("食べる"); with the
        cache the call is served from _fg_cache and not invoked a second time.

        We patch both generate_furigana AND generate_reading (and the tagger, to
        avoid StopIteration from the mock) so the only actual calls observed in
        mock_fg.call_args_list come from the parser's own invocations — not from
        nested tagger usage inside the real util functions.
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        # Two lines; both tokenize to the same conjugated verb (mined 食べる).
        # Distinct sentence text ensures sentence-level calls don't accidentally
        # match "食べる" and inflate the expression-level count.
        taberux2_subs = self._make_subs_two_lines("食べた", "また食べた")
        token_taberu = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ", orth_base="食べる")
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
        entries from the first parse. Uses a conjugated verb (surface 食べた ≠
        orthBase 食べる) so the memoized isolated ``generate_furigana(mined)``
        path is exercised — surface-mined nouns bypass it (Task 1.2).
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        token = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ", orth_base="食べる")

        def make_mock_subs():
            mock_line = MagicMock()
            mock_line.text = "食べた"
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
        """Same mined form on two lines → generate_furigana called once (with_index path).

        Conjugated verb (surface 食べた, mined orthBase 食べる) so the isolated
        ``generate_furigana`` path is exercised; nouns bypass it (Task 1.2).
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        taberux2_subs = self._make_subs_two_lines("食べた", "また食べた")
        token_taberu = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ", orth_base="食べる")
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
        """Cache reset between two parse_subtitle_file_with_index calls.

        Conjugated verb (surface 食べた ≠ orthBase 食べる) so the memoized
        isolated ``generate_furigana(mined)`` path runs; surface-mined nouns
        bypass it (Task 1.2).
        """
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("placeholder", encoding="utf-8")

        token = _make_token("食べた", "動詞", lemma="食べる", kana="タベタ", orth_base="食べる")

        def make_mock_subs():
            mock_line = MagicMock()
            mock_line.text = "食べた"
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


def _attest_lookup(*attested):
    """Spy term-OR-reading existence probe (has_offline_definitions shape).

    Returns a callable ``list[str] -> dict[str, bool]`` that reports each input
    True iff it is in ``attested``, and records every call on ``.calls`` so
    tests can assert the lookup was (or was not) invoked and how often.
    """
    aset = set(attested)
    calls: list[list[str]] = []

    def lookup(words):
        calls.append(list(words))
        return {w: (w in aset) for w in words}

    lookup.calls = calls  # type: ignore[attr-defined]
    return lookup


class TestKanaWordRecovery:
    """Parser-seam recovery of pure-hiragana content words the script gate drops.

    _should_include_word admits a token should_include rejects when ALL hold:
    POS ∈ {動詞,形容詞,形状詞} (never 名詞), content_gate_ok passes, the surface
    is pure hiragana, and its mined-form card front is attested via the injected
    term-OR-reading existence probe. No probe wired ⇒ today's behavior.
    """

    def _service(self, test_config, lookup):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config, kana_attest_lookup=lookup)

    def test_recovers_keijoushi_surface_front(self, test_config):
        # 形状詞 きれい: mined_form is the surface (きれい); attested as 綺麗's
        # reading in real JMdict, so a term-OR-reading probe finds it.
        lookup = _attest_lookup("きれい")
        service = self._service(test_config, lookup)
        token = _make_token("きれい", "形状詞", pos2="一般", lemma="奇麗", orth_base="きれい")
        assert service._should_include_word(token) is True
        assert lookup.calls == [["きれい"]]  # probed the mined-form card front

    @pytest.mark.parametrize(
        ("context", "surface", "lemma", "orth_base"),
        [
            ("見ている", "いる", "居る", "いる"),
            ("そこにある", "ある", "有る", "ある"),
            ("買ってくれる", "くれる", "呉れる", "くれる"),
            ("書いておく", "おく", "置く", "おく"),
            ("読んでしまう", "しまう", "仕舞う", "しまう"),
        ],
    )
    def test_does_not_recover_auxiliary_capable_verb(self, test_config, context, surface, lemma, orth_base):
        # 動詞 pos2=非自立可能 (real unidic tag for いる/ある/くれる/おく/しまう —
        # in AUX context and standalone alike, the tokens are byte-identical).
        # Attested on purpose: the pos2 backstop, not a dict miss, must drop them,
        # or every ている line mints an いる card. Standalone main-verb uses are
        # the accepted casualty (see _KANA_RECOVER_REJECT_POS2).
        lookup = _attest_lookup(surface)
        service = self._service(test_config, lookup)
        token = _make_token(surface, "動詞", pos2="非自立可能", lemma=lemma, orth_base=orth_base)
        assert service._should_include_word(token) is False, context
        assert lookup.calls == []  # pos2 gate short-circuits before the lookup

    @pytest.mark.parametrize(
        ("surface", "lemma"),
        [("すごい", "凄い"), ("かわいい", "可愛い"), ("あざとい", "あざとい"), ("しがない", "しがない")],
    )
    def test_recovers_pure_hiragana_adjectives(self, test_config, surface, lemma):
        lookup = _attest_lookup(surface)
        service = self._service(test_config, lookup)
        token = _make_token(surface, "形容詞", pos2="一般", lemma=lemma, orth_base=surface)
        assert service._should_include_word(token) is True

    @pytest.mark.parametrize(("surface", "lemma"), [("こと", "事"), ("もの", "物"), ("ため", "為")])
    def test_does_not_recover_formal_noun(self, test_config, surface, lemma):
        # 名詞 formal nouns pass content_gate_ok but are blocked by the POS
        # backstop {動詞,形容詞,形状詞}; the probe must not even be consulted.
        lookup = _attest_lookup(surface)  # attested — proves the POS gate, not the dict, drops it
        service = self._service(test_config, lookup)
        token = _make_token(surface, "名詞", pos2="普通名詞", lemma=lemma, orth_base=surface)
        assert service._should_include_word(token) is False
        assert lookup.calls == []  # POS gate short-circuits before the lookup

    @pytest.mark.parametrize(
        ("surface", "pos1", "lemma"),
        [("って", "助詞", "って"), ("の", "助詞", "の"), ("けど", "接続詞", "けれど")],
    )
    def test_does_not_recover_grammar_fragments(self, test_config, surface, pos1, lemma):
        # 助詞 fails content_gate_ok; 接続詞 fails the POS backstop. Either way
        # not recovered and the probe is never consulted.
        lookup = _attest_lookup(surface)
        service = self._service(test_config, lookup)
        token = _make_token(surface, pos1, pos2="*", lemma=lemma, orth_base=surface)
        assert service._should_include_word(token) is False
        assert lookup.calls == []

    @pytest.mark.parametrize(
        ("construction", "surface", "lemma", "orth_base"),
        [
            ("ようだ", "よう", "様", "よう"),
            ("みたいな", "みたい", "みたい", "みたい"),
            ("みたいだ", "みたい", "みたい", "みたい"),
        ],
    )
    def test_does_not_recover_auxiliary_stem_keijoushi(self, test_config, construction, surface, lemma, orth_base):
        # 形状詞 pos2=助動詞語幹 auxiliaries (よう in ようだ, みたい in みたいな/みたいだ)
        # pass {動詞,形容詞,形状詞} + content_gate_ok and are JMdict-attested, but
        # are grammar not vocabulary. The pos2 backstop drops them before the probe.
        lookup = _attest_lookup(surface)  # attested — proves pos2 gate, not the dict, drops it
        service = self._service(test_config, lookup)
        token = _make_token(surface, "形状詞", pos2="助動詞語幹", lemma=lemma, orth_base=orth_base)
        assert service._should_include_word(token) is False, construction
        assert lookup.calls == []  # pos2 gate short-circuits before the lookup

    def test_recovers_pure_hiragana_verb_ta_inflection(self, test_config):
        # わかった → わかっ token deinflects to orthBase わかる (the mined card front),
        # a sanctioned 動詞 一般 recovery that must survive the auxiliary-stem gate.
        lookup = _attest_lookup("わかる")
        service = self._service(test_config, lookup)
        token = _make_token("わかっ", "動詞", pos2="一般", lemma="分かる", orth_base="わかる")
        assert service._should_include_word(token) is True
        assert lookup.calls == [["わかる"]]

    def test_does_not_recover_non_attested_kana(self, test_config):
        # A pure-hiragana verb the dictionary does NOT attest stays dropped.
        lookup = _attest_lookup()  # attests nothing
        service = self._service(test_config, lookup)
        token = _make_token("ぬるぽ", "動詞", pos2="一般", lemma="ぬるぽ", orth_base="ぬるぽ")
        assert service._should_include_word(token) is False
        assert lookup.calls == [["ぬるぽ"]]  # probed, missed

    def test_no_dict_does_not_recover(self, test_config):
        # No probe wired ⇒ safe degrade to today's behavior (kana dropped).
        service = self._service(test_config, None)
        token = _make_token("きれい", "形状詞", pos2="一般", lemma="奇麗", orth_base="きれい")
        assert service._should_include_word(token) is False

    def test_accepted_token_never_probes(self, test_config):
        # A normal kanji verb is admitted by should_include; the recovery branch
        # (and its lookup) must not run for already-accepted tokens.
        lookup = _attest_lookup("食べる")
        service = self._service(test_config, lookup)
        token = _make_token("食べる", "動詞", pos2="一般", lemma="食べる", orth_base="食べる")
        assert service._should_include_word(token) is True
        assert lookup.calls == []

    def test_lookup_memoized_per_distinct_surface_pos1(self, test_config):
        # Perf budget (deterministic, not wall-clock): the attestation lookup
        # runs at most once per distinct (surface, pos1) across many repeats.
        lookup = _attest_lookup("きれい", "すごい")
        service = self._service(test_config, lookup)
        for _ in range(50):
            t = _make_token("きれい", "形状詞", pos2="一般", lemma="奇麗", orth_base="きれい")
            assert service._should_include_word(t) is True
        assert len(lookup.calls) == 1  # 50 きれい occurrences → one probe
        for _ in range(50):
            t = _make_token("すごい", "形容詞", pos2="一般", lemma="凄い", orth_base="すごい")
            assert service._should_include_word(t) is True
        assert len(lookup.calls) == 2  # +1 for the distinct すごい

    def test_negative_result_is_also_memoized(self, test_config):
        # A miss must be cached too, or repeated non-attested tokens re-probe.
        lookup = _attest_lookup()
        service = self._service(test_config, lookup)
        for _ in range(20):
            t = _make_token("ぬるぽ", "動詞", pos2="一般", lemma="ぬるぽ", orth_base="ぬるぽ")
            assert service._should_include_word(t) is False
        assert len(lookup.calls) == 1

    def test_count_and_mine_both_reject_aux_identically(self, test_config, tmp_path):
        # count==mine parity for the REJECT side: a ている line must yield no
        # いる in either count_lemmas or the mined set, even with いる attested.
        sub_file = tmp_path / "aux.ass"
        sub_file.write_text("x", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = "見ている"
        mock_line.start = 0
        mock_line.end = 1000
        mock_line.is_comment = False
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        tokens = [
            _make_token("見", "動詞", pos2="非自立可能", lemma="見る", kana="ミ", orth_base="見る"),
            _make_token("て", "助詞", pos2="接続助詞", lemma="て", kana="テ", orth_base="て"),
            _make_token("いる", "動詞", pos2="非自立可能", lemma="居る", kana="イル", orth_base="いる"),
        ]
        mock_tagger = MagicMock()
        mock_tagger.return_value = tokens
        lookup = _attest_lookup("いる")
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config, kana_attest_lookup=lookup)
            counts = service.count_lemmas(sub_file)
            words = service.parse_subtitle_file(sub_file)
        assert "居る" not in counts  # count side rejected the aux
        assert "いる" not in {w.mined_form for w in words}  # mine side too
        assert counts.get("見る") == 1  # kanji 非自立可能 verb is untouched
        assert "見る" in {w.mined_form for w in words}
        assert lookup.calls == []  # pos2 reject fired before any probe

    def test_count_and_mine_both_recover_identically(self, test_config, tmp_path):
        # count==mine parity (T-38): count_lemmas and parse_subtitle_file both
        # route through _should_include_word, so both recover the kana word.
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = "きれい"
        mock_line.start = 0
        mock_line.end = 1000
        mock_line.is_comment = False
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        token = _make_token("きれい", "形状詞", pos2="一般", lemma="奇麗", kana="キレイ", orth_base="きれい")
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]
        lookup = _attest_lookup("きれい")
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(test_config, kana_attest_lookup=lookup)
            counts = service.count_lemmas(sub_file)
            words = service.parse_subtitle_file(sub_file)
        assert counts.get("奇麗") == 1  # count_lemmas recovered (lemma-keyed count)
        assert "きれい" in {w.mined_form for w in words}  # mining recovered same token


class TestKanaRecoveryLexicalizedWindow:
    """U4: reject kana-RECOVERY acceptances that sit inside an attested expression.

    A pure-hiragana content-word fragment (すみ from すみません, しれ from
    かもしれない) the script gate drops but recovery would re-admit is suppressed
    when joining it with contiguous FUNCTIONAL neighbors (pos1 ∈ {助詞, 助動詞},
    ≤3/side) forms a string the term-OR-reading probe attests. Functional-only:
    a content neighbor (ものすごい's もの) never joins, so real vocabulary abutting
    a lexicalized homograph is never suppressed.
    """

    def _service(self, test_config, lookup):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config, kana_attest_lookup=lookup)

    def _mine(self, service, tokens, idx):
        # Dummy span: recovery candidates are pure hiragana, so the should_include
        # branch (the only one consulting the katakana span guard) is never taken.
        cand = tokens[idx]
        return service._mine_token(cand, cand.surface, 0, len(cand.surface), tokens)

    # --- 1. すみません: すむ recovery rejected via the attested full window. ---

    def test_rejects_sumimasen_fragment(self, test_config):
        # attest {すむ, すみません}: the bare-verb attestation proves recovery WOULD
        # fire; the window すみ+ませ+ん = すみません proves the window reject is what
        # drops it (for the designed reason, not a dict miss).
        lookup = _attest_lookup("すむ", "すみません")
        service = self._service(test_config, lookup)
        tokens = [
            _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
            _make_token("ませ", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
            _make_token("ん", "助動詞", pos2="*", lemma="ぬ", orth_base="ん"),
        ]
        assert self._mine(service, tokens, 0) is False
        assert ["すむ"] in lookup.calls  # recovery probed the bare front
        assert any("すみません" in call for call in lookup.calls)  # window probed

    # --- 2. Same surface, no attested window → recovery still works. ---

    def test_recovers_when_window_not_attested(self, test_config):
        # attest {すむ} only: すみ+ます = すみます is NOT attested, so recovery holds.
        lookup = _attest_lookup("すむ")
        service = self._service(test_config, lookup)
        tokens = [
            _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
            _make_token("ます", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
        ]
        assert self._mine(service, tokens, 0) is True

    # --- 3. かもしれない: しれる recovery rejected. ---

    def test_rejects_kamoshirenai_fragment(self, test_config):
        lookup = _attest_lookup("しれる", "かもしれない")
        service = self._service(test_config, lookup)
        tokens = [
            _make_token("弱い", "形容詞", pos2="一般", lemma="弱い", orth_base="弱い"),
            _make_token("か", "助詞", pos2="副助詞", lemma="か", orth_base="か"),
            _make_token("も", "助詞", pos2="係助詞", lemma="も", orth_base="も"),
            _make_token("しれ", "動詞", pos2="一般", lemma="知れる", orth_base="しれる"),
            _make_token("ない", "助動詞", pos2="*", lemma="ない", orth_base="ない"),
        ]
        assert self._mine(service, tokens, 3) is False
        assert any("かもしれない" in call for call in lookup.calls)

    # --- 4. ものすごい: content neighbor もの blocks the join → すごい recovered. ---

    def test_content_neighbor_does_not_block_recovery(self, test_config):
        # ものすごい IS attested, yet すごい must STILL recover: もの is a 名詞 (content
        # noun), not 助詞/助動詞, so the window ものすごく never forms and is never
        # probed. The functional-only false-positive guard.
        lookup = _attest_lookup("すごい", "ものすごい")
        service = self._service(test_config, lookup)
        tokens = [
            _make_token("もの", "名詞", pos2="普通名詞", lemma="物", orth_base="もの"),
            _make_token("すごく", "形容詞", pos2="一般", lemma="凄い", orth_base="すごい"),
            _make_token("愛想", "名詞", pos2="普通名詞", lemma="愛想", orth_base="愛想"),
        ]
        assert self._mine(service, tokens, 1) is True
        assert lookup.calls == [["すごい"]]  # only the bare recovery probe; no window
        assert all("ものすごい" not in call for call in lookup.calls)

    # --- 5. Window cap: a functional run > 3/side probes only within-cap windows. ---

    def test_window_cap_three_per_side(self, test_config):
        lookup = _attest_lookup("みる")  # recovery attested; no window attested
        service = self._service(test_config, lookup)
        tokens = [
            _make_token("Ａ", "助詞", pos2="*", lemma="Ａ", orth_base="Ａ"),  # idx0: beyond left cap
            _make_token("Ｂ", "助詞", pos2="*", lemma="Ｂ", orth_base="Ｂ"),  # idx1
            _make_token("Ｃ", "助詞", pos2="*", lemma="Ｃ", orth_base="Ｃ"),  # idx2
            _make_token("Ｄ", "助詞", pos2="*", lemma="Ｄ", orth_base="Ｄ"),  # idx3
            _make_token("みつ", "動詞", pos2="一般", lemma="見る", orth_base="みる"),  # idx4 candidate
            _make_token("Ｅ", "助動詞", pos2="*", lemma="Ｅ", orth_base="Ｅ"),  # idx5
            _make_token("Ｆ", "助動詞", pos2="*", lemma="Ｆ", orth_base="Ｆ"),  # idx6
            _make_token("Ｇ", "助動詞", pos2="*", lemma="Ｇ", orth_base="Ｇ"),  # idx7
            _make_token("Ｈ", "助動詞", pos2="*", lemma="Ｈ", orth_base="Ｈ"),  # idx8: beyond right cap
        ]
        assert self._mine(service, tokens, 4) is True  # nothing attested → recovered
        # calls[0] is the bare recovery probe (["みる"]); calls[1] is the batched
        # window probe. Windows reach at most 3 functional tokens per side, so the
        # farthest neighbors Ａ (idx0) and Ｈ (idx8) never appear in any window.
        assert lookup.calls[0] == ["みる"]
        window_probe = "".join(lookup.calls[1])
        assert "Ａ" not in window_probe and "Ｈ" not in window_probe
        assert "Ｂ" in window_probe and "Ｇ" in window_probe  # idx1 / idx7 within cap
        assert len(lookup.calls) == 2  # one batched window call, not per-string

    # --- 6. T-38 count==mine parity: the reject fires identically at all four sites. ---

    def test_count_equals_mine_all_sites_reject(self, test_config, tmp_path):
        text = "本当にすみません"

        def tokens():
            return [
                _make_token("本当", "名詞", "普通名詞", lemma="本当", kana="ホントウ", orth_base="本当"),
                _make_token("に", "助詞", "格助詞", lemma="に", kana="ニ", orth_base="に"),
                _make_token("すみ", "動詞", "一般", lemma="済む", kana="スミ", orth_base="すむ"),
                _make_token("ませ", "助動詞", "*", lemma="ます", kana="マセ", orth_base="ます"),
                _make_token("ん", "助動詞", "*", lemma="ぬ", kana="ン", orth_base="ん"),
            ]

        def invoke(fn):
            sub_file = tmp_path / "reject.ass"
            sub_file.write_text("x", encoding="utf-8")
            mock_line = MagicMock()
            mock_line.text = text
            mock_line.start = 0
            mock_line.end = 1000
            mock_line.is_comment = False
            mock_subs = MagicMock()
            mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
            mock_tagger = MagicMock()
            mock_tagger.return_value = tokens()
            with (
                patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
                patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
            ):
                service = SubtitleParserService(test_config, kana_attest_lookup=_attest_lookup("すむ", "すみません"))
                return fn(service, sub_file)

        mine = invoke(lambda s, f: s.parse_subtitle_file(f))
        idx_words, idx_lines = invoke(lambda s, f: s.parse_subtitle_file_with_index(f))
        counts = invoke(lambda s, f: s.count_lemmas(f))

        def parse_units(s, _f):
            return s.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], want_line_index=True)

        pt_words, pt_index, pt_counts = invoke(parse_units)

        # 本当 mined everywhere; the すむ fragment rejected everywhere.
        assert {w.lemma for w in mine} == {"本当"}
        assert {w.lemma for w in idx_words} == {"本当"}
        assert {lm for line in idx_lines for lm in line.lemmas} == {"本当"}
        assert set(counts) == {"本当"}
        assert {w.lemma for w in pt_words} == {"本当"}
        assert {lm for line in pt_index for lm in line.lemmas} == {"本当"}
        assert set(pt_counts) == {"本当"}

    # --- 7. No probe wired → recovery (and the window layer) is byte-identical off. ---

    def test_no_lookup_wired_window_layer_inert(self, test_config):
        # Without a kana_attest_lookup, recovery never fires, so the window layer
        # is unreachable and cannot raise or alter behavior: the pure-hiragana
        # fragment is dropped exactly as pre-recovery, the kanji noun still mined.
        service = self._service(test_config, None)
        tokens = [
            _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
            _make_token("ませ", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
            _make_token("ん", "助動詞", pos2="*", lemma="ぬ", orth_base="ん"),
        ]
        assert self._mine(service, tokens, 0) is False
        kanji = _make_token("食べる", "動詞", pos2="一般", lemma="食べる", orth_base="食べる")
        assert self._mine(service, [kanji], 0) is True

    # --- 8. Memo correctness: same surface, two contexts, no (surface,pos1) poison. ---

    def test_same_surface_two_contexts_differ(self, test_config):
        lookup = _attest_lookup("すむ", "すみません")

        def reject_ctx():
            return [
                _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
                _make_token("ませ", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
                _make_token("ん", "助動詞", pos2="*", lemma="ぬ", orth_base="ん"),
            ]

        def recover_ctx():
            return [
                _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
                _make_token("ます", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
            ]

        # Reject-then-recover on one instance: the (surface,pos1) recover cache is
        # shared (すむ attested once), but the window verdict is keyed on the joined
        # window string, so すみません → reject while すみます → recover.
        service = self._service(test_config, lookup)
        assert self._mine(service, reject_ctx(), 0) is False
        assert self._mine(service, recover_ctx(), 0) is True
        # Reverse order on a fresh instance → same outcomes (order-independent memo).
        service2 = self._service(test_config, lookup)
        assert self._mine(service2, recover_ctx(), 0) is True
        assert self._mine(service2, reject_ctx(), 0) is False

    # --- 9. Real fugashi: あさって misparse (漁る) suppressed end-to-end. ---

    def test_asatte_misparse_rejected_real_fugashi(self, test_config, tmp_path):
        # 再測定はあさって tokenizes あさっ(漁る/あさる) + て(助詞); the window あさって
        # is attested (明後日's kana reading) → the 漁る junk card is suppressed.
        # Differential control (attributes the reject to the WINDOW, not a dict
        # miss): with ONLY the bare front あさる attested — the window あさって NOT
        # attested — recovery fires and the 漁る/あさる junk card WOULD survive.
        srt = _write_srt(tmp_path, "asatte.srt", "再測定はあさって")

        no_window = _attest_lookup("あさる")  # bare front only, window not attested
        recovered = SubtitleParserService(test_config, kana_attest_lookup=no_window).parse_subtitle_file(srt)
        assert "漁る" in {w.lemma for w in recovered}  # recovery would fire...
        assert any("あさって" in call for call in no_window.calls)  # ...and the window WAS probed

        with_window = _attest_lookup("あさる", "あさって")  # window now attested → reject
        words = SubtitleParserService(test_config, kana_attest_lookup=with_window).parse_subtitle_file(srt)
        assert "漁る" not in {w.lemma for w in words}
        assert "あさる" not in {w.mined_form for w in words}
        assert any("あさって" in call for call in with_window.calls)  # window path drove the reject

    # --- 10. Cap-clear must not evict a pre-cached window this candidate needs. ---

    def test_cap_clear_preserves_precached_window_verdict(self, test_config, monkeypatch):
        # Regression: the shorter window すみませ is cached from a prior candidate,
        # so this call's ``uncached`` is only [すみません]. Driving the cap below the
        # combined size triggers the clear-on-cap, wiping すみませ. The per-call
        # verdict must come from a local snapshot, not a re-read of the (now empty)
        # shared cache — else ``cache["すみませ"]`` raises KeyError and aborts the run.
        lookup = _attest_lookup("すむ", "すみません")  # すみません attested; すみませ not
        service = self._service(test_config, lookup)
        service._kana_window_cache["すみませ"] = False  # pre-cached shorter window
        monkeypatch.setattr("anki_miner.services.subtitle_parser._FRONT_CACHE_CAP", 1)
        tokens = [
            _make_token("すみ", "動詞", pos2="一般", lemma="済む", orth_base="すむ"),
            _make_token("ませ", "助動詞", pos2="*", lemma="ます", orth_base="ます"),
            _make_token("ん", "助動詞", pos2="*", lemma="ぬ", orth_base="ん"),
        ]
        # No KeyError, and the attested すみません window still rejects recovery.
        assert self._mine(service, tokens, 0) is False


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

    def test_strips_gloss_with_fullwidth_decoration(self, service):
        """Gloss tails carrying non-ASCII decoration (fullwidth parens) must
        still strip — these defeat a plain tail.isascii() check and used to
        break every lemma-keyed lookup (frequency/pitch/offline-definition)."""
        token = _make_token("ロック", "名詞", lemma="ロック-rock（音楽）")
        assert service._extract_lemma(token) == "ロック"

    def test_strips_hyphenated_gloss_whole(self, service):
        """A gloss that is itself hyphenated must strip from the FIRST hyphen."""
        token = _make_token("メリーゴーランド", "名詞", lemma="メリーゴーランド-merry-go-round")
        assert service._extract_lemma(token) == "メリーゴーランド"
        token = _make_token("チェックアウト", "名詞", lemma="チェックアウト-check-out")
        assert service._extract_lemma(token) == "チェックアウト"

    def test_strips_pos_name_disambiguator(self, service):
        """unidic decorates pronoun homographs with their own POS name."""
        token = _make_token("君", "代名詞", lemma="君-代名詞")
        assert service._extract_lemma(token) == "君"
        token = _make_token("私", "代名詞", lemma="私-代名詞")
        assert service._extract_lemma(token) == "私"

    def test_pos_tail_only_strips_when_it_matches_pos1(self, service):
        """A Japanese tail that is not the token's own pos1 stays intact."""
        token = _make_token("君", "名詞", lemma="君-代名詞")
        assert service._extract_lemma(token) == "君-代名詞"


class TestMiningBase:
    """Tests for _mining_base (source-orthography dictionary form with
    derived-sub-lemma folding — see morphology.mining_base)."""

    @pytest.fixture
    def service(self, test_config):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config)

    def test_returns_orth_base(self, service):
        """orthBase keeps the source kanji variant that lemma normalizes away."""
        token = _make_token("乞わ", "動詞", lemma="請う", orth_base="乞う")
        assert service._mining_base(token) == "乞う"

    def test_none_falls_back_to_lemma(self, service):
        """fugashi maps unidic's ``*`` placeholder to None → fall back to lemma."""
        token = _make_token("食べた", "動詞", lemma="食べる")
        token.feature.orthBase = None
        assert service._mining_base(token) == "食べる"

    def test_fallback_branch_keeps_gloss_stripping(self, service):
        """The lemma fallback inherits extract_lemma's ASCII-gloss strip."""
        token = _make_token("スクランブル", "名詞", lemma="スクランブル-scramble")
        token.feature.orthBase = None
        assert service._mining_base(token) == "スクランブル"

    def test_missing_attribute_falls_back(self, service):
        """Synthetic merged-compound tokens have no orthBase attribute
        (_SyntheticToken's SimpleNamespace feature) — must not crash."""
        token = _make_token_no_feature("食べた")
        assert service._mining_base(token) == "食べた"

    # --- derived sub-lemma folding (potential / ra-nuki / adjective ク-form) ---

    def test_folds_godan_potential_to_lemma(self, service):
        """可能動詞 fold onto the parent verb so they dedup against its card."""
        token = _make_token("保てる", "動詞", lemma="保つ", orth_base="保てる", l_form="タモツ", kana_base="タモテル")
        assert service._mining_base(token) == "保つ"

    def test_folds_more_potential_paradigm_rows(self, service):
        cases = [
            ("読める", "読む", "ヨム", "ヨメル"),
            ("話せる", "話す", "ハナス", "ハナセル"),
            ("書ける", "書く", "カク", "カケル"),
            ("泳げる", "泳ぐ", "オヨグ", "オヨゲル"),
            ("掴める", "掴む", "ツカム", "ツカメル"),
            ("死ねる", "死ぬ", "シヌ", "シネル"),
            ("呼べる", "呼ぶ", "ヨブ", "ヨベル"),
            ("買える", "買う", "カウ", "カエル"),
        ]
        for orth_base, lemma, l_form, kana_base in cases:
            token = _make_token(orth_base, "動詞", lemma=lemma, orth_base=orth_base, l_form=l_form, kana_base=kana_base)
            assert service._mining_base(token) == lemma, orth_base

    def test_folds_ranuki_to_lemma(self, service):
        """ら抜き potentials (見れる/食べれる) fold via the (れる, る) pair."""
        token = _make_token("見れる", "動詞", lemma="見る", orth_base="見れる", l_form="ミル", kana_base="ミレル")
        assert service._mining_base(token) == "見る"
        token = _make_token(
            "食べれる", "動詞", lemma="食べる", orth_base="食べれる", l_form="タベル", kana_base="タベレル"
        )
        assert service._mining_base(token) == "食べる"

    def test_folds_mid_conjugation_potential_token(self, service):
        """書けない → token 書け carries orthBase=書ける; still folds to 書く."""
        token = _make_token("書け", "動詞", lemma="書く", orth_base="書ける", l_form="カク", kana_base="カケル")
        assert service._mining_base(token) == "書く"

    def test_folds_katakana_verb_potential(self, service):
        token = _make_token(
            "サボれる", "動詞", lemma="サボる", orth_base="サボれる", l_form="サボル", kana_base="サボレル"
        )
        assert service._mining_base(token) == "サボる"

    def test_folds_adjective_ku_form(self, service):
        """Archaic i-adjective bases (良かれ → orthBase 良し) fold to the
        modern dictionary form via the (し, い) pair."""
        token = _make_token("良かれ", "形容詞", lemma="良い", orth_base="良し", l_form="ヨイ", kana_base="ヨシ")
        assert service._mining_base(token) == "良い"
        token = _make_token("無かれ", "形容詞", lemma="無い", orth_base="無し", l_form="ナイ", kana_base="ナシ")
        assert service._mining_base(token) == "無い"

    # --- suffix-pair guard: lemma canonicalization must never leak ---

    def test_guard_blocks_leading_kanji_swap(self, service):
        """帰れる's unidic lemma is 返る — folding would mine the wrong verb."""
        token = _make_token("帰れる", "動詞", lemma="返る", orth_base="帰れる", l_form="カエル", kana_base="カエレル")
        assert service._mining_base(token) == "帰れる"
        token = _make_token(
            "混ぜれる", "動詞", lemma="交ぜる", orth_base="混ぜれる", l_form="マゼル", kana_base="マゼレル"
        )
        assert service._mining_base(token) == "混ぜれる"

    def test_guard_blocks_non_leading_kanji_swap(self, service):
        """逢→会 canonicalization sits mid-word; the byte-exact guard blocks it."""
        token = _make_token(
            "出逢える", "動詞", lemma="出会う", orth_base="出逢える", l_form="デアウ", kana_base="デアエル"
        )
        assert service._mining_base(token) == "出逢える"
        token = _make_token(
            "巡り合える",
            "動詞",
            lemma="巡り会う",
            orth_base="巡り合える",
            l_form="メグリアウ",
            kana_base="メグリアエル",
        )
        assert service._mining_base(token) == "巡り合える"

    def test_guard_blocks_okurigana_variant(self, service):
        """Okurigana canonicalization (表せる → lemma 表わす) must keep the
        source spelling — same kanji, different kana, invisible to any
        kanji-based guard."""
        token = _make_token(
            "表せる", "動詞", lemma="表わす", orth_base="表せる", l_form="アラワス", kana_base="アラワセル"
        )
        assert service._mining_base(token) == "表せる"
        token = _make_token(
            "行なえる", "動詞", lemma="行う", orth_base="行なえる", l_form="オコナウ", kana_base="オコナエル"
        )
        assert service._mining_base(token) == "行なえる"
        token = _make_token("落せる", "動詞", lemma="落とす", orth_base="落せる", l_form="オトス", kana_base="オトセル")
        assert service._mining_base(token) == "落せる"

    def test_guard_blocks_jiru_zuru_canonicalization(self, service):
        """Citation-form 漢語+じる verbs carry the archaic ずる lemma with
        divergent readings; じる↛ずる is not a paradigm pair, so the modern
        spelling stays on the card."""
        token = _make_token(
            "信じる", "動詞", lemma="信ずる", orth_base="信じる", l_form="シンズル", kana_base="シンジル"
        )
        assert service._mining_base(token) == "信じる"
        token = _make_token(
            "感じる", "動詞", lemma="感ずる", orth_base="感じる", l_form="カンズル", kana_base="カンジル"
        )
        assert service._mining_base(token) == "感じる"

    def test_polyphonic_same_string_is_noop(self, service):
        """言う: readings diverge (イウ vs ユウ) but lemma==orthBase, so the
        (う, う)-less pair walk still returns the identical string either way."""
        token = _make_token("言う", "動詞", lemma="言う", orth_base="言う", l_form="イウ", kana_base="ユウ")
        assert service._mining_base(token) == "言う"

    def test_no_fold_when_readings_equal(self, service):
        """Lexicalized potentials (見える/聞こえる/できる) have their own lemma
        with matching readings — never folded."""
        token = _make_token("見える", "動詞", lemma="見える", orth_base="見える", l_form="ミエル", kana_base="ミエル")
        assert service._mining_base(token) == "見える"
        token = _make_token("できる", "動詞", lemma="出来る", orth_base="できる", l_form="デキル", kana_base="デキル")
        assert service._mining_base(token) == "できる"

    def test_no_fold_for_non_verb_pos(self, service):
        """The fold is POS-gated to 動詞/形容詞 — a noun with divergent
        readings keeps its orthBase untouched."""
        token = _make_token("山", "名詞", lemma="別", orth_base="山", l_form="ベツ", kana_base="ヤマ")
        assert service._mining_base(token) == "山"

    def test_no_fold_when_readings_missing_or_placeholder(self, service):
        """None (factory default) and unidic's '*' placeholder never fold."""
        token = _make_token("保てる", "動詞", lemma="保つ", orth_base="保てる")
        assert service._mining_base(token) == "保てる"
        token = _make_token("保てる", "動詞", lemma="保つ", orth_base="保てる", l_form="*", kana_base="*")
        assert service._mining_base(token) == "保てる"


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


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_folds_potential_verb(tmp_path):
    """保てる must mine as 保つ (可能動詞 fold), with the reading re-derived
    from the folded form. Guards future unidic bumps changing the feature
    layout (lForm/kanaBase)."""
    srt_file = tmp_path / "potential.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "秩序を保てるはずがない\n",
        encoding="utf-8",
    )
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    by_surface = {w.surface: w for w in words}
    assert "保てる" in by_surface, f"got surfaces: {set(by_surface)}"
    word = by_surface["保てる"]
    assert word.mined_form == "保つ"
    assert word.orth_base == "保つ"
    assert word.expression_reading == "たもつ"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_guard_keeps_source_orthography(tmp_path):
    """帰れる (lemma 返る) and 出逢える (lemma 出会う) must NOT fold — the
    suffix-pair guard keeps the source spelling."""
    srt_file = tmp_path / "guard.srt"
    srt_file.write_text(
        "1\n"
        "00:00:01,000 --> 00:00:05,000\n"
        "もう帰れるかな\n"
        "\n"
        "2\n"
        "00:00:06,000 --> 00:00:10,000\n"
        "君に出逢えるなんて\n",
        encoding="utf-8",
    )
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    by_surface = {w.surface: w for w in words}
    assert by_surface["帰れる"].mined_form == "帰れる"
    assert by_surface["出逢える"].mined_form == "出逢える"


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
def test_real_fugashi_pronoun_lemma_is_clean(tmp_path):
    """君 must carry the clean lemma 君 (not 君-代名詞) so lemma-keyed
    lookups (frequency/pitch) hit, and count_lemmas keys the same string."""
    srt_file = tmp_path / "pronoun.srt"
    srt_file.write_text(
        "1\n" "00:00:01,000 --> 00:00:05,000\n" "君を待つ\n",
        encoding="utf-8",
    )
    config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
    service = SubtitleParserService(config)
    words = service.parse_subtitle_file(srt_file)

    by_surface = {w.surface: w for w in words}
    assert "君" in by_surface, f"got surfaces: {set(by_surface)}"
    assert by_surface["君"].lemma == "君"

    counts = service.count_lemmas(srt_file)
    assert counts["君"] == 1
    assert not any("代名詞" in lemma for lemma in counts)


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
            # Isolate the regex filter from the default-ON structural strip (U1),
            # which would otherwise peel the leading (田中) speaker tag itself.
            strip_subtitle_annotations=False,
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
            # Isolate the regex backref path from the default-ON structural strip
            # (U1), which would peel the leading (田中) before the regex runs.
            strip_subtitle_annotations=False,
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
        """くれ (呉れる, 非自立可能) must not be mined from 買ってくれた even with
        the kana recovery active AND くれる attested — the pos2 reject, not a
        dict miss, drops it. (Without the wired lookup this test was false-safe:
        recovery short-circuited before the gate under test ever ran.)"""
        srt_file = tmp_path / "benefactive.srt"
        srt_file.write_text(
            "1\n00:00:01,000 --> 00:00:05,000\n本を買ってくれた\n",
            encoding="utf-8",
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        lookup = _attest_lookup("くれる", "いる", "ある")
        service = SubtitleParserService(config, kana_attest_lookup=lookup)
        words = service.parse_subtitle_file(srt_file)
        lemmas = {w.lemma for w in words}
        assert "買う" in lemmas
        assert not any("くれ" in lemma for lemma in lemmas)

    @pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
    @pytest.mark.parametrize(
        ("line", "wanted", "aux_front"),
        [
            ("猫を見ている", {"猫", "見る"}, "いる"),
            ("本を読んでしまった", {"本", "読む"}, "しまう"),
            ("手紙を書いておく", {"手紙", "書く"}, "おく"),
            ("犬を飼ってくれる", {"犬", "飼う"}, "くれる"),
            ("そこにある", set(), "ある"),
        ],
    )
    def test_real_fugashi_aux_context_mints_no_aux_card(self, tmp_path, line, wanted, aux_front):
        """End-to-end 非自立可能 guard through real fugashi: the aux headword is
        deliberately attested, and still no aux card is minted (aux-context
        benchmark category pins the same through the fixture dict)."""
        srt_file = tmp_path / "aux.srt"
        srt_file.write_text(
            f"1\n00:00:01,000 --> 00:00:05,000\n{line}\n",
            encoding="utf-8",
        )
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        lookup = _attest_lookup("いる", "ある", "くれる", "おく", "しまう")
        service = SubtitleParserService(config, kana_attest_lookup=lookup)
        words = service.parse_subtitle_file(srt_file)
        fronts = {w.mined_form for w in words}
        assert aux_front not in fronts, line
        assert wanted <= fronts, line


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
        # GUARD: the token-path == raw-re-tokenize byte-identity these fixtures
        # assert only holds for lines WITHOUT a kana_overridden compound (the
        # reading-attestation pass deliberately diverges the display stream for
        # those — see TestCompoundReadingAttestation). 刑務所/爆発的 are
        # correct-concat compounds (kept, never overridden), so they stay
        # equivalent; do not add a known-wrong-reading compound (バカ力,
        # 体じゅう…) to these lines.
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

    def test_term_lookup_none_keeps_current_behavior(self, tmp_path):
        """The safe-degrade guarantee is ``term_lookup=None`` == pre-feature output.

        (NOT "empty dict == None": an empty-but-present dict now activates the
        attested-or-bail merge gate and bails every unattested synthetic. The
        equality below holds only because this fixture line mints no morphology
        synthetic — 走り出す is a matcher span, which the empty dict also can't
        attest — so the gate is inert here. The load-bearing case is the None
        one, which the assertion pins.)
        """
        srt_file = _write_srt(tmp_path, "plain.srt", "彼は急に走り出した。")
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        with_empty_dict = SubtitleParserService(config, term_lookup=_lookup_for(set()))
        none_case = SubtitleParserService(config)
        assert with_empty_dict.parse_subtitle_file(srt_file) == none_case.parse_subtitle_file(srt_file)


class TestKinshipHonorificReadings:
    """Honorific-kinship compounds read にい/ねえ/とう/かあ, not the isolated
    head reading (お兄ちゃん → にいちゃん, not あにちゃん). Real tagger E2E.

    Covers all three layers: Expression (merge-pass override), Sentence
    furigana/reading on BOTH parse entrypoints, and the bold variant.
    """

    @pytest.mark.parametrize(
        "line,mined,reading,furigana",
        [
            ("お兄ちゃん まだ寝てたの", "兄ちゃん", "にいちゃん", "兄[にい]ちゃん"),
            ("お兄様はハンターになる", "兄様", "にいさま", None),
            ("お姉ちゃん おはよう", "姉ちゃん", "ねえちゃん", "姉[ねえ]ちゃん"),
            ("姉さんが来た", "姉さん", "ねえさん", None),
            ("お父さんが帰る", "父さん", "とうさん", None),
            ("お母さんに聞く", "母さん", "かあさん", None),
        ],
    )
    def test_expression_reading_and_furigana(self, tmp_path, line, mined, reading, furigana):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "kin.srt", line)
        words = SubtitleParserService(cfg).parse_subtitle_file(srt)
        word = next(w for w in words if w.mined_form == mined)
        assert word.expression_reading == reading
        if furigana is not None:
            assert word.expression_furigana == furigana

    def test_sentence_furigana_both_entrypoints(self, tmp_path):
        """Sentence furigana uses the corrected head on both parse paths — pins
        the 1c wiring independently of the Expression merge-pass fix."""
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "kin.srt", "お兄ちゃん まだ寝てたの")
        svc = SubtitleParserService(cfg)
        plain = svc.parse_subtitle_file(srt)
        indexed, _idx = svc.parse_subtitle_file_with_index(srt)
        for words in (plain, indexed):
            sf = words[0].sentence_furigana
            assert "兄[にい]" in sf
            assert "兄[あに]" not in sf

    def test_bold_sentence_furigana_uses_corrected_head(self, tmp_path):
        """The bold variant (config.bold_target_in_sentence, off by default) also
        reads にい — pins the wrap_target_furigana_from_tokens call site."""
        import dataclasses

        cfg = dataclasses.replace(
            AnkiMinerConfig(media_temp_folder=tmp_path / "media"),
            bold_target_in_sentence=True,
        )
        srt = _write_srt(tmp_path, "kin.srt", "お兄ちゃん まだ寝てたの")
        words = SubtitleParserService(cfg).parse_subtitle_file(srt)
        word = next(w for w in words if w.mined_form == "兄ちゃん")
        assert "兄[にい]" in word.sentence_furigana_bolded
        assert "<b>" in word.sentence_furigana_bolded

    def test_standalone_kinship_head_not_overridden(self, tmp_path):
        """兄 alone (no honorific suffix) keeps あに."""
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "kin.srt", "兄が来た")
        words = SubtitleParserService(cfg).parse_subtitle_file(srt)
        word = next(w for w in words if w.surface == "兄")
        assert word.expression_reading == "あに"

    def test_ichinichi_ambiguous_left_alone(self, tmp_path):
        """一日 in a date frame stays ついたち — the resolver never touches it."""
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "kin.srt", "月の一日に会う")
        words = SubtitleParserService(cfg).parse_subtitle_file(srt)
        word = next((w for w in words if w.surface == "一日"), None)
        if word is not None:
            assert word.expression_reading == "ついたち"


class TestDecorationGlyphStripE2E:
    """TV-caption decoration glyphs (➡/📱, 2026-07 audit F4) never reach mined
    sentences or their furigana. Real tagger E2E over the subtitle path."""

    def test_arrow_and_device_glyphs_absent_from_sentence_fields(self, tmp_path):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "deco.srt", "📱われわれの通常兵器では➡")
        words = SubtitleParserService(cfg).parse_subtitle_file(srt)
        assert words, "line should still mine normally"
        for w in words:
            assert "➡" not in w.sentence
            assert "\U0001f4f1" not in w.sentence
            assert "➡" not in w.sentence_furigana
            assert w.sentence == "われわれの通常兵器では"


class TestCompoundReadingAttestation:
    """Dictionary-attested readings for merged compounds (2026-07 audit F2).

    Real-tagger E2E with a fake reading_lookup: the attestation must reach
    Expression reading/furigana, the curation reading, sentence furigana on
    BOTH parse entrypoints, and the bold variant — while a missing/empty
    lookup keeps parsing byte-identical.

    NOTE for fixture authors: byte-identity between the token path and a raw
    re-tokenize (test_output_equivalence_*) only holds for lines WITHOUT a
    kana_overridden compound — an attested override deliberately diverges the
    display stream from raw tokenization.
    """

    _FAKE = {
        "バカ力": ["ばかぢから"],
        "体じゅう": ["からだじゅう"],
        "Ｓ級": ["えすきゅう"],
        "兄ちゃん": ["にいちゃん", "あんちゃん"],
        "兄様": ["にいさま", "あにさま"],  # real Jitendex score tie
        "姉さん": ["ねえさん", "あねさん"],
        "副作用": ["ふくさよう"],
        "現実的": ["げんじつてき"],
    }

    def _parse(self, tmp_path, line, reading_lookup=None, **cfg_kwargs):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media", bold_target_in_sentence=True, **cfg_kwargs)
        srt = _write_srt(tmp_path, "attest.srt", line)
        return SubtitleParserService(cfg, reading_lookup=reading_lookup).parse_subtitle_file(srt)

    def _lookup(self, terms):
        return {t: self._FAKE[t] for t in terms if t in self._FAKE}

    @pytest.mark.parametrize(
        ("line", "mined", "reading", "furigana"),
        [
            ("フ バカ力だな", "バカ力", "ばかぢから", "バカ 力[ぢから]"),
            ("体じゅうが痛い", "体じゅう", "からだじゅう", "体[からだ]じゅう"),
            ("Ｓ級ハンターが来た", "Ｓ級", "えすきゅう", "Ｓ級[えすきゅう]"),
        ],
    )
    def test_expression_fields_use_attested_reading(self, tmp_path, line, mined, reading, furigana):
        words = self._parse(tmp_path, line, reading_lookup=self._lookup)
        word = next(w for w in words if w.mined_form == mined)
        assert word.expression_reading == reading
        assert word.expression_furigana == furigana

    def test_sentence_furigana_and_bold_use_attested_reading(self, tmp_path):
        words = self._parse(tmp_path, "フ バカ力だな", reading_lookup=self._lookup)
        word = next(w for w in words if w.mined_form == "バカ力")
        assert "力[ぢから]" in word.sentence_furigana
        assert "力[りょく]" not in word.sentence_furigana
        assert "<b>バカ 力[ぢから]</b>" in word.sentence_furigana_bolded

    def test_both_entrypoints_agree(self, tmp_path):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "attest2.srt", "体じゅうが痛い")
        plain = SubtitleParserService(cfg, reading_lookup=self._lookup).parse_subtitle_file(srt)
        indexed, _ = SubtitleParserService(cfg, reading_lookup=self._lookup).parse_subtitle_file_with_index(srt)
        by_form = {w.mined_form: w for w in indexed}
        for w in plain:
            assert by_form[w.mined_form].sentence_furigana == w.sentence_furigana
            assert by_form[w.mined_form].expression_reading == w.expression_reading

    def test_kinship_survives_attestation_with_multi_readings(self, tmp_path):
        # Production-like: dictionary attests both にいちゃん and あんちゃん;
        # the curated kinship reading must win (keep-before-select ordering).
        for line, mined, reading in [
            ("お兄ちゃん まだ寝てたの", "兄ちゃん", "にいちゃん"),
            ("お兄様はハンターになる", "兄様", "にいさま"),
            ("姉さんが来た", "姉さん", "ねえさん"),
        ]:
            words = self._parse(tmp_path, line, reading_lookup=self._lookup)
            word = next(w for w in words if w.mined_form == mined)
            assert word.expression_reading == reading

    def test_kinship_survives_dictionary_offering_only_variant(self, tmp_path):
        # Even if the dictionary attested ONLY the non-special variant, the
        # curated table outranks it (kana_special guard).
        words = self._parse(
            tmp_path,
            "お兄ちゃん まだ寝てたの",
            reading_lookup=lambda ts: {"兄ちゃん": ["あんちゃん"]},
        )
        word = next(w for w in words if w.mined_form == "兄ちゃん")
        assert word.expression_reading == "にいちゃん"
        assert "兄[にい]" in word.sentence_furigana

    def test_correct_concat_compounds_keep_per_morpheme_sentence_rendering(self, tmp_path):
        # 副作用/現実的: concat readings are attested → kept (no override), so
        # the sentence keeps today's per-morpheme furigana (judge r2 guard).
        words = self._parse(tmp_path, "副作用が現実的だ", reading_lookup=self._lookup)
        word = words[0]
        assert "副[ふく] 作用[さよう]" in word.sentence_furigana
        assert "副作用[ふくさよう]" not in word.sentence_furigana

    def test_empty_live_lookup_is_byte_identical_to_none(self, tmp_path):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "attest3.srt", "体じゅうが痛いだろう")
        base = SubtitleParserService(cfg).parse_subtitle_file(srt)
        live = SubtitleParserService(cfg, reading_lookup=lambda ts: {}).parse_subtitle_file(srt)
        assert live == base


class TestCompoundMatcherReadingAttestation:
    """Matcher-path attestation (mock tagger): the attested reading replaces the
    span-concat kana and the headword-re-tokenize regen for matcher merges."""

    def _tokens(self):
        return [
            _make_token("トカゲ", "名詞", "普通名詞", lemma="トカゲ", kana="トカゲ"),
            _make_token("の", "助詞", "格助詞", lemma="の", kana="ノ"),
            _make_token("しっぽ切り", "名詞", "普通名詞", lemma="しっぽ切り", kana="シッポキリ"),
        ]

    def _parse(self, tmp_path, test_config, reading_lookup):
        sub_file = tmp_path / "test.srt"
        sub_file.write_text("stub", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = "トカゲのしっぽ切り"
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        mock_tagger = MagicMock()
        mock_tagger.return_value = self._tokens()
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(
                test_config,
                term_lookup=_lookup_for({"トカゲのしっぽ切り"}),
                reading_lookup=reading_lookup,
            )
            return service.parse_subtitle_file(sub_file)

    def test_attested_reading_reaches_reading_and_expression(self, tmp_path, test_config):
        words = self._parse(tmp_path, test_config, lambda ts: {"トカゲのしっぽ切り": ["とかげのしっぽぎり"]})
        word = next(w for w in words if w.mined_form == "トカゲのしっぽ切り")
        assert word.reading == "とかげのしっぽぎり"
        assert word.expression_reading == "とかげのしっぽぎり"
        assert "切[ぎ]り" in word.expression_furigana
        assert "切[ぎ]り" in word.sentence_furigana


class TestInflectedCompoundHeadwordReading:
    """Inflected kind-A spans (surface ≠ headword): expression fields take the
    HEADWORD's attested reading; the sentence span keeps concat kana (declared
    residual for sentence ruby only)."""

    def test_expression_uses_headword_attestation(self, tmp_path):
        cfg = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        srt = _write_srt(tmp_path, "kindA.srt", "手っ取り早く済ませよう")
        parser = SubtitleParserService(
            cfg,
            term_lookup=lambda ts: {"手っ取り早い"} & set(ts),
            reading_lookup=lambda ts: {"手っ取り早い": ["てっとりばやい"]} if "手っ取り早い" in ts else {},
        )
        words = parser.parse_subtitle_file(srt)
        word = next(w for w in words if w.mined_form == "手っ取り早い")
        assert word.expression_reading == "てっとりばやい"
        assert word.reading == "てっとりばやい"
        assert "早[ばや]" in word.expression_furigana


class TestKindACompoundKanaAttestedLeak:
    """Kind-A compound whose INFLECTED surface is itself an attested headword
    (絶え間なく is a JMdict adverb entry; 行き過ぎ a noun) stamps kana_attested on
    the span, and the pre-U6 ``compound and kana_attested`` expression branch
    then applied the span's inflected concat kana (たえまなく) to the expression
    fields even though the mined card front is the deinflected headword
    (絶え間ない → たえまない). Audit evidence: 絶え間ない/たえまなく,
    行き過ぎる/いきすぎ, 行ってくる/いってき. The fix gates that branch on
    ``mined == surface`` so inflected kind-A spans fall through to the
    headword-attestation elif.

    Fed a pre-built ``CompoundSyntheticToken`` via a mock tagger so the span's
    kind and kana are pinned exactly; the real attestation pass
    (``attest_merged_readings``) stamps ``kana_attested`` from the surface-keyed
    reading_lookup, faithful to the production leak."""

    def _parse(self, tmp_path, token, line, reading_lookup):
        sub_file = tmp_path / "kindA_leak.srt"
        sub_file.write_text("stub", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = line
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))
        mock_tagger = MagicMock()
        mock_tagger.return_value = [token]
        with (
            patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs),
            patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger),
        ):
            service = SubtitleParserService(
                AnkiMinerConfig(media_temp_folder=tmp_path / "media"),
                reading_lookup=reading_lookup,
            )
            return service.parse_subtitle_file(sub_file)

    def test_inflected_kindA_expression_takes_headword_reading(self, tmp_path):
        # 絶え間なく (inflected surface, itself an attested adverb headword) →
        # mined headword 絶え間ない. Expression fields must show the HEADWORD's
        # reading たえまない, NOT the span's attested inflected kana たえまなく.
        token = CompoundSyntheticToken(
            surface="絶え間なく",
            pos1="形容詞",
            pos2="一般",
            lemma="絶え間ない",
            kana="タエマナク",
        )
        lookup = lambda ts: {  # noqa: E731
            k: v for k, v in {"絶え間なく": ["たえまなく"], "絶え間ない": ["たえまない"]}.items() if k in ts
        }
        words = self._parse(tmp_path, token, "絶え間なく続く", lookup)
        word = next(w for w in words if w.mined_form == "絶え間ない")
        # The span WAS stamped kana_attested (the leak's precondition).
        assert getattr(token.feature, "kana_attested", False) is True
        # Expression fields track the deinflected headword.
        assert word.expression_reading == "たえまない"
        assert word.expression_furigana == "絶[た]え 間[ま]ない"
        # The sentence-level .reading may keep the span's attested inflected kana
        # (declared residual for sentence ruby only — spec U6).
        assert word.reading == "たえまなく"

    def test_kindB_kana_attested_byte_identical(self, tmp_path):
        # Kind-B (surface == headword == mined, tail uninflected): the attested
        # compound branch still applies the dictionary-corrected kana verbatim.
        # 折り紙 attests おりがみ (rendaku overrides the オリカミ concat) — pin
        # the exact post-fix values; the fix must not touch this path.
        token = CompoundSyntheticToken(
            surface="折り紙",
            pos1="名詞",
            pos2="普通名詞",
            lemma="折り紙",
            kana="オリカミ",
        )
        lookup = lambda ts: {"折り紙": ["おりがみ"]} if "折り紙" in ts else {}  # noqa: E731
        words = self._parse(tmp_path, token, "折り紙を折る", lookup)
        word = next(w for w in words if w.mined_form == "折り紙")
        assert getattr(token.feature, "kana_attested", False) is True
        assert word.expression_reading == "おりがみ"
        assert word.expression_furigana == "折[お]り 紙[がみ]"
        assert word.reading == "おりがみ"

    def test_uninflected_kindA_attested_stays_on_kana_attested_branch(self, tmp_path):
        # Edge case (judge-mandated): a kind-A-POS compound appearing UNINFLECTED
        # (surface == headword == mined 飛び込む) keeps mined == surface, so it
        # stays on the kana_attested branch; its attested kana IS the headword
        # reading, so behavior must not change.
        token = CompoundSyntheticToken(
            surface="飛び込む",
            pos1="動詞",
            pos2="一般",
            lemma="飛び込む",
            kana="トビコム",
        )
        lookup = lambda ts: {"飛び込む": ["とびこむ"]} if "飛び込む" in ts else {}  # noqa: E731
        words = self._parse(tmp_path, token, "海に飛び込む", lookup)
        word = next(w for w in words if w.mined_form == "飛び込む")
        assert getattr(token.feature, "kana_attested", False) is True
        assert word.expression_reading == "とびこむ"
        assert word.expression_furigana == "飛[と]び 込[こ]む"
        assert word.reading == "とびこむ"


class TestParseRelevantConfigFields:
    """Drift tripwire: every name in PARSE_RELEVANT_CONFIG_FIELDS must remain a
    real symbol the parser module references, so the Deck Builder cache-reuse
    assertion can't silently guard a renamed/deleted field."""

    def test_listed_fields_appear_in_module_source(self):
        import inspect

        from anki_miner.services import subtitle_parser
        from anki_miner.services.subtitle_parser import (
            PARSE_RELEVANT_CONFIG_FIELDS,
        )

        source = inspect.getsource(subtitle_parser)
        for field in PARSE_RELEVANT_CONFIG_FIELDS:
            assert field in source, f"{field} not referenced in subtitle_parser module"


# Modern JMdict headwords attested by the fake offline term_lookup for the
# verb-front resolver tests. Covers every verb/adjective exercised below plus
# the archaic 〜ずる siblings and the potential/ra-nuki base forms — so the
# resolver runs its full ranking (not a degenerate empty-attestation degrade)
# and only the じる/ずる cases actually override.
_RESOLVER_ATTESTED_HEADWORDS = {
    "感じる",
    "感ずる",
    "論じる",
    "論ずる",
    "信じる",
    "信ずる",
    "生じる",
    "生ずる",
    "乞う",
    "彷徨う",
    "出逢う",
    "立つ",
    "待つ",
    "言う",
    "帰れる",
    "帰る",
    "見る",
    "保つ",
    "剛腕",
}


def _resolver_term_lookup(terms):
    return {t for t in terms if t in _RESOLVER_ATTESTED_HEADWORDS}


class TestVerbFrontResolver:
    """End-to-end: the parser rewrites archaic じる/ずる verb fronts to the
    modern JMdict headword via the real tagger + injected offline term_lookup.
    """

    def _mine(self, sentence, term_lookup=_resolver_term_lookup):
        service = SubtitleParserService(AnkiMinerConfig(), term_lookup=term_lookup)
        unit = ReadingUnit(text=sentence, index=0, location_label="t")
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
        return words

    def _one(self, sentence, **kw):
        words = self._mine(sentence, **kw)
        assert len(words) == 1, [w.mined_form for w in words]
        return words[0]

    # --- Produces the modern form (asserts PRODUCED, not just no false override). ---

    def test_kanjita_produces_kanjiru(self):
        word = self._one("感じた")
        assert word.mined_form == "感じる"
        assert word.orth_base == "感じる"
        # Lemma is left as the token lemma (the archaic 感ずる) — it is the i+1 /
        # occurrence / cross-episode correlation key and must NOT be folded.
        assert word.lemma == "感ずる"

    def test_ronjita_produces_ronjiru(self):
        assert self._one("論じた").mined_form == "論じる"

    def test_shinjirarenai_produces_shinjiru(self):
        assert self._one("信じられない").mined_form == "信じる"

    def test_shojita_produces_shojiru(self):
        assert self._one("生じた").mined_form == "生じる"

    # --- Reading realignment: the card-front reading follows the modern form. ---

    def test_kanjita_resolved_reading_is_modern_kana(self):
        word = self._one("感じた")
        assert word.resolved_reading == "かんじる"
        # Expression reading (the card front's own reading) matches too.
        assert word.expression_reading == "かんじる"
        # lemma_reading stays the archaic lemma's own reading for the audio retry.
        assert word.lemma_reading == "かんずる"

    # --- Regression guards: no false override. ---

    def test_kou_unchanged(self):
        word = self._one("乞う")
        assert word.mined_form == "乞う"
        assert word.resolved_reading == ""

    def test_samayotta_unchanged(self):
        assert self._one("彷徨った").mined_form == "彷徨う"

    def test_deatta_unchanged(self):
        assert self._one("出逢った").mined_form == "出逢う"

    def test_noun_never_resolves(self):
        # 剛腕 is a noun: the resolver never runs, mined_form stays the surface.
        word = self._one("剛腕")
        assert word.mined_form == "剛腕"
        assert word.resolved_reading == ""

    def test_kaereru_unchanged(self):
        # 帰れる does NOT fold (lemma 返る is a kanji swap), so it is resolver-
        # eligible — but its own orthBase 帰れる is already the longest prefix,
        # so no override fires.
        word = self._one("帰れる")
        assert word.mined_form == "帰れる"
        assert word.resolved_reading == ""

    # --- Fold guard: mining_base folds win; the resolver never un-folds. ---

    def test_mireru_folds_to_base_not_unfolded(self):
        word = self._one("見れる")
        assert word.mined_form == "見る"
        assert word.resolved_reading == ""

    def test_motereru_folds_to_base(self):
        word = self._one("保てる")
        assert word.mined_form == "保つ"
        assert word.resolved_reading == ""

    # --- Cross-conjugation: attested inflected surface must not win. ---

    def test_tatta_resolves_to_tatsu(self):
        assert self._one("立った").mined_form == "立つ"

    def test_matta_resolves_to_matsu_not_matta(self):
        # 待った is itself an attested JMdict headword ("matta!") but is the
        # inflected surface — it must not become the card front.
        assert self._one("待った").mined_form == "待つ"

    def test_itta_resolves_to_iu(self):
        assert self._one("言った").mined_form == "言う"

    # --- Safe degrade: no offline lookup → today's archaic orthBase, no crash. ---

    def test_no_term_lookup_keeps_archaic_orthbase(self):
        word = self._one("感じた", term_lookup=None)
        assert word.mined_form == "感ずる"
        assert word.resolved_reading == ""


class TestMinedFormAttestOrRemap:
    """U3: a derived/garbage verb-adjective front that matches no dictionary
    headword remaps to its attested lemma — but ONLY when the lemma/orthBase
    readings diverge. The readings-diverge precondition is load-bearing: it
    protects the Issue #19/#5 same-reading-variant contract (乞う/請う,
    怖れる/恐れる keep the source orthography, never remap). Attestation
    decides: a front the dictionary DOES attest is always kept.

    These are attest-pattern tests — the fake dictionary attests specific
    forms so each decision is provably taken for the designed reason.
    """

    @staticmethod
    def _dict(*attested):
        """Fake TermLookup attesting exactly the given headwords (subset)."""
        wanted = set(attested)
        return lambda terms: {t for t in terms if t in wanted}

    @staticmethod
    def _front(service, token):
        """Drive _resolve_front with the token's own orthBase as the
        (uninflected) span — resolve_dictionary_form is a no-op here, so the
        attest-or-remap guard is the sole decider."""
        ob = token.feature.orthBase
        return service._resolve_front(token, ob, ob, 0, len(ob))

    def _service(self, test_config, term_lookup):
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            return SubtitleParserService(test_config, term_lookup=term_lookup)

    def test_unattested_derived_front_remaps_to_lemma(self, test_config):
        # orthBase 呼ばる (garbage classical-passive derivation) is NOT attested;
        # lemma 呼ぶ IS; readings diverge (よばる vs よぶ) → remap to 呼ぶ.
        service = self._service(test_config, self._dict("呼ぶ"))
        token = _make_token("呼ばる", "動詞", lemma="呼ぶ", orth_base="呼ばる", l_form="ヨブ", kana_base="ヨバル")
        assert self._front(service, token) == "呼ぶ"

    def test_attested_front_is_kept(self, test_config):
        # Same token, but the dictionary ALSO attests 呼ばる → attestation
        # decides: the front is a real headword, so KEEP it.
        service = self._service(test_config, self._dict("呼ぶ", "呼ばる"))
        token = _make_token("呼ばる", "動詞", lemma="呼ぶ", orth_base="呼ばる", l_form="ヨブ", kana_base="ヨバル")
        assert self._front(service, token) == "呼ばる"

    def test_same_reading_variant_never_remaps(self, test_config):
        # orthBase 怖れる, lemma 恐れる, readings EQUAL (both おそれる). Even though
        # only 恐れる is attested, the #19/#5 contract preserves the source
        # spelling 怖れる — the readings-diverge precondition is not met.
        service = self._service(test_config, self._dict("恐れる"))
        token = _make_token(
            "怖れる", "動詞", lemma="恐れる", orth_base="怖れる", l_form="オソレル", kana_base="オソレル"
        )
        assert self._front(service, token) == "怖れる"

    def test_equal_reading_okurigana_variant_blocked_by_reading_gate(self, test_config):
        # Same-kanji okurigana variant that reads the same (変る/変わる, both かわる):
        # it PASSES the kanji-stem gate (stem 変), so ONLY the readings-diverge
        # gate keeps it — proving that gate is still load-bearing (#19/#5).
        service = self._service(test_config, self._dict("変わる"))
        token = _make_token("変る", "動詞", lemma="変わる", orth_base="変る", l_form="カワル", kana_base="カワル")
        assert self._front(service, token) == "変る"

    def test_wrong_homograph_lemma_blocked_by_kanji_gate(self, test_config):
        # 帰れる (potential "can go home") has unidic lemma 返る ("revert") — a
        # DIFFERENT-kanji homograph. Readings diverge (かえれる vs かえる), so only the
        # okurigana-only (kanji-stem) gate stops the remap. Remapping to 返る would
        # ship the wrong verb; the source spelling 帰れる is kept.
        service = self._service(test_config, self._dict("返る"))
        token = _make_token("帰れる", "動詞", lemma="返る", orth_base="帰れる", l_form="カエル", kana_base="カエレル")
        assert self._front(service, token) == "帰れる"

    def test_no_term_lookup_unchanged(self, test_config):
        # Safe-degrade: no offline dict wired → front untouched.
        service = self._service(test_config, None)
        token = _make_token("呼ばる", "動詞", lemma="呼ぶ", orth_base="呼ばる", l_form="ヨブ", kana_base="ヨバル")
        assert self._front(service, token) == "呼ばる"

    def test_lemma_not_attested_keeps_front(self, test_config):
        # Neither front nor lemma attested → no attested target to remap onto,
        # so the source spelling is kept (never remap onto an unattested lemma).
        service = self._service(test_config, self._dict("無関係な語"))
        token = _make_token("呼ばる", "動詞", lemma="呼ぶ", orth_base="呼ばる", l_form="ヨブ", kana_base="ヨバル")
        assert self._front(service, token) == "呼ばる"

    def test_noun_never_remaps(self, test_config):
        # Non-verb/adjective: _resolve_front returns orth_base untouched, so the
        # guard can never touch a noun front.
        service = self._service(test_config, self._dict("呼ぶ"))
        token = _make_token("呼ばる", "名詞", lemma="呼ぶ", orth_base="呼ばる", l_form="ヨブ", kana_base="ヨバル")
        assert self._front(service, token) == "呼ばる"

    def test_missing_readings_do_not_remap(self, test_config):
        # No lForm/kanaBase (synthetic/OOV token): cannot prove readings diverge,
        # so the guard conservatively keeps the front (mirrors mining_base).
        service = self._service(test_config, self._dict("呼ぶ"))
        token = _make_token("呼ばる", "動詞", lemma="呼ぶ", orth_base="呼ばる")
        assert self._front(service, token) == "呼ばる"

    @pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
    def test_chiseru_kept_kanji_stem_differs_end_to_end(self, test_config):
        # Real unidic-lite token: 治せる parses as orthBase 治せる, lemma 直す
        # (readings なおせる vs なおす). The 治≠直 kanji difference means the lemma is
        # a canonicalized variant, so the kanji-stem gate BLOCKS the remap even
        # though only 直す is attested — the source spelling 治せる is kept (its
        # definition still resolves via the mined-form→lemma miss fallback).
        service = SubtitleParserService(test_config, term_lookup=self._dict("直す"))
        unit = ReadingUnit(text="治せる", index=0, location_label="t")
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
        assert [w.mined_form for w in words] == ["治せる"]

    @pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
    def test_real_verb_unaffected_end_to_end(self, test_config):
        # A plain conjugated verb (見せた → orthBase 見せる, its own lemma) is
        # untouched by the guard when the dictionary attests it.
        service = SubtitleParserService(test_config, term_lookup=self._dict("見せる"))
        unit = ReadingUnit(text="見せた", index=0, location_label="t")
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
        assert [w.mined_form for w in words] == ["見せる"]


@pytest.mark.parametrize(
    ("orth_base", "lemma", "expected"),
    [
        ("呼ばる", "呼ぶ", True),  # same kanji stem 呼, okurigana ばる→ぶ
        ("抜る", "抜く", True),  # same kanji stem 抜
        ("変る", "変わる", True),  # same kanji stem 変 (okurigana variant)
        ("帰れる", "返る", False),  # kanji differs 帰≠返 (lemma canonicalization)
        ("治せる", "直す", False),  # kanji differs 治≠直
        ("殺る", "遣る", False),  # kanji differs 殺≠遣 (#19/#5 homograph)
        ("きれる", "切る", False),  # a kanji appears only in the lemma tail
        ("食べる", "食べる", False),  # identical → no differing okurigana (guard early-returns upstream)
    ],
)
def test_differs_by_okurigana_only(orth_base, lemma, expected):
    from anki_miner.services.subtitle_parser import _differs_by_okurigana_only

    assert _differs_by_okurigana_only(orth_base, lemma) is expected


def _gate_term_lookup(dictionary):
    """Fake TermLookup: attests exactly the given headword set (subset semantics)."""
    wanted = set(dictionary)
    return lambda terms: {t for t in terms if t in wanted}


@pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
class TestCompoundMergeAttestGate:
    """Attested-or-bail gating end-to-end through the real parse pipeline
    (morphology merge → matcher → emit). A wired term_lookup gates the
    junk-prone noun-suffix + prefix passes; ``term_lookup=None`` safe-degrades
    to the pre-gate output byte-for-byte.
    """

    def _mine(self, sentence, dictionary=None):
        term_lookup = _gate_term_lookup(dictionary) if dictionary is not None else None
        service = SubtitleParserService(AnkiMinerConfig(), term_lookup=term_lookup)
        unit = ReadingUnit(text=sentence, index=0, location_label="t")
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
        return {w.mined_form for w in words}

    # --- unattested compounds bail to the bare noun/root ------------------

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("状況的", {"状況"}),  # noun-suffix bail (drop 的)
            ("会議中", {"会議"}),  # noun-suffix bail (drop 中)
            ("超反応", {"反応"}),  # prefix bail (drop 超)
        ],
    )
    def test_unattested_compound_bails_to_bare_noun(self, sentence, expected):
        assert self._mine(sentence, dictionary=set()) == expected

    # --- attested compounds stay whole -----------------------------------

    @pytest.mark.parametrize(
        "sentence,dictionary,expected",
        [
            ("刑務所", {"刑務所"}, {"刑務所"}),  # noun-suffix mint
            ("不可能", {"不可能"}, {"不可能"}),  # prefix mint (形状詞 root)
            ("無関係", {"無関係"}, {"無関係"}),  # prefix mint (名詞 root)
            ("入院中", {"入院中"}, {"入院中"}),  # noun-suffix mint
            ("可能性", {"可能性"}, {"可能性"}),  # matcher via the 形状詞 head
        ],
    )
    def test_attested_compound_stays_whole(self, sentence, dictionary, expected):
        assert self._mine(sentence, dictionary=dictionary) == expected

    def test_matcher_recovers_subspan_from_bailed_chain(self):
        # 入院中的: the full chain 入院中的 is unattested → the noun-suffix pass
        # bails to [入院, 中, 的]; the matcher then recovers the attested 入院中
        # and 的 is dropped by the inclusion gate.
        assert self._mine("入院中的", dictionary={"入院中"}) == {"入院中"}

    def test_verb_nominalizer_never_gated(self):
        # 言い方 (方 nominalizer) mints even with an empty dictionary AND with no
        # dictionary — the verb-nominalizer pass is never gated.
        assert self._mine("言い方", dictionary=set()) == {"言い方"}
        assert self._mine("言い方", dictionary=None) == {"言い方"}

    def test_kinship_reading_preserved_though_unattested(self):
        # The curated kinship carve-out survives the gate: 兄ちゃん is not
        # dictionary-attested, but mints (with its にい reading) anyway.
        service = SubtitleParserService(AnkiMinerConfig(), term_lookup=_gate_term_lookup(set()))
        unit = ReadingUnit(text="お兄ちゃん", index=0, location_label="t")
        words, _index, _counts = service.parse_text_units([unit], want_line_index=False)
        word = next(w for w in words if w.mined_form == "兄ちゃん")
        assert word.expression_reading == "にいちゃん"

    # --- no-dict safe degrade: term_lookup=None == pre-gate output --------

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("刑務所", {"刑務所"}),
            ("不可能", {"不可能"}),
            ("会議中", {"会議中"}),  # junk minted, exactly as pre-gate
            ("状況的", {"状況的"}),  # junk minted, exactly as pre-gate
            ("言い方", {"言い方"}),
        ],
    )
    def test_no_dict_safe_degrade_matches_pregate(self, sentence, expected):
        assert self._mine(sentence, dictionary=None) == expected

    def test_attest_probed_once_per_surface_across_repeated_corpus(self):
        # Perf discipline: the memoized attest probes each distinct surface
        # through the underlying dictionary at most once across a repeated
        # corpus. 会議中 bails on every one of 12 lines, but is probed once.
        calls: list[str] = []

        def spy(terms):
            calls.extend(terms)
            return set()  # attest nothing → 会議中 bails each line

        service = SubtitleParserService(AnkiMinerConfig(), term_lookup=spy)
        units = [ReadingUnit(text="会議中", index=i, location_label="t") for i in range(12)]
        words, _index, _counts = service.parse_text_units(units, want_line_index=False)
        assert {w.mined_form for w in words} == {"会議"}
        assert calls.count("会議中") == 1, calls


# ---------------------------------------------------------------------------
# Structural subtitle-annotation stripping (Task U1)
# ---------------------------------------------------------------------------


class TestAnnotationStripping:
    """Default-ON structural strip of SFX captions / speaker tags / inline
    furigana at the parser choke point (config.strip_subtitle_annotations)."""

    @staticmethod
    def _keyed_tagger():
        """Fake tagger returning tokens by exact input text.

        Covers both the stripped form the tagger sees when stripping is ON and
        the raw annotation form it sees when stripping is OFF, so the two runs
        exercise the same tagger.
        """

        def tokenize(text):
            table = {
                # Legit dialogue line (never altered by the strip).
                "本を読む": [
                    _make_token("本", "名詞", pos2="普通名詞", lemma="本", kana="ホン"),
                    _make_token("を", "助詞"),
                    _make_token("読む", "動詞", lemma="読む", kana="ヨム"),
                ],
                # Annotation line, STRIPPED form (stripping ON): only the filler
                # ん… survives, which is not mineable.
                "ん…": [
                    _make_token("ん", "感動詞", lemma="ん", kana="ン"),
                    _make_token("…", "補助記号"),
                ],
                # Annotation line, RAW form (stripping OFF): the speaker-tag
                # name 旬 tokenizes as a mineable 名詞 — the junk we kill.
                "（水篠(みずしの) 旬(しゅん)）ん…": [
                    _make_token("水篠", "名詞", pos2="固有名詞", lemma="水篠", kana="ミズシノ"),
                    _make_token("みずしの", "名詞", pos2="普通名詞", lemma="みずしの", kana="ミズシノ"),
                    _make_token("旬", "名詞", pos2="普通名詞", lemma="旬", kana="シュン"),
                    _make_token("しゅん", "名詞", pos2="普通名詞", lemma="しゅん", kana="シュン"),
                    _make_token("ん", "感動詞", lemma="ん", kana="ン"),
                    _make_token("…", "補助記号"),
                ],
                # Inline-furigana line, STRIPPED form: 瀕死 kept, ひんし gone.
                "瀕死の重傷": [
                    _make_token("瀕死", "名詞", pos2="普通名詞", lemma="瀕死", kana="ヒンシ"),
                    _make_token("の", "助詞"),
                    _make_token("重傷", "名詞", pos2="普通名詞", lemma="重傷", kana="ジュウショウ"),
                ],
            }
            return table.get(text, [])

        tagger = MagicMock()
        tagger.side_effect = tokenize
        return tagger

    @staticmethod
    def _subs(*texts):
        """Mock subs whose __iter__ yields a FRESH line iterator each call, so a
        test may run both parse_subtitle_file and parse_raw_entries on it."""
        lines = []
        for txt in texts:
            ln = MagicMock()
            ln.text = txt
            ln.start = 1000
            ln.end = 3000
            lines.append(ln)
        subs = MagicMock()
        subs.__iter__ = MagicMock(side_effect=lambda: iter(lines))
        return subs

    def _make_service(self, config, tagger, subs):
        """Construct the service and return it with the pysubs2.load patch as a
        live context manager the caller keeps open across parse calls."""
        cm = patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=subs)
        cm.start()
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=tagger):
            service = SubtitleParserService(config)
        return service, cm

    def test_speaker_tag_word_stripped_default_on(self, test_config, tmp_path):
        """Default-ON: the speaker-tag name 旬 never reaches emitted words; the
        legit dialogue word survives."""
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("（水篠(みずしの) 旬(しゅん)）ん…", "本を読む")
        service, cm = self._make_service(test_config, self._keyed_tagger(), subs)
        try:
            words = service.parse_subtitle_file(sub_file)
        finally:
            cm.stop()

        surfaces = {w.surface for w in words}
        lemmas = {w.lemma for w in words}
        readings = {w.reading for w in words} | {w.expression_reading for w in words}
        assert "旬" not in surfaces and "旬" not in lemmas
        assert "しゅん" not in readings and "ずし" not in readings
        assert "読む" in lemmas  # legit dialogue survives

    def test_config_off_is_byte_identical_old_behavior(self, tmp_path):
        """Stripping OFF: the raw annotation line reaches the tagger unchanged,
        so 旬 IS mined — proving the strip (not something else) removes it."""
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            strip_subtitle_annotations=False,
        )
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("（水篠(みずしの) 旬(しゅん)）ん…")
        service, cm = self._make_service(config, self._keyed_tagger(), subs)
        try:
            words = service.parse_subtitle_file(sub_file)
        finally:
            cm.stop()

        assert "旬" in {w.surface for w in words}

    def test_emitted_sentence_is_the_stripped_text(self, test_config, tmp_path):
        """The Sentence stored on the card is the STRIPPED line, with the inline
        furigana gone (no leftover (ひんし))."""
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("瀕死(ひんし)の重傷")
        service, cm = self._make_service(test_config, self._keyed_tagger(), subs)
        try:
            words = service.parse_subtitle_file(sub_file)
        finally:
            cm.stop()

        hinshi = next(w for w in words if w.surface == "瀕死")
        assert hinshi.sentence == "瀕死の重傷"
        assert "(ひんし)" not in hinshi.sentence
        assert "ひんし" not in hinshi.sentence

    def test_parse_raw_entries_display_strips_when_on(self, test_config, tmp_path):
        """Display path (curation player / timing viewer) strips too so the shown
        cue text matches what mining sees."""
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("瀕死(ひんし)の重傷")
        service, cm = self._make_service(test_config, self._keyed_tagger(), subs)
        try:
            entries = service.parse_raw_entries(sub_file)
        finally:
            cm.stop()

        assert len(entries) == 1
        assert entries[0][2] == "瀕死の重傷"

    def test_parse_raw_entries_no_strip_when_off(self, tmp_path):
        """Display path leaves the annotation intact when stripping is OFF."""
        config = AnkiMinerConfig(
            media_temp_folder=tmp_path / "media",
            strip_subtitle_annotations=False,
        )
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("瀕死(ひんし)の重傷")
        service, cm = self._make_service(config, self._keyed_tagger(), subs)
        try:
            entries = service.parse_raw_entries(sub_file)
        finally:
            cm.stop()

        assert len(entries) == 1
        assert entries[0][2] == "瀕死(ひんし)の重傷"

    def test_whole_line_caption_yields_no_words(self, test_config, tmp_path):
        """A pure SFX caption line strips to empty and is skipped entirely on
        both the mining and display paths."""
        sub_file = tmp_path / "t.ass"
        sub_file.write_text("x", encoding="utf-8")

        subs = self._subs("（スマホのバイブ音）")
        service, cm = self._make_service(test_config, self._keyed_tagger(), subs)
        try:
            words = service.parse_subtitle_file(sub_file)
            entries = service.parse_raw_entries(sub_file)
        finally:
            cm.stop()

        assert words == []
        assert entries == []


class TestKatakanaFragmentGuard:
    """Post-acceptance reject of all-katakana tokenizer-fragments of unmerged runs.

    Live-audit 2026-07: unknown katakana names/compounds (アイスベア → アイス|ベア,
    アンデット → アン|デット, レッドゲート, ヒヒッ, ウオオッ) short-unit segment into
    dictionary-matching fragments (ベア, レッド, ヒヒ are real JMdict headwords)
    that pass ``should_include``'s >=2-char katakana floor and mine as junk cards
    ("increase in basic salary" for ベア). Attestation cannot catch them; the ONLY
    signal is positional — the token sits INSIDE a longer unmerged katakana run —
    so the guard rejects an all-katakana token whose raw-text neighbor (either
    side) continues the katakana run. Active ONLY with an offline dict wired
    (gated on the compound matcher): without one, legit full runs can never be
    merged upstream, so the rule would reject both halves of every real compound.
    """

    def _invoke(self, tmp_path, test_config, text, tokens, dictionary, fn):
        """Build the service under a mocked tagger+subs and run ``fn(service, file)``.

        ``dictionary`` is ``None`` → no ``term_lookup`` wired (compound matcher
        None, guard inactive); a set → an offline dict attesting exactly those
        headwords (guard active). ``tokens`` is the mock tagger's return for any
        input line — callers control the katakana split directly.
        """
        sub_file = tmp_path / "kata.srt"
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
            kwargs = {} if dictionary is None else {"term_lookup": _lookup_for(dictionary)}
            service = SubtitleParserService(test_config, **kwargs)
            return fn(service, sub_file)

    def _mine(self, tmp_path, test_config, text, tokens, dictionary):
        return self._invoke(tmp_path, test_config, text, tokens, dictionary, lambda s, f: s.parse_subtitle_file(f))

    # --- 1. Fragment of an unmerged run: both halves rejected (positional). ---

    def test_both_fragments_of_unmerged_run_rejected(self, tmp_path, test_config):
        # アイスベア splits アイス|ベア; the full run is NOT a headword, so nothing
        # merges. ベア is attested standalone in the fake dict yet STILL rejected —
        # proving the reject is positional, not attestation-driven.
        tokens = [
            _make_token("アイス", "名詞", "普通名詞", lemma="アイス", kana="アイス"),
            _make_token("ベア", "名詞", "普通名詞", lemma="ベア", kana="ベア"),
        ]
        words = self._mine(tmp_path, test_config, "アイスベア", tokens, {"ベア"})
        assert words == []  # both fragments dropped

    # --- 2. Full run IS a headword → merged; the synthetic is never rejected. ---

    def test_attested_full_run_merges_and_survives(self, tmp_path, test_config):
        tokens = [
            _make_token("アン", "名詞", "普通名詞", lemma="アン", kana="アン"),
            _make_token("デッド", "名詞", "普通名詞", lemma="デッド", kana="デッド"),
        ]
        words = self._mine(tmp_path, test_config, "アンデッド", tokens, {"アンデッド"})
        assert [w.lemma for w in words] == ["アンデッド"]
        assert words[0].mined_form == "アンデッド"

    def test_compound_synthetic_with_katakana_neighbor_never_rejected(self, tmp_path, test_config):
        # アンデッド is attested (merges) but the trailing ゾンビ is a separate,
        # unmerged token: the merged synthetic's span [0,5) abuts katakana ゾ on
        # its right, yet the CompoundSyntheticToken is EXEMPT. The residual ゾンビ
        # is the accepted precision-over-recall loss (its left neighbor ド is
        # katakana).
        tokens = [
            _make_token("アン", "名詞", "普通名詞", lemma="アン", kana="アン"),
            _make_token("デッド", "名詞", "普通名詞", lemma="デッド", kana="デッド"),
            _make_token("ゾンビ", "名詞", "普通名詞", lemma="ゾンビ", kana="ゾンビ"),
        ]
        words = self._mine(tmp_path, test_config, "アンデッドゾンビ", tokens, {"アンデッド"})
        assert [w.lemma for w in words] == ["アンデッド"]  # synthetic kept, ゾンビ dropped

    # --- 3. Standalone katakana (no adjacent katakana) is kept. ---

    def test_standalone_katakana_loanword_kept(self, tmp_path, test_config):
        # Guard active (empty dict wired) but バス has no katakana neighbor.
        tokens = [_make_token("バス", "名詞", "普通名詞", lemma="バス", kana="バス")]
        words = self._mine(tmp_path, test_config, "バス", tokens, set())
        assert [w.surface for w in words] == ["バス"]

    # --- 4. Onomatopoeia-run fragments (adjacent ー/ッ continue the run). ---

    @pytest.mark.parametrize(
        ("text", "fragment", "trailer"),
        [
            ("ヒヒッ", "ヒヒ", "ッ"),  # ヒヒ + small-tsu trailer → ヒヒ rejected
            ("ウオオッ", "ウオ", "オッ"),  # ウオ + オッ → ウオ rejected (right neighbor オ)
        ],
    )
    def test_onomatopoeia_run_fragment_rejected(self, tmp_path, test_config, text, fragment, trailer):
        tokens = [
            _make_token(fragment, "名詞", "普通名詞", lemma=fragment, kana=fragment),
            _make_token(trailer, "補助記号", "*", lemma=trailer, kana=trailer),
        ]
        words = self._mine(tmp_path, test_config, text, tokens, set())
        assert words == []

    # --- 5. No dict wired → guard inactive → byte-identical old behavior. ---

    def test_no_dict_guard_inactive_byte_identical(self, tmp_path, test_config):
        tokens = [
            _make_token("アイス", "名詞", "普通名詞", lemma="アイス", kana="アイス"),
            _make_token("ベア", "名詞", "普通名詞", lemma="ベア", kana="ベア"),
        ]
        words = self._mine(tmp_path, test_config, "アイスベア", tokens, None)
        # Pin the exact pre-guard emitted set: both fragments still mined.
        assert [w.surface for w in words] == ["アイス", "ベア"]

    # --- 6. T-38 count==mine parity across all four span-iterating call sites. ---

    def test_count_equals_mine_at_all_sites(self, tmp_path, test_config):
        text = "猫アイスベア"

        def tokens():
            return [
                _make_token("猫", "名詞", "普通名詞", lemma="猫", kana="ネコ"),
                _make_token("アイス", "名詞", "普通名詞", lemma="アイス", kana="アイス"),
                _make_token("ベア", "名詞", "普通名詞", lemma="ベア", kana="ベア"),
            ]

        mine = self._invoke(tmp_path, test_config, text, tokens(), set(), lambda s, f: s.parse_subtitle_file(f))
        idx_words, idx_lines = self._invoke(
            tmp_path, test_config, text, tokens(), set(), lambda s, f: s.parse_subtitle_file_with_index(f)
        )
        counts = self._invoke(tmp_path, test_config, text, tokens(), set(), lambda s, f: s.count_lemmas(f))

        def parse_units(s, _f):
            unit = ReadingUnit(text=text, index=0, location_label="t")
            return s.parse_text_units([unit], want_line_index=True)

        pt_words, pt_index, pt_counts = self._invoke(tmp_path, test_config, text, tokens(), set(), parse_units)

        expected = {"猫"}  # both katakana fragments dropped at every site
        assert {w.lemma for w in mine} == expected
        assert {w.lemma for w in idx_words} == expected
        assert {lm for line in idx_lines for lm in line.lemmas} == expected
        assert set(counts) == expected
        assert {w.lemma for w in pt_words} == expected
        assert {lm for line in pt_index for lm in line.lemmas} == expected
        assert set(pt_counts) == expected

    # --- 7. Katakana-dense line: documented accepted-loss budget (exact set). ---

    def test_katakana_dense_accepted_loss_budget(self, tmp_path, test_config):
        # スマホ|ケース|と|バッグ. スマホ and ケース are both legit loanwords, but
        # their full run スマホケース is no headword, so BOTH are dropped (the
        # accepted precision-over-recall loss: 2 legit tokens). バッグ survives
        # because the hiragana particle と separates it from the katakana run —
        # whitespace/non-katakana does NOT continue a run. If a future change
        # alters this budget, this exact-set assertion surfaces it.
        tokens = [
            _make_token("スマホ", "名詞", "普通名詞", lemma="スマホ", kana="スマホ"),
            _make_token("ケース", "名詞", "普通名詞", lemma="ケース", kana="ケース"),
            _make_token("と", "助詞", "格助詞", lemma="と", kana="ト"),
            _make_token("バッグ", "名詞", "普通名詞", lemma="バッグ", kana="バッグ"),
        ]
        words = self._mine(tmp_path, test_config, "スマホケースとバッグ", tokens, set())
        assert [w.surface for w in words] == ["バッグ"]

    # --- 8. Author-inserted ・ separator: NOT a run — both halves survive. ---

    def test_nakaguro_separated_pair_both_kept(self, tmp_path, test_config):
        # アイス・ベア: the middle dot ・ (U+30FB) is an author-inserted SEPARATOR,
        # not an unmerged run. Both アイス and ベア are attested katakana headwords
        # and MUST survive (アイス・ベア / メリット・デメリット / オン・オフ class).
        # The ・ token itself is 補助記号 → dropped by should_include.
        tokens = [
            _make_token("アイス", "名詞", "普通名詞", lemma="アイス", kana="アイス"),
            _make_token("・", "補助記号", "*", lemma="・", kana="・"),
            _make_token("ベア", "名詞", "普通名詞", lemma="ベア", kana="ベア"),
        ]
        words = self._mine(tmp_path, test_config, "アイス・ベア", tokens, {"アイス", "ベア"})
        assert [w.surface for w in words] == ["アイス", "ベア"]

    # --- 9. Literal whitespace separator: NOT a run — both halves survive. ---

    def test_whitespace_separated_pair_both_kept(self, tmp_path, test_config):
        # アイ ウォン: a literal space between two katakana tokens does NOT continue
        # a run (space-separated transliteration is out of scope). Both survive —
        # pins the exact constraint the brief named (existing dense test used a
        # hiragana particle と, not a space).
        tokens = [
            _make_token("アイ", "名詞", "普通名詞", lemma="アイ", kana="アイ"),
            _make_token("ウォン", "名詞", "普通名詞", lemma="ウォン", kana="ウォン"),
        ]
        words = self._mine(tmp_path, test_config, "アイ ウォン", tokens, {"アイ", "ウォン"})
        assert [w.surface for w in words] == ["アイ", "ウォン"]

    # --- 10. A line with no katakana runs is byte-identical with/without a dict. ---

    def test_no_katakana_line_byte_parity(self, tmp_path, test_config):
        def tokens():
            return [
                _make_token("猫", "名詞", "普通名詞", lemma="猫", kana="ネコ"),
                _make_token("と", "助詞", "格助詞", lemma="と", kana="ト"),
                _make_token("犬", "名詞", "普通名詞", lemma="犬", kana="イヌ"),
            ]

        with_dict = self._mine(tmp_path, test_config, "猫と犬", tokens(), set())
        without_dict = self._mine(tmp_path, test_config, "猫と犬", tokens(), None)
        assert with_dict == without_dict
        assert [w.surface for w in with_dict] == ["猫", "犬"]
