"""Test that ExportDialog's Browse button opens the save dialog at home, not ''."""

from pathlib import Path

import pytest

import anki_miner.gui.widgets.dialogs.export_dialog as export_dialog_mod
from anki_miner.gui.widgets.dialogs.export_dialog import ExportDialog


@pytest.fixture
def dlg(test_config, qtbot):
    dialog = ExportDialog(words=[], config=test_config)
    qtbot.addWidget(dialog)
    return dialog


class TestBrowseStartDir:
    def test_browse_opens_save_dialog_at_home(self, dlg, monkeypatch):
        """_on_browse must pass a path under home as the initial-path arg to the save picker."""
        captured: dict = {}

        def fake_save(parent, title, initial_path, file_filter, *a, on_done, **kw):
            captured["initial"] = initial_path
            on_done("")  # user cancels

        monkeypatch.setattr(export_dialog_mod.file_dialogs, "pick_save_file", fake_save)
        dlg._on_browse()

        home = str(Path.home())
        initial = captured.get("initial", "")
        assert initial.startswith(home), f"Expected initial path under home={home!r}; got {initial!r}"
        assert initial != "", "initial path must not be empty"

    def test_browse_preserves_suggested_filename(self, dlg, monkeypatch):
        """The default filename (e.g. words.csv) must be preserved in the initial path."""
        captured: dict = {}

        def fake_save(parent, title, initial_path, file_filter, *a, on_done, **kw):
            captured["initial"] = initial_path
            on_done("")

        monkeypatch.setattr(export_dialog_mod.file_dialogs, "pick_save_file", fake_save)
        dlg._on_browse()

        initial = captured.get("initial", "")
        # CSV is the default format; suggested filename is words.csv
        assert "words.csv" in initial, f"Suggested filename must be in initial path; got {initial!r}"
