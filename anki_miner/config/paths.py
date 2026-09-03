"""Default filesystem locations for anki_miner."""

import os
import tempfile
from pathlib import Path

# Why the home directory could not be used, or None when it resolved normally.
# A failing Path.home() silently relocates config, logs, dictionaries and
# caches into the system temp directory, where they also vanish on reboot. The
# reason is recorded here so the session-start receipt can report it instead of
# leaving the user with an install that looks empty for no stated cause.
HOME_FALLBACK_REASON: str | None = None


def _default_anki_miner_home() -> Path:
    global HOME_FALLBACK_REASON
    try:
        return Path.home() / ".anki_miner"
    except Exception as exc:  # noqa: BLE001 — bucket: home resolution; any failure must still yield a usable dir
        HOME_FALLBACK_REASON = f"{type(exc).__name__}: {exc}"
        return Path(tempfile.gettempdir()) / ".anki_miner"


ANKI_MINER_HOME: Path = Path(os.environ.get("ANKI_MINER_HOME") or _default_anki_miner_home())
