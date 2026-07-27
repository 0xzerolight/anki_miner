"""Form panel base class for consistent settings panels."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base.setting_anchor import SettingAnchorHost, SettingTextProvider
from anki_miner.gui.widgets.base.sizing import configure_card_layout, make_label_fit_text


class FormPanel(SettingAnchorHost, QFrame):
    """Base class for settings panels with consistent card styling.

    Provides:
    - Card-style container with border and padding
    - Header with icon and title
    - Form layout for labeled fields
    - Helper text support
    - Section dividers
    - Setting anchors, so search can address each field (D11)

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
        configure_card_layout(self._main_layout)

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
        # Heading of the section fields are currently landing in, read lazily by
        # the anchor text providers so search matches the section name too.
        self._active_section_label: QLabel | None = None

        self._main_layout.addLayout(self._form_layout)

        self.setLayout(self._main_layout)

        # Size policy - expand width, fit content height
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def _new_form_layout(self) -> QFormLayout:
        """Build a QFormLayout configured to match panel conventions."""
        layout = QFormLayout()
        # Rows inside one card are more closely related to each other than the
        # cards are, so their gap is the smaller of the two (D40).
        layout.setSpacing(SPACING.xxs)
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

    def add_field(
        self,
        label: str,
        widget: QWidget,
        helper: str = "",
        *,
        anchor: str = "",
        anchor_focus: QWidget | None = None,
        anchor_text: SettingTextProvider | None = None,
        anchor_ignore: str = "",
    ) -> QWidget:
        """Add a labeled field to the form.

        Args:
            label: Field label text
            widget: Input widget
            helper: Optional tooltip text shown on hover over the field
            anchor: Stable anchor name. Defaults to the panel attribute
                ``widget`` is bound to; pass it explicitly for containers and
                loop-built widgets, which have no attribute to derive from.
            anchor_focus: Control inside ``widget`` that should take focus after
                a search jump. Defaults to ``widget`` itself.
            anchor_text: Extra searchable strings, resolved lazily. Use it so a
                composite row names the controls nested inside it.
            anchor_ignore: Reason this row is infrastructure rather than a
                setting. Registers no anchor.

        Returns:
            The widget that was added (for chaining)
        """
        # Create label with proper sizing (fits text, doesn't expand)
        field_label = self._create_field_label(label)

        if helper:
            widget.setToolTip(helper)

        if field_label is None:
            self._active_form_layout.addRow(widget)
        else:
            field_label.setBuddy(widget)
            self._active_form_layout.addRow(field_label, widget)

        self._register_setting_anchor(
            widget,
            field_label=field_label,
            anchor=anchor,
            anchor_focus=anchor_focus,
            anchor_text=anchor_text,
            anchor_ignore=anchor_ignore,
            required=True,
        )
        return widget

    def add_widget(
        self,
        widget: QWidget,
        stretch: int = 0,
        *,
        anchor: str = "",
        anchor_focus: QWidget | None = None,
        anchor_text: SettingTextProvider | None = None,
        anchor_ignore: str = "",
    ) -> QWidget:
        """Add a widget directly to the main layout (not in form).

        Unlike :meth:`add_field`, anchoring here is opt-in: this path carries
        status readouts, helper prose and action buttons as often as it carries
        settings. Pass ``anchor`` for the ones that are settings.

        Args:
            widget: Widget to add
            stretch: Layout stretch factor
            anchor: Stable anchor name; omit to register no anchor
            anchor_focus: Control inside ``widget`` that should take focus
            anchor_text: Extra searchable strings, resolved lazily
            anchor_ignore: Reason this widget is infrastructure, not a setting

        Returns:
            The widget that was added
        """
        self._main_layout.addWidget(widget, stretch)
        self._register_setting_anchor(
            widget,
            field_label=None,
            anchor=anchor,
            anchor_focus=anchor_focus,
            anchor_text=anchor_text,
            anchor_ignore=anchor_ignore,
            required=False,
        )
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
        self._main_layout.addSpacing(SPACING.xxs)

        section_label = QLabel(title)
        section_font = QFont()
        section_font.setPixelSize(FONT_SIZES.body_sm)
        section_font.setWeight(QFont.Weight.DemiBold)
        section_label.setFont(section_font)

        self._main_layout.addWidget(section_label)
        self._active_section_label = section_label

        # Open a fresh form layout so fields added next render under this
        # section heading. Without this, every field would land in the
        # initial form layout and all section labels would stack below.
        self._active_form_layout = self._new_form_layout()
        self._main_layout.addLayout(self._active_form_layout)

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

    # ------------------------------------------------------------------
    # Setting anchors (D11)
    # ------------------------------------------------------------------

    def _register_setting_anchor(
        self,
        widget: QWidget,
        *,
        field_label: QLabel | None,
        anchor: str,
        anchor_focus: QWidget | None,
        anchor_text: SettingTextProvider | None,
        anchor_ignore: str,
        required: bool,
    ) -> None:
        """Register (or deliberately skip) the anchor for one added widget."""
        if not self.ANCHOR_NAMESPACE:
            # Not a settings surface — plain FormPanel use, e.g. in tests.
            return
        if anchor_ignore:
            self.ignore_setting_widget(widget, anchor_ignore)
            return
        if isinstance(widget, QLabel):
            # A bare label row is prose (guidance, warnings), not a control.
            return
        name = anchor or self._panel_attribute_name(widget)
        if not name:
            if not required:
                return
            raise ValueError(
                f"{type(self).__name__}: {type(widget).__name__} is not bound to a panel "
                "attribute, so its anchor id cannot be derived — pass anchor= or anchor_ignore="
            )
        self.register_setting(
            name,
            widget,
            self._field_text_provider(widget, field_label, anchor_text),
            focus=anchor_focus,
        )

    def _panel_attribute_name(self, widget: QWidget) -> str:
        """Return the attribute ``widget`` is bound to on this panel, if any.

        Code-derived, so the resulting id survives reordering, restyling and
        translation. Loop-built and container widgets have no attribute; those
        call sites pass ``anchor=`` instead.
        """
        for name, value in vars(self).items():
            if value is widget:
                return name.lstrip("_")
        return ""

    def _field_text_provider(
        self,
        widget: QWidget,
        field_label: QLabel | None,
        extra: SettingTextProvider | None,
    ) -> SettingTextProvider:
        """Build the lazy searchable-text provider for one field.

        Everything is read at call time from widgets that already hold the
        translated strings, so the index tracks whatever translator is installed
        — see ``setting_anchor``'s module docstring.
        """
        section_label = self._active_section_label
        title_label = self._title_label

        def provider() -> tuple[str, ...]:
            parts: list[str] = []
            if field_label is not None:
                parts.append(field_label.text())
            if isinstance(widget, QAbstractButton):
                # A label-less checkbox carries its own caption; a QLineEdit's
                # text() is the user's value, not a name, so it is never read.
                parts.append(widget.text())
            parts.append(widget.toolTip())
            if extra is not None:
                parts.extend(extra())
            if section_label is not None:
                parts.append(section_label.text())
            parts.append(title_label.text())
            return tuple(parts)

        return provider
