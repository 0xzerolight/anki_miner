"""Machine-local UI session state: window geometry, last route, last folders.

Stored in ``ui_state.ini`` beside ``gui_config.json``, deliberately NOT inside
it. Where the window sat, which screen you were on and which folders you last
browsed are facts about *this machine*, so under D7 they must never travel in an
exported settings file or a profile sidecar. A separate file is what guarantees
that rather than an exclusion list anyone can forget to update:
:meth:`GUIConfigManager.export_config` and :mod:`.profile_store` only ever
serialise :class:`AnkiMinerConfig`, which none of these keys are part of.

What is deliberately absent is as load-bearing as what is here: no scroll
offsets, no field text, no form drafts. Resuming a half-typed form from three
days ago is confusing rather than helpful, so every page opens at the top with
whatever its config says.

Nothing here is ever required to succeed. Every read returns a benign default
and every write is best-effort — a read-only home, a full disk or a corrupt INI
must not stop the app from closing or degrade anything except this convenience.

The INI path is resolved from ``GUIConfigManager.CONFIG_FILE`` **at call time**
and never snapshotted at import: ``tests/_home_isolation.py`` retargets that
class attribute per test, so a module-level snapshot would keep writing into the
user's real ``~/.anki_miner`` and trip the ``guard_real_home`` tripwire.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSettings

from anki_miner.gui.utils.config_manager import GUIConfigManager

logger = logging.getLogger(__name__)

FILENAME = "ui_state.ini"

_GEOMETRY_KEY = "window/geometry"
_CURATOR_GEOMETRY_KEY = "curator/geometry"
_CURATOR_SPLIT_KEY = "curator/split_main"
_CURATOR_SIDE_GROUP = "curator/split_side"
_CURATOR_CLIP_EXPANDED_KEY = "curator/clip_expanded"
_MAIN_TAB_KEY = "navigation/main_tab"
_SUBTAB_GROUP = "navigation/subtab"
_DIRECTORY_GROUP = "directories"


def state_file() -> Path:
    """Return the ``ui_state.ini`` path, computed fresh on every call.

    Derived from ``GUIConfigManager.CONFIG_FILE`` rather than from an
    ``ANKI_MINER_HOME`` snapshot — see the module docstring.
    """
    return GUIConfigManager.CONFIG_FILE.parent / FILENAME


def _open(*, for_write: bool = False) -> QSettings | None:
    """Return a ``QSettings`` over the INI, or ``None`` if it cannot be opened."""
    try:
        path = state_file()
        if for_write:
            path.parent.mkdir(parents=True, exist_ok=True)
        return QSettings(str(path), QSettings.Format.IniFormat)
    except Exception:
        logger.debug("Could not open the UI session state file", exc_info=True)
        return None


def _commit(settings: QSettings) -> None:
    """Flush ``settings`` and log — never raise — when the write did not land."""
    settings.sync()
    status = settings.status()
    if status != QSettings.Status.NoError:
        logger.warning("UI session state could not be saved (QSettings status %s)", status)


# ---------------------------------------------------------------------------
# Window geometry
# ---------------------------------------------------------------------------


def _as_blob(raw: object) -> QByteArray | None:
    """Return ``raw`` as a byte blob, or ``None`` if it is not one.

    A value of the wrong type (a hand-edited or truncated INI) is reported as
    absent. A blob that is the right *type* but not decodable is returned
    as-is: Qt's ``restoreGeometry`` / ``restoreState`` are the authority on
    validity and answer ``False``, which is the caller's cue to fall back.
    """
    if isinstance(raw, QByteArray):
        return raw
    if isinstance(raw, bytes | bytearray):
        return QByteArray(bytes(raw))
    return None


def _read_blob(settings: QSettings, key: str, what: str) -> QByteArray | None:
    """Read one blob-valued key, reporting an unreadable one as absent."""
    try:
        return _as_blob(settings.value(key))
    except Exception:
        logger.debug("Unreadable %s in the UI session state", what, exc_info=True)
        return None


def load_geometry() -> QByteArray | None:
    """Return the stored ``saveGeometry()`` blob, or ``None`` if there is none."""
    settings = _open()
    if settings is None:
        return None
    return _read_blob(settings, _GEOMETRY_KEY, "window geometry")


def save_geometry(blob: QByteArray) -> None:
    """Store a ``saveGeometry()`` blob (size, position, maximised/full-screen)."""
    settings = _open(for_write=True)
    if settings is None:
        return
    try:
        settings.setValue(_GEOMETRY_KEY, blob)
        _commit(settings)
    except Exception:
        logger.warning("Could not save the window geometry", exc_info=True)


# ---------------------------------------------------------------------------
# Word curator layout
# ---------------------------------------------------------------------------


def load_curator_clip_expanded() -> bool:
    """Whether the curator's audio clip strip was left expanded.

    Its own key rather than a fourth element of the layout tuple: the strip is
    not a splitter position and does not vary with the pane composition the
    layout blobs are keyed by. Defaults to collapsed — the strip is a niche
    repair, so a user who has never opened it never sees it.
    """
    settings = _open()
    if settings is None:
        return False
    try:
        return bool(settings.value(_CURATOR_CLIP_EXPANDED_KEY, False, type=bool))
    except Exception:
        logger.warning("Could not read the curator audio clip state", exc_info=True)
        return False


def save_curator_clip_expanded(expanded: bool) -> None:
    """Remember whether the curator's audio clip strip is expanded."""
    settings = _open(for_write=True)
    if settings is None:
        return
    try:
        settings.setValue(_CURATOR_CLIP_EXPANDED_KEY, expanded)
        _commit(settings)
    except Exception:
        logger.warning("Could not save the curator audio clip state", exc_info=True)


