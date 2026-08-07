"""Whether the embedded video surface may be constructed.

The curator and the subtitle viewer both embed a ``QOpenGLWidget``
(:class:`~anki_miner.gui.widgets.mpv_video_widget.MpvVideoWidget`) that libmpv
renders into. Constructing it brings up a real GL context, which on a host whose
GL driver cannot load cleanly **aborts the process** — no Python traceback,
nothing catchable, because nothing in-process can catch a ``SIGABRT``. The one
field report of this was a packaging fault (the Linux bundle's ``libstdc++``
shadowing the host's, fixed in ``packaging/linux-launcher.sh``), so this module
is a diagnostic lever rather than a supported configuration.

``preview_enabled()`` is consulted at the one construction site
(``SubtitlePlayerWidget._setup_ui``). Off means the GL widget is never built —
audio still plays through a ``vo=null`` core, so the player degrades rather than
disappears.

``ANKI_MINER_NO_VIDEO_PREVIEW=1`` is the only way to turn it off, deliberately:
a support reply can hand it to a user whose app dies before the settings screen
is reachable, and it never mutates their config. There is no matching setting,
and there should not be one — see the notice in ``SubtitlePlayerWidget``, which
names the variable rather than pointing at a checkbox that does not exist.

The decision is module-level state rather than a parameter for the same reason
``file_dialogs`` keeps its own: the construction site has no environment access
of its own to consult, and resolving once keeps the answer stable for the life
of the process.
"""

from __future__ import annotations

import os

#: Forces the preview off. Fails *open* on any value other than the documented
#: truthy set, so a stray empty string cannot silently disable video for someone
#: who exported it by accident.
ENV_VAR = "ANKI_MINER_NO_VIDEO_PREVIEW"

_TRUTHY = frozenset({"1", "true", "yes", "on"})

_env_off: bool | None = None


def _env_disabled() -> bool:
    """Whether the env override is set, resolved once per process."""
    global _env_off
    if _env_off is None:
        _env_off = os.environ.get(ENV_VAR, "").strip().lower() in _TRUTHY
    return _env_off


def preview_enabled() -> bool:
    """Whether a GL video surface may be constructed."""
    return not _env_disabled()


def _reset_for_tests() -> None:
    """Drop the process-lifetime cache of the env decision."""
    global _env_off
    _env_off = None
