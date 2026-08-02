"""Tests for the app-wide file-dialog wrappers.

The wrappers open every picker with ``QFileDialog.open()`` and deliver the
result through a keyword-only ``on_done``. Two things here are load-bearing and
easy to lose:

* ``exec()`` is never called. The static ``QFileDialog.get*`` helpers went
  through it, and for a native dialog that runs the shell enumeration on the
  GUI thread — the Issue #100 freeze.
* The Qt state each wrapper sets. ``getSaveFileName`` gave us ``AcceptSave``
  for free; a bare ``QFileDialog`` constructor defaults to ``AcceptOpen``, so
  without an explicit call the save pickers silently become open browsers.

Module-global state discipline: tests flip the native flag ONLY via
``monkeypatch.setattr`` so no value leaks across tests under xdist loadfile.
"""

import ast
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFileDialog, QWidget

from anki_miner.config import create_default_config
from anki_miner.gui.app import _seed_file_dialog_mode
from anki_miner.gui.utils import file_dialogs


class _StubDialog:
    """Stands in for QFileDialog, recording the state each wrapper set."""

    FileMode = QFileDialog.FileMode
    AcceptMode = QFileDialog.AcceptMode
    Option = QFileDialog.Option
    instances: list["_StubDialog"] = []

    def __init__(self, parent=None, caption="", directory="", filter=""):  # noqa: A002
        self.parent_arg = parent
        self.caption = caption
        self.directory = directory
        self.filter = filter
        self.file_mode = None
        self.accept_mode = None
        self.opts = QFileDialog.Option(0)
        self.attributes: dict = {}
        self.opened = False
        self.exec_calls = 0
        self.deleted = False
        self._selection: list[str] = []
        self._finished_slots: list = []
        type(self).instances.append(self)

    # --- the surface file_dialogs touches -----------------------------------
    def setFileMode(self, mode):  # noqa: N802
        self.file_mode = mode

    def setAcceptMode(self, mode):  # noqa: N802
        self.accept_mode = mode

    def setOptions(self, options):  # noqa: N802
        self.opts = options

    def options(self):
        return self.opts

    def setAttribute(self, attr, on):  # noqa: N802
        self.attributes[attr] = on

    def testAttribute(self, attr):  # noqa: N802
        return self.attributes.get(attr, False)

    def selectedFiles(self):  # noqa: N802
        return list(self._selection)

    def open(self):
        self.opened = True

    def exec(self):
        self.exec_calls += 1
        return 0

    def exec_(self):
        self.exec_calls += 1
        return 0

    def deleteLater(self):  # noqa: N802
        self.deleted = True

    def reject(self):
        self.fire(QDialog.DialogCode.Rejected)

    @property
    def finished(self):
        return self

    def connect(self, slot):
        self._finished_slots.append(slot)

    # --- test driving --------------------------------------------------------
    def fire(self, code, selection=()):
        self._selection = list(selection)
        for slot in list(self._finished_slots):
            slot(int(code))


@pytest.fixture
def stub(monkeypatch):
    _StubDialog.instances = []
    monkeypatch.setattr(file_dialogs, "QFileDialog", _StubDialog)
    monkeypatch.setattr(file_dialogs, "_live", [])
    return _StubDialog


