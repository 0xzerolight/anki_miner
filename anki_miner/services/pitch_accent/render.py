"""Self-contained pitch-accent graph / overline rendering for Anki cards.

Python port of Yomitan's ``PronunciationGenerator``
(``ext/js/display/pronunciation-generator.js``) plus the mora helpers it calls in
``ext/js/language/ja/japanese.js`` (upstream commit ``e2ed450``), GPL-3.0. The
ported symbols are:

=====================================  ============================
This module                            Yomitan source
=====================================  ============================
``is_mora_pitch_high``                 ``japanese.js`` ``isMoraPitchHigh``
``get_kana_morae``                     ``japanese.js`` ``getKanaMorae``
``get_kana_diacritic_info``            ``japanese.js`` ``getKanaDiacriticInfo`` (+ ``DIACRITIC_MAPPING``)
``render_pitch_graph_svg``             ``pronunciation-generator.js`` ``createPronunciationGraph`` (+ ``_addGraphDot``/``_addGraphDotDownstep``/``_addGraphTriangle``/``_createGraphCircle``)
``render_pitch_text``                  ``pronunciation-generator.js`` ``createPronunciationText``
=====================================  ============================

``getKanaDiacriticInfo`` returns only the base character here (the ``type``
field — ``dakuten``/``handakuten`` — is unused by the renderer, so it is
dropped).

Yomitan builds the pronunciation DOM then runs ``CssStyleApplier`` over it,
inlining every matching rule from ``ext/data/pronunciation-style.json`` so the
serialized markup renders identically inside an Anki field, which has no access
to the note-type stylesheet. This module emits that already-inlined markup
directly: the geometry is the mechanical port, and the per-element ``style="..."``
attributes are the hand-resolved ``pronunciation-style.json`` selectors (the
element structure is fixed, so the matching is resolved once by hand rather than
by a runtime CSS engine). Colors stay ``currentColor`` so the graph tracks the
card's text color in light and dark themes.

``getDownstepPositions`` is reused from
:mod:`anki_miner.services.pitch_accent_service` (already ported there for pitch
categorization) rather than re-ported; the small-kana set for mora splitting is
reused as :data:`~anki_miner.services.pitch_accent_service._COMBINING_KANA`.
"""

from __future__ import annotations

import html

from anki_miner.services.pitch_accent_service import _COMBINING_KANA, _HL_PATTERN_RE

# --- Mora helpers (ported from japanese.js) ---------------------------------


def is_mora_pitch_high(mora_index: int, pitch_value: int | str) -> bool:
    """Whether the mora at ``mora_index`` is high in the given accent.

    Ported from Yomitan ``isMoraPitchHigh`` (``japanese.js``, upstream commit
    ``e2ed450``). ``pitch_value`` is either an integer downstep position or an
    ``[HL]+`` mora string. For the string form, an out-of-range index reads low
    (Python raises on out-of-range indexing where JS yields ``undefined``, which
    is ``!== 'H'``); the graph deliberately probes one mora past the end for the
    trailing particle, so the bounds guard is load-bearing.
    """
    if isinstance(pitch_value, str):
        return 0 <= mora_index < len(pitch_value) and pitch_value[mora_index] == "H"
    if pitch_value == 0:
        return mora_index > 0
    if pitch_value == 1:
        return mora_index < 1
    return 0 < mora_index < pitch_value


def get_kana_morae(text: str) -> list[str]:
    """Split a kana reading into mora, merging small kana into the previous one.

    Ported from Yomitan ``getKanaMorae`` (``japanese.js``, upstream commit
    ``e2ed450``). Small combining kana (ゃゅょ etc.) attach to the preceding
    mora; small っ/ッ and the long-vowel ー stand alone. The small-kana set is
    the one shared with ``count_mora`` (``_COMBINING_KANA``), so
    ``len(get_kana_morae(r)) == count_mora(r)``.
    """
    morae: list[str] = []
    for c in text:
        if c in _COMBINING_KANA and morae:
            morae[-1] += c
        else:
            morae.append(c)
    return morae


def _iter_positions(pattern: str):
    """Yield each accent token in a pattern string as an ``int`` or ``[HL]+`` str.

    A pattern is one or more comma-separated tokens (e.g. ``"0"``, ``"0,2"``,
    ``"LHHH"``). An ``[HL]+`` token is yielded verbatim so the renderer draws the
    exact dictionary contour; a numeric token is yielded as an ``int``.
    Unparseable tokens are skipped.
    """
    for raw in pattern.split(","):
        token = raw.strip()
        if not token:
            continue
        if _HL_PATTERN_RE.match(token):
            yield token
        else:
            try:
                yield int(token)
            except ValueError:
                continue


