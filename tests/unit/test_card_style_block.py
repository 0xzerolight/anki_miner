"""Tests for the self-contained per-card glossary ``<style>`` block (Yomitan model)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.dictionary.card_style_block import _minify_css, build_card_style_block


class TestBuildCardStyleBlock:
    def test_wraps_in_single_style_element(self):
        out = build_card_style_block(custom_css="", dict_css="")
        assert out.startswith("<style>")
        assert out.endswith("</style>")
        assert out.count("<style>") == 1

    def test_includes_base_sheet(self):
        # The bundled base glossary.css is always embedded (its scope hook proves it).
        out = build_card_style_block(custom_css="", dict_css="")
        assert ".yomitan-glossary" in out

    def test_order_base_then_dict_then_custom(self):
        out = build_card_style_block(custom_css="CUSTOMMARK{}", dict_css="DICTMARK{}")
        assert out.index("yomitan-glossary") < out.index("DICTMARK") < out.index("CUSTOMMARK")

    def test_dict_and_custom_embedded_verbatim(self):
        out = build_card_style_block(custom_css=".c{color:blue}", dict_css=".d{color:green}")
        assert ".d{color:green}" in out
        assert ".c{color:blue}" in out

    def test_empty_dict_and_custom_still_non_empty(self):
        # Base is never empty, so a block is always produced.
        out = build_card_style_block(custom_css="  ", dict_css="  ")
        assert out.startswith("<style>")
        assert ".yomitan-glossary" in out


class TestMinifyCss:
    def test_strips_comments(self):
        assert "hello" not in _minify_css("/* hello */ a{b:c}")

    def test_collapses_whitespace_and_tightens_braces(self):
        out = _minify_css("a  {\n  color: red;\n}")
        assert " {" not in out
        assert "\n" not in out
        assert "color: red" in out  # value colon spacing preserved (safe for authored CSS)

    def test_smaller_than_source(self):
        raw = "/* a comment */\n.x {\n    color: red;\n}\n"
        assert len(_minify_css(raw)) < len(raw)


class TestNoNoteTypeCssWrite:
    """The whole point of the self-contained model: Anki Miner never writes note-type CSS."""

    def test_ankiservice_has_no_model_styling_methods(self):
        from anki_miner.services.anki_service import AnkiService

        assert not hasattr(AnkiService, "get_model_styling")
        assert not hasattr(AnkiService, "update_model_styling")

    def test_no_updatemodelstyling_anywhere_in_production_source(self):
        import anki_miner

        root = Path(anki_miner.__file__).parent
        offenders = [
            str(p.relative_to(root))
            for p in root.rglob("*.py")
            if "updateModelStyling" in p.read_text(encoding="utf-8")
            or "update_model_styling" in p.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"note-type CSS write path resurfaced in {offenders}"
