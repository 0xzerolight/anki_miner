"""Tests for the resource-download dialog wrapper — release handshake + results.

The modal worker loop itself is exercised by test_resource_download_worker; here
we cover the pure wiring around it: the pre-run resource-release handshake and
the per-item results text (replaced / could-not-remove lines).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from anki_miner.config import create_default_config
from anki_miner.gui.widgets.dialogs import resource_download_dialog as mod
from anki_miner.gui.widgets.dialogs.resource_download_dialog import (
    _show_results_dialog,
    run_resource_download,
)
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
)

MOD = "anki_miner.gui.widgets.dialogs.resource_download_dialog"


def test_release_false_aborts_without_downloading():
    config = create_default_config()
    parent = MagicMock()
    ran_modal = MagicMock()

    with (
        patch(f"{MOD}.QMessageBox.warning") as warn,
        patch.object(mod, "_run_download_modal", ran_modal),
    ):
        result = run_resource_download(parent, config, release_resources=lambda: False)

    assert result is None
    ran_modal.assert_not_called()  # nothing touched disk
    warn.assert_called_once()


def test_release_true_proceeds_to_modal():
    config = create_default_config()
    parent = MagicMock()

    with (
        patch.object(mod, "_run_download_modal", return_value=None) as ran_modal,
        patch(f"{MOD}.QMessageBox.warning"),
    ):
        run_resource_download(parent, config, release_resources=lambda: True)

    ran_modal.assert_called_once()


def _results_body(summary: ResourceDownloadSummary) -> str:
    captured: dict[str, str] = {}

    class _FakeBox:
        def __init__(self, *_a, **_kw):
            pass

        def setIcon(self, *_a):
            pass

        def setWindowTitle(self, *_a):
            pass

        def setText(self, text):
            captured["text"] = text

        def exec(self):
            return 0

    with patch(f"{MOD}.QMessageBox", MagicMock(side_effect=_FakeBox)):
        _show_results_dialog(MagicMock(), summary)
    return captured["text"]


def test_results_dialog_lists_replaced_copy():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        removed_dicts=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = _results_body(ResourceDownloadSummary(results=[result]))
    assert "Replaced older copy" in body
    assert "Jitendex.org [2025-11-05]" in body


def test_results_dialog_surfaces_failed_removal():
    result = ResourceDownloadResult(
        "jitendex",
        "dict",
        "Jitendex",
        "u",
        ok=True,
        detail="100 entries",
        dict_id="jitendex",
        failed_removals=[("jitendex-org-2025-11-05", "Jitendex.org [2025-11-05]")],
    )
    body = _results_body(ResourceDownloadSummary(results=[result]))
    assert "Could not remove older copy" in body
    assert "Jitendex.org [2025-11-05]" in body
