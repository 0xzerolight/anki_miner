"""The header must not spend more height than its content needs.

The header is the settings-profile and theme selectors at the right end of the
main tab row (D16). The owner asked that the empty space around the chrome come
down, not that any control go, so this asserts *proportion*: the header may not
be dramatically taller than the text it draws. Which controls it carries is
test_header_corner's job.

Everything is measured against live font metrics rather than pixel literals, so
the assertion still holds at 0.8x and 1.5x text where a hard-coded number would
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

        The header draws two compact selectors on one line of text, so its
        height should be within a small multiple of a text line — not the
        double-height block it was.
        """
        header = HeaderWidget()
        qtbot.addWidget(header)
        header.ensurePolished()

        assert header.sizeHint().height() <= _line_height(header) * 3
