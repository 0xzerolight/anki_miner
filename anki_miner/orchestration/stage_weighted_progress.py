"""Stage-weighted progress adapter.

The mining pipeline reports progress per stage: media extraction, definition
lookup, optional glossary lookup, and card creation each call
``on_start -> on_progress -> on_complete`` independently. Forwarded straight to
the GUI, that resets and refills the bar 0->100 once per stage, so the bar hits
100% at the end of media extraction (stage 1 of 4) long before mining finishes.

``StageWeightedProgress`` wraps the real callback and maps each stage's local
progress into a contiguous band of one global 0->100 sweep. It auto-advances to
the next band on every ``on_start``, so callers only supply the per-run weight
list (one entry per reporting stage, in firing order) and otherwise pass it
through unchanged. The caller signals the end of the whole sweep with an
explicit :meth:`finish` — not all stages reliably fire ``on_complete`` (e.g.
card creation early-returns on an empty batch), so completion must not be
inferred from per-stage events.
"""

from __future__ import annotations

from anki_miner.interfaces.progress import ProgressCallback


class StageWeightedProgress:
    """Map per-stage ``ProgressCallback`` events onto a single 0->100 sweep.

    Args:
        inner: The real callback to forward to (e.g. ``GUIProgressCallback``).
        weights: One weight per reporting stage, in the order the stages fire
            ``on_start``. Need not sum to 1.0 — they are normalized.
    """

    def __init__(self, inner: ProgressCallback, weights: list[float]) -> None:
        self._inner = inner
        total = sum(weights)
        self._weights = [w / total for w in weights] if total > 0 else []
        self._stage = -1
        self._cursor = 0.0  # fraction completed by finished stages
        self._stage_total = 0
        self._started = False
        self._finished = False

    def _stage_weight(self) -> float:
        if 0 <= self._stage < len(self._weights):
            return self._weights[self._stage]
        return 0.0

    def on_start(self, total: int, description: str) -> None:
        # Each stage begins with its own on_start; advance to the next band.
        self._stage += 1
        self._stage_total = max(total, 0)
        # Emit a single global on_start only on the very first stage so the GUI
        # configures the bar (max=100) once and never resets between stages.
        if not self._started:
            self._inner.on_start(100, description)
            self._started = True

    def on_progress(self, current: int, item_description: str) -> None:
        frac = current / self._stage_total if self._stage_total > 0 else 1.0
        frac = min(max(frac, 0.0), 1.0)
        pct = int((self._cursor + self._stage_weight() * frac) * 100)
        self._inner.on_progress(min(pct, 100), item_description)

    def on_complete(self) -> None:
        # A stage finished: bank its band so the next stage starts where it
        # left off. The whole-episode completion is signalled via finish(),
        # not here — some stages skip on_complete (empty card batch).
        self._cursor += self._stage_weight()

    def on_error(self, item_description: str, error_message: str) -> None:
        self._inner.on_error(item_description, error_message)

    def finish(self) -> None:
        """End the whole-episode sweep: snap the bar to 100% and complete.

        Idempotent and safe to call even if no stage ever started.
        """
        if self._finished or not self._started:
            return
        self._finished = True
        self._inner.on_progress(100, "")
        self._inner.on_complete()
