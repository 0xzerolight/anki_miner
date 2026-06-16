#!/usr/bin/env python3
"""Thin shim the agent invokes to drive the E2E harness.

Sets ANKI_MINER_HOME to the isolated E2E test home (mirroring E2EConfig's default
``~/.anki_miner_e2e``, honoring ANKI_MINER_E2E_HOME), forces offscreen Qt, and
keeps temp media for soak — ALL before importing anki_miner / tests.e2e — then
delegates to ``tests.e2e.runner.main``. Excluded from the wheel (scripts/), so it
never ships. Stdlib-only at module top.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_HOME = os.environ.get("ANKI_MINER_E2E_HOME") or str(Path.home() / ".anki_miner_e2e")
os.environ["ANKI_MINER_HOME"] = _TEST_HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["ANKI_MINER_KEEP_TEMP"] = "1"
sys.path.insert(0, str(_REPO_ROOT))

from tests.e2e.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
