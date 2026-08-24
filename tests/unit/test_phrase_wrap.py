"""Tests for the BudouX display-only phrase wrapper.

The helper feeds Qt plain-text views strings that wrap at Japanese phrase
boundaries instead of mid-word: WORD JOINER (U+2060) suppresses breaks inside
each BudouX chunk, bare chunk joins leave the boundaries breakable. The
transform is display-only, so its one hard contract is losslessness: stripping
the joiners must always give back the input byte-for-byte.
"""

from __future__ import annotations

from unittest.mock import patch

import budoux
import pytest

from anki_miner.gui.utils.phrase_wrap import WORD_JOINER, phrase_wrap_ja

_SENTENCES = [
    "今日はとてもいい天気ですね。",
    "朝ごはんを食べる",
    "彼女を想う気持ちが、長い長い夜のあいだずっと消えなかった。",
]


class TestPhraseWrapJa:
    @pytest.mark.parametrize("text", _SENTENCES)
    def test_strip_roundtrip_is_lossless(self, text):
        assert phrase_wrap_ja(text).replace(WORD_JOINER, "") == text

    @pytest.mark.parametrize("text", _SENTENCES)
    def test_joiner_count_matches_intra_phrase_pairs(self, text):
        # One joiner per intra-chunk character pair and none anywhere else:
        # sum(len(chunk) - 1) == len(text) - len(chunks). Holds for any
        # segmentation, so a model update can't break it.
        chunks = budoux.load_default_japanese_parser().parse(text)
        assert phrase_wrap_ja(text).count(WORD_JOINER) == len(text) - len(chunks)

    def test_breaks_land_only_between_phrases(self):
        text = "今日はとてもいい天気ですね。"
        chunks = budoux.load_default_japanese_parser().parse(text)
        assert phrase_wrap_ja(text) == "".join(WORD_JOINER.join(chunk) for chunk in chunks)

    def test_kana_only_text_is_wrapped(self):
        text = "ひらがなだけのぶんしょうです"
        assert WORD_JOINER in phrase_wrap_ja(text)

    def test_ascii_passes_through_untouched(self):
        # A single-chunk ASCII result would weld "hello world" into one
        # unbreakable run; Latin text already wraps at spaces, so skip it.
        assert phrase_wrap_ja("hello world, no Japanese here.") == "hello world, no Japanese here."

    def test_empty_passes_through(self):
        assert phrase_wrap_ja("") == ""

    def test_episode_prefixed_text_still_wraps_the_japanese(self):
        text = "[Ep01 - Winter] 今日はとてもいい天気ですね。"
        wrapped = phrase_wrap_ja(text)
        assert WORD_JOINER in wrapped
        assert wrapped.replace(WORD_JOINER, "") == text

    def test_parser_failure_returns_input(self):
        with patch(
            "anki_miner.gui.utils.phrase_wrap._japanese_parser",
            side_effect=RuntimeError("model load failed"),
        ):
            assert phrase_wrap_ja("今日はいい天気") == "今日はいい天気"
