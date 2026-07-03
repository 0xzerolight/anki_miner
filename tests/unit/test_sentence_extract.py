"""Tests for anki_miner.utils.sentence_extract.extract_sentence (3.3).

The DOM-analog cases mirror the *sentence strings* pinned by Yomitan's
``extractSentence`` fixtures in ``test/data/html/document-util.html`` (upstream
commit e2ed450). Those fixtures' inputs are DOM ranges; several exercise
DOM-only behavior (imposter <input>/<textarea>, TextSourceElement <button>/<img>
alt text, layout-aware scan-extent truncation) with no single-string analog and
are deliberately dropped. The remaining cases are hand-authored plain
``(string, position)`` inputs whose expected output equals the fixture's
expected sentence. Original cases cover the quote-stack, terminator-run,
vertical-punctuation, whitespace-trim, and offset-guard paths.
"""

from __future__ import annotations

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils.sentence_extract import extract_sentence


def _caret(text: str, at: int) -> str:
    """Return only the extracted sentence for a zero-width caret at ``at``."""
    return extract_sentence(text, at, at)[0]


class TestExtractSentenceDomAnalogs:
    """String analogs of the 13 DOM scan fixtures (DOM-only cases dropped)."""

    def test_caret_before_quote_takes_whole(self):
        text = "真白「心配してくださって、ありがとございます」"
        assert _caret(text, 0) == text

    def test_caret_inside_quote_delimits_to_quote(self):
        text = "真白「心配してくださって、ありがとございます」"
        assert _caret(text, 5) == "心配してくださって、ありがとございます"

    def test_nested_quotes(self):
        text = "真白「心配して「くださって」、ありがと「ございます」」"
        assert _caret(text, 16) == "心配して「くださって」、ありがと「ございます」"

    def test_first_of_two_sentences(self):
        text = "ありがとございます。ありがとございます。"
        assert _caret(text, 4) == "ありがとございます。"

    def test_second_of_two_sentences(self):
        text = "ありがとございます。ありがとございます。"
        assert _caret(text, 14) == "ありがとございます。"

    def test_mixed_terminator_run(self):
        text = "ありがとございます。！？ありがとございます。！？"
        assert _caret(text, 4) == "ありがとございます。！？"

    def test_repeated_terminator_run(self):
        text = "ありがとございます！！！ありがとございます！！！"
        assert _caret(text, 4) == "ありがとございます！！！"

    def test_terminate_at_newlines(self):
        text = "\n".join(f"ありがとございます{i}" for i in range(1, 6))
        caret = text.index("ありがとございます3")
        assert extract_sentence(text, caret, caret)[0] == "ありがとございます3"

    def test_newlines_not_terminated_when_disabled(self):
        text = "前の行\n本の行\n次の行"
        caret = text.index("本")
        # With newline termination off, the walk runs to the ends (no terminators).
        assert extract_sentence(text, caret, caret, terminate_at_newlines=False)[0] == text


class TestExtractSentenceOriginal:
    def test_non_zero_width_term_and_offset(self):
        # A real mined span (not a caret): 行こう after a terminator.
        text = "よし。行こう。"
        sentence, offset = extract_sentence(text, 3, 6)
        assert sentence == "行こう。"
        assert offset == 0
        assert sentence[offset : offset + 3] == "行こう"

    def test_offset_preserves_term_position(self):
        text = "はい。本を読む。"
        # 読む occupies [5, 7); the sentence starts at 本 (index 3).
        sentence, offset = extract_sentence(text, 5, 7)
        assert sentence == "本を読む。"
        assert offset == 2
        assert sentence[offset : offset + 2] == "読む"

    def test_embedded_quote_does_not_terminate_narration(self):
        text = "彼は「行く」と言った。"
        # The mined verb 言っ sits outside the quote; the quoted 「行く」 is
        # absorbed, so the whole narration is one sentence.
        assert extract_sentence(text, 7, 9)[0] == text

    def test_vertical_terminator_run(self):
        text = "読む︒︕次の話"
        # Vertical ideographic full stop + exclamation both terminate at end.
        assert extract_sentence(text, 0, 2)[0] == "読む︒︕"

    def test_whitespace_trimmed_inside_anchor_bounds(self):
        text = "  hello  "
        sentence, offset = extract_sentence(text, 2, 7)
        assert sentence == "hello"
        assert offset == 0

    def test_invalid_offsets_return_text_unchanged(self):
        assert extract_sentence("abc", -1, 2) == ("abc", -1)
        assert extract_sentence("abc", 2, 5) == ("abc", 2)
        assert extract_sentence("abc", 3, 1) == ("abc", 3)


class TestParserTrimIntegration:
    """Parser-level canary: after trimming, the target offsets still index the
    surface in the trimmed sentence (the Issue #20 invariant survives rebase)."""

    def test_offsets_survive_rebasing(self, test_config, tmp_path):
        from anki_miner.services.subtitle_parser import SubtitleParserService

        srt = tmp_path / "clip.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nはい。本を読む。\n",
            encoding="utf-8",
        )
        config = replace(test_config, trim_to_sentence=True)
        words = SubtitleParserService(config).parse_subtitle_file(srt)

        assert words, "expected at least one mined word"
        for word in words:
            # Every emitted sentence is trimmed to the target's own sentence…
            assert word.sentence == "本を読む。"
            # …and the carried offsets still slice out the surface (invariant).
            assert word.sentence[word.surface_start : word.surface_end] == word.surface
            if word.highlight_end >= 0:
                assert word.highlight_end <= len(word.sentence)

    def test_bold_furigana_matches_trimmed_text(self, test_config, tmp_path):
        # trim + bold together: the furigana-from-tokens path must re-tokenize the
        # trimmed text, or the untrimmed cue leaks into the bolded furigana.
        from anki_miner.services.subtitle_parser import SubtitleParserService

        srt = tmp_path / "clip.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nはい。本を読む。\n",
            encoding="utf-8",
        )
        config = replace(test_config, trim_to_sentence=True, bold_target_in_sentence=True)
        words = SubtitleParserService(config).parse_subtitle_file(srt)

        assert words
        for word in words:
            # No untrimmed-cue leakage; exactly one bolded run around the target.
            assert "はい" not in word.sentence_furigana_bolded
            assert word.sentence_furigana_bolded.count("<b>") == 1
            assert word.surface in word.sentence_bolded

    def test_default_off_keeps_whole_cue(self, test_config, tmp_path):
        from anki_miner.services.subtitle_parser import SubtitleParserService

        srt = tmp_path / "clip.srt"
        srt.write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nはい。本を読む。\n",
            encoding="utf-8",
        )
        # Default config (trim_to_sentence=False): whole cue is preserved.
        words = SubtitleParserService(test_config).parse_subtitle_file(srt)
        assert words
        assert all(word.sentence == "はい。本を読む。" for word in words)


def test_config_trim_to_sentence_defaults_off():
    assert AnkiMinerConfig().trim_to_sentence is False
