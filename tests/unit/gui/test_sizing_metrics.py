"""Font-metric sizing primitives shared by buttons and item-view rows.

These replace hard-coded pixel floors (e.g. ModernButton's old ``setMinimumHeight(36)``)
so control geometry tracks the UI text scale instead of drifting out from under it at
0.8x or 1.5x. Every assertion here is about *tracking*, not about exact pixel values:
pinning literals would just re-create the constant the helpers exist to remove.
"""

from PyQt6.QtWidgets import QFrame, QLabel, QListWidget, QPushButton

from anki_miner.gui.widgets.base.sizing import apply_button_size, metric_row_height


def _enlarge(widget) -> None:
    """Grow a widget's own font, the way a raised text scale eventually does.

    The global scale reaches widgets through the application stylesheet
    (``Theme.set_font_scale`` only invalidates the compiled-QSS cache; callers
    repaint via ``apply_to_app``). These helpers are one layer below that: their
    contract is "derive from *this* widget's rendered font", so the tests perturb
    the widget font directly rather than mutating application-wide state.
    """
    font = widget.font()
    font.setPixelSize(font.pixelSize() * 2 if font.pixelSize() > 0 else 28)
    widget.setFont(font)


class TestApplyButtonSize:
    def test_sets_a_positive_minimum_height(self, qtbot):
        button = QPushButton("Mine Episode")
        qtbot.addWidget(button)

        apply_button_size(button)

        assert button.minimumHeight() > 0

    def test_minimum_height_clears_the_font(self, qtbot):
        """The control must never be shorter than the text it renders."""
        button = QPushButton("Mine Episode")
        qtbot.addWidget(button)

        apply_button_size(button)

        assert button.minimumHeight() >= button.fontMetrics().height()

    def test_height_grows_with_the_rendered_font(self, qtbot):
        small = QPushButton("Mine Episode")
        qtbot.addWidget(small)
        apply_button_size(small)
        baseline = small.minimumHeight()

        large = QPushButton("Mine Episode")
        qtbot.addWidget(large)
        _enlarge(large)
        apply_button_size(large)

        assert large.minimumHeight() > baseline

    def test_square_buttons_are_square(self, qtbot):
        """Icon/arrow buttons (the chain reorder controls) must not stretch."""
        button = QPushButton("↑")
        qtbot.addWidget(button)

        apply_button_size(button, square=True)

        assert button.minimumWidth() == button.minimumHeight()
        assert button.maximumWidth() == button.minimumWidth()

    def test_non_square_buttons_are_not_width_constrained(self, qtbot):
        button = QPushButton("+ Add Dictionary…")
        qtbot.addWidget(button)

        apply_button_size(button)

        # Left free to size to its label; only the height floor is imposed.
        assert button.maximumWidth() > button.minimumHeight()

    def test_does_not_overwrite_a_caller_defined_size_policy(self, qtbot):
        """Callers set stretch deliberately; the helper only owns geometry."""
        button = QPushButton("Preview")
        qtbot.addWidget(button)
        before = button.sizePolicy()

        apply_button_size(button)

        assert button.sizePolicy().horizontalPolicy() == before.horizontalPolicy()
        assert button.sizePolicy().verticalPolicy() == before.verticalPolicy()

    def test_is_idempotent(self, qtbot):
        button = QPushButton("Mine Episode")
        qtbot.addWidget(button)

        apply_button_size(button)
        once = button.minimumHeight()
        apply_button_size(button)

        assert button.minimumHeight() == once


class TestMetricRowHeight:
    def test_returns_a_positive_height(self, qtbot):
        view = QListWidget()
        qtbot.addWidget(view)

        assert metric_row_height(view) > 0

    def test_clears_the_font(self, qtbot):
        view = QListWidget()
        qtbot.addWidget(view)

        assert metric_row_height(view) >= view.fontMetrics().height()

    def test_grows_with_the_rendered_font(self, qtbot):
        small = QListWidget()
        qtbot.addWidget(small)
        baseline = metric_row_height(small)

        large = QListWidget()
        qtbot.addWidget(large)
        _enlarge(large)

        assert metric_row_height(large) > baseline

    def test_vertical_padding_is_additive(self, qtbot):
        view = QListWidget()
        qtbot.addWidget(view)

        tight = metric_row_height(view, vertical_padding=0)
        roomy = metric_row_height(view, vertical_padding=12)

        assert roomy == tight + 24  # padding applies to both edges

    def test_accepts_an_embedded_row_widget(self, qtbot):
        """Queue rows are QFrames set via setItemWidget, not item views."""
        row = QFrame()
        qtbot.addWidget(row)

        assert metric_row_height(row) > 0

    def test_japanese_text_still_fits(self, qtbot):
        """CJK glyphs are taller than Latin; the row must clear them too."""
        view = QListWidget()
        qtbot.addWidget(view)
        probe = QLabel("営業部の会議")
        qtbot.addWidget(probe)
        probe.setFont(view.font())

        assert metric_row_height(view) >= probe.fontMetrics().height()