# --- SVG pitch graph (ported from createPronunciationGraph) -----------------

_SVGNS = "http://www.w3.org/2000/svg"

# Inlined declaration blocks, hand-resolved from ext/data/pronunciation-style.json
# against the fixed element structure createPronunciationGraph produces.
_GRAPH_STYLE = "display:inline-block;vertical-align:middle;height:1.5em;"
_GRAPH_LINE_STYLE = "fill:none;stroke-width:5;stroke:currentColor;"
_GRAPH_LINE_TAIL_STYLE = "fill:none;stroke-width:5;stroke:currentColor;stroke-dasharray:5 5;"
_GRAPH_DOT_STYLE = "stroke-width:5;fill:currentColor;stroke:currentColor;"
_GRAPH_DOT_DOWNSTEP1_STYLE = "fill:none;stroke-width:5;stroke:currentColor;"
_GRAPH_DOT_DOWNSTEP2_STYLE = "fill:currentColor;"
_GRAPH_TRIANGLE_STYLE = "fill:none;stroke-width:5;stroke:currentColor;"


def _circle(class_name: str, style: str, x: int, y: int, radius: int) -> str:
    return f'<circle class="{class_name}" style="{style}" cx="{x}" cy="{y}" r="{radius}"/>'


def render_pitch_graph_svg(morae: list[str], position: int | str) -> str:
    """Render one OJAD-style pitch contour as a self-contained inline SVG string.

    Ported from Yomitan ``createPronunciationGraph``
    (``pronunciation-generator.js``, upstream commit ``e2ed450``). Each mora is a
    dot at y=25 (high) or y=75 (low); a high mora with a low successor is a
    downstep (a hollow ring around a filled centre). A solid line joins the mora
    dots and a dashed tail continues to a downward triangle marking the following
    particle's pitch. Geometry constants (50px mora pitch, r=15/5 dots, the
    ``M0 13 L15 -13 L-15 -13 Z`` triangle) are ported verbatim; styles are the
    inlined ``pronunciation-style.json`` rules, all ``currentColor``.
    """
    ii = len(morae)
    width = 50 * (ii + 1)
    header = (
        f'<svg xmlns="{_SVGNS}" class="pronunciation-graph" focusable="false" '
        f'viewBox="0 0 {width} 100" style="{_GRAPH_STYLE}">'
    )
    if ii <= 0:
        return header + "</svg>"

    dots: list[str] = []
    path_points: list[str] = []
    for i in range(ii):
        high = is_mora_pitch_high(i, position)
        high_next = is_mora_pitch_high(i + 1, position)
        x = i * 50 + 25
        y = 25 if high else 75
        if high and not high_next:
            dots.append(_circle("pronunciation-graph-dot-downstep1", _GRAPH_DOT_DOWNSTEP1_STYLE, x, y, 15))
            dots.append(_circle("pronunciation-graph-dot-downstep2", _GRAPH_DOT_DOWNSTEP2_STYLE, x, y, 5))
        else:
            dots.append(_circle("pronunciation-graph-dot", _GRAPH_DOT_STYLE, x, y, 15))
        path_points.append(f"{x} {y}")

    path1 = f'<path class="pronunciation-graph-line" style="{_GRAPH_LINE_STYLE}" ' f'd="M{" L".join(path_points)}"/>'

    # Tail: drop all but the last mora point, then extend to the particle marker.
    tail_points = path_points[ii - 1 :]
    high = is_mora_pitch_high(ii, position)
    x = ii * 50 + 25
    y = 25 if high else 75
    triangle = (
        f'<path class="pronunciation-graph-triangle" style="{_GRAPH_TRIANGLE_STYLE}" '
        f'd="M0 13 L15 -13 L-15 -13 Z" transform="translate({x},{y})"/>'
    )
    tail_points.append(f"{x} {y}")
    path2 = (
        f'<path class="pronunciation-graph-line-tail" style="{_GRAPH_LINE_TAIL_STYLE}" '
        f'd="M{" L".join(tail_points)}"/>'
    )

    # DOM order matches Yomitan: both paths, then the mora dots, then the triangle.
    return header + path1 + path2 + "".join(dots) + triangle + "</svg>"


