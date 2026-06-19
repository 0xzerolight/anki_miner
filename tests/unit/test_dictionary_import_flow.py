"""Tests for DictionaryImportFlow dialog start-directory (F12).

The Add/Re-import Yomitan-zip dialogs should open at the dictionaries dir
(``config.dicts_root``) instead of falling back to the home directory.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QWidget

from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow

MOD = "anki_miner.gui.controllers.dictionary_import_flow"


def _make_flow(dicts_root: Path) -> DictionaryImportFlow:
    cfg = MagicMock()
    cfg.dicts_root = dicts_root
    return DictionaryImportFlow(
        parent=MagicMock(spec=QWidget),
        panel=MagicMock(),
        get_config=lambda: cfg,
        persist_chain=MagicMock(),
        notify_config_changed=MagicMock(),
    )


def test_add_dict_dialog_defaults_to_dicts_dir():
    dicts_root = Path("/home/u/.anki_miner/dicts")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{MOD}.resolve_start_dir", return_value=str(dicts_root)) as rsd,
        patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=("", "")),
    ):
        flow.add_dict()  # empty selection → early return after the dialog

    rsd.assert_called_once()
    assert rsd.call_args.kwargs.get("default_dir") == dicts_root


def test_reimport_dict_dialog_defaults_to_dicts_dir():
    dicts_root = Path("/home/u/.anki_miner/dicts")
    flow = _make_flow(dicts_root)

    with (
        patch(f"{MOD}.resolve_start_dir", return_value=str(dicts_root)) as rsd,
        patch(f"{MOD}.QFileDialog.getOpenFileName", return_value=("", "")),
    ):
        flow.reimport_dict("some-dict")  # empty selection → early return

    rsd.assert_called_once()
    assert rsd.call_args.kwargs.get("default_dir") == dicts_root
