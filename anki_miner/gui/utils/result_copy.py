"""The words finished work uses about itself (D47-B).

The app used to congratulate the user: *"Success!"*, *"Done! Created 42
cards…"*, *"All words already in Anki!"*, *"Copied!"*. That voice suits a thing
you use for a minute. This one grinds through a forty-minute queue unattended,
and on the twentieth exclamation mark the punctuation stops carrying meaning
and starts costing the reader a beat to skip. So the results state what changed
and stop: *"Created 42 cards in 'Mining'"*, *"No cards created. Every word is
already in Anki."*, *"Copied"*.

Every formatter here is **pure** — no widget, no config, no clock. That is what
lets the receipt model stay a model: ``controllers/run_receipt`` holds the
numbers and holds no wording, ``widgets/inline_receipt`` shows a line it did
not compose, and the sentence a run ends on is decided in exactly one place.

Plurals are two literal sources selected by count rather than Qt's ``%n``: the
German catalog is test-gated on numerus, so a ``%n`` source added here reddens
the suite until someone who speaks German finishes both forms. Two literals
cost one extra line and are translatable by anyone.
"""

from __future__ import annotations

from PyQt6.QtCore import QCoreApplication

from anki_miner.models.processing import TerminalOutcome
from anki_miner.utils.i18n import tr_format


def created_cards(count: int, deck: str | None = None) -> str:
    """State how many cards a run made, and where they went.

    Args:
        count: Cards created. Zero is a real answer, not an error.
        deck: Destination deck name, quoted into the sentence when known.
            Omitted callers get the shorter form rather than an empty ``''``.

    Returns:
        ``"Created 42 cards in 'Mining'"``, ``"Created 1 card"``, or
        ``"No cards created."``
    """
    if count <= 0:
        return QCoreApplication.translate("ResultCopy", "No cards created.")
    if deck:
        template = (
            QCoreApplication.translate("ResultCopy", "Created %1 card in '%2'")
            if count == 1
            else QCoreApplication.translate("ResultCopy", "Created %1 cards in '%2'")
        )
        return tr_format(template, count, deck)
    template = (
        QCoreApplication.translate("ResultCopy", "Created %1 card")
        if count == 1
        else QCoreApplication.translate("ResultCopy", "Created %1 cards")
    )
    return tr_format(template, count)


def nothing_new_to_mine() -> str:
    """The zero-card ending that is a *result*, not a failure.

    Re-mining an episode you already mined produces nothing, and the old
    ``"All words already in Anki!"`` read as a cheer for having done nothing.
    Saying the outcome first and the reason second is what stops a user
    hunting for the setting they broke.
    """
    return QCoreApplication.translate("ResultCopy", "No cards created. Every word is already in Anki.")


def copied() -> str:
    """Clipboard confirmation. It happened; there is nothing to celebrate."""
    return QCoreApplication.translate("ResultCopy", "Copied")


def run_summary(
    outcome: TerminalOutcome,
    *,
    items_completed: int,
    items_total: int,
    item_noun: str,
    notes_added: int,
    duration: str,
    suspended: bool = False,
) -> str:
    """Compose the line a finished run leaves behind (D20).

    Two shapes, not one per outcome: a clean run states what it produced, and
    every other ending states how far it got first. That "3 of 12" is the part
    the old per-item dialogs threw away.

    The item count is printed only when there is more than one — a single-item
    screen has nothing to count, and "1 episodes" is how you tell that a
    template was written for the plural case and never checked.

    Args:
        outcome: How the run ended.
        items_completed: Items that finished, however the run ended.
        items_total: Items the run set out to do, frozen at launch.
        item_noun: Already-plural noun for the screen's unit ("episodes"). An
            empty string suppresses the count, same as a single-item run.
        notes_added: Confirmed Anki notes created.
        duration: Pre-formatted elapsed time (see
            :func:`~anki_miner.gui.utils.progress_telemetry.format_duration_words`).
        suspended: Whether the machine slept mid-run, in which case the clock
            is active time and the line says so.
    """
    multi = items_total > 1 and bool(item_noun)

    if outcome is TerminalOutcome.SUCCESS:
        line = (
            tr_format(
                QCoreApplication.translate("ResultCopy", "Mining complete — %1 %2, %3 notes added in %4"),
                items_completed,
                item_noun,
                notes_added,
                duration,
            )
            if multi
            else tr_format(
                QCoreApplication.translate("ResultCopy", "Mining complete — %1 notes added in %2"),
                notes_added,
                duration,
            )
        )
    else:
        lead = {
            TerminalOutcome.CANCELLED: QCoreApplication.translate("ResultCopy", "Cancelled"),
            TerminalOutcome.PARTIAL: QCoreApplication.translate("ResultCopy", "Finished with errors"),
            TerminalOutcome.FAILED: QCoreApplication.translate("ResultCopy", "Mining failed"),
        }[outcome]
        line = (
            tr_format(
                QCoreApplication.translate("ResultCopy", "%1 — %2 of %3 %4 completed; %5 notes added in %6"),
                lead,
                items_completed,
                items_total,
                item_noun,
                notes_added,
                duration,
            )
            if multi
            else tr_format(
                QCoreApplication.translate("ResultCopy", "%1 — %2 notes added in %3"),
                lead,
                notes_added,
                duration,
            )
        )

    if suspended:
        # The clock is active time (D23). Saying so is the difference between
        # an honest 40 minutes and an unexplained missing hour.
        line = f"{line} {QCoreApplication.translate('ResultCopy', '(asleep time excluded)')}"
    return line