def load_curator_layout(side_key: str) -> tuple[QByteArray | None, QByteArray | None, QByteArray | None]:
    """Return ``(geometry, main_split, side_split)`` for the word curator.

    The curator is rebuilt for every item in a mining queue, so without this a
    user who widens the video column re-widens it once per word. Any part that
    is missing or unreadable comes back as ``None``, which the caller reads as
    "compute the default".

    Args:
        side_key: The side column's pane composition (see
            :func:`save_curator_layout`). A blob saved under a different
            composition is not returned.
    """
    settings = _open()
    if settings is None:
        return (None, None, None)
    return (
        _read_blob(settings, _CURATOR_GEOMETRY_KEY, "curator geometry"),
        _read_blob(settings, _CURATOR_SPLIT_KEY, "curator split"),
        _read_blob(settings, f"{_CURATOR_SIDE_GROUP}/{side_key}", "curator side split"),
    )


def save_curator_layout(
    geometry: QByteArray,
    main_split: QByteArray | None,
    side_split: QByteArray | None,
    *,
    side_key: str,
) -> None:
    """Store the curator's window geometry and splitter positions.

    Args:
        geometry: A ``saveGeometry()`` blob.
        main_split: A ``QSplitter.saveState()`` blob for the table/side split,
            or ``None`` on a table-only curator.
        side_split: A ``saveState()`` blob for the side column, or ``None``
            when that column holds a single pane.
        side_key: The side column's pane composition, e.g.
            ``"player+sentences+dict"``. Load-bearing rather than cosmetic:
            ``QSplitter.restoreState`` does not reject a blob listing more
            panes than the splitter has -- it applies the prefix -- so a video
            curator's three sizes would silently mis-size a manga curator's
            two. Keying by composition means each shape restores only its own.
    """
    settings = _open(for_write=True)
    if settings is None:
        return
    try:
        settings.setValue(_CURATOR_GEOMETRY_KEY, geometry)
        if main_split is not None:
            settings.setValue(_CURATOR_SPLIT_KEY, main_split)
        if side_split is not None:
            settings.setValue(f"{_CURATOR_SIDE_GROUP}/{side_key}", side_split)
        _commit(settings)
    except Exception:
        logger.warning("Could not save the word curator layout", exc_info=True)


