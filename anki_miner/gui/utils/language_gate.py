"""Capability-driven visibility for language-specific settings surfaces.

Panels declare ``(widget, capability)`` pairs where they build the widgets and
apply them from ``load_from_config`` -- the panels take a parent only, so the
config-carrying load is the sole place an active language is in scope. A gate
never re-shows: it hides what the active language cannot use and leaves
everything else exactly as the panel built it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from anki_miner.gui.widgets.base.form_panel import FormPanel

__all__ = ["apply_language_gate", "field_row_widgets"]


def apply_language_gate(pairs: Iterable[tuple[QWidget, str]], capabilities: frozenset[str]) -> None:
    """Hide every widget whose required capability the active language lacks."""
    for widget, capability in pairs:
        if capability not in capabilities:
            widget.setVisible(False)


def field_row_widgets(panel: FormPanel, widget: QWidget) -> tuple[QWidget, ...]:
    """The whole form row *widget* sits in: its label too, when it has one."""
    for label, field in getattr(panel, "_form_rows", ()):
        if field is widget:
            return (label, field) if label is not None else (field,)
    return (widget,)