def render_pitch_graph_field(pattern: str, reading: str) -> str:
    """Render the pitch-graph card field for a raw pattern string + kana reading.

    ``pattern`` may hold several comma-separated accents (e.g. ``"0,2"``); each
    parseable token yields one graph and they are concatenated (the graphs are
    inline-block, so they flow left to right / wrap). Returns ``""`` when the
    reading has no mora or the pattern has no parseable token, so the caller can
    leave the field untouched.
    """
    morae = get_kana_morae(reading)
    if not morae:
        return ""
    return "".join(render_pitch_graph_svg(morae, pos) for pos in _iter_positions(pattern))


# --- Kana diacritic table (ported from japanese.js DIACRITIC_MAPPING) --------

# Triplets of (base, dakuten, handakuten); ``-`` marks a base with no handakuten.
# Copied verbatim from japanese.js (upstream commit e2ed450).
_DIACRITIC_KANA = (
    "うゔ-かが-きぎ-くぐ-けげ-こご-さざ-しじ-すず-せぜ-そぞ-ただ-ちぢ-つづ-てで-とど-"
    "はばぱひびぴふぶぷへべぺほぼぽワヷ-ヰヸ-ウヴ-ヱヹ-ヲヺ-カガ-キギ-クグ-ケゲ-コゴ-"
    "サザ-シジ-スズ-セゼ-ソゾ-タダ-チヂ-ツヅ-テデ-トド-ハバパヒビピフブプヘベペホボポ"
)
_DIACRITIC_TO_BASE: dict[str, str] = {}
for _i in range(0, len(_DIACRITIC_KANA), 3):
    _base = _DIACRITIC_KANA[_i]
    _dakuten = _DIACRITIC_KANA[_i + 1]
    _handakuten = _DIACRITIC_KANA[_i + 2]
    _DIACRITIC_TO_BASE[_dakuten] = _base
    if _handakuten != "-":
        _DIACRITIC_TO_BASE[_handakuten] = _base
del _i, _base, _dakuten, _handakuten


def get_kana_diacritic_info(character: str) -> str | None:
    """Return the undiacritized base kana for a (han)dakuten kana, else ``None``.

    Ported from Yomitan ``getKanaDiacriticInfo`` (``japanese.js``, upstream commit
    ``e2ed450``). Yomitan returns ``{character, type}``; the pronunciation
    renderer only reads ``character``, so this returns the base char alone
    (が→か, ぱ→は, ゔ→う).
    """
    return _DIACRITIC_TO_BASE.get(character)


# --- Overline pitch text (ported from createPronunciationText) --------------

# Inlined declaration blocks, hand-resolved from ext/data/pronunciation-style.json
# against the fixed element structure createPronunciationText produces. The
# #c83c28 red is Yomitan's devoice/nasal indicator color; everything else is
# currentColor so the overline tracks the card's text color.
_TEXT_STYLE = "display:inline;"
_MORA_STYLE = "display:inline-block;position:relative;"
_MORA_HIGH_NEXT_LOW_STYLE = "display:inline-block;position:relative;padding-right:0.1em;margin-right:0.1em;"
_CHARACTER_STYLE = "display:inline;"
_CHARACTER_GROUP_STYLE = "display:inline-block;position:relative;"
# Low mora: the line span exists but stays invisible (no display:block).
_MORA_LINE_STYLE = "border-color:currentColor;"
# High mora: a solid overline across the mora.
_MORA_LINE_HIGH_STYLE = (
    "border-color:currentColor;display:block;user-select:none;pointer-events:none;"
    "position:absolute;top:0.1em;left:0;right:0;height:0;border-top-width:0.1em;border-top-style:solid;"
)
# High mora dropping to low next: the overline gains a right-hand downstep tick
# (the [data-pitch-next=low] rule overrides ``right`` and ``height``).
_MORA_LINE_HIGH_NEXT_LOW_STYLE = (
    "border-color:currentColor;display:block;user-select:none;pointer-events:none;"
    "position:absolute;top:0.1em;left:0;right:-0.1em;height:0.4em;"
    "border-top-width:0.1em;border-top-style:solid;border-right-width:0.1em;border-right-style:solid;"
)
_DEVOICE_INDICATOR_STYLE = (
    "display:block;position:absolute;left:50%;top:50%;width:1.125em;height:1.125em;"
    "border-radius:50%;box-sizing:border-box;z-index:1;transform:translate(-50%, -50%);"
    "border:1.5px dotted #c83c28;"
)
_NASAL_INDICATOR_STYLE = (
    "display:block;position:absolute;right:-0.125em;top:0.125em;width:0.375em;height:0.375em;"
    "border-radius:50%;box-sizing:border-box;z-index:1;border:1.5px solid #c83c28;"
)
# Combining handakuten sits under an opacity:0 span (hidden on cards; the red
# nasal indicator carries the visual cue), but is kept for faithful markup.
_NASAL_DIACRITIC_STYLE = "position:absolute;width:0;height:0;opacity:0;"

