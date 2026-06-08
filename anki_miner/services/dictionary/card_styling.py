"""Card-styling composition (Issue #44).

Pure string helpers that assemble the CSS anki_miner pushes into a note type's
styling via AnkiConnect, wrapped in a *managed marker block* so re-applying only
ever touches our block — never the user's hand-written card CSS.

The combined CSS is ``[default stylesheet] + [user custom CSS]`` surrounded by
``BEGIN_MARKER`` / ``END_MARKER`` comments. ``apply_managed_block`` inserts or
replaces that block idempotently; ``strip_managed_block`` removes it for a full
revert. No Qt, no HTTP — every function here is a pure ``str`` transform and is
unit-tested in isolation (see ``tests/unit/test_card_styling.py``).
"""

from __future__ import annotations

import re

from anki_miner.services.dictionary.card_style_presets import DEFAULT_PRESET_ID, load_preset_css

# Marker comments delimiting the block we own inside the note type's CSS. Kept
# deliberately distinctive so a regex split is unambiguous; users are told (in
# the marker text and the GUI helper) not to paste these into their custom CSS.
BEGIN_MARKER = "/* === ANKI MINER DICT STYLES (managed — do not edit) === */"
END_MARKER = "/* === END ANKI MINER DICT STYLES === */"

# A complete managed block, markers inclusive, plus any leading blank lines so a
# strip leaves no dangling gap. DOTALL lets ``.`` span newlines; non-greedy so
# two adjacent blocks don't collapse into one match.
_BLOCK_RE = re.compile(
    r"\n*" + re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER),
    re.DOTALL,
)


def load_default_card_css() -> str:
    """Return the bundled default stylesheet text (the ``"default"`` preset)."""
    return load_preset_css(DEFAULT_PRESET_ID)


def build_managed_block(*, preset: str, custom_css: str) -> str:
    """Assemble the managed block.

    The block is the selected preset's CSS (per ``preset``) followed by the
    user's ``custom_css``. Both markers are always emitted — even when both
    inputs are empty — so a later :func:`apply_managed_block` or
    :func:`strip_managed_block` can locate the block unambiguously.
    """
    sections: list[str] = []
    preset_css = load_preset_css(preset).strip()
    if preset_css:
        sections.append(preset_css)
    custom = custom_css.strip()
    if custom:
        sections.append(custom)
    body = "\n\n".join(sections)
    if body:
        return f"{BEGIN_MARKER}\n{body}\n{END_MARKER}"
    return f"{BEGIN_MARKER}\n{END_MARKER}"


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
