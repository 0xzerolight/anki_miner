"""ExportDialog runs the export off the GUI thread (S9).

The file I/O in ``_on_export`` must be dispatched via ``run_off_thread`` so a
large export never freezes the GUI. The export button is disabled in flight and
success/error surface on the completion callbacks.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QMessageBox

from anki_miner.gui.widgets.dialogs import export_dialog as export_dialog_mod
from anki_miner.gui.widgets.dialogs.export_dialog import ExportDialog


@pytest.fixture
def dlg(test_config, qtbot):
    dialog = ExportDialog(words=[], config=test_config)
    qtbot.addWidget(dialog)
    return dialog


@pytest.fixture
def capture_off_thread(monkeypatch):
    """Replace run_off_thread with a capturing stub (no real worker thread)."""
    captured: dict = {}

    def fake(parent, work, on_done, on_error=None, *, error_prefix=""):
        captured["parent"] = parent
        captured["work"] = work
        captured["on_done"] = on_done
        captured["on_error"] = on_error
        return MagicMock()

    monkeypatch.setattr(export_dialog_mod, "run_off_thread", fake)
    return captured


def _stub_service(monkeypatch) -> list[str]:
    """Record synchronous ExportService calls; each returns a fake count."""
    calls: list[str] = []
    for name in ("export_csv", "export_tsv", "export_vocab_list"):
        monkeypatch.setattr(
            export_dialog_mod.ExportService,
            name,
            lambda self, *a, _n=name, **k: (calls.append(_n) or 7),
        )
    return calls


class TestExportOffThread:
    def test_export_dispatched_off_thread_button_disabled(self, dlg, tmp_path, monkeypatch, capture_off_thread):
        calls = _stub_service(monkeypatch)
        dlg._output_path = tmp_path / "words.csv"
        dlg._export_btn.setEnabled(True)

        dlg._on_export()

        # Dispatched via run_off_thread, not run inline on the GUI thread.
        assert "work" in capture_off_thread, "export must go through run_off_thread"
        assert calls == [], "the blocking export must not run on the GUI thread"
        assert dlg._export_btn.isEnabled() is False, "button disabled in flight"

        # The deferred work is what actually calls the service.
        result = capture_off_thread["work"]()
        assert calls == ["export_csv"]
        assert result == 7

    def test_success_callback_surfaces_info_and_accepts(self, dlg, tmp_path, monkeypatch, capture_off_thread):
        _stub_service(monkeypatch)
        infos: list[tuple[str, str]] = []
        monkeypatch.setattr(
            QMessageBox, "information", lambda *a, **k: infos.append((a[1], a[2])) or QMessageBox.StandardButton.Ok
        )
        accepted: list[bool] = []
        monkeypatch.setattr(dlg, "accept", lambda: accepted.append(True))

        dlg._output_path = tmp_path / "words.csv"
        dlg._on_export()
        capture_off_thread["on_done"](7)

        assert infos, "success must surface an info dialog"
        assert accepted == [True], "dialog must accept on success"

    def test_error_callback_surfaces_critical_and_reenables(self, dlg, tmp_path, monkeypatch, capture_off_thread):
        _stub_service(monkeypatch)
        criticals: list[tuple[str, str]] = []
        monkeypatch.setattr(
            QMessageBox, "critical", lambda *a, **k: criticals.append((a[1], a[2])) or QMessageBox.StandardButton.Ok
        )

        dlg._output_path = tmp_path / "words.csv"
        dlg._on_export()
        assert dlg._export_btn.isEnabled() is False

        capture_off_thread["on_error"]("disk full")

        assert criticals, "failure must surface a critical dialog"
        assert "disk full" in criticals[-1][1]
        assert dlg._export_btn.isEnabled() is True, "button re-enabled after error"

    def test_no_output_path_is_noop(self, dlg, monkeypatch, capture_off_thread):
        _stub_service(monkeypatch)
        dlg._output_path = None
        dlg._on_export()
        assert "work" not in capture_off_thread