_NASAL_DIACRITIC_CHAR = "゚"  # combining handakuten


def _character_span(character: str, original_text: str | None = None) -> str:
    original = f' data-original-text="{html.escape(original_text)}"' if original_text is not None else ""
    return f'<span class="pronunciation-character" style="{_CHARACTER_STYLE}"{original}>{html.escape(character)}</span>'


def _render_mora(
    index: int,
    mora: str,
    high: bool,
    high_next: bool,
    nasal: bool,
    devoice: bool,
) -> str:
    """Render one mora span (overline + optional devoice/nasal indicators)."""
    pitch = "high" if high else "low"
    pitch_next = "high" if high_next else "low"
    data_attrs = f' data-position="{index}" data-pitch="{pitch}" data-pitch-next="{pitch_next}"'

    children = [_character_span(ch) for ch in mora]

    if devoice:
        data_attrs += ' data-devoice="true"'
        children.append(f'<span class="pronunciation-devoice-indicator" style="{_DEVOICE_INDICATOR_STYLE}"></span>')

    if nasal and children:
        data_attrs += ' data-nasal="true"'
        first_char = mora[0]
        base = get_kana_diacritic_info(first_char)
        if base is not None:
            data_attrs += f' data-original-text="{html.escape(mora)}"'
            first_span = _character_span(base, original_text=first_char)
        else:
            first_span = children[0]
        children[0] = (
            f'<span class="pronunciation-character-group" style="{_CHARACTER_GROUP_STYLE}">'
            f"{first_span}"
            f'<span class="pronunciation-nasal-diacritic" style="{_NASAL_DIACRITIC_STYLE}">{_NASAL_DIACRITIC_CHAR}</span>'
            f'<span class="pronunciation-nasal-indicator" style="{_NASAL_INDICATOR_STYLE}"></span>'
            f"</span>"
        )

    if high and not high_next:
        line_style = _MORA_LINE_HIGH_NEXT_LOW_STYLE
    elif high:
        line_style = _MORA_LINE_HIGH_STYLE
    else:
        line_style = _MORA_LINE_STYLE
    children.append(f'<span class="pronunciation-mora-line" style="{line_style}"></span>')

    mora_style = _MORA_HIGH_NEXT_LOW_STYLE if (high and not high_next) else _MORA_STYLE
    return f'<span class="pronunciation-mora" style="{mora_style}"{data_attrs}>{"".join(children)}</span>'


def render_pitch_text(
    morae: list[str],
    position: int | str,
    nasal: tuple[int, ...] = (),
    devoice: tuple[int, ...] = (),
) -> str:
    """Render the reading with a per-mora overline (downstep contour) as HTML.

    Ported from Yomitan ``createPronunciationText``
    (``pronunciation-generator.js``, upstream commit ``e2ed450``). Each mora is a
    relatively-positioned inline-block carrying an absolutely-positioned overline
    span that is drawn only for high mora (with a right-hand tick at a downstep).
    ``nasal``/``devoice`` are 1-based mora positions (as stored on
    :class:`~anki_miner.services.pitch_accent_service.PitchEntry`): a devoiced
    mora gets a dotted red ring, a nasal mora a small red ring plus the base-kana
    substitution (が→か゚). All markup is self-contained inline styles.
    """
    nasal_set = set(nasal)
    devoice_set = set(devoice)
    parts = [
        _render_mora(
            i,
            mora,
            is_mora_pitch_high(i, position),
            is_mora_pitch_high(i + 1, position),
            (i + 1) in nasal_set,
            (i + 1) in devoice_set,
        )
        for i, mora in enumerate(morae)
    ]
    return f'<span class="pronunciation-text" style="{_TEXT_STYLE}">{"".join(parts)}</span>'


def render_pitch_text_field(
    pattern: str,
    reading: str,
    nasal: tuple[int, ...] = (),
    devoice: tuple[int, ...] = (),
) -> str:
    """Render the pitch-overline card field for a raw pattern string + reading.

    Mirrors :func:`render_pitch_graph_field`: one overline run per parseable
    accent token, concatenated. Returns ``""`` when the reading has no mora or the
    pattern has no parseable token so the caller can leave the field untouched.
    """
    morae = get_kana_morae(reading)
    if not morae:
        return ""
    return "".join(render_pitch_text(morae, pos, nasal, devoice) for pos in _iter_positions(pattern))
