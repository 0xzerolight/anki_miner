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
from anki_miner.services.compound_matcher import CompoundSyntheticToken
from anki_miner.services.reading.models import ReadingUnit
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
