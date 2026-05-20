"""Form panel base class for consistent settings panels."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base.sizing import make_label_fit_text


class FormPanel(QFrame):
    """Base class for settings panels with consistent card styling.

    Provides:
    - Card-style container with border and padding
    - Header with icon and title
    - Form layout for labeled fields
    - Helper text support
    - Section dividers

    Usage:
        panel = FormPanel("Anki Settings", icon="anki")
        panel.add_field("Deck Name", deck_input, "Select target deck")
        panel.add_section("Advanced")
        panel.add_field("Port", port_input)
    """

    def __init__(self, title: str, parent=None):
        """Initialize the form panel.

        Args:
            title: Panel title
            parent: Parent widget
        """
        super().__init__(parent)
        self._title = title

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the panel UI."""
        self.setObjectName("card")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        # Main layout
        self._main_layout = QVBoxLayout()
        self._main_layout.setContentsMargins(SPACING.md, SPACING.md, SPACING.md, SPACING.md)
        self._main_layout.setSpacing(SPACING.sm)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING.xs)

        self._title_label = QLabel(self._title)
        self._title_label.setObjectName("heading3")
        title_font = QFont()
        title_font.setPixelSize(FONT_SIZES.h3)
        title_font.setWeight(QFont.Weight.Bold)
        self._title_label.setFont(title_font)

        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        self._main_layout.addLayout(header_layout)

        # Initial form layout for fields added before any section.
        # add_section() opens a new form layout so subsequent fields render
        # under their section heading instead of all stacking in one form.
        self._form_layout = self._new_form_layout()
        self._active_form_layout: QFormLayout = self._form_layout

        self._main_layout.addLayout(self._form_layout)

        self.setLayout(self._main_layout)

        # Size policy - expand width, fit content height
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def _new_form_layout(self) -> QFormLayout:
        """Build a QFormLayout configured to match panel conventions."""
        layout = QFormLayout()
        layout.setSpacing(SPACING.sm)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        return layout

    def _create_field_label(self, text: str) -> QLabel | None:
        """Create a label for a form field with proper sizing.

        Args:
            text: Label text

        Returns:
            Configured QLabel that fits its text content, or None if text is empty
        """
        if not text:
            return None
        label = QLabel(f"{text}:")
        label.setObjectName("field-label")
        make_label_fit_text(label)
        return label

    def add_field(self, label: str, widget: QWidget, helper: str = "") -> QWidget:
        """Add a labeled field to the form.

        Args:
            label: Field label text
            widget: Input widget
            helper: Optional helper text below the field

        Returns:
            The widget that was added (for chaining)
        """
        # Create label with proper sizing (fits text, doesn't expand)
        field_label = self._create_field_label(label)

        if helper:
            # Create container for widget + helper
            container = QWidget()
            container_layout = QVBoxLayout()
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(SPACING.xs)  # More spacing between widget and helper

            container_layout.addWidget(widget)

            helper_label = QLabel(helper)
            # QLabel defaults to AutoText, which renders strings containing
            # angle brackets (e.g. ``<b>{cloze-body}</b>`` in the bold-target
            # helper) as HTML. Helpers are descriptive copy, never rich text,
            # so force PlainText so users see the literal markup. Issue #20.
            helper_label.setTextFormat(Qt.TextFormat.PlainText)
            helper_label.setObjectName("helper-text")
            helper_font = QFont()
            helper_font.setPixelSize(FONT_SIZES.small)
            helper_label.setFont(helper_font)
            helper_label.setWordWrap(True)
            container_layout.addWidget(helper_label)

            container.setLayout(container_layout)
            # Allow container to expand vertically for multi-line helper text
            container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

            if field_label is None:
                # No label - widget spans full width
                self._active_form_layout.addRow(container)
            else:
                self._active_form_layout.addRow(field_label, container)
        else:
            if field_label is None:
                # No label - widget spans full width
                self._active_form_layout.addRow(widget)
            else:
                self._active_form_layout.addRow(field_label, widget)

        return widget

    def add_widget(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """Add a widget directly to the main layout (not in form).

        Args:
            widget: Widget to add
            stretch: Layout stretch factor

        Returns:
            The widget that was added
        """
        self._main_layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout) -> None:
        """Add a layout directly to the main layout.

        Args:
            layout: Layout to add
        """
        self._main_layout.addLayout(layout)

    def add_section(self, title: str) -> None:
        """Add a section divider with title.

        Args:
            title: Section title
        """
        # Add spacing before section
        self._main_layout.addSpacing(8)

        section_label = QLabel(title)
        section_font = QFont()
        section_font.setPixelSize(FONT_SIZES.body)
        section_font.setWeight(QFont.Weight.DemiBold)
        section_label.setFont(section_font)

        self._main_layout.addWidget(section_label)

        # Open a fresh form layout so fields added next render under this
        # section heading. Without this, every field would land in the
        # initial form layout and all section labels would stack below.
        self._active_form_layout = self._new_form_layout()
        self._main_layout.addLayout(self._active_form_layout)

    def add_spacing(self, size: int = 8) -> None:
        """Add vertical spacing.

        Args:
            size: Spacing in pixels
        """
        self._main_layout.addSpacing(size)

    def add_stretch(self, factor: int = 1) -> None:
        """Add stretch to push content.

        Args:
            factor: Stretch factor
        """
        self._main_layout.addStretch(factor)

    @property
    def main_layout(self) -> QVBoxLayout:
        """Get the main layout for direct manipulation."""
        return self._main_layout
