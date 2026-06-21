"""Card-styling composition (Issue #44).

Pure string helpers that assemble the CSS anki_miner pushes into a note type's
styling via AnkiConnect, wrapped in a *managed marker block* so re-applying only
ever touches our block — never the user's hand-written card CSS.

The combined CSS is ``[preset stylesheet] + [user custom CSS]`` surrounded by a
BEGIN / END marker pair; the BEGIN marker records the preset id so the applied
look is recoverable via :func:`detect_applied_preset`. ``apply_managed_block``
inserts or replaces that block idempotently; ``strip_managed_block`` removes it
for a full revert. No Qt, no HTTP — every function here is a pure ``str``
transform and is unit-tested in isolation (see ``tests/unit/test_card_styling.py``).

Preset CSS itself is guarded by miner-only ``ol[data-count]`` markup so the
managed block never restyles Yomitan-exported glossary HTML sharing the note
type — see ``card_style_presets`` and ``TestPresetYomitanLeak``.
"""

from __future__ import annotations

import re

from anki_miner.services.dictionary.card_style_presets import DEFAULT_PRESET_ID, load_preset_css

# Marker comments delimiting the block we own inside the note type's CSS. Kept
# deliberately distinctive so a regex split is unambiguous; users are told (in
# the marker text and the GUI helper) not to paste these into their custom CSS.
#
# The BEGIN marker records which preset filled the block (``preset=<id>``) so the
# Settings sync can read the note type back and report what's actually live —
# the cure for the old "combo says one thing, Anki has another" confusion. Legacy
# blocks written before this change carry no ``preset=`` segment; the regex below
# still matches them and detection falls back to the saved preference.
END_MARKER = "/* === END ANKI MINER DICT STYLES === */"

# Fixed prefix shared by every BEGIN marker (current and legacy). Stable public
# anchor for callers/tests that only need to check "is this our block" without
# parsing the embedded preset id.
BEGIN_MARKER_PREFIX = "/* === ANKI MINER DICT STYLES (managed"

# Matches the BEGIN marker line for both the current (``preset=<id>``) and the
# legacy (``managed — do not edit``) forms: anything up to the closing ``*/`` on
# one line after the fixed prefix.
_BEGIN_RE = r"/\* === ANKI MINER DICT STYLES \(managed[^\n]*\*/"

# A complete managed block, markers inclusive, plus any leading blank lines so a
# strip leaves no dangling gap. DOTALL lets ``.`` span newlines; non-greedy so
# two adjacent blocks don't collapse into one match.
_BLOCK_RE = re.compile(
    r"\n*" + _BEGIN_RE + r".*?" + re.escape(END_MARKER),
    re.DOTALL,
)

# Captures the recorded preset id from a BEGIN marker line, if present.
_PRESET_RE = re.compile(r"preset=([A-Za-z0-9_-]+)")


def _begin_marker(preset: str) -> str:
    """Return the BEGIN marker line recording ``preset`` as the block's source."""
    return f"/* === ANKI MINER DICT STYLES (managed; preset={preset}; do not edit) === */"


def load_default_card_css() -> str:
    """Return the bundled default stylesheet text (the ``"default"`` preset)."""
    return load_preset_css(DEFAULT_PRESET_ID)


def build_managed_block(*, preset: str, custom_css: str) -> str:
    """Assemble the managed block.

    The block is the selected preset's CSS (per ``preset``) followed by the
    user's ``custom_css``. The BEGIN marker records ``preset`` so the block's
    source is recoverable via :func:`detect_applied_preset`. Both markers are
    always emitted — even when both inputs are empty — so a later
    :func:`apply_managed_block` or :func:`strip_managed_block` can locate the
    block unambiguously.
    """
    begin = _begin_marker(preset)
    sections: list[str] = []
    preset_css = load_preset_css(preset).strip()
    if preset_css:
        sections.append(preset_css)
    custom = custom_css.strip()
    if custom:
        sections.append(custom)
    body = "\n\n".join(sections)
    if body:
        return f"{begin}\n{body}\n{END_MARKER}"
    return f"{begin}\n{END_MARKER}"


def detect_applied_preset(existing_css: str) -> str | None:
    """Return the preset id recorded in ``existing_css``'s managed block.

    - ``None`` when no complete managed block is present (Anki Miner is not
      managing this note type's styling — i.e. "Off").
    - The recorded preset id (e.g. ``"minimal"``) for blocks written by the
      current version.
    - ``""`` for a legacy block that carries no ``preset=`` segment — present,
      but its source is unknown, so the caller should fall back to the saved
      preference.
    """
    block = _BLOCK_RE.search(existing_css)
    if block is None:
        return None
    # Search only the BEGIN marker line, not the whole block: user custom CSS in
    # the body can contain the literal ``preset=`` (e.g. ``content:"preset=x"``)
    # and would otherwise be misdetected as the recorded preset id.
    begin = re.search(_BEGIN_RE, block.group(0))
    match = _PRESET_RE.search(begin.group(0)) if begin else None
    return match.group(1) if match else ""


def apply_managed_block(existing_css: str, block: str) -> str:
    """Insert or replace the managed block within ``existing_css``.

    Idempotent: re-running with the same ``block`` yields identical output. Only
    the span between (and including) the markers is replaced; the user's own card
    CSS before and after is preserved in place. Any *additional* managed blocks
    left over from a corrupted prior state are removed so the result always holds
    exactly one block. If no *complete* block is present (e.g. a half-deleted
    marker), the block is appended fresh rather than corrupting existing CSS.
    """
    existing = existing_css.rstrip()
    matches = list(_BLOCK_RE.finditer(existing))
    if not matches:
        return f"{existing}\n\n{block}\n" if existing else f"{block}\n"

    # Replace the first block in place; build the result by slicing so the freshly
    # inserted block is never re-matched. Strip any later duplicate blocks.
    first = matches[0]
    head = existing[: first.start()].rstrip()
    tail = _BLOCK_RE.sub("", existing[first.end() :]).strip()
    prefix = f"{head}\n\n" if head else ""
    if tail:
        return f"{prefix}{block}\n\n{tail}\n"
    return f"{prefix}{block}\n"


def strip_managed_block(existing_css: str) -> str:
    """Remove the managed block (markers inclusive) for a full revert.

    Leaves surrounding user CSS intact, collapsing the blank-line gap left
    behind. A true no-op (returns the input verbatim) when no block is present,
    and returns ``""`` when the block was the entire content.
    """
    if not _BLOCK_RE.search(existing_css):
        return existing_css
    stripped = _BLOCK_RE.sub("", existing_css)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return f"{stripped}\n" if stripped else ""
