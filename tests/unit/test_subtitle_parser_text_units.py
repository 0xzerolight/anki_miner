"""Tests for SubtitleParserService.parse_text_units (reading-tab entrypoint).

The reading pipeline hands mined text as ``ReadingUnit``s (book paragraphs /
manga blocks) instead of subtitle files. ``parse_text_units`` is the single
public parser entrypoint it calls: one tokenize pass per unit, unit text as the
card sentence verbatim, dummy index-based timing.
"""

import dataclasses
from collections import Counter
from unittest.mock import MagicMock, patch

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import LineLemmas
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.compound_matcher import CompoundSyntheticToken
from anki_miner.services.subtitle_parser import SubtitleParserService


def _make_token(surface, pos1, *, pos2=None, lemma=None, kana=None, orth_base=None):
    """Minimal mock fugashi token (see test_subtitle_parser._make_token).

    ``orthBase`` defaults to the lemma and ``lForm``/``kanaBase`` are pinned to
    None so an auto-created truthy MagicMock attribute never leaks into
    ``mined_form`` or trips the mining_base fold.
    """
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    token.feature.orthBase = orth_base if orth_base is not None else token.feature.lemma
    token.feature.lForm = None
    token.feature.kanaBase = None
    return token


def _write_srt(path, lines):
    """Write ``lines`` as a minimal SRT (3s cues), one line per cue."""
    blocks = []
    for i, text in enumerate(lines):
        start = (i + 1) * 3
        end = start + 2
        blocks.append(f"{i + 1}\n00:00:{start:02d},000 --> 00:00:{end:02d},000\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _strip_timing(word):
    """dataclasses.asdict minus the timing fields that legitimately differ."""
    d = dataclasses.asdict(word)
    for key in ("start_time", "end_time", "duration"):
        d.pop(key)
    return d


class TestParseTextUnits:
    def test_words_match_subtitle_path_modulo_timing(self, tmp_path):
        """Same text via the subtitle path vs the unit path → identical words except timing."""
        lines = ["猫が魚を食べる", "犬も魚を食べた", "鳥が空を飛ぶ"]
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        srt = _write_srt(tmp_path / "parity.srt", lines)
        sub_words = service.parse_subtitle_file(srt)

        units = [ReadingUnit(text=t, index=i, location_label=f"p.{i}") for i, t in enumerate(lines)]
        unit_words, index, _counts = service.parse_text_units(units, want_line_index=False)

        assert index is None
        assert len(unit_words) > 0
        assert [_strip_timing(w) for w in unit_words] == [_strip_timing(w) for w in sub_words]

    def test_counter_parity_with_count_lemmas_including_dropped_synthetic(self, test_config, tmp_path):
        """Counter equals count_lemmas on shared text, and a span-dropped compound
        synthetic is counted in neither (the T-38 mine-vs-count drop-rule gate)."""
        text = "本を読む"
        tokens = [
            _make_token("本", "名詞", lemma="本", kana="ホン"),
            _make_token("読む", "動詞", lemma="読む", kana="ヨム", orth_base="読む"),
            # Concatenated compound surface not find-able in `text` → dropped by
            # _iter_token_spans (the whitespace-merge case count_lemmas defends).
            CompoundSyntheticToken(
                surface="気がする", pos1="動詞", pos2="非自立可能", lemma="気がする", kana="キガスル"
            ),
        ]
        mock_tagger = MagicMock(return_value=tokens)

        sub_file = tmp_path / "count.srt"
        sub_file.write_text("placeholder", encoding="utf-8")
        mock_line = MagicMock()
        mock_line.text = text
        mock_line.start = 1000
        mock_line.end = 3000
        mock_subs = MagicMock()
        mock_subs.__iter__ = MagicMock(return_value=iter([mock_line]))

        with patch("anki_miner.services.subtitle_parser.get_shared_tagger", return_value=mock_tagger):
            service = SubtitleParserService(test_config)

        with patch("anki_miner.services.subtitle_parser.pysubs2.load", return_value=mock_subs):
            expected = service.count_lemmas(sub_file)

        units = [ReadingUnit(text=text, index=0, location_label="p.0")]
        _words, _index, counter = service.parse_text_units(units, want_line_index=False)

        assert counter == expected
        assert counter == Counter({"本": 1, "読む": 1})
        assert "気がする" not in counter

    def test_unit_text_is_sentence_verbatim(self, tmp_path):
        """Unit text (multiple sentences, no re-windowing) reaches every word's sentence field."""
        text = "猫が魚を食べる。犬が走る。"
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        units = [ReadingUnit(text=text, index=0, location_label="p.0")]
        words, _index, _counts = service.parse_text_units(units, want_line_index=False)

        assert words
        assert all(w.sentence == text for w in words)

    def test_line_index_built_with_index_as_start_time(self, tmp_path):
        """want_line_index=True → LineLemmas per unit, start_time == float(unit.index), duration 0."""
        lines = ["猫が魚を食べる", "犬が走る"]
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        units = [ReadingUnit(text=t, index=i * 5, location_label=f"p.{i}") for i, t in enumerate(lines)]
        words, index, counter = service.parse_text_units(units, want_line_index=True)

        assert index is not None
        assert all(isinstance(e, LineLemmas) for e in index)
        assert [e.line_text for e in index] == lines
        assert [e.start_time for e in index] == [0.0, 5.0]
        assert all(e.end_time == e.start_time for e in index)
        assert all(e.duration == 0.0 for e in index)
        # Duration filters are inert (duration 0.0) but words/counter still flow.
        assert words
        assert counter

    def test_want_line_index_false_returns_none(self, tmp_path):
        """want_line_index=False → the index element is None."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        units = [ReadingUnit(text="猫が魚を食べる", index=0, location_label="p.0")]
        _words, index, _counts = service.parse_text_units(units, want_line_index=False)

        assert index is None

    def test_normalizes_reading_text_before_tokenizing(self, tmp_path):
        """Reading/OCR units get pre-tokenization JP normalization (Bug J4).

        mokuro OCR emits Kangxi radicals (⼝ U+2F1D) and halfwidth katakana
        (ﾊﾟｿｺﾝ) that mis-tokenize verbatim — the content word is dropped and the
        radical/halfwidth glyphs reach the card sentence. parse_text_units now
        applies the same normalize_for_tokenization + standardize_kanji_variants
        the subtitle path runs via clean_subtitle_text, so the unit tokenizes to
        the real word and the stored sentence is the normalized form.
        """
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)

        # Halfwidth katakana ﾊﾟｿｺﾝ → パソコン; Kangxi radical ⼝ → 口.
        units = [
            ReadingUnit(text="ﾊﾟｿｺﾝを使う", index=0, location_label="p.0"),
            ReadingUnit(text="⼝を開ける", index=1, location_label="p.1"),
        ]
        words, _index, counts = service.parse_text_units(units, want_line_index=False)

        mined = {w.mined_form for w in words}
        assert "パソコン" in mined  # halfwidth-folded, not dropped as garbage
        assert "口" in mined  # Kangxi radical folded to the real kanji

        # Displayed sentence matches what was tokenized (normalized, not raw).
        sentences = {w.sentence for w in words}
        assert sentences == {"パソコンを使う", "口を開ける"}
        assert "ﾊﾟｿｺﾝ" not in "".join(sentences)
        assert "⼝" not in "".join(sentences)
        # Counter keys on the normalized lemmas too.
        assert counts["パソコン"] == 1

    def test_resets_caches_and_handles_empty_units(self, test_config):
        """_reset_caches runs first (seeded caches cleared); empty units → empty results."""
        with patch("anki_miner.services.subtitle_parser.get_shared_tagger"):
            service = SubtitleParserService(test_config)

        service._fg_cache = {"seed": "x"}
        service._rd_cache = {"seed": "y"}

        words, index, counter = service.parse_text_units([], want_line_index=False)

        assert service._fg_cache == {}
        assert service._rd_cache == {}
        assert words == []
        assert index is None
        assert counter == Counter()


class TestParseTextUnitsSubtitleCleanup:
    """subtitle_cleanup=True (Reading→Subtitles cue kind) runs the video path's
    annotation strip + user regex; default False leaves manga/book units alone.

    Real fugashi throughout (the guards must fire against attested tokenization,
    not stubbed tokens): the strip changes what MeCab actually tokenizes.
    """

    def test_inline_furigana_stripped_fixes_misparse(self, tmp_path):
        """瀕死(ひんし)の腑抜(ふぬ)け: without cleanup the kana furigana splits
        腑抜け into 腑 + 抜ける and leaks into the sentence; cleanup=True strips it
        so the compound tokenizes whole and the sentence is clean."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="瀕死(ひんし)の腑抜(ふぬ)け", index=0, location_label="0:01")]

        off_words, _oi, off_counts = service.parse_text_units(units, want_line_index=False)
        # Attest the misparse the strip fixes: furigana splits 腑抜け and leaks.
        assert "腑抜け" not in off_counts
        assert "腑" in off_counts and "抜ける" in off_counts
        assert all("(" in w.sentence for w in off_words)

        on_words, _i, on_counts = service.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert "腑抜け" in on_counts  # compound recovered
        assert "腑" not in on_counts and "抜ける" not in on_counts
        assert {w.sentence for w in on_words} == {"瀕死の腑抜け"}

    def test_caption_cue_skipped_no_count_no_index(self, tmp_path):
        """A whole-line SFX caption cue collapses to empty → no word, no count,
        no LineLemmas entry, and cannot misalign the following cue's i+1 index."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [
            ReadingUnit(text="（スマホのバイブ音）", index=0, location_label="0:01"),
            ReadingUnit(text="猫が魚を食べる", index=1, location_label="0:05"),
        ]
        words, index, counts = service.parse_text_units(units, want_line_index=True, subtitle_cleanup=True)

        # The caption contributed nothing; only the real cue mined.
        assert "音" not in counts and "スマホ" not in counts
        assert counts == Counter({"猫": 1, "魚": 1, "食べる": 1})
        assert all(w.sentence == "猫が魚を食べる" for w in words)
        # Exactly one index entry (the caption cue produced none) → no misalign.
        assert index is not None
        assert [e.line_text for e in index] == ["猫が魚を食べる"]
        assert [e.start_time for e in index] == [1.0]  # index=1 of the real cue

    def test_leading_speaker_tag_peeled(self, tmp_path):
        """（旬）… leading speaker tag is peeled under cleanup."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="（旬）猫が魚を食べる", index=0, location_label="0:01")]
        words, _i, _c = service.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert {w.sentence for w in words} == {"猫が魚を食べる"}
        assert all("旬" not in w.sentence for w in words)

    def test_physical_line_annotations_never_become_dialogue(self, tmp_path):
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="猫が好き\n（案内）犬が眠る", index=0, location_label="0:01")]

        assert service._clean_line_text(units[0].text) == "猫が好き 犬が眠る"
        words, _i, _c = service.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)

        assert {w.sentence for w in words} == {"猫が好き\n犬が眠る"}
        assert "案内" not in {w.mined_form for w in words}

    def test_strip_off_switch_leaves_annotations(self, tmp_path):
        """strip_subtitle_annotations=False disables the strip even under cleanup
        (the existing off-switch is the escape hatch — no new gate)."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media", strip_subtitle_annotations=False)
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="（旬）猫が魚を食べる", index=0, location_label="0:01")]
        words, _i, _c = service.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert any("旬" in w.sentence for w in words)  # tag NOT peeled

    def test_regex_filter_double_gated(self, tmp_path):
        """The user regex applies only when use_subtitle_regex_filter AND a
        non-empty pattern are both set (mirrors the video path's double gate)."""
        text = "猫が魚を食べるよ"
        units = [ReadingUnit(text=text, index=0, location_label="0:01")]
        base = {
            "media_temp_folder": tmp_path / "media",
            "subtitle_regex_filter": "よ$",
            "subtitle_regex_replacement": "",
        }

        on = SubtitleParserService(AnkiMinerConfig(**base, use_subtitle_regex_filter=True))
        on_words, _i, _c = on.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert {w.sentence for w in on_words} == {"猫が魚を食べる"}  # trailing よ removed

        off = SubtitleParserService(AnkiMinerConfig(**base, use_subtitle_regex_filter=False))
        off_words, _i2, _c2 = off.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert all(w.sentence == text for w in off_words)  # master switch off → unchanged

        empty = {"media_temp_folder": tmp_path / "media", "subtitle_regex_filter": "", "subtitle_regex_replacement": ""}
        blank = SubtitleParserService(AnkiMinerConfig(**empty, use_subtitle_regex_filter=True))
        blank_words, _i3, _c3 = blank.parse_text_units(units, want_line_index=False, subtitle_cleanup=True)
        assert all(w.sentence == text for w in blank_words)  # empty pattern → no filter

    def test_default_false_leaves_book_units_byte_identical(self, tmp_path):
        """subtitle_cleanup defaults False: a book paragraph with parenthetical
        kana is NOT stripped, and the words match the explicit-False call exactly."""
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="瀕死(ひんし)の状態", index=0, location_label="p.1")]

        default_words, _di, _dc = service.parse_text_units(units, want_line_index=False)
        explicit_words, _ei, _ec = service.parse_text_units(units, want_line_index=False, subtitle_cleanup=False)

        assert [_strip_timing(w) for w in default_words] == [_strip_timing(w) for w in explicit_words]
        # The paren furigana survives on the book path (no subtitle cleanup).
        assert all("(" in w.sentence for w in default_words)


class TestReadingPathDecorationStrip:
    """Reading/OCR units share the decoration strip via normalize_for_tokenization
    (F4, 2026-07 audit); other interior whitespace is stored verbatim."""

    def test_strips_glyphs_but_preserves_other_whitespace(self, tmp_path):
        config = AnkiMinerConfig(media_temp_folder=tmp_path / "media")
        service = SubtitleParserService(config)
        units = [ReadingUnit(text="📱通常兵器では➡", index=0, location_label="p.0")]
        words, _index, _counts = service.parse_text_units(units, want_line_index=False)
        assert words
        assert all(w.sentence == "通常兵器では" for w in words)
        # Unrelated interior whitespace stays (no blanket collapse on this path).
        units2 = [ReadingUnit(text="通常兵器　その他", index=0, location_label="p.0")]
        words2, _i, _c = service.parse_text_units(units2, want_line_index=False)
        assert any("　" in w.sentence for w in words2)
