"""Modern button widget with multiple style variants."""

from PyQt6.QtWidgets import QPushButton

from anki_miner.gui.resources.icons.icon_provider import IconProvider


class ModernButton(QPushButton):
    """Enhanced button widget with style variants and icon support.

    Variants:
    - primary: Solid primary color background (default)
    - secondary: Outlined with primary border
    - ghost: Transparent with subtle hover
    - danger: Red for destructive actions

    Features:
    - Icon + text combination
    - Loading state
    - Keyboard shortcut display in tooltip
    """

    def __init__(self, text: str = "", icon: str = "", variant: str = "primary", parent=None):
        """Initialize the modern button.

        Args:
            text: Button text
            icon: Icon name from IconProvider
            variant: Button variant ('primary', 'secondary', 'ghost', 'danger')
            parent: Optional parent widget
        """
        # Combine icon and text
        if icon:
            icon_char = IconProvider.get_icon(icon)
            display_text = f"{icon_char} {text}" if text else icon_char
        else:
            display_text = text

        super().__init__(display_text, parent)

        self._variant = variant
        self._is_loading = False
        self._original_text = display_text

        # Apply variant styling
        self.setObjectName(variant)

        # Set minimum size for better touch targets
        self.setMinimumHeight(36)

        # Set accessibility properties
        self.setAccessibleName(text if text else "Button")
        if icon:
            self.setAccessibleDescription(f"{text} button" if text else f"{icon} button")

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
            loading_icon = IconProvider.get_icon("processing")
            self.setText(f"{loading_icon} Loading...")
            self.setEnabled(False)
            self.setAccessibleDescription("Button is loading, please wait")
        else:
            # Restore original text
            self.setText(self._original_text)
            self.setEnabled(True)
            self.setAccessibleDescription("")

    def set_icon(self, icon: str) -> None:
        """Update button icon.

        Args:
            icon: Icon name from IconProvider
        """
        icon_char = IconProvider.get_icon(icon)
        current_text = self.text()

        # Check if there's already an icon (non-ASCII char at start)
        if current_text and ord(current_text[0]) > 127:
            # Replace existing icon
            parts = current_text.split(" ", 1)
            new_text = f"{icon_char} {parts[1]}" if len(parts) > 1 else icon_char
        else:
            # Add icon
            new_text = f"{icon_char} {current_text}" if current_text else icon_char

        self.setText(new_text)
        self._original_text = new_text

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
