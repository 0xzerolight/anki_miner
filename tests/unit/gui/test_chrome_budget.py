"""The permanent chrome must not spend more height than its content needs.

The owner kept today's frame — including the Report a Bug / Star / Join Discord
links — and asked only that the empty space around it come down. So these tests
assert *proportion*, not the presence or absence of any control: the header may
not be dramatically taller than the text it draws.

Everything is measured against live font metrics rather than pixel literals, so
the assertions still hold at 0.8x and 1.5x text where a hard-coded number would
either go stale or start failing.
"""

from PyQt6.QtWidgets import QLabel

from anki_miner.gui.widgets.header_widget import HeaderWidget


def _line_height(widget) -> int:
    probe = QLabel("Anki Miner", widget)
    probe.ensurePolished()
    return probe.fontMetrics().lineSpacing()


class TestHeaderChromeBudget:
    def test_header_is_not_dominated_by_padding(self, qtbot):
        """The complaint: 'a lot of padding currently'.

        The header draws one title line plus a couple of compact controls, so
        its height should be within a small multiple of a text line — not the
        double-height block it was.
        """
        header = HeaderWidget()
        qtbot.addWidget(header)
        header.ensurePolished()

        assert header.sizeHint().height() <= _line_height(header) * 3

    def test_vertical_margins_are_tighter_than_horizontal(self, qtbot):
        """Horizontal breathing room is cheap; vertical space is the scarce axis
        in an 800px window, so the two must not use the same value."""
        header = HeaderWidget()
        qtbot.addWidget(header)
        layout = header.layout()
        assert layout is not None
        margins = layout.contentsMargins()

        assert margins.top() * 2 <= margins.left()
        assert margins.bottom() * 2 <= margins.left()

    def test_the_kept_controls_are_still_present(self, qtbot):
        """The owner kept the frame; this trim removes space, never controls."""
        header = HeaderWidget()
        qtbot.addWidget(header)

        labels = [w.text() for w in header.findChildren(QLabel)]
        assert any("Anki Miner" in text for text in labels)
