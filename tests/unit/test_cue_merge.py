"""Table-driven tests for the automatic cue-merge rule (FUTURE_IDEAS 6)."""

from __future__ import annotations

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.cue_merge import (
    MAX_MERGE_GAP_SECONDS,
    MAX_MERGED_SECONDS,
    auto_line_expansion,
    ends_sentence,
    merge_budget_seconds,
)

JA_RULES = get_profile("ja").sentence_rules
KO_RULES = get_profile("ko").sentence_rules
ZH_RULES = get_profile("zh").sentence_rules


def _cues(*texts: str, start: float = 0.0, length: float = 2.0, gap: float = 0.5):
    """Evenly spaced cues: (start, end, text) with ``gap`` seconds between."""
    out = []
    at = start
    for text in texts:
        out.append((at, at + length, text))
        at += length + gap
    return out


class TestEndsSentence:
    @pytest.mark.parametrize(
        ("text", "rules", "expected"),
        [
            ("食べるのテスト。", JA_RULES, True),
            ("本当ですか？", JA_RULES, True),
            ("やめろ！", JA_RULES, True),
            ("「なるほど。」", JA_RULES, True),  # a trailing closer does not hide it
            ("だから ", JA_RULES, False),
            ("だから…", JA_RULES, False),  # an ellipsis never terminates
            ("……。", JA_RULES, True),  # the 。 after the ellipsis does
            ("我们走吧。", ZH_RULES, True),
            ("我们", ZH_RULES, False),
            ("갔습니다.", KO_RULES, True),  # ko's terminator set carries "."
            ("그리고", KO_RULES, False),
            ("그리고.", JA_RULES, False),  # ja's does not
        ],
    )
    def test_terminal_punctuation(self, text, rules, expected):
        assert ends_sentence(text, rules) is expected


class TestAutoLineExpansion:
    def test_terminated_cue_is_its_own_sentence(self):
        entries = _cues("一行目です。", "二行目です。", "三行目です。")
        assert auto_line_expansion(entries, 1, JA_RULES) == (0, 0)

    def test_unterminated_cue_absorbs_the_next(self):
        entries = _cues("一行目です。", "だから僕は", "行きました。")
        assert auto_line_expansion(entries, 1, JA_RULES) == (0, 1)

    def test_unterminated_previous_cue_is_absorbed(self):
        entries = _cues("だから僕は", "行きました。", "次です。")
        assert auto_line_expansion(entries, 1, JA_RULES) == (1, 0)

    def test_both_sides_merge(self):
        entries = _cues("前です。", "だから", "僕は", "行きました。")
        assert auto_line_expansion(entries, 2, JA_RULES) == (1, 1)

    def test_cue_cap_bounds_each_side(self):
        entries = _cues("あ", "い", "う", "え", "お", "か")
        assert auto_line_expansion(entries, 3, JA_RULES) == (2, 2)

    def test_gap_stops_the_merge(self):
        entries = [
            (0.0, 2.0, "だから僕は"),
            (2.0 + MAX_MERGE_GAP_SECONDS + 0.1, 6.0, "行きました。"),
        ]
        assert auto_line_expansion(entries, 0, JA_RULES) == (0, 0)

    def test_gap_exactly_at_the_bound_still_merges(self):
        entries = [
            (0.0, 2.0, "だから僕は"),
            (2.0 + MAX_MERGE_GAP_SECONDS, 6.0, "行きました。"),
        ]
        assert auto_line_expansion(entries, 0, JA_RULES) == (0, 1)

    def test_window_cap_stops_the_merge(self):
        entries = [
            (0.0, MAX_MERGED_SECONDS, "だから僕は"),
            (MAX_MERGED_SECONDS, MAX_MERGED_SECONDS + 5.0, "行きました。"),
        ]
        assert auto_line_expansion(entries, 0, JA_RULES) == (0, 0)

    def test_file_edges_clamp(self):
        entries = _cues("だから僕は")
        assert auto_line_expansion(entries, 0, JA_RULES) == (0, 0)

    def test_forward_absorption_spends_the_budget_the_backward_side_needed(self):
        """The backward span is measured over the ALREADY-extended window, so a
        forward merge that fills the budget blocks the preceding cue."""
        entries = [
            (0.0, 5.0, "だから"),
            (5.5, 20.0, "僕は"),
            (20.5, 33.0, "行きました。"),
        ]
        assert auto_line_expansion(entries, 1, JA_RULES) == (0, 1)

    def test_gap_is_measured_from_the_window_s_last_cue(self):
        """Overlapping cues (an ASS sign over dialogue): the second hop is
        judged against the cue just absorbed, not against the word's own."""
        entries = [
            (0.0, 6.0, "だから"),
            (0.5, 2.0, "僕は"),
            (7.0, 9.0, "行きました。"),
        ]
        assert auto_line_expansion(entries, 0, JA_RULES) == (0, 1)

    def test_korean_period_terminates(self):
        entries = _cues("갔습니다.", "그리고", "왔습니다.")
        assert auto_line_expansion(entries, 1, KO_RULES) == (0, 1)

    def test_chinese_line_merges_forward(self):
        entries = _cues("你好。", "我们一起", "走吧。")
        assert auto_line_expansion(entries, 1, ZH_RULES) == (0, 1)


class TestMergeBudget:
    def test_padding_comes_off_both_sides(self):
        assert merge_budget_seconds(0.3) == pytest.approx(MAX_MERGED_SECONDS - 0.6)

    def test_absurd_padding_floors_at_zero(self):
        assert merge_budget_seconds(100.0) == 0.0
