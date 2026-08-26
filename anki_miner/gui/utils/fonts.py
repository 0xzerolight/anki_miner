"""Scale-aware font helpers for the GUI layer.

Decision D44-B: the interface face and the fixed-width face come from the
operating system, so the app looks like it belongs on the desktop it runs on.
Nothing here names ``Segoe UI``, ``Consolas`` or any other platform-specific
family as a *request* -- those exist on exactly one platform, and everywhere
else the font system silently substituted whatever it liked, which is why the
same screen had different metrics per OS and nobody chose that.

Decision D45-B: Japanese is content, not chrome. Japanese-bearing surfaces ask
for an explicitly Japanese face so kanji take Japanese rather than Chinese
glyph forms -- this app also ships Simplified and Traditional Chinese
interfaces, and those languages share characters with Japanese at different
preferred shapes. A bundled Noto Sans JP is registered only when the machine
has no Japanese face at all, so a bare Linux install still renders Japanese
instead of tofu.

Resolution happens once per process and is cached; :func:`reset_font_cache` is
the test seam.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication, QTextBlockFormat, QTextCursor, QTextDocument
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles._variables import FONT_SIZES, TYPOGRAPHY
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.utils.timing import timed_phase

logger = logging.getLogger(__name__)

#: Last-resort family names, used only when no ``QGuiApplication`` exists yet
#: (import-time helpers, headless unit tests). Qt understands both as generics.
_INTERFACE_FALLBACK = "sans-serif"
_MONOSPACE_FALLBACK = "monospace"

#: Installed Japanese faces in preference order. None of these is required to
#: exist; the first one the font database actually lists for Japanese wins.
#: Windows first, then macOS, then the usual Linux packages.
_JAPANESE_PREFERENCES: tuple[str, ...] = (
    "Yu Gothic UI",
    "Yu Gothic",
    "Meiryo",
    "Hiragino Sans",
    "Hiragino Kaku Gothic ProN",
    "Noto Sans CJK JP",
    "Noto Sans JP",
    "IPAPGothic",
    "TakaoPGothic",
)

#: Family name the bundled OTF registers itself under, and the file it lives in.
BUNDLED_JAPANESE_FAMILY = "Noto Sans JP"
BUNDLED_JAPANESE_FILE = "NotoSansJP-Regular.otf"

#: Dynamic property the Japanese QSS rules select on. Values are the two content
#: roles below; anything else leaves the widget on the interface font.
JAPANESE_PROPERTY = "japanese"
JAPANESE_BODY = "body"
JAPANESE_FEATURE = "feature"


@dataclass(frozen=True)
class ResolvedFamilies:
    """The three families this process draws with, as the font database named them."""

    interface: str
    monospace: str
    japanese: str


_families: ResolvedFamilies | None = None


def _has_gui() -> bool:
    return QGuiApplication.instance() is not None


def _japanese_families() -> list[str]:
    """Japanese-capable families, minus the vertical-writing ``@`` aliases.

    Qt lists every CJK family twice on some platforms: ``Meiryo`` and
    ``@Meiryo``, the second being the rotated vertical-writing variant. Picking
    one of those would render the interface sideways.

    Timed (Task 28, perf audit): this is Qt's font-DB population cost, paid
    once at boot by ``resolved_families()`` — the PA2 lazy-resolve rewrite is
    gated on whether this call is actually slow enough (>50ms) to matter.
    """
    with timed_phase("font-db-japanese-families", logger, level=logging.DEBUG):
        return [
            name for name in QFontDatabase.families(QFontDatabase.WritingSystem.Japanese) if not name.startswith("@")
        ]


def _resolve_japanese(interface_family: str) -> str:
    """Pick a Japanese family, registering the bundled face only as a last resort.

    Installed faces are queried *before* ``addApplicationFont`` is called, so a
    machine that already has a Japanese face never pays for the bundled one.
    """
    installed = _japanese_families()
    if interface_family in installed:
        return interface_family
    for candidate in _JAPANESE_PREFERENCES:
        if candidate in installed:
            return candidate
    if installed:
        # Deterministic, so two machines with the same fonts agree. Never the
        # first unordered database result.
        return sorted(installed)[0]
    return _register_bundled_japanese() or interface_family


def _register_bundled_japanese() -> str | None:
    """Load the bundled Noto Sans JP and return the family Qt registered it as.

    Registration failure is logged and returns ``None``: a missing or corrupt
    font asset must never abort startup, it only means Japanese falls back to
    whatever Qt would have chosen anyway.
    """
    path = get_resource_dir() / "fonts" / BUNDLED_JAPANESE_FILE
    if not path.is_file():
        logger.warning("Bundled Japanese font missing at %s; falling back to Qt", path)
        return None
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id == -1:
        logger.warning("Bundled Japanese font at %s could not be registered", path)
        return None
    registered = QFontDatabase.applicationFontFamilies(font_id)
    if not registered:
        logger.warning("Bundled Japanese font at %s registered no families", path)
        return None
    return registered[0]


def resolved_families() -> ResolvedFamilies:
    """Return (and cache) the interface, fixed-width and Japanese families.

    Requires a ``QGuiApplication``; without one the platform generics are
    returned and nothing is cached, so the first call under a real application
    still resolves properly.
    """
    global _families
    if _families is not None:
        return _families
    if not _has_gui():
        return ResolvedFamilies(_INTERFACE_FALLBACK, _MONOSPACE_FALLBACK, _INTERFACE_FALLBACK)

    interface = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family() or _INTERFACE_FALLBACK
    monospace = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family() or _MONOSPACE_FALLBACK
    _families = ResolvedFamilies(interface, monospace, _resolve_japanese(interface))
    return _families


def reset_font_cache() -> None:
    """Drop the resolved families and the stylesheet compiled from them."""
    global _families
    _families = None
    Theme._compiled_qss.clear()


def _quote(family: str) -> str:
    """Quote a family name for QSS, dropping characters that could end the rule."""
    return "'{}'".format(family.replace("'", "").replace('"', "").replace(";", "").replace("\\", ""))


def font_family_variables() -> dict[str, str]:
    """QSS substitution values for the three resolved families.

    ``common.qss`` names these instead of a hard-coded browser-style stack, so
    what the stylesheet asks for is exactly what the font service resolved.
    """
    families = resolved_families()
    return {
        "font-family-interface": _quote(families.interface),
        "font-family-mono": _quote(families.monospace),
        "font-family-japanese": _quote(families.japanese),
    }


def initialize_application_fonts(app: QGuiApplication) -> None:
    """Put the platform interface font on the application and resolve the rest.

    Called once, after ``QApplication`` construction and before the first
    widget, so every widget is built and measured against the font it will be
    drawn with. Any failure is logged and swallowed: an app that cannot resolve
    a font must still start.
    """
    try:
        app.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont))
        resolved_families()
    except Exception:
        logger.exception("Font initialization failed; continuing with Qt defaults")


def _scaled(pixel_size: int) -> int:
    return max(1, round(pixel_size * Theme.get_font_scale()))


def make_scaled_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Build a QFont whose pixel size is multiplied by the active global UI font scale.

    Args:
        pixel_size: Base font size in pixels (before scaling).
        weight: Font weight.

    Returns:
        QFont with pixel size scaled by the current Theme font scale (minimum 1px).
    """
    f = QFont()
    f.setPixelSize(_scaled(pixel_size))
    f.setWeight(weight)
    return f


