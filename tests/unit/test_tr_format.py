"""Tests for anki_miner.utils.i18n.tr_format."""

from anki_miner.utils.i18n import tr_format


def test_single_placeholder() -> None:
    assert tr_format("Hello %1!", "world") == "Hello world!"


def test_multiple_placeholders() -> None:
    assert tr_format("Step %1: %2", "parse", 3) == "Step parse: 3"


def test_no_collision_percent10() -> None:
    """The regex pass must not confuse %10 with %1 followed by '0'."""
    args = ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")
    result = tr_format("%1 %10", *args)
    assert result == "a j"


def test_placeholder_with_no_arg_left_untouched() -> None:
    result = tr_format("Value: %1 Extra: %2", "only_one")
    assert result == "Value: only_one Extra: %2"
