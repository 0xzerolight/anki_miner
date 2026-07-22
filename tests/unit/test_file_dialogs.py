"""Tests for the app-wide file-dialog wrappers (Issue #100 freeze fix).

The wrappers default every picker to Qt's non-native dialog (the OS-native
Windows dialog froze the GUI thread inside comdlg32 on the reporter's machine)
and honor ``use_native_file_dialogs`` to restore native pickers.

Module-global state discipline: tests flip the native flag ONLY via
``monkeypatch.setattr`` so no value leaks across tests under xdist loadfile.
"""

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from anki_miner.config import create_default_config
from anki_miner.gui.app import _seed_file_dialog_mode
from anki_miner.gui.utils import file_dialogs


class _CapturingDialog:
    """Stands in for QFileDialog; records the options each static call got."""

    Option = QFileDialog.Option
    calls: list[QFileDialog.Option]

    def __init_subclass__(cls) -> None:  # pragma: no cover - not subclassed
        raise TypeError

    @classmethod
    def reset(cls) -> None:
        cls.calls = []

    @classmethod
    def getOpenFileName(cls, parent, caption, directory, filter, options):  # noqa: A002,N802
        cls.calls.append(options)
        return "", ""

    @classmethod
    def getOpenFileNames(cls, parent, caption, directory, filter, options):  # noqa: A002,N802
        cls.calls.append(options)
        return [], ""

    @classmethod
    def getSaveFileName(cls, parent, caption, directory, filter, options):  # noqa: A002,N802
        cls.calls.append(options)
        return "", ""

    @classmethod
    def getExistingDirectory(cls, parent, caption, directory, options):  # noqa: N802
        cls.calls.append(options)
        return ""


def _capture(monkeypatch) -> type[_CapturingDialog]:
    _CapturingDialog.reset()
    monkeypatch.setattr(file_dialogs, "QFileDialog", _CapturingDialog)
    return _CapturingDialog


def test_default_injects_dont_use_native(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(file_dialogs, "_use_native", False)

    file_dialogs.get_open_file_name(None, "c", "d", "f")
    file_dialogs.get_open_file_names(None, "c", "d", "f")
    file_dialogs.get_save_file_name(None, "c", "d", "f")

    assert len(cap.calls) == 3
    for options in cap.calls:
        assert options & QFileDialog.Option.DontUseNativeDialog


def test_native_mode_omits_the_flag(monkeypatch):
    cap = _capture(monkeypatch)
    monkeypatch.setattr(file_dialogs, "_use_native", True)

    file_dialogs.get_open_file_name(None, "c", "d", "f")
    file_dialogs.get_save_file_name(None, "c", "d", "f")

    for options in cap.calls:
        assert not options & QFileDialog.Option.DontUseNativeDialog


def test_existing_directory_preserves_show_dirs_only(monkeypatch):
    # Qt's default for getExistingDirectory is ShowDirsOnly; an explicit
    # options= REPLACES that default, so the wrapper must re-add it in BOTH
    # modes or folder pickers silently start listing files.
    cap = _capture(monkeypatch)

    monkeypatch.setattr(file_dialogs, "_use_native", False)
    file_dialogs.get_existing_directory(None, "c", "d")
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    file_dialogs.get_existing_directory(None, "c", "d")

    non_native, native = cap.calls
    assert non_native & QFileDialog.Option.ShowDirsOnly
    assert non_native & QFileDialog.Option.DontUseNativeDialog
    assert native & QFileDialog.Option.ShowDirsOnly
    assert not native & QFileDialog.Option.DontUseNativeDialog


def test_seed_from_config(monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", False)
    from dataclasses import replace

    _seed_file_dialog_mode(replace(create_default_config(), use_native_file_dialogs=True))
    assert file_dialogs.use_native() is True

    _seed_file_dialog_mode(replace(create_default_config(), use_native_file_dialogs=False))
    assert file_dialogs.use_native() is False


def test_seed_tolerates_none_config(monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", False)
    _seed_file_dialog_mode(None)
    assert file_dialogs.use_native() is False


def test_no_direct_qfiledialog_call_sites_remain():
    # Regression net: every picker must route through the wrappers so the
    # non-native default (Issue #100) covers new code too.
    package_root = Path(file_dialogs.__file__).resolve().parents[2]
    offenders = []
    for path in package_root.rglob("*.py"):
        if path.name == "file_dialogs.py":
            continue
        if "QFileDialog.get" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == []
