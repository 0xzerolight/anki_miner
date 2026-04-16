"""Modern button widget with multiple style variants."""

from PyQt6.QtWidgets import QPushButton


class ModernButton(QPushButton):
    """Enhanced button widget with style variants.

    Variants:
    - primary: Solid primary color background (default)
    - secondary: Outlined with primary border
    - ghost: Transparent with subtle hover
    - danger: Red for destructive actions

    Features:
    - Loading state
    - Keyboard shortcut display in tooltip
    """

    def __init__(self, text: str = "", variant: str = "primary", parent=None):
        """Initialize the modern button.

        Args:
            text: Button text
            variant: Button variant ('primary', 'secondary', 'ghost', 'danger')
            parent: Optional parent widget
        """
        super().__init__(text, parent)

        self._variant = variant
        self._is_loading = False
        self._original_text = text

        # Apply variant styling
        self.setObjectName(variant)

        # Set minimum size for better touch targets
        self.setMinimumHeight(36)

        # Set accessibility properties
        self.setAccessibleName(text if text else "Button")

    def set_variant(self, variant: str) -> None:
        """Change the button variant.

        Args:
            variant: New variant ('primary', 'secondary', 'ghost', 'danger')
        """
        self._variant = variant
        self.setObjectName(variant)
        if style := self.style():
            style.unpolish(self)
            style.polish(self)

    def set_loading(self, loading: bool) -> None:
        """Set loading state.

        When loading, button is disabled and shows a loading indicator.

        Args:
            loading: Whether button is in loading state
        """
        self._is_loading = loading

        if loading:
            # Show loading indicator
            self.setText("Loading...")
            self.setEnabled(False)
            self.setAccessibleDescription("Button is loading, please wait")
        else:
            # Restore original text
            self.setText(self._original_text)
            self.setEnabled(True)
            self.setAccessibleDescription("")

    def set_shortcut_hint(self, shortcut: str) -> None:
        """Add keyboard shortcut hint to tooltip.

        Args:
            shortcut: Keyboard shortcut (e.g., "Ctrl+P")
        """
        current_tooltip = self.toolTip()

        if current_tooltip:
            new_tooltip = f"{current_tooltip} ({shortcut})"
        else:
            new_tooltip = f"Keyboard shortcut: {shortcut}"

        self.setToolTip(new_tooltip)