def test_default_injects_dont_use_native(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", False)

    file_dialogs.pick_open_file(None, "c", "d", "f", on_done=lambda _v: None)
    file_dialogs.pick_open_files(None, "c", "d", "f", on_done=lambda _v: None)
    file_dialogs.pick_save_file(None, "c", "d", "f", on_done=lambda _v: None)

    assert len(stub.instances) == 3
    for dialog in stub.instances:
        assert dialog.opts & QFileDialog.Option.DontUseNativeDialog


def test_native_mode_omits_the_flag(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)

    file_dialogs.pick_open_file(None, "c", "d", "f", on_done=lambda _v: None)
    file_dialogs.pick_save_file(None, "c", "d", "f", on_done=lambda _v: None)

    for dialog in stub.instances:
        assert not dialog.opts & QFileDialog.Option.DontUseNativeDialog


def test_directory_preserves_show_dirs_only(stub, monkeypatch):
    # Qt's default for a directory dialog is ShowDirsOnly; an explicit
    # setOptions REPLACES that default, so the wrapper must re-add it in BOTH
    # modes or folder pickers silently start listing files.
    monkeypatch.setattr(file_dialogs, "_use_native", False)
    file_dialogs.pick_directory(None, "c", "d", on_done=lambda _v: None)
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    file_dialogs.pick_directory(None, "c", "d", on_done=lambda _v: None)

    non_native, native = stub.instances
    assert non_native.opts & QFileDialog.Option.ShowDirsOnly
    assert non_native.opts & QFileDialog.Option.DontUseNativeDialog
    assert native.opts & QFileDialog.Option.ShowDirsOnly
    assert not native.opts & QFileDialog.Option.DontUseNativeDialog
    assert native.file_mode == QFileDialog.FileMode.Directory


def test_each_wrapper_sets_its_qt_modes(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)

    file_dialogs.pick_open_file(None, on_done=lambda _v: None)
    file_dialogs.pick_open_files(None, on_done=lambda _v: None)
    file_dialogs.pick_save_file(None, on_done=lambda _v: None)
    file_dialogs.pick_directory(None, on_done=lambda _v: None)

    one, many, save, folder = stub.instances
    assert one.file_mode == QFileDialog.FileMode.ExistingFile
    assert many.file_mode == QFileDialog.FileMode.ExistingFiles
    assert folder.file_mode == QFileDialog.FileMode.Directory
    # The save picker is the one that silently degrades: without AcceptSave the
    # user gets an open browser with no filename field.
    assert save.file_mode == QFileDialog.FileMode.AnyFile
    assert save.accept_mode == QFileDialog.AcceptMode.AcceptSave


def test_pickers_never_call_exec(stub, monkeypatch):
    # The whole point of the change: exec() is what ran the native Windows
    # dialog on the GUI thread (Issue #100).
    monkeypatch.setattr(file_dialogs, "_use_native", True)

    file_dialogs.pick_open_file(None, on_done=lambda _v: None)
    file_dialogs.pick_open_files(None, on_done=lambda _v: None)
    file_dialogs.pick_save_file(None, on_done=lambda _v: None)
    file_dialogs.pick_directory(None, on_done=lambda _v: None)

    assert [d.exec_calls for d in stub.instances] == [0, 0, 0, 0]
    assert all(d.opened for d in stub.instances)


def test_pickers_decline_quit_on_close(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    file_dialogs.pick_open_file(None, on_done=lambda _v: None)
    assert stub.instances[0].testAttribute(Qt.WidgetAttribute.WA_QuitOnClose) is False


def test_accept_delivers_the_selection_once(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    seen: list = []
    file_dialogs.pick_open_file(None, on_done=seen.append)

    stub.instances[0].fire(QDialog.DialogCode.Accepted, ["/tmp/a.txt"])

    assert seen == ["/tmp/a.txt"]
    assert file_dialogs._live == []
    assert stub.instances[0].deleted


def test_cancel_delivers_the_empty_value(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    single: list = []
    multi: list = []
    file_dialogs.pick_open_file(None, on_done=single.append)
    file_dialogs.pick_open_files(None, on_done=multi.append)

    stub.instances[0].fire(QDialog.DialogCode.Rejected)
    stub.instances[1].fire(QDialog.DialogCode.Rejected)

    # Mirrors what the blocking wrappers returned, so call sites keep their
    # existing "if not path: return" guards.
    assert single == [""]
    assert multi == [[]]


def test_cancel_all_pickers_runs_no_continuation(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    seen: list = []
    file_dialogs.pick_open_file(None, on_done=seen.append)

    file_dialogs.cancel_all_pickers()

    assert seen == []
    assert file_dialogs._live == []


def test_parent_dialog_closing_cancels_its_picker(monkeypatch, qtbot):
    # A picker outlives the dialog that spawned it: the Known Words manager can
    # be dismissed while its import picker is open, and the continuation would
    # then import into a screen the user already closed. Uses a real
    # QFileDialog so the parent's finished->cancel wiring is exercised for real.
    monkeypatch.setattr(file_dialogs, "_use_native", False)
    monkeypatch.setattr(file_dialogs, "_live", [])
    parent = QDialog()
    qtbot.addWidget(parent)
    seen: list = []

    dialog = file_dialogs.pick_open_file(parent, "c", "", "", on_done=seen.append)
    assert dialog.isVisible()

    parent.reject()

    assert seen == []
    assert file_dialogs._live == []


def test_dead_parent_skips_the_continuation(stub, monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    seen: list = []

    class _Dead:
        """A parent whose C++ object is gone: widget_alive() says False."""

    monkeypatch.setattr(file_dialogs, "widget_alive", lambda w: not isinstance(w, _Dead))
    entry = file_dialogs._Picker(stub(), _Dead(), seen.append, "")
    file_dialogs._live.append(entry)

    file_dialogs._finish(entry, int(QDialog.DialogCode.Accepted))

    assert seen == []


def test_a_live_continuation_still_raises(stub, monkeypatch):
    # The import flows re-raise deliberately after releasing their token. A
    # blanket suppress(RuntimeError) around on_done would swallow that.
    monkeypatch.setattr(file_dialogs, "_use_native", True)

    def boom(_value):
        raise RuntimeError("worker construction failed")

    file_dialogs.pick_open_file(None, on_done=boom)

    with pytest.raises(RuntimeError, match="worker construction failed"):
        stub.instances[0].fire(QDialog.DialogCode.Accepted, ["/tmp/a.txt"])


def test_seed_from_config(monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(file_dialogs, "_use_native", False)
    _seed_file_dialog_mode(replace(create_default_config(), use_native_file_dialogs=True))
    assert file_dialogs.use_native() is True

    _seed_file_dialog_mode(replace(create_default_config(), use_native_file_dialogs=False))
    assert file_dialogs.use_native() is False


def test_seed_tolerates_none_config(monkeypatch):
    monkeypatch.setattr(file_dialogs, "_use_native", True)
    _seed_file_dialog_mode(None)
    assert file_dialogs.use_native() is True


def test_native_is_the_default():
    # Deliberately unpatched: every other test in this file sets the flag by
    # hand, so without this the flip would be invisible to the suite.
    assert create_default_config().use_native_file_dialogs is True
    assert file_dialogs.use_native() is True


def test_no_direct_qfiledialog_call_sites_remain():
    # Regression net: every picker must route through the wrappers, so the
    # non-blocking contract (Issue #100) covers new code too. Matching import
    # statements rather than the bare symbol — the name also appears in prose
    # (e.g. session_state's docstring), and a raw-text scan would fail on that.
    package_root = Path(file_dialogs.__file__).resolve().parents[2]
    offenders = []
    for path in sorted(package_root.rglob("*.py")):
        if path.name == "file_dialogs.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "QFileDialog" not in source:
            continue
        relative = str(path.relative_to(package_root))
        if "QFileDialog.get" in source:
            offenders.append(f"{relative}: static QFileDialog.get* helper")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {alias.name for alias in node.names}
            else:
                continue
            if any(name.split(".")[-1] == "QFileDialog" for name in names):
                offenders.append(f"{relative}: imports QFileDialog directly")
    assert offenders == []


def test_widget_alive_tolerates_non_sip_objects(qtbot):
    from anki_miner.gui.utils.qt_helpers import widget_alive

    widget = QWidget()
    qtbot.addWidget(widget)
    assert widget_alive(widget) is True
    # Test doubles aren't sip-tracked; isdeleted would reject them outright.
    assert widget_alive(object()) is True  # type: ignore[arg-type]
