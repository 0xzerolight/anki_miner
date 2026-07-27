"""Base widget classes for consistent UI patterns."""

from .eliding_label import ElidingLabel
from .enhanced_dialog import EnhancedDialog
from .form_panel import FormPanel
from .setting_anchor import SettingAnchor, SettingAnchorHost, SettingTextProvider
from .sizing import (
    configure_expanding_container,
    field_label_width,
    make_label_fit_text,
    make_widget_expand_vertically,
    make_widget_shrink_to_fit,
)
from .status_badge import StatusBadge

__all__ = [
    "FormPanel",
    "SettingAnchor",
    "SettingAnchorHost",
    "SettingTextProvider",
    "StatusBadge",
    "ElidingLabel",
    "EnhancedDialog",
    "make_label_fit_text",
    "make_widget_expand_vertically",
    "make_widget_shrink_to_fit",
    "configure_expanding_container",
    "field_label_width",
]
