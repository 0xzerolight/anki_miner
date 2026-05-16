"""Modern button widget with multiple style variants."""

from PyQt6.QtWidgets import QPushButton


class ModernButton(QPushButton):
    """Enhanced button widget with style variants.

    Variants:
    - primary: Solid primary color background (default)
    - secondary: Outlined with primary border
    - ghost: Transparent with subtle hover
    - danger: Red for destructive actions
    """

    def __init__(self, text: str = "", variant: str = "primary", parent=None):
        """Initialize the modern button.

        Args:
            text: Button text
            variant: Button variant ('primary', 'secondary', 'ghost', 'danger')
            parent: Optional parent widget
        """
        super().__init__(text, parent)

        # Apply variant styling
        self.setObjectName(variant)

        # Set minimum size for better touch targets
        self.setMinimumHeight(36)

        # Set accessibility properties
        self.setAccessibleName(text if text else "Button")
