"""Auto-responder for the blocking word-curation modal (E2E soak harness).

Unattended/looped mining runs would otherwise stall forever on the modal
``WordCurationDialog``. :class:`AutoCurationResponder` is a context manager that
patches that dialog (and, in ``full_window`` mode, the post-run results +
first-run welcome dialogs) with no-op fakes so a run completes without a human.

The headline soak feature drives the bare mining tab (``full_window=False``):
it only needs the curation dialog patched. ``full_window=True`` additionally
patches the dialogs a full ``MainWindow``-driven run pops, so the run never
blocks on a result popup or the first-launch welcome offer.

Why patch at the import site
----------------------------
``MiningTabBase._on_curation_requested`` resolves ``WordCurationDialog`` from
``anki_miner.gui.widgets._mining_tab_base``'s namespace (it is imported into that
module). Patching the *definition* site would not affect that already-bound name,
so the responder patches the name as imported INTO ``_mining_tab_base`` — exactly
what ``tests/unit/test_mining_tab_base_curation.py`` does.

DialogCode equality
-------------------
The slot compares ``dialog.exec() == WordCurationDialog.DialogCode.Accepted``,
where ``WordCurationDialog`` is this module's fake. So the fake's
``DialogCode.Accepted`` and the value its ``exec()`` returns must be the *same*
object. The responder captures the REAL ``WordCurationDialog.DialogCode`` before
patching and reuses it for both, which guarantees the equality holds.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

from tests.e2e.config import validate_curation_policy

# Patch targets: the dialog symbols as imported INTO the modules that USE them
# (not their definition sites — see the module docstring).
_CURATION_TARGET = "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog"
_RESULTS_TARGET = "anki_miner.gui.main_window.ResultsDialog"
# run_setup_wizard is imported function-locally inside
# MainWindow._maybe_offer_first_run_setup, so the only stable target is its
# definition module (it replaced the retired WelcomeDialog flow).
_SETUP_WIZARD_TARGET = "anki_miner.gui.widgets.dialogs.setup_wizard.run_setup_wizard"


def _make_fake_curation_dialog(responder: AutoCurationResponder) -> type:
    """Build a fake ``WordCurationDialog`` class bound to ``responder``.

    Captures the REAL ``DialogCode`` enum so the fake's ``Accepted`` member and
    the value returned by ``exec()`` are the identical object the real slot
    compares against.
    """
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog as _Real

    real_dialog_code = _Real.DialogCode

    class _FakeCurationDialog:
        # Reuse the real enum so ``exec() == DialogCode.Accepted`` holds in the
        # real slot regardless of which side resolves the name.
        DialogCode = real_dialog_code

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # Real call site: WordCurationDialog(words, parent, mark_known_callback=...,
            # media_context=..., lookup_fn=...). First positional arg is the offered
            # words list.
            offered = list(args[0]) if args else list(kwargs.get("words", []))
            self._offered = offered
            responder.offered.append(offered)

        def exec(self) -> Any:
            # Always "accept": even policy="none" confirms an empty selection
            # (completed, 0 cards) rather than cancelling (None).
            return real_dialog_code.Accepted

        def get_selected_words(self) -> list:
            return responder._select(self._offered)

        def reject(self) -> None:  # pragma: no cover - cancel path; not hit on accept
            """No-op: the cancel path calls this on an open dialog."""

        def deleteLater(self) -> None:  # match QDialog surface for teardown guard
            pass

    return _FakeCurationDialog


def _make_fake_results_dialog() -> type:
    """Build a no-op ``ResultsDialog`` fake.

    ``MainWindow._on_processing_result`` constructs ``ResultsDialog(result, self,
    undo_callback=...)``, calls ``dialog.exec()``, then reads ``dialog.undo_completed``.
    The fake records nothing, returns immediately from ``exec()``, and reports
    ``undo_completed=False`` so the undo callback is never invoked (no card deletion).
    """
    from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog as _Real

    real_dialog_code = _Real.DialogCode

    class _FakeResultsDialog:
        DialogCode = real_dialog_code

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.undo_completed = False

        def exec(self) -> Any:
            return real_dialog_code.Accepted

    return _FakeResultsDialog


def _fake_run_setup_wizard(parent: Any, config: Any) -> Any:
    """No-op stand-in for ``run_setup_wizard`` that auto-skips the first-run flow.

    ``MainWindow._maybe_offer_first_run_setup`` calls ``run_setup_wizard(self,
    config)`` and folds its return into the config. Returning the config unchanged
    is a zero-touch skip (no Qt modal, no AnkiConnect, no network), after which
    the caller persists ``first_run_setup_done`` as usual.
    """
    return config


class AutoCurationResponder:
    """Context manager that auto-answers the curation modal during a run.

    Args:
        policy: How to map the offered words to a selection.
            ``"all"`` keeps every offered word, ``"first_n"`` keeps the first
            ``first_n``, ``"none"`` keeps nothing (an empty but ACCEPTED
            selection — completed with 0 cards, not a cancel).
        first_n: Cap used when ``policy == "first_n"``.
        full_window: When True, also patch the post-run ``ResultsDialog`` and
            the first-run ``run_setup_wizard`` so a full ``MainWindow``-driven
            run does not block on either of them.

    Attributes:
        offered: One entry per dialog opened, each the list of words that dialog
            was asked to curate. Lets callers/reports detect re-offered known
            words accumulating across sessions (a harness-hunted symptom).
    """

    def __init__(self, policy: str = "all", first_n: int = 0, full_window: bool = False) -> None:
        validate_curation_policy(policy, first_n)
        self.policy = policy
        self.first_n = first_n
        self.full_window = full_window
        self.offered: list[list] = []
        self._stack: ExitStack | None = None

    def _select(self, words: list) -> list:
        """Apply the configured policy to one offered word list."""
        if self.policy == "all":
            return list(words)
        if self.policy == "first_n":
            return list(words[: self.first_n])
        # "none"
        return []

    def __enter__(self) -> AutoCurationResponder:
        stack = ExitStack()
        try:
            stack.enter_context(patch(_CURATION_TARGET, _make_fake_curation_dialog(self)))
            if self.full_window:
                stack.enter_context(patch(_RESULTS_TARGET, _make_fake_results_dialog()))
                stack.enter_context(patch(_SETUP_WIZARD_TARGET, _fake_run_setup_wizard))
        except Exception:
            # If a later patch fails, unwind the ones already started.
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """Restore every patched symbol, even on exception."""
        if self._stack is not None:
            self._stack.close()
            self._stack = None
