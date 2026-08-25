"""Child-side ffsubsync runner: one library call per process, one JSON verdict.

Reached only through :func:`anki_miner.gui.launch.main`'s ``--ffsubsync-child``
dispatch, which re-enters this application because ffsubsync 0.5.1 ships no
``__main__`` and a frozen bundle has no interpreter to run one with.

Contract with the parent (:mod:`.ffsubsync_engine`):

* **stdout carries exactly one thing**: the verdict line. ffsubsync's own
  logging already goes to stderr (its package ``__init__`` says so), but its
  VLC/GUI progress modes ``print()`` and any dependency is free to as well, so
  the whole run happens under ``redirect_stdout(sys.stderr)``.
* **The exit code says whether there is a verdict, not what it is.** ffsubsync's
  ``retval`` rides in the JSON: a low-quality reject writes the *original*
  subtitles and returns 0, so an exit code alone cannot tell the parent's
  quality gate a rejected sync from a good one.
* Values are coerced to JSON-native types — ffsubsync's offset can arrive as a
  numpy scalar, which :mod:`json` will not serialize.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from collections.abc import Callable, Sequence
from typing import Any

__all__ = ["CHILD_FLAG", "main"]

#: Dispatch flag. Mirrored as a literal in ``gui/launch.py`` (which must not
#: import this module at boot — it would drag the whole services package in);
#: the two are pinned equal by test.
CHILD_FLAG = "--ffsubsync-child"

#: The keys the parent's quality gate consumes, and how to make each JSON-safe.
_VERDICT_KEYS: dict[str, Callable[[Any], Any]] = {
    "retval": int,
    "sync_was_successful": bool,
    "offset_seconds": float,
    "framerate_scale_factor": float,
}


def _sync(argv: Sequence[str]) -> dict[str, Any]:
    # Deferred import: ffsubsync pulls numpy and its VAD stack, and this module
    # is reached from the application's entry script.
    from ffsubsync.ffsubsync import make_parser
    from ffsubsync.ffsubsync import run as ffsubsync_run

    result = ffsubsync_run(make_parser().parse_args(list(argv)))
    return {key: None if (value := result.get(key)) is None else coerce(value) for key, coerce in _VERDICT_KEYS.items()}


def main(argv: Sequence[str]) -> int:
    """Run ffsubsync over *argv* and print its verdict as one JSON line."""
    try:
        with contextlib.redirect_stdout(sys.stderr):
            verdict = _sync(argv)
    # noqa: BLE001 — a crash (incl. argparse's SystemExit on a rejected argv) is a verdict-less exit, never a traceback the parent has to interpret
    except (Exception, SystemExit):
        logging.getLogger(__name__).warning("ffsubsync child failed", exc_info=True)
        return 1

    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    return 0
