"""Base widget classes for consistent UI patterns."""

from .enhanced_dialog import EnhancedDialog
from .form_panel import FormPanel
from .sizing import (
    configure_expanding_container,
    make_label_fit_text,
    make_widget_expand_vertically,
    make_widget_shrink_to_fit,
)
from .status_badge import StatusBadge

__all__ = [
    "FormPanel",
    "StatusBadge",
    "EnhancedDialog",
    "make_label_fit_text",
    "make_widget_expand_vertically",
    "make_widget_shrink_to_fit",
    "configure_expanding_container",
]
