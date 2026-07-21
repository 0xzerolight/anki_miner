"""Default filesystem locations for anki_miner."""

import os
import tempfile
from pathlib import Path


def _default_anki_miner_home() -> Path:
    try:
        return Path.home() / ".anki_miner"
    except Exception:
        return Path(tempfile.gettempdir()) / ".anki_miner"


ANKI_MINER_HOME: Path = Path(os.environ.get("ANKI_MINER_HOME") or _default_anki_miner_home())
