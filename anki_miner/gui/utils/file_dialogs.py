"""App-wide file-dialog wrappers defaulting to Qt's non-native dialogs.

Issue #100: the stall watchdog caught the GUI thread frozen inside the Windows
NATIVE ``QFileDialog.getOpenFileName`` (Explorer shell/cloud enumeration can
hang indefinitely on a bad network, and the app cannot recover because its own
GUI thread is parked inside comdlg32). Qt's built-in dialog never touches that
machinery — and follows the app's QSS themes — so it is the default on every
platform. ``config.use_native_file_dialogs`` restores native dialogs for users
who prefer them.

The native/non-native choice is module-level state rather than a parameter:
many call sites (e.g. ``FileSelector``) have no config access, and the setting
is a pure UI preference. Seeded at startup (``gui/app.py``) and re-seeded on
every config commit (``MainWindow.update_config``).

All wrappers mirror the ``QFileDialog.get*`` static signatures used in this
codebase (parent, caption, directory[, filter]). ``get_existing_directory``
must re-add ``ShowDirsOnly``: Qt's default for that dialog, replaced (not
extended) the moment an explicit ``options=`` is passed.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFileDialog, QWidget

_use_native = False


def set_use_native(enabled: bool) -> None:
    """Set whether native OS file dialogs are used (default False)."""
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


def get_open_file_name(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
) -> tuple[str, str]:
    """``QFileDialog.getOpenFileName`` honoring the app dialog mode."""
    return QFileDialog.getOpenFileName(parent, caption, directory, filter, options=_options())


def get_open_file_names(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
) -> tuple[list[str], str]:
    """``QFileDialog.getOpenFileNames`` honoring the app dialog mode."""
    return QFileDialog.getOpenFileNames(parent, caption, directory, filter, options=_options())


def get_save_file_name(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
    filter: str = "",  # noqa: A002 - mirrors the Qt signature
) -> tuple[str, str]:
    """``QFileDialog.getSaveFileName`` honoring the app dialog mode."""
    return QFileDialog.getSaveFileName(parent, caption, directory, filter, options=_options())


def get_existing_directory(
    parent: QWidget | None,
    caption: str = "",
    directory: str = "",
) -> str:
    """``QFileDialog.getExistingDirectory`` honoring the app dialog mode."""
    return QFileDialog.getExistingDirectory(
        parent,
        caption,
        directory,
        _options(QFileDialog.Option.ShowDirsOnly),
    )