# ---------------------------------------------------------------------------
# Navigation route
# ---------------------------------------------------------------------------


def load_route() -> tuple[str | None, dict[str, str]]:
    """Return ``(main_tab_key, {container_key: subtab_key})`` as last saved.

    Both parts are stable identifiers, never translated labels or stack
    indices. Unknown keys are the caller's to ignore; this layer only reports
    what is on disk.
    """
    settings = _open()
    if settings is None:
        return None, {}
    try:
        main_tab = settings.value(_MAIN_TAB_KEY)
        subtabs: dict[str, str] = {}
        settings.beginGroup(_SUBTAB_GROUP)
        for key in settings.childKeys():
            value = settings.value(key)
            if isinstance(value, str) and value:
                subtabs[key] = value
        settings.endGroup()
    except Exception:
        logger.debug("Unreadable navigation state in the UI session state", exc_info=True)
        return None, {}
    return (main_tab if isinstance(main_tab, str) and main_tab else None), subtabs


def save_route(main_tab: str | None, subtabs: Mapping[str, str]) -> None:
    """Store the current main tab and *every* container's current sub-tab.

    All containers are written, not just the visible one, so visiting Reading
    and then switching to Tools before quitting does not erase where Reading
    was left.
    """
    settings = _open(for_write=True)
    if settings is None:
        return
    try:
        if main_tab:
            settings.setValue(_MAIN_TAB_KEY, main_tab)
        else:
            settings.remove(_MAIN_TAB_KEY)
        # Rewrite the group wholesale: setValue alone would leave a container
        # that no longer exists behind as a stale row forever.
        settings.remove(_SUBTAB_GROUP)
        for container, subtab in subtabs.items():
            if container and subtab:
                settings.setValue(f"{_SUBTAB_GROUP}/{container}", subtab)
        _commit(settings)
    except Exception:
        logger.warning("Could not save the navigation state", exc_info=True)


# ---------------------------------------------------------------------------
# Per-workflow accepted folders
# ---------------------------------------------------------------------------


def remembered_directory(history_key: str | None) -> str | None:
    """Return the folder last *accepted* for ``history_key``, or ``None``.

    The stored string is returned verbatim. Emptiness is judged on the stripped
    text, but a path is never handed to the filesystem stripped — a folder whose
    name legitimately ends in a space must survive the round trip (see
    ``FileSelector.path_or_none``). Existence is not checked here; that belongs
    to :func:`~anki_miner.gui.utils.dialog_paths.resolve_start_dir`, which owns
    the whole fallback chain.
    """
    if not history_key:
        return None
    settings = _open()
    if settings is None:
        return None
    try:
        value = settings.value(f"{_DIRECTORY_GROUP}/{history_key}")
    except Exception:
        logger.debug("Unreadable remembered folder for %s", history_key, exc_info=True)
        return None
    if isinstance(value, str) and value.strip():
        return value
    return None


def remember_accepted_path(history_key: str | None, path: str, *, file_mode: bool) -> None:
    """Record the folder implied by a path the user just ACCEPTED in a dialog.

    For an accepted file the parent folder is remembered; for an accepted
    folder, that folder itself. Call this only for a non-empty ``QFileDialog``
    return: typing, dropping, auto-fill and a cancelled dialog are not choices
    about where the user keeps that kind of file, and must not move the anchor.
    """
    if not history_key or not path.strip():
        return
    settings = _open(for_write=True)
    if settings is None:
        return
    try:
        chosen = Path(path)
        directory = chosen.parent if file_mode else chosen
        settings.setValue(f"{_DIRECTORY_GROUP}/{history_key}", str(directory))
        _commit(settings)
    except Exception:
        logger.warning("Could not remember the folder for %s", history_key, exc_info=True)
