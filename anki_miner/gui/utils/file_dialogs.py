"""App-wide file-dialog wrappers. Non-blocking, native by default.

Every picker in the app goes through here, and every one of them is opened
with ``QFileDialog.open()`` — never ``exec()`` and never a static
``QFileDialog.get*`` helper. That distinction is the whole point of this
module, and Issue #100 is why.

Issue #100: the stall watchdog caught the GUI thread frozen inside the Windows
NATIVE ``QFileDialog.getOpenFileName``. The static helper runs through
``QDialog::exec()``, which for a native dialog reaches
``QWindowsDialogHelperBase::exec()`` and runs Explorer's shell/cloud
enumeration **on the GUI thread**; on a bad network that call never returns and
the app cannot recover, because its own event loop is parked inside comdlg32.
The 2026-07 fix blamed native dialogs and forced Qt's built-in one everywhere.
That was the wrong culprit: the blocking *call shape* was.

``open()`` returns immediately and never enters ``QDialog::exec()``, so the
calling-thread path is unreachable. On Windows the platform helper instead runs
the native dialog on its own ``QWindowsDialogThread`` and marshals the result
back through a queued connection. A wedged shell call then parks that thread
instead of the app. So native dialogs are the default again, on every platform,
and ``config.use_native_file_dialogs`` turns them off for anyone who prefers
Qt's own (it also follows the app's QSS themes).

Two consequences of ``open()`` that every caller inherits:

* **The result arrives by callback, not by return.** ``on_done`` is
  keyword-only and always fires exactly once — with the empty value (``""`` or
  ``[]``) when the user cancels — so a call site keeps whatever
  ``if not path: return`` guard it already had.
* **The picker is a real top-level window that outlives its parent.** It
  declines ``WA_QuitOnClose`` (it must not hold the app open), it is cancelled
  when its parent dialog closes under it, and ``cancel_all_pickers`` kills the
  lot at shutdown.

A cancelled picker deliberately does **not** invoke ``on_done``. Its
continuations start import workers and touch panels that are already being torn
down; there is nothing useful left to continue into. Do not "fix" this.

The native/non-native choice is module-level state rather than a parameter:
many call sites (e.g. ``FileSelector``) have no config access, and the setting
is a pure UI preference. Seeded at startup (``gui/app.py``) and re-seeded on
every config commit (``MainWindow.update_config``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFileDialog, QWidget

from anki_miner.gui.utils.qt_helpers import widget_alive

_use_native = True


def set_use_native(enabled: bool) -> None:
    """Set whether native OS file dialogs are used (default True)."""
    global _use_native
    _use_native = bool(enabled)


def use_native() -> bool:
    """Return whether native OS file dialogs are enabled."""
    return _use_native


_NO_OPTIONS = QFileDialog.Option(0)


def _options(base: QFileDialog.Option = _NO_OPTIONS) -> QFileDialog.Option:
    if _use_native:
        return base
    return base | QFileDialog.Option.DontUseNativeDialog


class _Picker:
    """One live picker: the dialog, who owns it, and what to do with the result."""

    __slots__ = ("dialog", "parent", "on_done", "empty", "silenced")

    def __init__(
        self,
        dialog: QFileDialog,
        parent: QWidget | None,
        on_done: Callable[[Any], None],
        empty: Any,
    ) -> None:
        self.dialog = dialog
        self.parent = parent
        self.on_done = on_done
        self.empty = empty
        self.silenced = False

    def value(self, code: int) -> Any:
        """The picked value, or the empty value when the user cancelled."""
        if code != int(QDialog.DialogCode.Accepted):
            return self.empty
        selected = list(self.dialog.selectedFiles())
        if isinstance(self.empty, list):
            return selected
        return selected[0] if selected else self.empty

    def cancel(self) -> None:
        """Close this picker without running its continuation."""
        self.silenced = True
        if widget_alive(self.dialog):
            self.dialog.reject()
        else:
            _drop(self)


# Live pickers, held so Python cannot garbage-collect a dialog that is still on
# screen. Entries leave on ``finished``, on parent destruction, or via
# ``cancel_all_pickers``.
_live: list[_Picker] = []


def _drop(entry: _Picker) -> None:
    if entry in _live:
        _live.remove(entry)


def _finish(entry: _Picker, code: int) -> None:
    _drop(entry)
    silenced = entry.silenced
    value = None if silenced else entry.value(code)
    if widget_alive(entry.dialog):
        entry.dialog.deleteLater()
    if silenced:
        return
    # A queued ``finished`` can outlive the widget that asked for the picker.
    # Check liveness rather than wrapping the call in suppress(RuntimeError):
    # the continuations re-raise deliberately (see the import flows), and a
    # blanket suppress would swallow that.
    if entry.parent is not None and not widget_alive(entry.parent):
        return
    entry.on_done(value)


def _launch(
    dialog: QFileDialog,
    parent: QWidget | None,
    on_done: Callable[[Any], None],
    empty: Any,
) -> QFileDialog:
    """Register, wire the lifetime signals, and show ``dialog`` non-modally."""
    dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
    entry = _Picker(dialog, parent, on_done, empty)
    _live.append(entry)
    dialog.finished.connect(lambda code: _finish(entry, code))
    if parent is not None:
        # The dialog is a child, so it dies with the parent and never emits
        # ``finished``; drop the entry so the registry cannot go stale.
        parent.destroyed.connect(lambda *_: _drop(entry))
        if isinstance(parent, QDialog):
            # A picker outlives the dialog that spawned it. Without this, the
            # Known Words manager can be dismissed while its import picker is
            # still up, and the continuation then imports into a dead screen.
            parent.finished.connect(lambda *_: entry.cancel())
    dialog.open()
    return dialog


def cancel_all_pickers() -> None:
    """Close every live picker without running any continuation.

    Called from ``MainWindow.closeEvent``: an open picker would otherwise
    outlive the main window, and its continuation would land in a
    half-torn-down application.
    """
    for entry in list(_live):
        entry.cancel()


def pick_open_file(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
    *,
    on_done: Callable[[str], None],
) -> QFileDialog:
    """Ask for one existing file. ``on_done`` gets the path, or "" if cancelled."""
    dialog = QFileDialog(parent, caption, directory, filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setOptions(_options())
    return _launch(dialog, parent, on_done, "")


def pick_open_files(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
    *,
    on_done: Callable[[list[str]], None],
) -> QFileDialog:
    """Ask for several existing files. ``on_done`` gets the list, or [] if cancelled."""
    dialog = QFileDialog(parent, caption, directory, filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    dialog.setOptions(_options())
    return _launch(dialog, parent, on_done, [])


def pick_save_file(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
    *,
    on_done: Callable[[str], None],
) -> QFileDialog:
    """Ask where to save. ``on_done`` gets the path, or "" if cancelled.

    ``AcceptSave`` is not optional: a bare ``QFileDialog`` constructor defaults
    to ``AcceptOpen``, which the static ``getSaveFileName`` used to set for us.
    Without it the user gets an open-only browser with no filename field.
    """
    dialog = QFileDialog(parent, caption, directory, filter)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setOptions(_options())
    return _launch(dialog, parent, on_done, "")


def pick_directory(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    *,
    on_done: Callable[[str], None],
) -> QFileDialog:
    """Ask for a folder. ``on_done`` gets the path, or "" if cancelled.

    ``ShowDirsOnly`` has to be re-added: it is Qt's default for a directory
    dialog, and an explicit ``setOptions`` replaces that default rather than
    extending it.
    """
    dialog = QFileDialog(parent, caption, directory)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setOptions(_options(QFileDialog.Option.ShowDirsOnly))
    return _launch(dialog, parent, on_done, "")
