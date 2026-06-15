"""Default filesystem locations for anki_miner."""

import os
from pathlib import Path

ANKI_MINER_HOME: Path = Path(os.environ.get("ANKI_MINER_HOME") or Path.home() / ".anki_miner")
