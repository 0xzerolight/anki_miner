"""Whether the embedded video surface may be constructed, and the crash sentinel.

The curator and the subtitle viewer both embed a ``QOpenGLWidget``
(:class:`~anki_miner.gui.widgets.mpv_video_widget.MpvVideoWidget`) that libmpv
renders into. Constructing it brings up a real GL context, which on some hosts
**aborts the process** — a field report on Arch + AppImage died inside
``QOpenGLWidget.__init__`` on every single video mining run, with no Python
traceback, because the host GL driver was dlopened against a mismatched C++
runtime. Nothing in-process can catch a ``SIGABRT``, so the only defences are
not constructing the widget and noticing afterwards that we did.

This module owns both:

* **The gate.** ``preview_enabled()`` is consulted at the one construction site
  (``SubtitlePlayerWidget._setup_ui``). Off means the GL widget is never built —
  audio still plays through a ``vo=null`` core, so the player degrades rather
  than disappears.
* **The sentinel.** :func:`arm_crash_marker` writes a durable file immediately
  before GL bring-up and :func:`clear_crash_marker` removes it once a frame has
  actually been painted. A marker found at the next launch means the previous
  process died in between, and the boot step turns the preview off for the user
  (see ``MainWindow._maybe_auto_disable_video_preview``).

The enabled/disabled choice is module-level state rather than a parameter for
the same reason ``file_dialogs`` keeps its own: the construction site has no
config access. Seeded at startup (``gui/app.py``, before ``QApplication`` so no
widget can precede it) and re-seeded on every config commit
(``MainWindow.update_config``).

Precedence is **env > config**. ``ANKI_MINER_NO_VIDEO_PREVIEW=1`` wins over the
setting and is never written back, so it stays a diagnostic lever rather than a
state change: a support reply can hand it to a user whose app dies before the
settings screen is reachable, without mutating their config.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Forces the preview off regardless of config. Fails *open* on any value other
#: than the documented truthy set, so a stray empty string cannot silently
#: disable video for someone who exported it by accident.
ENV_VAR = "ANKI_MINER_NO_VIDEO_PREVIEW"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_enabled = True
_env_off: bool | None = None
_armed = False


def _env_disabled() -> bool:
    """Whether the env override is set, resolved once per process."""
    global _env_off
    if _env_off is None:
        _env_off = os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY
    return _env_off


def seed_from_config(config: Any) -> None:
    """Adopt ``config.video_preview_enabled`` as the current setting."""
    global _enabled
    _enabled = bool(getattr(config, "video_preview_enabled", True))


def preview_enabled() -> bool:
    """Whether a GL video surface may be constructed."""
    if _env_disabled():
        return False
    return _enabled


def suppressed_reason() -> Literal["env", "setting", ""]:
    """Why the preview is off — ``"env"``, ``"setting"``, or ``""`` when it is on."""
    if _env_disabled():
        return "env"
    if not _enabled:
        return "setting"
    return ""


# ------------------------------------------------------------ crash sentinel


def _marker_path() -> Path:
    # Imported lazily: this module is pulled in by mpv_video_widget, which must
    # stay cheap to import, and runtime_state drags in the config manager.
    from anki_miner.gui.utils.runtime_state import video_preview_marker_path

    return video_preview_marker_path()


def arm_crash_marker(**fields: Any) -> None:
    """Write the sentinel durably. Once per process; never raises.

    ``fsync`` on both the file and its parent directory is load-bearing: the
    failure this guards against aborts the process milliseconds later, and a
    marker still sitting in the page cache would be lost with it.
    """
    global _armed
    if _armed:
        return
    _armed = True
    payload = {"pid": os.getpid(), "python": sys.version.split()[0], **fields}
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(path.parent)
    except Exception:
        # A sentinel we cannot write is a lost diagnostic, never a reason to
        # block the widget the user asked for. Broader than OSError on purpose:
        # this runs INSIDE MpvVideoWidget.__init__, before super(), so anything
        # escaping here breaks the curator for everybody to protect a
        # diagnostic — and _marker_path's lazy import can raise ImportError.
        logger.debug("could not arm the video-preview crash marker", exc_info=True)


def _fsync_dir(directory: Path) -> None:
    """fsync a directory entry so the marker's *name* is durable too (POSIX)."""
    if not hasattr(os, "O_DIRECTORY"):  # pragma: no cover - Windows
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def clear_crash_marker() -> None:
    """Remove the sentinel. Idempotent; never raises."""
    try:
        _marker_path().unlink(missing_ok=True)
    except Exception:
        # Same never-raises contract as arm_crash_marker: this one is called
        # from paintGL, where an exception reaches Qt's paint machinery.
        logger.debug("could not clear the video-preview crash marker", exc_info=True)


def consume_crash_marker() -> dict[str, Any] | None:
    """Read and delete the sentinel, returning its payload if one was present.

    Corrupt contents still consume the marker and report the crash — the file's
    *existence* is the signal; its JSON is only the detail line. Returning it
    twice, or wedging boot on a truncated write, would both be worse.
    """
    try:
        path = _marker_path()
    except Exception:
        # Resolving the path is not free (it imports the config manager), and a
        # boot step that cannot answer "did we crash?" must answer "no".
        logger.debug("could not resolve the video-preview crash marker", exc_info=True)
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.debug("could not delete the consumed crash marker", exc_info=True)
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"detail": raw[:500]}
    return payload if isinstance(payload, dict) else {"detail": raw[:500]}


def _reset_for_tests() -> None:
    """Drop the process-lifetime caches (env decision, armed-once flag)."""
    global _enabled, _env_off, _armed
    _enabled = True
    _env_off = None
    _armed = False
