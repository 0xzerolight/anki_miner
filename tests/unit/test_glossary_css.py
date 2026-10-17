"""Tests for the house glossary stylesheet (resources/glossary.css).

Seven structural rules plus the ``images`` and ``tables`` groups. Every rule is
guarded by miner-only ``ol[data-count]`` markup so it never restyles
Yomitan-exported glossary HTML sharing a note type; the sheet carries no
``!important``, no nesting and no theme selectors, and the minified core rides
every styled field, so it stays under 1 KiB.
"""

import re
from pathlib import Path

from anki_miner.services.dictionary.card_style_block import (
    OWNED_STYLE_MARKER,
    _minify_css,
    base_css,
    split_group_regions,
)
from anki_miner.services.dictionary.card_style_presets import load_glossary_css
from anki_miner.services.dictionary.sense_presentation import present_sense
from tests.unit.test_sense_presentation import MULTI

_NOTE_TYPES = Path(__file__).resolve().parents[1] / "fixtures" / "note_types"


def _no_comments(css: str) -> str:
    """Strip CSS comments so prose in the header can't trip token checks."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _iter_rules(css: str):
    """Yield (selector_group, declarations) for each top-level rule."""
    for block in _no_comments(css).split("}"):
        if "{" not in block:
            continue
        selector_group, declarations = block.split("{", 1)
        selector_group = selector_group.strip()
        if selector_group:
            yield selector_group, declarations


def _selectors(css: str):
    for selector_group, declarations in _iter_rules(css):
        for selector in selector_group.split(","):
            yield selector.strip(), declarations


class TestSheetDiscipline:
    def test_every_selector_guarded_by_miner_only_markup(self):
        # `data-count` on the outer <ol> is the one marker only anki_miner emits;
        # without it a rule would stack onto Yomitan-exported glossary HTML. The bare
        # wrapper is the single exception, and it may set custom properties only.
        seen = 0
        for selector, declarations in _selectors(load_glossary_css()):
            seen += 1
            assert selector.startswith(".yomitan-glossary"), selector
            if "ol[data-count]" in selector:
                continue
            assert selector == ".yomitan-glossary", f"{selector!r} lacks the ol[data-count] guard"
            for decl in filter(None, (d.strip() for d in declarations.split(";"))):
                assert decl.startswith("--"), f"the bare wrapper rule may only set custom properties: {decl!r}"
        assert seen >= 7

    def test_no_important_no_nesting_no_theme_selectors(self):
        css = _no_comments(load_glossary_css())
        assert "!important" not in css
        assert ".nightMode" not in css
        assert "prefers-color-scheme" not in css
        depth = 0
        for ch in css:
            if ch == "{":
                depth += 1
                assert depth == 1, "nested block (CSS nesting or an at-rule wrapper)"
            elif ch == "}":
                depth -= 1
                assert depth == 0, "unbalanced braces"
        assert depth == 0

    def test_every_custom_property_resolves(self):
        # `--image` is stamped inline by the renderer on each recolor layer; any
        # other var() must be defined in the sheet or carry its own fallback.
        css = _no_comments(load_glossary_css())
        uses = re.findall(r"var\(\s*(--[-\w]+)\s*([,)])", css)
        assert uses, "the images group's var(--image) is gone"
        for name, follow in uses:
            if name == "--image" or follow == ",":
                continue
            assert re.search(rf"{re.escape(name)}\s*:", css), f"var({name}) is neither defined nor given a fallback"

    def test_no_rule_subject_is_a_bare_type_selector(self):
        # A bare `table`/`th`/`details` subject would match markup we did not emit and
        # out-rank the note type on its own element; the renderer stamps a
        # `gloss-sc-<tag>` class on every structured-content element instead.
        for selector, _ in _selectors(load_glossary_css()):
            subject = selector.split()[-1]
            assert any(ch in subject for ch in ".#["), f"{selector!r} takes a bare type selector as its subject"

    def test_group_markers_sit_between_complete_rules(self):
        # base_css concatenates minified regions with no separator, which is only
        # sound when every region boundary falls right after a `}`.
        regions = split_group_regions(load_glossary_css())
        assert [group for group, _ in regions] == ["core", "images", "core", "tables", "core"]
        for group, raw in regions:
            minified = _minify_css(raw)
            assert not minified or minified.endswith("}"), f"{group!r} region ends mid-rule"
        assert base_css('<img class="gloss-image"><table>') == _minify_css(load_glossary_css())


class TestBaseCss:
    def test_core_fits_one_kib(self):
        assert len(base_css("").encode()) <= 1024

    def test_core_keeps_the_restyler_contract(self):
        # card_restyler finds its own block by the leading marker and the
        # ol[data-count] token, and splits the dict CSS tail at the first newline.
        core = base_css("")
        assert core.startswith(OWNED_STYLE_MARKER)
        assert "ol[data-count]" in core
        assert "\n" not in core

    def test_core_drops_the_outer_list_chrome(self):
        # Kiku numbers the outer <ol> when the wrapper loses `list-style: none`.
        assert ".yomitan-glossary > ol[data-count]{margin: 0;padding: 0;list-style: none;" in base_css("")

    def test_core_draws_gloss_bullets_as_circles(self):
        assert (
            ".yomitan-glossary ol[data-count] ol.gloss-glosses{margin: 0.1em 0;padding-left: 1.4em;"
            "list-style-type: circle;}" in base_css("")
        )

    def test_images_region_only_when_the_field_has_an_image(self):
        assert ".gloss-image" not in base_css("")
        assert ".gloss-image" in base_css('<img class="gloss-image" src="p.svg">')

    def test_tables_region_on_table_details_or_summary(self):
        assert ".gloss-sc-table" not in base_css("")
        for markup in ("<table>", "<details>", "<summary>"):
            css = base_css(markup)
            assert ".gloss-sc-details" in css, markup
            assert ".gloss-sc-table" in css, markup

    def test_images_rules_keep_the_monochrome_recolor(self):
        css = base_css('<img class="gloss-image">')
        assert "[data-appearance=monochrome]" in css
        assert "mask-image: var(--image)" in css
        assert "background-color: currentColor" in css
        assert "image-rendering: pixelated" in css

    def test_tables_rules_use_literal_colours(self):
        # The --am-* tunables died with the old core; a note type's --amn-accent
        # still wins where it is set. _minify_css tightens the commas.
        css = base_css("<table>")
        assert "border: 1px solid rgba(128,128,128,0.4)" in css
        assert "color: var(--amn-accent,#4f7cff)" in css
        assert "--am-" not in css


class TestLapisListDodge:
    """Lapis (and Anki Miner Note, which inherits it) has three list rules that the
    gloss list must escape: "Format Definitions" flattens `ul[data-sc-content="glossary"]`
    and any `ul:not([data-sc-content])` under a JMdict entry into a " | " run, and its
    `--jitendex-format: "minimal"` mode hides every `[data-sc-content]` other than
    `glossary`. An `<ol class="gloss-glosses">` with no `data-sc-content` matches none.
    """

    def test_lapis_still_ships_the_three_rules(self):
        css = (_NOTE_TYPES / "lapis.css").read_text(encoding="utf-8")
        assert 'ul[data-sc-content="glossary"]' in css
        assert "ul:not([data-sc-content])" in css
        assert (
            '[data-jitendex-format~="minimal"] .definition [data-sc-content]:not([data-sc-content="glossary"])' in css
        )

    def test_gloss_list_is_an_ol_without_data_sc_content(self):
        body = present_sense(MULTI, styled=False)
        assert body.startswith('<ol class="gloss-glosses"')
        assert "data-sc-content" not in body