def make_scaled_monospace_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Build the platform fixed-width font at a scaled pixel size.

    The style hint is set as well as the family so that if the platform's own
    fixed font is somehow unavailable, Qt still substitutes a monospaced face
    rather than a proportional one -- a log or a clock in a proportional face
    is unreadable in a different way than a missing font.
    """
    f = QFont(resolved_families().monospace)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setFixedPitch(True)
    f.setPixelSize(_scaled(pixel_size))
    f.setWeight(weight)
    return f


def make_japanese_font(pixel_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Build the resolved Japanese face at a scaled pixel size."""
    f = QFont(resolved_families().japanese)
    f.setPixelSize(_scaled(pixel_size))
    f.setWeight(weight)
    return f


def japanese_cell_font() -> QFont:
    """A Japanese face that carries **no** size, for table and list items.

    Item fonts resolve against the view's own font, so setting only the family
    gives a cell Japanese glyph shapes while leaving the row exactly as tall as
    the shared data-surface rule made it. Ruby and taller line boxes stay out of
    table rows on purpose: the density is what makes the curator scannable.
    """
    return QFont(resolved_families().japanese)


def apply_japanese_font(widget: QWidget, *, role: str = JAPANESE_BODY) -> None:
    """Mark a widget as Japanese content and give it the matching face and size.

    The dynamic property is what the ``*[japanese="…"]`` rules in ``common.qss``
    select on -- a stylesheet ``QWidget`` rule beats ``setFont`` for family and
    size, so the property is the only way a widget can escape the interface
    font. The Python font is set as well so the widget reports honest metrics
    *before* it is polished, which is when reserved heights are measured.

    Args:
        widget: The content surface.
        role: ``"body"`` for readings and sentences, ``"feature"`` for the one
            expression or subtitle line the eye goes to first.
    """
    size = FONT_SIZES.japanese_feature if role == JAPANESE_FEATURE else FONT_SIZES.japanese_body
    widget.setFont(make_japanese_font(size, QFont.Weight(widget.font().weight())))
    widget.setProperty(JAPANESE_PROPERTY, role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def japanese_line_spacing(role: str = JAPANESE_BODY) -> int:
    """Height of one line of Japanese content, leading included."""
    from PyQt6.QtGui import QFontMetrics

    size = FONT_SIZES.japanese_feature if role == JAPANESE_FEATURE else FONT_SIZES.japanese_body
    spacing = QFontMetrics(make_japanese_font(size)).lineSpacing()
    return max(1, round(spacing * TYPOGRAPHY.japanese_leading_percent / 100))


def apply_japanese_block_format(document: QTextDocument | None) -> None:
    """Give every block in a document the Japanese leading.

    Qt stylesheets have no ``line-height`` property -- the two declarations that
    pretended otherwise were deleted -- so leading is a block format, and it has
    to be reapplied after each ``setPlainText`` because that replaces the blocks.

    ``None`` is accepted because ``QTextEdit.document()`` is typed as optional;
    a document-less editor simply has no blocks to format.
    """
    if document is None:
        return
    # The mode argument is an int, not the enum: the scoped member's ``.value``
    # is what Qt wants here, and passing the member raises.
    proportional = cast(int, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
    block_format = QTextBlockFormat()
    block_format.setLineHeight(TYPOGRAPHY.japanese_leading_percent, proportional)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.mergeBlockFormat(block_format)
