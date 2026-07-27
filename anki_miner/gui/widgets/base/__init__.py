"""Base widget classes for consistent UI patterns."""

from .animated_tab_bar import AnimatedTabBar, install_animated_tab_bar
from .eliding_label import ElidingLabel
from .enhanced_dialog import EnhancedDialog
from .form_panel import FormPanel
from .screen_issue_banner import (
    ScreenIssue,
    ScreenIssueBanner,
    ScreenIssueHost,
    clear_reported_issue,
    report_screen_issue,
)
from .setting_anchor import SettingAnchor, SettingAnchorHost, SettingTextProvider
from .sizing import (
    PAGE_SCROLL_OBJECT_NAME,
    PageWidth,
    apply_button_size,
    configure_card_layout,
    configure_expanding_container,
    configure_scrolled_page,
    field_label_width,
    make_label_fit_text,
    make_widget_expand_vertically,
    make_widget_shrink_to_fit,
    page_width_cap,
)
from .status_badge import StatusBadge

__all__ = [
    "AnimatedTabBar",
    "install_animated_tab_bar",
    "FormPanel",
    "ScreenIssue",
    "ScreenIssueBanner",
    "ScreenIssueHost",
    "clear_reported_issue",
    "report_screen_issue",
    "SettingAnchor",
    "SettingAnchorHost",
    "SettingTextProvider",
    "StatusBadge",
    "ElidingLabel",
    "EnhancedDialog",
    "PAGE_SCROLL_OBJECT_NAME",
    "PageWidth",
    "apply_button_size",
    "configure_card_layout",
    "configure_scrolled_page",
    "page_width_cap",
    "make_label_fit_text",
    "make_widget_expand_vertically",
    "make_widget_shrink_to_fit",
    "configure_expanding_container",
    "field_label_width",
]
