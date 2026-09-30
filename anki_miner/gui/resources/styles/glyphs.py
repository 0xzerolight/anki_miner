"""Theme-coloured control glyphs for the stylesheet (D17).

Qt draws a combo box's arrow and a check box's tick only from an ``image:`` in
the stylesheet, and a stylesheet ``url()`` must name a file: an SVG ``data:``
URL draws nothing. So each glyph is written once per colour into a folder that
lives for the process, under a name derived from the colour, and the compiled
stylesheet points at it. Every theme draws them in its own colours and no theme
gains a key: the chevron takes ``text`` (``text-disabled`` when disabled) and
the tick takes ``text-on-primary``, which is what sits on the accent fill.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from PyQt6.QtGui import QColor

_CHEVRON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
    '<path d="M2.5 4.5 L6 8 L9.5 4.5" fill="none" stroke="{color}" stroke-width="1.6" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
    '<path d="M3 7.2 L5.8 10 L11 4" fill="none" stroke="{color}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)

_glyph_dir: Path | None = None


def _directory() -> Path:
    """The process's glyph folder, created on first use and removed at exit."""
    global _glyph_dir
    if _glyph_dir is None or not _glyph_dir.is_dir():
        _glyph_dir = Path(tempfile.mkdtemp(prefix="anki-miner-glyphs-"))
        atexit.register(shutil.rmtree, _glyph_dir, True)
    return _glyph_dir


def _write(kind: str, template: str, color: str) -> str:
    """Write ``template`` in ``color`` once and return its path in stylesheet form."""
    hex_color = QColor(color).name()  # "#rrggbb"; an invalid value reads as black
    path = _directory() / f"{kind}-{hex_color[1:]}.svg"
    if not path.exists():
        path.write_text(template.format(color=hex_color), encoding="utf-8")
    return path.as_posix()


def glyph_variables(colors: Mapping[str, str]) -> dict[str, str]:
    """The ``${glyph-*}`` stylesheet variables for one theme's colours."""
    text = colors.get("text", "#000000")
    return {
        "glyph-chevron": _write("chevron", _CHEVRON_SVG, text),
        "glyph-chevron-disabled": _write("chevron", _CHEVRON_SVG, colors.get("text-disabled", text)),
        "glyph-check": _write("check", _CHECK_SVG, colors.get("text-on-primary", "#ffffff")),
        "glyph-check-disabled": _write("check", _CHECK_SVG, colors.get("text-on-primary", "#ffffff")),
    }
