"""Canonical home-isolation primitives, shared by ``tests/conftest.py`` and the
standalone (non-pytest) E2E runner.

Why this module exists: ``anki_miner.config.paths`` reads
``ANKI_MINER_HOME = Path(os.environ.get("ANKI_MINER_HOME") or Path.home()/".anki_miner")``
ONCE at import. Many modules then do ``from ...paths import ANKI_MINER_HOME``,
each snapshotting an INDEPENDENT binding — so setting the env var alone does not
redirect already-imported modules; you must patch each module's own bound name.
A past pytest run overwrote the user's real ``~/.anki_miner/gui_config.json``;
the isolation fixtures + the ``guard_real_home`` tripwire are the fix.

The standalone E2E runner runs OUTSIDE pytest and never sees ``conftest.py``, so
these primitives live here as the ONE source of truth both consume. conftest's
fixtures call into this module; the runner imports ``set_test_home`` /
``guard_real_home`` directly.
"""

import importlib
from contextlib import contextmanager
from pathlib import Path

# Each entry: (module dotted path, attribute name, value-builder taking the tmp
# home Path). Only patched when the module imports and the attribute already
# exists, so an upstream refactor (renamed/removed binding) silently no-ops
# instead of erroring inside the fixture. We patch each module's OWN bound name
# because ``from ...paths import ANKI_MINER_HOME`` creates an independent binding
# that patching ``paths.ANKI_MINER_HOME`` alone would not update.
HOME_CONSUMERS = (
    ("anki_miner.config.paths", "ANKI_MINER_HOME", lambda home: home),
    ("anki_miner.config.config", "ANKI_MINER_HOME", lambda home: home),
    ("anki_miner.gui.utils.service_factory", "ANKI_MINER_HOME", lambda home: home),
    ("anki_miner.gui.utils.recent_files", "ANKI_MINER_HOME", lambda home: home),
    (
        "anki_miner.gui.widgets.panels.dictionary_settings_panel",
        "ANKI_MINER_HOME",
        lambda home: home,
    ),
    ("anki_miner.gui.controllers.zip_import_flow", "ANKI_MINER_HOME", lambda home: home),
    ("anki_miner.services.history_service", "DEFAULT_DB_PATH", lambda home: home / "history.db"),
)


def apply_home_patches(tmp_home: Path) -> list[tuple[object, str, object]]:
    """Redirect every imported home snapshot + ``GUIConfigManager.CONFIG_FILE`` to
    ``tmp_home``; return ``(obj, attr, original)`` triples for exact restoration.

    Patches each module's OWN bound name (see ``HOME_CONSUMERS``) because
    ``from ...paths import ANKI_MINER_HOME`` snapshots an independent binding that
    patching ``paths.ANKI_MINER_HOME`` alone would not update. Missing module/attr is
    skipped so an upstream rename no-ops instead of erroring inside the fixture.
    """
    saved: list[tuple[object, str, object]] = []
    for mod_path, attr, build in HOME_CONSUMERS:
        try:
            module = importlib.import_module(mod_path)
        except Exception:
            continue
        if not hasattr(module, attr):
            continue
        saved.append((module, attr, getattr(module, attr)))
        setattr(module, attr, build(tmp_home))

    # GUIConfigManager.CONFIG_FILE is a CLASS attribute, not a module global.
    try:
        cm_module = importlib.import_module("anki_miner.gui.utils.config_manager")
        gcm_cls = getattr(cm_module, "GUIConfigManager", None)
    except Exception:
        gcm_cls = None
    if gcm_cls is not None and hasattr(gcm_cls, "CONFIG_FILE"):
        saved.append((gcm_cls, "CONFIG_FILE", gcm_cls.CONFIG_FILE))
        gcm_cls.CONFIG_FILE = tmp_home / "gui_config.json"
    return saved


def restore_home_patches(saved: list[tuple[object, str, object]]) -> None:
    """Undo ``apply_home_patches`` in reverse so stacked patches unwind cleanly."""
    for obj, attr, original in reversed(saved):
        setattr(obj, attr, original)


def set_test_home(tmp_home: Path) -> list[tuple[object, str, object]]:
    """Point the whole process at ``tmp_home``: set ``ANKI_MINER_HOME`` env var AND
    apply the in-process binding patches; return the saved triples for restoration.

    The standalone runner sets the env var pre-import so freshly-imported modules
    snapshot the tmp home directly. This call is belt-and-suspenders AFTER that:
    it re-asserts the env var and patches any module that imported a home snapshot
    transitively before the runner got to it. Safe to call when the env var is
    already set (it is idempotently overwritten to ``str(tmp_home)``).

    Imported here, not at module top, to avoid a hard ``os`` dependency on import
    of callers that only want the pure patch helpers.
    """
    import os

    os.environ["ANKI_MINER_HOME"] = str(tmp_home)
    return apply_home_patches(tmp_home)


def snapshot_home(root: Path) -> dict[str, tuple[int, float]]:
    """Map every file under ``root`` to ``(size, mtime_ns)``; empty if absent."""
    snap: dict[str, tuple[int, float]] = {}
    if not root.exists():
        return snap
    for path in root.rglob("*"):
        if path.is_file():
            try:
                st = path.stat()
            except OSError:
                continue
            snap[str(path)] = (st.st_size, st.st_mtime_ns)
    return snap


@contextmanager
def guard_real_home(watched: Path):
    """Tripwire context manager: raise ``AssertionError`` if ``watched`` is mutated.

    Snapshots ``watched`` on enter and compares on exit; a changed file set
    (created / deleted / modified) raises with the same detail-message style as
    conftest's ``_guard_real_home`` fixture. Lets the standalone runner replicate
    the real-home tripwire without pytest.

    It never creates the dir: absent-before/absent-after is fine.
    """
    before = snapshot_home(watched)
    yield
    after = snapshot_home(watched)

    if before != after:
        added = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        modified = sorted(p for p in (set(before) & set(after)) if before[p] != after[p])
        parts = []
        if added:
            parts.append(f"created: {added}")
        if removed:
            parts.append(f"deleted: {removed}")
        if modified:
            parts.append(f"modified: {modified}")
        raise AssertionError(
            f"Test mutated the real anki_miner home {watched}! " + "; ".join(parts) + ". "
            "A module is writing to the user's real data dir — add its home-path "
            "snapshot to HOME_CONSUMERS in tests/_home_isolation.py."
        )
