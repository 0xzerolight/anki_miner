"""th display wrap: zero-width spaces between newmm tokens, display only."""

from __future__ import annotations

from anki_miner.languages.th.style import th_zwsp_wrap

ZWSP = "\N{ZERO WIDTH SPACE}"


def test_wrap_inserts_a_break_opportunity_between_tokens():
    wrapped = th_zwsp_wrap("วันนี้อากาศดีมากครับ")
    assert wrapped != "วันนี้อากาศดีมากครับ"
    assert wrapped.count(ZWSP) == 3  # four tokens, three joins


def test_stripping_the_inserted_character_yields_the_input_unchanged():
    for line in ("วันนี้อากาศดีมากครับ", "ผมชอบกินข้าวครับ", "ดู Netflix กัน", "กรุงเทพฯ"):
        assert th_zwsp_wrap(line).replace(ZWSP, "") == line


def test_a_line_that_already_carries_zwsp_is_not_doubled():
    line = "สวัสดี" + ZWSP + "ครับ"
    assert th_zwsp_wrap(line).count(ZWSP) == 1


def test_empty_and_latin_input_pass_through():
    assert th_zwsp_wrap("") == ""
    assert th_zwsp_wrap("Netflix") == "Netflix"


def test_a_tokenizer_failure_returns_the_input_rather_than_raising(monkeypatch):
    import anki_miner.languages.th.style as style

    monkeypatch.setattr(style, "_tagger", lambda: (_ for _ in ()).throw(RuntimeError("no engine")))
    assert style.th_zwsp_wrap("วันนี้อากาศดี") == "วันนี้อากาศดี"
