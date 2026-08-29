"""One implementation of a mining-language change, for both spec-6.1 triggers.

Trigger 1 is the Settings selector (``MainWindow.request_mining_language``);
trigger 2 is a settings-profile switch whose snapshot names another language
(``ProfileController._switch_locked``). Both owe the same things: refuse while
resources are busy, refuse - or flush on confirmation - when queues hold work,
and prewarm plus re-point the surfaces once the change is durable.

ORDER IS THE CONTRACT. Nothing is cleared, unlinked or written until every
refusal branch has been passed AND the new config is committed. The durable
queue snapshots are the user's pending work; a refusal must leave them alone,
so ``queue_state_store.discard_all()`` runs only from ``flush_queues``, which
runs only after ``update_config`` returned.
"""

from __future__ import annotations

import logging
from typing import Any

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.gui.utils import queue_state_store
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

MUTATION_KIND = "language-switch"
_CTX = "LanguageSwitch"


def queued_screens(window: Any) -> tuple[Any, ...]:
    """Every queue screen still holding rows. Read-only, never raises."""
    screens = getattr(window, "iter_queue_screens", None)
    if not callable(screens):
        return ()
    pending: list[Any] = []
    for screen in screens():
        try:
            snapshot = screen.queue_snapshot()
        except Exception:
            logger.exception("Could not read the queue on %s", type(screen).__name__)
            continue
        if snapshot.items:
            pending.append(screen)
    return tuple(pending)


def confirm_queue_flush(parent: QWidget | None, screens: tuple[Any, ...], display_name: str) -> bool:
    """Ask before discarding queued work: destructive and irreversible (D24)."""
    rows = sum(len(screen.queue_snapshot().items) for screen in screens)
    reply = QMessageBox.question(
        parent,
        QCoreApplication.translate(_CTX, "Switch mining language"),
        tr_format(
            QCoreApplication.translate(
                _CTX,
                "Switching to %1 discards %n queued item(s), on screen and in the copy saved "
                "for the next launch. Continue?",
                "",
                rows,
            ),
            display_name,
        ),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def flush_queues(window: Any) -> None:
    """Empty every queue screen, then drop the snapshots. Called after the commit."""
    for screen in queued_screens(window):
        clear = getattr(screen, "clear_queue", None)
        if callable(clear):
            clear()
    queue_state_store.discard_all()


def commit_language_change(window: Any, previous_config: Any, *, flush: bool, first_visit: bool) -> None:
    """Everything a DURABLE language change owes, on both triggers."""
    if flush:
        flush_queues(window)
    for name in ("restart_prewarm", "sync_mining_language_surfaces"):
        hook = getattr(window, name, None)
        if callable(hook):
            hook()
    logger.info("Mining language changed from %s to %s", previous_config.language, window.get_config().language)


def request_language_change(window: Any, code: str) -> bool:
    """Move the app onto mining language ``code``; return whether it moved."""
    if code == window.get_config().language:
        return False
    try:
        profile = get_profile(code)
    except (LookupError, ValueError, ImportError) as exc:
        logger.warning("Mining language %r is not available here: %s", code, exc)
        _refuse(window, QCoreApplication.translate(_CTX, "That mining language is not available in this build."))
        return False

    # A profile builds without the packages it parses with - every zh
    # third-party import is function-local - so the profile's own probe is what
    # decides whether the destination can mine a word. Refused BEFORE the guard:
    # this costs the user nothing, and the reason names what is missing.
    probe = profile.unavailable_reason
    reason = probe() if probe is not None else None
    if reason:
        logger.info("Refusing a switch to %r: %s", code, reason)
        _refuse(window, reason)
        return False

    with window._dictionary_mutation_guard(MUTATION_KIND) as ready:
        if not ready:
            _refuse(
                window,
                QCoreApplication.translate(_CTX, "Settings are busy. Nothing was switched."),
            )
            return False
        # READ THE CONFIG HERE, not before the guard: the guard's preflight
        # commits pending Settings edits through update_config, so a config read
        # earlier is already stale and switching from it would write every
        # just-committed edit back to its pre-edit value.
        config = window.get_config()
        first_visit = code not in config.language_stash
        # Covers mining, card backfill and prewarm - none of which the settings
        # preflight knows about - and drops the SQLite handles a chain swap wants
        # dropped anyway. Same preflight a profile switch runs. This BUSY CHECK
        # precedes every read or write of the queues: a refusal here must cost
        # the user nothing.
        if not window.release_dictionary_resources():
            _refuse(
                window,
                QCoreApplication.translate(_CTX, "Mining is running. Stop it, then switch language."),
            )
            return False
        pending = queued_screens(window)
        if pending and not confirm_queue_flush(window, pending, profile.display_name):
            return False  # Declined: nothing touched, on screen or on disk.
        try:
            window.update_config(switch_language(config, code))
        except Exception as error:  # noqa: BLE001 - a failed commit must not crash a Qt slot
            logger.exception("Could not switch the mining language to %r", code)
            _refuse(
                window,
                tr_format(
                    QCoreApplication.translate(_CTX, "Could not switch to %1: %2. Nothing was switched."),
                    profile.display_name,
                    error,
                ),
            )
            return False
    commit_language_change(window, config, flush=bool(pending), first_visit=first_visit)
    return True


def _refuse(window: Any, summary: str) -> None:
    """Report a refusal on the window's banner (D24), never in a modal."""
    from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssue

    show = getattr(window, "show_screen_issue", None)
    if callable(show):
        show(ScreenIssue(summary=summary))
    else:
        logger.warning("%s", summary)
