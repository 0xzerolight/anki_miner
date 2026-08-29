"""Capability-driven visibility for language-specific settings surfaces.

Panels declare ``(widget, capability)`` pairs where they build the widgets and
apply them from ``load_from_config`` -- the panels take a parent only, so the
config-carrying load is the sole place an active language is in scope. The gate
is two-way and owns the whole visibility of a paired widget: it hides what the
active language cannot use and re-shows what it can, so a switch away and back
lands where it started instead of hiding the rows until the next restart.
Nothing else may drive a paired widget's visibility.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from anki_miner.gui.widgets.base.form_panel import FormPanel

__all__ = ["apply_language_gate", "field_row_widgets"]


def apply_language_gate(pairs: Iterable[tuple[QWidget, str]], capabilities: frozenset[str]) -> None:
    """Show every widget whose required capability the active language has, hide the rest."""
    for widget, capability in pairs:
        widget.setVisible(capability in capabilities)


def field_row_widgets(panel: FormPanel, widget: QWidget) -> tuple[QWidget, ...]:
    """The whole form row *widget* sits in: its label too, when it has one."""
    for label, field in getattr(panel, "_form_rows", ()):
        if field is widget:
            return (label, field) if label is not None else (field,)
    return (widget,)
