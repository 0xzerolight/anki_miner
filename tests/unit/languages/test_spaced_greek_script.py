"""The Greek script gate (E.10 D3): Greek and Coptic plus Greek Extended letters."""

from __future__ import annotations

from anki_miner.languages._spaced.script import GreekScript, is_greek_letter


def test_greek_letters_are_monotonic_and_polytonic_letters_only():
    assert all(is_greek_letter(c) for c in "αωΑΩάΐΰϊςσ")
    assert is_greek_letter("\u1f71")  # alpha with oxia, Greek Extended
    assert is_greek_letter("\u1f00")  # alpha with psili, Greek Extended
    # Greek question mark, ano teleia and numeral sign are punctuation, not letters.
    assert not any(is_greek_letter(c) for c in "\u037e\u0387\u0375az1-'.食한ы")


def test_the_gate_needs_one_greek_letter():
    script = GreekScript()
    assert script.filter_options() == ()
    assert script.matches("anything", "βιβλίο") is False
    assert script.contains_target_script("βιβλίο")
    assert script.contains_target_script("OK, ευχαριστώ")
    assert not script.contains_target_script("hola")
    assert not script.contains_target_script("книга")
    assert not script.contains_target_script("日本語")
    assert not script.contains_target_script("3,14\u037e")
