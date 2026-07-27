"""Modern button widget with multiple style variants."""

from typing import Literal

from PyQt6.QtWidgets import QPushButton

#: The five roles a button can carry. Anything outside this list is a typo, and
#: renders as the ordinary quiet button.
ButtonVariant = Literal["primary", "secondary", "ghost", "danger", "critical"]


class ModernButton(QPushButton):
    """Enhanced button widget with style variants.

    Variants (D41 — accent is a scarce signal, red means destruction):

    - ``primary``: the one task action on a screen. Solid accent.
    - ``secondary``: the ordinary quiet control. Neutral text on a bordered box.
    - ``ghost``: quiet to the point of losing its border.
    - ``danger``: red *outline*, for reversible removals.
    - ``critical``: solid red. Reserved for the two irreversible actions in the
      app — deleting a settings profile and resetting the user Known Words list.

    Only ``primary`` stays eligible to become a dialog's automatic default, so
    Enter lands on the task action rather than on whichever quiet button
    happened to be built first. A button that is *explicitly* made the default
    still renders with the accent fill, because that is what Enter will press.
    """

    def __init__(self, text: str = "", variant: ButtonVariant = "primary", parent=None):
        """Initialize the modern button.

        Args:
            text: Button text
            variant: Button role — see the class docstring
            parent: Optional parent widget
        """
        super().__init__(text, parent)

        # Apply variant styling
        self.setObjectName(variant)

        # A quiet button must not silently become the Enter target of a dialog
        # just by being constructed first. Qt only auto-promotes buttons whose
        # autoDefault is on, so declining it here is what moves Enter onto the
        # primary action; an explicit setDefault() still works and still paints
        # accent through the QSS `:default` rules.
        if variant != "primary":
            self.setAutoDefault(False)

        # Set minimum size for better touch targets
        self.setMinimumHeight(36)

        # Set accessibility properties
        self.setAccessibleName(text if text else self.tr("Button"))
