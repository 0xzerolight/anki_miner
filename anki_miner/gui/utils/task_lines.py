"""The one place a ``TaskSnapshot`` becomes words (D14, D17, D18, D22).

``TaskRegistry`` made the application's live work a single fact. Rendering it
was still per-surface, and a third renderer -- the mini job monitor -- is
exactly the drift D14-B was chosen to prevent: two windows quoting the same run
and disagreeing about which stage it is in, or one printing a percentage the
other declined to print. So the sentences live here and the widgets only place
them.

Two shapes are needed, and only two:

* :func:`format_task_line` is the *detailed* line -- stage, detail, position,
  clock. It is what the strip above a queue says, and what the mini monitor
  says. It leaves the run's title out, because both surfaces already name the
  run somewhere the eye has just been.
* :func:`format_task_summary` is the *compact* line -- title plus the single
  most specific true thing. It is what fits in a status bar and in a menu row.

Both obey the same two honesty rules the registry enforces upstream: a
percentage is printed only when there is a real denominator, and a run that is
cancelling stops quoting a position it is about to abandon.

The translation contexts are deliberately the widget class names these
functions were lifted out of. Moving a string to a new context orphans every
existing translation of it; keeping ``CurrentJobStrip`` and ``StatusBarWidget``
means the catalogs stay exactly as they are.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.gui.utils.progress_telemetry import format_clock
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskSnapshot

__all__ = [
    "CANCEL_EXPLANATION_DELAY_S",
    "format_task_line",
    "format_task_summary",
]

#: How long a cancel has to keep waiting before the line explains itself. Below
#: this the worker almost always stops first, and text that appears and vanishes
#: within a second is noise.
CANCEL_EXPLANATION_DELAY_S = 2.0


def format_task_line(snapshot: TaskSnapshot) -> str:
    """Render the detailed line for one run.

    Every part is omitted when the registry has no honest value for it -- there
    is no invented phase name and no synthetic percentage.

    The run's title is deliberately *not* the first thing on the line: both
    callers sit inside something that already names the run, and repeating it
    would spend the width on what the user is looking at. It stands in only when
    the run has not yet said anything more specific.

    Args:
        snapshot: The immutable task state to describe.

    Returns:
        A single ``·``-separated line.
    """
    parts: list[str] = []

    if snapshot.cancelling:
        parts.append(QCoreApplication.translate("CurrentJobStrip", "Cancelling…"))
        # Most cancels land within a second, and narrating those reads as
        # nervousness. Past that, silence starts to look like a hang, so the
        # wait names whatever the run last said it was doing.
        if snapshot.cancelling_age_s >= CANCEL_EXPLANATION_DELAY_S:
            waiting_for = snapshot.detail or snapshot.stage_name
            parts.append(
                tr_format(QCoreApplication.translate("CurrentJobStrip", "Finishing %1"), waiting_for)
                if waiting_for
                else QCoreApplication.translate("CurrentJobStrip", "Finishing the current item")
            )
        if snapshot.total:
            parts.append(
                tr_format(
                    QCoreApplication.translate("CurrentJobStrip", "%1 / %2"),
                    snapshot.current,
                    snapshot.total,
                )
            )
        parts.append(format_clock(snapshot.elapsed_s))
        return " · ".join(parts)

    if snapshot.stage_name:
        if snapshot.stage_index is not None and snapshot.stage_total:
            parts.append(
                tr_format(
                    QCoreApplication.translate("CurrentJobStrip", "%1 (%2 of %3)"),
                    snapshot.stage_name,
                    snapshot.stage_index,
                    snapshot.stage_total,
                )
            )
        else:
            parts.append(snapshot.stage_name)

    if snapshot.detail:
        parts.append(snapshot.detail)

    if not parts:
        parts.append(snapshot.title)

    if snapshot.total:
        parts.append(
            tr_format(
                QCoreApplication.translate("CurrentJobStrip", "%1 / %2"),
                snapshot.current,
                snapshot.total,
            )
        )

    parts.append(format_clock(snapshot.elapsed_s))
    return " · ".join(parts)


def format_task_summary(snapshot: TaskSnapshot) -> str:
    """Render the compact line: the title plus the most specific true thing.

    The ladder matters: a percentage is printed only when the registry has a
    real denominator, and otherwise the phase or the producer's own detail
    stands in. There is no synthetic fallback percentage -- inventing one is what
    made progress race and then sit.

    Args:
        snapshot: The immutable task state to describe.

    Returns:
        One short phrase, suitable for a status bar or a menu row.
    """
    if snapshot.cancelling:
        # A percentage of a run that is being abandoned is not a forecast of
        # anything, so it is dropped rather than left ticking beside
        # "Cancelling…".
        cancelling = QCoreApplication.translate("StatusBarWidget", "Cancelling…")
        return f"{snapshot.title} · {cancelling}"
    fraction = snapshot.fraction
    if fraction is not None:
        return f"{snapshot.title} {int(fraction * 100)}%"
    for phase in (snapshot.stage_name, snapshot.detail):
        if phase:
            return f"{snapshot.title} · {phase}"
    return snapshot.title
