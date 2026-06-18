"""Per-run artifact directory for the E2E harness.

Every harness run gets a timestamped :class:`RunDir` under ``E2EConfig.runs_root``.
Drivers write ordered screenshots (``01_<name>.png``, ``02_...``) and JSON dumps
into it as a step trail, so a failed run leaves a readable on-disk record an
agent / CI artifact upload can inspect.

Retention is deliberately dumb: the directory is ALWAYS kept (never auto-pruned).
Keeping every run is the simplest thing that gives a failure something to point
at; pruning is a caller concern, not this module's.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QApplication, QWidget

__all__ = ["RunDir"]


class RunDir:
    """A single run's artifact directory (timestamped, append-only).

    Args:
        runs_root: Parent directory (``E2EConfig.runs_root``) the run dir is
            created under. Created if absent.
        label: Optional suffix appended to the timestamped dir name so several
            runs in the same second / same report stay distinguishable.

    For cross-process soak, children write their artifacts into the PARENT's
    run dir instead of creating their own: use :meth:`adopt` to wrap an
    existing directory as a ``RunDir`` without creating a new timestamped
    subdir.

    Retention policy: every run dir is ALWAYS kept (never auto-pruned).
    Pruning is a caller concern; the harness never deletes artifact dirs.
    """

    def __init__(self, runs_root: Path, label: str = "") -> None:
        # datetime.now() is fine here — this is harness bookkeeping, not the
        # production code path the "no wall-clock in tests" rule guards.
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        name = f"{stamp}_{label}" if label else stamp
        self._path = Path(runs_root) / name
        self._path.mkdir(parents=True, exist_ok=True)
        # Monotonic step counter so saved files sort in capture order regardless
        # of the (possibly identical-to-the-second) timestamp.
        self._step = 0

    @classmethod
    def adopt(cls, path: Path) -> RunDir:
        """Wrap an *existing* directory as a RunDir WITHOUT creating a new subdir.

        The step counter starts at 0 (independent of any files already in the
        directory). Use this when a child process needs to write its artifacts
        into a parent's run dir rather than creating its own timestamped dir.

        Args:
            path: The exact directory to use. Created (with parents) if absent.

        Returns:
            A :class:`RunDir` whose :attr:`path` is ``path`` itself.
        """
        instance = cls.__new__(cls)
        instance._path = Path(path)
        instance._path.mkdir(parents=True, exist_ok=True)
        instance._step = 0
        return instance

    @property
    def path(self) -> Path:
        """The run directory (always retained; safe to read after a failure)."""
        return self._path

    def _next_prefix(self) -> str:
        """Return the next zero-padded ordering prefix (``01``, ``02``, ...)."""
        self._step += 1
        return f"{self._step:02d}"

    def save_png(self, name: str, widget_or_pixmap: Any) -> Path:
        """Grab a widget (or save an already-grabbed pixmap) to ``NN_<name>.png``.

        Accepts either a :class:`~PyQt6.QtWidgets.QWidget` (``.grab()`` is called
        after forcing a layout/paint pass) or a :class:`~PyQt6.QtGui.QPixmap`
        (saved as-is). The monotonic ``NN_`` prefix keeps steps ordered.

        Args:
            name: Base file name (``.png`` appended if absent).
            widget_or_pixmap: The widget to grab or a ready pixmap.

        Returns:
            Path to the written PNG.
        """
        if not name.endswith(".png"):
            name = f"{name}.png"
        out = self._path / f"{self._next_prefix()}_{name}"

        if isinstance(widget_or_pixmap, QWidget):
            widget = widget_or_pixmap
            # Ensure the widget has had a layout + paint pass before grabbing,
            # otherwise an offscreen widget can grab blank. processEvents flushes
            # the deferred layout/paint Qt would otherwise only run on show.
            widget.adjustSize()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            pixmap = widget.grab()
        else:
            pixmap = widget_or_pixmap

        pixmap.save(str(out), "PNG")
        return out

    def save_json(self, name: str, obj: Any) -> Path:
        """Write ``obj`` as pretty JSON to ``NN_<name>.json``.

        Args:
            name: Base file name (``.json`` appended if absent).
            obj: Any JSON-serializable object. ``default=str`` coerces stray
                non-serializable values (e.g. ``Path``) rather than raising.

        Returns:
            Path to the written JSON file.
        """
        if not name.endswith(".json"):
            name = f"{name}.json"
        out = self._path / f"{self._next_prefix()}_{name}"
        out.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return out
