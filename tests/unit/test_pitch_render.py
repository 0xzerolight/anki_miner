"""Unit tests for self-contained pitch graph / overline rendering.

The Yomitan golden in ``anki-note-builder-test-results.json`` carries no
pronunciation SVG, and an independent Python port will not match Yomitan's
attribute/style ordering byte-for-byte anyway. So the geometry math
(``is_mora_pitch_high`` / mora splitting / coordinate mapping) is asserted
directly against values derivable from the ported constants, and the serialized
output is pinned with hand-authored Python snapshots (Yomitan is a geometry /
visual reference, not a string-equality target).
"""

from __future__ import annotations

from anki_miner.services.pitch_accent.render import (
    get_kana_morae,
    is_mora_pitch_high,
    render_pitch_graph_field,
    render_pitch_graph_svg,
)


class TestIsMoraPitchHigh:
    def test_heiban_low_first_then_high(self):
        # position 0: mora 0 low, every later mora high (moraIndex > 0)
        assert is_mora_pitch_high(0, 0) is False
        assert is_mora_pitch_high(1, 0) is True
        assert is_mora_pitch_high(5, 0) is True

    def test_atamadaka_high_first_then_low(self):
        # position 1: only mora 0 high (moraIndex < 1)
        assert is_mora_pitch_high(0, 1) is True
        assert is_mora_pitch_high(1, 1) is False
        assert is_mora_pitch_high(2, 1) is False

    def test_nakadaka_high_between_first_and_drop(self):
        # position 3: high for 0 < moraIndex < 3
        assert is_mora_pitch_high(0, 3) is False
        assert is_mora_pitch_high(1, 3) is True
        assert is_mora_pitch_high(2, 3) is True
        assert is_mora_pitch_high(3, 3) is False

    def test_hl_string_indexes_directly(self):
        assert is_mora_pitch_high(0, "LHHL") is False
        assert is_mora_pitch_high(1, "LHHL") is True
        assert is_mora_pitch_high(3, "LHHL") is False

    def test_hl_string_out_of_bounds_is_low(self):
        # JS `pitchValue[i] === 'H'` is `undefined === 'H'` past the end → false.
        assert is_mora_pitch_high(4, "LHHL") is False
        assert is_mora_pitch_high(10, "LH") is False


class TestGetKanaMorae:
    def test_simple_two_mora(self):
        assert get_kana_morae("はし") == ["は", "し"]

    def test_small_kana_merges_with_previous(self):
        assert get_kana_morae("きょう") == ["きょ", "う"]

    def test_small_tsu_is_its_own_mora(self):
        assert get_kana_morae("がっこう") == ["が", "っ", "こ", "う"]

    def test_katakana_small_merges(self):
        assert get_kana_morae("チャンス") == ["チャ", "ン", "ス"]

    def test_length_matches_count_mora(self):
        from anki_miner.services.pitch_accent_service import count_mora

        for reading in ("きょう", "がっこう", "チャンス", "せんせい", "とうきょう"):
            assert len(get_kana_morae(reading)) == count_mora(reading)


# Hand-authored serialized snapshot: はし [heiban] (position 0), morae は/し.
# Derived from the ported geometry: mora 0 low (y=75) → high (y=25); no downstep;
# dashed tail runs to a translate()'d particle triangle at x = 2*50+25 = 125.
_HASHI_HEIBAN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" class="pronunciation-graph" '
    'focusable="false" viewBox="0 0 150 100" '
    'style="display:inline-block;vertical-align:middle;height:1.5em;">'
    '<path class="pronunciation-graph-line" '
    'style="fill:none;stroke-width:5;stroke:currentColor;" d="M25 75 L75 25"/>'
    '<path class="pronunciation-graph-line-tail" '
    'style="fill:none;stroke-width:5;stroke:currentColor;stroke-dasharray:5 5;" '
    'd="M75 25 L125 25"/>'
    '<circle class="pronunciation-graph-dot" '
    'style="stroke-width:5;fill:currentColor;stroke:currentColor;" '
    'cx="25" cy="75" r="15"/>'
    '<circle class="pronunciation-graph-dot" '
    'style="stroke-width:5;fill:currentColor;stroke:currentColor;" '
    'cx="75" cy="25" r="15"/>'
    '<path class="pronunciation-graph-triangle" '
    'style="fill:none;stroke-width:5;stroke:currentColor;" '
    'd="M0 13 L15 -13 L-15 -13 Z" transform="translate(125,25)"/>'
    "</svg>"
)


