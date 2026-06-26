"""Tests for per-dictionary styles.css scoping (Issue #87)."""

from __future__ import annotations

import time

from anki_miner.services.dictionary.dict_css_scope import scope_dict_css

TITLE = "Jitendex.org [2026-06-06]"
SCOPE = '.yomitan-glossary [data-dictionary="Jitendex.org [2026-06-06]"]'


def test_empty_input_returns_empty():
    assert scope_dict_css("", TITLE) == ""
    assert scope_dict_css("   \n  ", TITLE) == ""


def test_simple_rule_is_scoped():
    out = scope_dict_css("span[data-sc-class='tag'] { color: red; }", TITLE)
    assert out == f"{SCOPE} span[data-sc-class='tag'] {{color: red;}}"


def test_comma_selector_list_each_prefixed():
    out = scope_dict_css("a, b c { margin: 0 }", TITLE)
    assert out == f"{SCOPE} a, {SCOPE} b c {{margin: 0}}"


def test_comma_inside_parens_not_split():
    out = scope_dict_css(":is(a, b) span { color: red }", TITLE)
    # The :is(...) comma must not split into two scoped selectors.
    assert out == f"{SCOPE} :is(a, b) span {{color: red}}"


def test_title_quotes_and_backslash_escaped():
    out = scope_dict_css("p { color: red }", 'Weird "Dict" \\ name')
    assert out.startswith('.yomitan-glossary [data-dictionary="Weird \\"Dict\\" \\\\ name"] p')


def test_title_angle_brackets_stripped():
    # A hostile title cannot break out of the <style> element via the scope.
    out = scope_dict_css("p { color: red }", "Evil</style><script>")
    assert "<" not in out and ">" not in out


def test_media_query_recursed_and_preserved():
    css = "@media (max-width: 600px) { span { color: red } }"
    out = scope_dict_css(css, TITLE)
    assert out.startswith("@media (max-width: 600px) {")
    assert f"{SCOPE} span {{color: red}}" in out
    assert out.rstrip().endswith("}")


def test_supports_recursed():
    css = "@supports (color: red) { a { color: red } }"
    out = scope_dict_css(css, TITLE)
    assert "@supports (color: red) {" in out
    assert f"{SCOPE} a {{color: red}}" in out


def test_nested_body_preserved_verbatim():
    # CSS nesting (& or bare nested selector) is kept inside the body so it
    # resolves against the scoped parent — we do not flatten it.
    css = "div { color: red; & span { color: blue } }"
    out = scope_dict_css(css, TITLE)
    assert out == f"{SCOPE} div {{color: red; & span {{ color: blue }}}}"


def test_url_rule_dropped():
    css = "a { color: red } b { background: url(http://evil/x.png) }"
    out = scope_dict_css(css, TITLE)
    assert f"{SCOPE} a {{color: red}}" in out
    assert "url(" not in out
    assert "evil" not in out


def test_import_at_rule_dropped():
    css = "@import url('http://evil/x.css'); a { color: red }"
    out = scope_dict_css(css, TITLE)
    assert "@import" not in out
    assert "evil" not in out
    assert f"{SCOPE} a {{color: red}}" in out


def test_font_face_dropped():
    css = "@font-face { font-family: x; } a { color: red }"
    out = scope_dict_css(css, TITLE)
    assert "@font-face" not in out
    assert f"{SCOPE} a {{color: red}}" in out


def test_style_close_tag_breakout_dropped():
    css = "a { color: red } b::after { content: '</style><script>alert(1)</script>' }"
    out = scope_dict_css(css, TITLE)
    assert "</style>" not in out
    assert "<script" not in out
    assert f"{SCOPE} a {{color: red}}" in out


def test_expression_dropped():
    css = "a { width: expression(alert(1)) }"
    assert scope_dict_css(css, TITLE) == ""


def test_oversized_input_skipped():
    big = "a { color: red }\n" * 50000
    assert scope_dict_css(big, TITLE) == ""


def test_image_funcs_dropped_with_and_without_vendor_prefix():
    # The forbidden-pattern rewrite must still drop remote-fetch image funcs,
    # bare and vendor-prefixed.
    for fn in ("image-set", "-webkit-image-set", "image-rect", "cross-fade", "-moz-element"):
        css = f"a {{ color: red }} b {{ background: {fn}(x) }}"
        out = scope_dict_css(css, TITLE)
        assert f"{SCOPE} a {{color: red}}" in out
        assert fn not in out


def test_pathological_under_cap_input_is_bounded_time():
    # Regression for the ReDoS in _FORBIDDEN_RE: a long single-token prelude
    # under the 512 KB cap must not trigger O(n^2) backtracking. Pre-fix this
    # ran for ~100 s; the O(n) form returns in milliseconds.
    css = ("x" * 100_000) + " { a: 1 }"
    start = time.perf_counter()
    scope_dict_css(css, TITLE)
    assert time.perf_counter() - start < 1.0


def test_comment_between_rules_ignored():
    css = "/* header */ a { color: red } /* footer */"
    out = scope_dict_css(css, TITLE)
    assert out == f"{SCOPE} a {{color: red}}"


def test_real_jitendex_shaped_rules():
    css = (
        'span[data-sc-class="tag"] { border-radius: 0.3em; font-weight: bold; }\n'
        'div[data-sc-content="example-sentence"] { border-color: #333; }\n'
        'div[data-sc-content="forms"] { & table { border-collapse: collapse; } }\n'
    )
    out = scope_dict_css(css, TITLE)
    assert f'{SCOPE} span[data-sc-class="tag"]' in out
    assert f'{SCOPE} div[data-sc-content="example-sentence"]' in out
    assert f'{SCOPE} div[data-sc-content="forms"]' in out
    assert "& table" in out  # nested body kept verbatim
