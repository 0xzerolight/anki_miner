"""Shared harness for the file-processing tool tabs (``_ToolTabBase`` subclasses).

One fake queue worker and the helpers every tool-tab test file used to copy.
The base contract itself is tested once, in ``test_tool_tab_contract.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from anki_miner.config import AnkiMinerConfig

#: The writability refusal of Generate, Retime, Condense and Audiobook Sync
#: lives on ``_ToolTabBase``, so their ``os.access`` is patched through the
#: base module. ``os`` is one shared module, so the patch also reaches
#: Download's own (differently worded) check.
OS_ACCESS = "anki_miner.gui.widgets._tool_tab_base.os.access"


class FakeToolWorker:
    """Stand-in for any FileQueueWorker a tool tab starts.

    Signals are per-instance MagicMocks, so ``connect()`` on two instances stays
    independent. ``file_note`` covers Retime and Condense; the three probe
    signals let the same fake stand in for Download's probe workers.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.file_started = MagicMock()
        self.file_progress = MagicMock()
        self.file_finished = MagicMock()
        self.file_note = MagicMock()
        self.file_skipped = MagicMock()
        self.queue_finished = MagicMock()
        self.error = MagicMock()
        self.tracks_probed = MagicMock()
        self.playlist_resolved = MagicMock()
        self.probe_error = MagicMock()
        self.finished = MagicMock()  # native QThread.finished (lifecycle release)
        self.deleteLater = MagicMock()
        self._started = False
        self._cancelled = False

    def start(self) -> None:
        self._started = True

    def cancel(self) -> None:
        self._cancelled = True

    def isRunning(self) -> bool:
        return self._started and not self._cancelled

    def wait(self, *args: Any) -> bool:
        return True


def capture_slots(signal_mock: MagicMock) -> list:
    """Record every slot connected to a FakeToolWorker signal; return the list."""
    slots: list = []
    original_connect = signal_mock.connect

    def _capture(slot):
        slots.append(slot)
        return original_connect(slot)

    signal_mock.connect = _capture
    return slots


def make_config(tmp_path: Path) -> AnkiMinerConfig:
    """A minimal config with writable paths under ``tmp_path``."""
    return AnkiMinerConfig(asr_models_root=tmp_path / "asr_models", media_temp_folder=tmp_path / "tmp")