class TestRenderPitchGraphSvg:
    def test_heiban_exact_snapshot(self):
        assert render_pitch_graph_svg(["は", "し"], 0) == _HASHI_HEIBAN_SVG

    def test_empty_morae_returns_bare_svg(self):
        svg = render_pitch_graph_svg([], 0)
        # viewBox width = 50 * (0 + 1); no paths, dots, or triangle.
        assert svg == (
            '<svg xmlns="http://www.w3.org/2000/svg" class="pronunciation-graph" '
            'focusable="false" viewBox="0 0 50 100" '
            'style="display:inline-block;vertical-align:middle;height:1.5em;"></svg>'
        )

    def test_viewbox_scales_with_mora_count(self):
        # width = 50 * (mora_count + 1)
        assert 'viewBox="0 0 200 100"' in render_pitch_graph_svg(["せ", "ん", "せ"], 0)

    def test_atamadaka_marks_a_downstep_on_first_mora(self):
        # position 1: mora 0 high, mora 1 low → downstep double circle at (25,25),
        # then a low dot at (75,75); dashed tail to the triangle at (125,75).
        svg = render_pitch_graph_svg(["は", "し"], 1)
        assert 'class="pronunciation-graph-dot-downstep1"' in svg
        assert 'class="pronunciation-graph-dot-downstep2"' in svg
        assert (
            '<circle class="pronunciation-graph-dot-downstep1" '
            'style="fill:none;stroke-width:5;stroke:currentColor;" '
            'cx="25" cy="25" r="15"/>' in svg
        )
        assert (
            '<circle class="pronunciation-graph-dot-downstep2" '
            'style="fill:currentColor;" cx="25" cy="25" r="5"/>' in svg
        )
        assert 'd="M25 25 L75 75"' in svg  # main line
        assert 'transform="translate(125,75)"' in svg  # tail triangle at low y

    def test_nakadaka_downstep_at_internal_mora(self):
        # とうきょう, position 2 → morae と/う/きょ/う (4 mora). Downstep after mora 1.
        morae = get_kana_morae("とうきょう")
        assert morae == ["と", "う", "きょ", "う"]
        svg = render_pitch_graph_svg(morae, 2)
        # mora 1 (x=75) is high with a low successor → downstep there.
        assert (
            '<circle class="pronunciation-graph-dot-downstep1" '
            'style="fill:none;stroke-width:5;stroke:currentColor;" '
            'cx="75" cy="25" r="15"/>' in svg
        )
        assert 'viewBox="0 0 250 100"' in svg  # 50 * (4 + 1)

    def test_uses_currentcolor_only_no_hardcoded_fill(self):
        svg = render_pitch_graph_svg(["は", "し"], 0)
        assert "currentColor" in svg
        assert "#" not in svg  # no hex colors baked into the graph


class TestRenderPitchGraphField:
    def test_single_position(self):
        assert render_pitch_graph_field("0", "はし") == _HASHI_HEIBAN_SVG

    def test_multi_position_concatenates_one_graph_per_token(self):
        html = render_pitch_graph_field("0,1", "はし")
        assert html == render_pitch_graph_svg(["は", "し"], 0) + render_pitch_graph_svg(["は", "し"], 1)

    def test_hl_string_position(self):
        # An [HL]+ pattern is passed through verbatim (not reduced to an int).
        html = render_pitch_graph_field("LHH", "はし")
        assert html == render_pitch_graph_svg(["は", "し"], "LHH")

    def test_empty_reading_returns_empty(self):
        assert render_pitch_graph_field("0", "") == ""

    def test_unparseable_pattern_returns_empty(self):
        assert render_pitch_graph_field("", "はし") == ""
        assert render_pitch_graph_field("abc", "はし") == ""
