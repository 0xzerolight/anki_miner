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

from anki_miner.models.processing import NotMinedReason, NotMinedReport, TerminalOutcome, WhitelistCoverage
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
    return QCoreApplication.translate("ResultCopy", "No cards created. Every word is already known.")


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
        notes_added: Confirmed Anki notes created; shown to the user as cards (D20 item 5).
        duration: Pre-formatted elapsed time (see
            :func:`~anki_miner.gui.utils.progress_telemetry.format_duration_words`).
        suspended: Whether the machine slept mid-run, in which case the clock
            is active time and the line says so.
    """
    multi = items_total > 1 and bool(item_noun)
    # D20 item 5: the receipt counts cards, the unit the status bar and the
    # results window already use. Two literals per shape, chosen by count,
    # never %n (see the module docstring).
    one = notes_added == 1

    if outcome is TerminalOutcome.SUCCESS:
        if multi:
            template = (
                QCoreApplication.translate("ResultCopy", "Mining complete — %1 %2, %3 card added in %4")
                if one
                else QCoreApplication.translate("ResultCopy", "Mining complete — %1 %2, %3 cards added in %4")
            )
            line = tr_format(template, items_completed, item_noun, notes_added, duration)
        else:
            template = (
                QCoreApplication.translate("ResultCopy", "Mining complete — %1 card added in %2")
                if one
                else QCoreApplication.translate("ResultCopy", "Mining complete — %1 cards added in %2")
            )
            line = tr_format(template, notes_added, duration)
    else:
        lead = {
            TerminalOutcome.CANCELLED: QCoreApplication.translate("ResultCopy", "Cancelled"),
            TerminalOutcome.PARTIAL: QCoreApplication.translate("ResultCopy", "Finished with errors"),
            TerminalOutcome.FAILED: QCoreApplication.translate("ResultCopy", "Mining failed"),
        }[outcome]
        if multi:
            template = (
                QCoreApplication.translate("ResultCopy", "%1 — %2 of %3 %4 completed; %5 card added in %6")
                if one
                else QCoreApplication.translate("ResultCopy", "%1 — %2 of %3 %4 completed; %5 cards added in %6")
            )
            line = tr_format(template, lead, items_completed, items_total, item_noun, notes_added, duration)
        else:
            template = (
                QCoreApplication.translate("ResultCopy", "%1 — %2 card added in %3")
                if one
                else QCoreApplication.translate("ResultCopy", "%1 — %2 cards added in %3")
            )
            line = tr_format(template, lead, notes_added, duration)

    if suspended:
        # The clock is active time (D23). Saying so is the difference between
        # an honest 40 minutes and an unexplained missing hour.
        line = f"{line} {QCoreApplication.translate('ResultCopy', '(asleep time excluded)')}"
    return line


def whitelist_summary(mined: int, total: int) -> str:
    """The receipt's whitelist clause: ``"Whitelist: 14 of 20 mined"``.

    No noun, so no plural: "1 of 1 mined" reads correctly and no catalog needs
    a numerus pair.

    Args:
        mined: Whitelist entries this run made a card for.
        total: Entries on the whitelist.
    """
    return tr_format(QCoreApplication.translate("ResultCopy", "Whitelist: %1 of %2 mined"), mined, total)


def whitelist_report(coverage: WhitelistCoverage) -> str:
    """The Activity Log's one run-level whitelist line.

    Every entry is named, not a preview: the list is the deliverable - it is
    what the user feeds the next run - and a whitelist is the user's own
    hand-written file, so its length is theirs. Entries sort by code point,
    which is at least stable between runs.

    Args:
        coverage: The run's folded whitelist coverage.
    """
    # A full sentence of its own, not the receipt clause with a "." bolted on:
    # appending an ASCII stop to a translated string puts a half-width period
    # inside CJK text, where the sibling sentences below end in a full-width
    # one. The English renders identically either way; the catalogs do not.
    line = tr_format(
        QCoreApplication.translate("ResultCopy", "Whitelist: %1 of %2 mined."),
        len(coverage.mined),
        len(coverage.entries),
    )
    if coverage.missing:
        line = f"{line} " + tr_format(
            QCoreApplication.translate("ResultCopy", "Not mined: %1."), ", ".join(sorted(coverage.missing))
        )
    if coverage.known:
        line = f"{line} " + tr_format(
            QCoreApplication.translate("ResultCopy", "Already known: %1."), ", ".join(sorted(coverage.known))
        )
    return line


def whitelist_unmined_text(coverage: WhitelistCoverage) -> str:
    """The clipboard payload: one unmined entry per line, ready to be the next whitelist file."""
    return "\n".join(sorted(coverage.missing))


def not_mined_header(count: int) -> str:
    """The first line of the run-end "Not mined" block."""
    if count == 1:
        return tr_format(
            QCoreApplication.translate("ResultCopy", "Not mined: %1 word — search this log for a word to see why."),
            count,
        )
    return tr_format(
        QCoreApplication.translate("ResultCopy", "Not mined: %1 words — search this log for a word to see why."),
        count,
    )


def not_mined_label(reason: NotMinedReason) -> str:
    """What one "Not mined" line says about its words, with the change that would mine them.

    Built per call so the active translator applies (deck_filter_tab's
    DROP_REASONS pattern).
    """
    labels = {
        NotMinedReason.WORD_TYPE: QCoreApplication.translate(
            "ResultCopy", "Name, interjection or other skipped word type — add it to your whitelist to mine it"
        ),
        NotMinedReason.SOUND_EFFECT: QCoreApplication.translate(
            "ResultCopy", "Sound effect — add it to your whitelist to mine it"
        ),
        NotMinedReason.KANA_ONLY: QCoreApplication.translate(
            "ResultCopy", "Kana-only word — add it to your whitelist to mine it"
        ),
        NotMinedReason.SCRIPT: QCoreApplication.translate(
            "ResultCopy", "Written partly in another script — add it to your whitelist to mine it"
        ),
        NotMinedReason.KNOWN: QCoreApplication.translate("ResultCopy", "Already known"),
        NotMinedReason.NO_DEFINITION: QCoreApplication.translate(
            "ResultCopy", "No dictionary entry — Settings → Dictionaries"
        ),
        NotMinedReason.UNRANKED: QCoreApplication.translate(
            "ResultCopy", "Not in your frequency list — Settings → Word Filters"
        ),
        NotMinedReason.FREQUENCY: QCoreApplication.translate(
            "ResultCopy", "Outside your frequency range — Settings → Word Filters"
        ),
        NotMinedReason.BLACKLIST: QCoreApplication.translate(
            "ResultCopy", "On your blacklist — Settings → Word Filters"
        ),
        NotMinedReason.SCRIPT_FILTER: QCoreApplication.translate(
            "ResultCopy", "Excluded by script type — Settings → Word Filters"
        ),
        NotMinedReason.NAME_LIST: QCoreApplication.translate("ResultCopy", "On a name list — Settings → Word Filters"),
        NotMinedReason.OCCURRENCE: QCoreApplication.translate(
            "ResultCopy", "Appears fewer times than your minimum — Settings → Word Filters"
        ),
        NotMinedReason.ONE_PER_SENTENCE: QCoreApplication.translate(
            "ResultCopy", "Another word from its sentence got the card — Settings → Sentences"
        ),
        NotMinedReason.I_PLUS_ONE: QCoreApplication.translate(
            "ResultCopy", "No sentence where it is the only unknown word — Settings → Sentences"
        ),
        NotMinedReason.SENTENCE_LENGTH: QCoreApplication.translate(
            "ResultCopy", "Sentence too long — Settings → Sentences"
        ),
        NotMinedReason.SAME_CARD: QCoreApplication.translate("ResultCopy", "Same card as another spelling in this run"),
        NotMinedReason.MEDIA_FAILED: QCoreApplication.translate("ResultCopy", "Media could not be extracted"),
        NotMinedReason.ANKI_DUPLICATE: QCoreApplication.translate("ResultCopy", "Anki already has a card for it"),
        NotMinedReason.ANKI_FAILED: QCoreApplication.translate("ResultCopy", "Anki did not confirm the card"),
    }
    return labels[reason]


def not_mined_lines(report: NotMinedReport) -> list[tuple[NotMinedReason, str]]:
    """One line per reason that names a word, in pipeline order, every word listed.

    No cap on purpose: the Activity Log's search box is the lookup.
    """
    lines: list[tuple[NotMinedReason, str]] = []
    for reason in NotMinedReason:
        forms = report.forms(reason)
        if forms:
            text = tr_format(
                QCoreApplication.translate("ResultCopy", "%1 (%2): %3"),
                not_mined_label(reason),
                len(forms),
                ", ".join(sorted(forms)),
            )
            lines.append((reason, text))
    return lines
