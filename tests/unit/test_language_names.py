"""Display names and --sub-langs expression parsing for the Download picker."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtCore")

from anki_miner.gui.utils.language_names import (  # noqa: E402
    COMMON_SUBTITLE_LANGS,
    format_lang_list,
    language_display_name,
    parse_lang_list,
)


class TestDisplayName:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("ja", "Japanese"),
            ("en", "English"),
            ("ko", "Korean"),
            ("zh-Hans", "Chinese (Simplified Han)"),
            ("zh-Hant", "Chinese (Traditional Han)"),
            ("pt-BR", "Portuguese (Brazil)"),
            ("zh", "Chinese"),
            ("ja-orig", "Japanese (original)"),
            ("zh-Hans-orig", "Chinese (Simplified Han, original)"),
        ],
    )
    def test_known_codes(self, code, expected):
        assert language_display_name(code) == expected

    @pytest.mark.parametrize("code", ["live_chat", "xx", "zzzz"])
    def test_unresolvable_codes_return_the_code(self, code):
        assert language_display_name(code) == code

    def test_blank_is_blank(self):
        assert language_display_name("  ") == ""

    def test_underscore_form_is_accepted(self):
        assert language_display_name("pt_BR") == "Portuguese (Brazil)"


class TestParseLangList:
    def test_simple_list(self):
        assert parse_lang_list("ja,en") == ("ja", "en")

    def test_whitespace_and_empties_are_dropped(self):
        assert parse_lang_list(" ja , , en ") == ("ja", "en")

    def test_empty_string_is_an_empty_selection(self):
        assert parse_lang_list("") == ()

    @pytest.mark.parametrize("value", ["en.*", "all", "all,-live_chat", "ja,-en", "ja en"])
    def test_expressions_are_not_simple_lists(self, value):
        assert parse_lang_list(value) is None

    def test_duplicates_collapse_keeping_order(self):
        assert parse_lang_list("ja,en,ja") == ("ja", "en")


class TestFormatLangList:
    def test_round_trip(self):
        assert format_lang_list(("ja", "en")) == "ja,en"

    def test_empty(self):
        assert format_lang_list(()) == ""


def test_common_langs_are_unique_and_resolvable():
    assert len(set(COMMON_SUBTITLE_LANGS)) == len(COMMON_SUBTITLE_LANGS)
    # Every curated code must resolve to something other than itself, or the
    # picker would show a bare code where a name belongs.
    for code in COMMON_SUBTITLE_LANGS:
        assert language_display_name(code) != code, code


def test_mining_languages_lead_the_curated_list():
    assert COMMON_SUBTITLE_LANGS[:4] == ("ja", "ko", "zh-Hans", "zh-Hant")
