"""The isolation contract — imported FIRST by every atlas driver process.

These drivers boot the *real* ``anki_miner.gui.app.main()``. That is the whole
point (a bare ``MainWindow`` has no tabs, so it proves nothing about the screens
users see), and it is also why every step below exists: without them the run
reaches the operator's real Anki collection, real dictionary store and real
``~/.anki_miner``.

Order is load-bearing:

1. :func:`bootstrap` sets ``ANKI_MINER_HOME`` and ``sys.path`` **before** any
   ``anki_miner`` import, because module-level constants capture the home once.
2. Config ``Path`` fields are redirected by *introspection*, not a hand list — a
   hand list silently misses fields added since it was written.
3. ``ankiconnect_url`` is pinned to a fake server; both ``AnkiMinerConfig`` and
   the e2e config default to the operator's real collection.
4. ``run_startup_store_recovery`` is neutralised at ``app.py``'s **own** binding.
   ``app.py`` does ``from ... import run_startup_store_recovery``, so patching
   the services module looks installed and is inert — the worst failure mode for
   the single most destructive path in the app.
5. Every blocking modal is patched to its safe branch *and logged*, because
   ``QApplication.quit()`` cannot escape a nested modal event loop: one
   unpatched dialog hangs the run until the backstop timer.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

#: Repository root, derived from this file so the harness works from any
#: checkout or worktree. The 2026-07-25 original hard-coded one audit worktree.
REPO = Path(__file__).resolve().parents[2]

REAL_HOME = Path.home() / ".anki_miner"

#: Throwaway home for the run. Overridable so two cells can be run side by side.
SCRATCH_HOME = Path(os.environ.get("ANKI_MINER_ATLAS_HOME", str(Path.home() / ".anki_miner_uiatlas")))

#: The shortcut service writes here, and it is outside every home redirect.
DESKTOP_DIR = Path.home() / ".local" / "share" / "applications"

_STORE_RECOVERY_FIRED = {"value": False}
_MODAL_LOG: list[dict] = []


def bootstrap(home: Path | None = None) -> None:
    """Set the home env var + ``sys.path`` BEFORE ``anki_miner`` is importable."""
    target = home or SCRATCH_HOME
    os.environ["ANKI_MINER_HOME"] = str(target)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))


def redirect_config_paths(cfg, home: Path):
    """Redirect every ``Path``-typed config field under the real home to ``home``.

    Introspection, not a hard-coded list: this survives config drift and picks up
    the optional ``Path | None`` fields a hand list omits.
    """
    import dataclasses

    updates = {}
    for f in dataclasses.fields(cfg):
        if "Path" not in str(f.type):
            continue
        value = getattr(cfg, f.name)
        if value is None:
            continue
        try:
            rel = Path(value).relative_to(REAL_HOME)
        except (ValueError, TypeError):
            continue  # not under the real home: a system binary or a user file
        updates[f.name] = home / rel
    return dataclasses.replace(cfg, **updates) if updates else cfg


def assert_isolated(cfg, home: Path) -> None:
    """Hard gate: no ``Path`` field may still resolve under the real home."""
    import dataclasses

    leaks = []
    for f in dataclasses.fields(cfg):
        if "Path" not in str(f.type):
            continue
        value = getattr(cfg, f.name)
        if value is None:
            continue
        try:
            Path(value).relative_to(REAL_HOME)
        except (ValueError, TypeError):
            continue
        leaks.append(f"{f.name}={value}")
    if leaks:
        raise AssertionError("Config still points at the REAL home: " + ", ".join(leaks))
    if home == REAL_HOME:
        raise AssertionError("Scratch home is the real home")


def preflight_instance_lock() -> None:
    """Refuse to run while a live Anki Miner holds the real instance lock.

    ``tryLock(0)``, never ``isLocked()`` — ``isLocked()`` reports only whether
    *this* object holds the lock, so it would always be false here. Runs before
    the home guards because ``tryLock`` rewrites a stale lock file.
    """
    from PyQt6.QtCore import QLockFile

    lock = QLockFile(str(REAL_HOME / "instance.lock"))
    if lock.tryLock(0):
        lock.unlock()
        return
    raise SystemExit(
        f"[gate] Anki Miner appears to be running (lock info: {lock.getLockInfo()}). "
        "Close it before running the atlas."
    )


def _record_modal(source: str, title, text) -> None:
    _MODAL_LOG.append({"source": source, "title": str(title), "text": str(text)[:400]})


def dump_modal_log(path: Path) -> None:
    path.write_text("\n".join(json.dumps(m, ensure_ascii=False) for m in _MODAL_LOG), encoding="utf-8")


def modal_log() -> list[dict]:
    return list(_MODAL_LOG)


@contextmanager
def patched_modals():
    """Patch every blocking modal entry point to its SAFE branch, and log it.

    Three families, all of them, because ``QApplication.quit()`` cannot escape a
    nested modal loop:

    * the static ``QMessageBox`` helpers,
    * ``QInputDialog.getText`` / ``getItem``,
    * the ``gui.utils.file_dialogs`` wrappers (the repo's established seam).
    """
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    buttons = QMessageBox.StandardButton

    def _question(parent=None, title="", text="", *a, **k):
        _record_modal("QMessageBox.question", title, text)
        return buttons.No  # safe branch: never triggers a re-import-all or a wipe

    def _mk(kind, ret):
        def _f(parent=None, title="", text="", *a, **k):
            _record_modal(f"QMessageBox.{kind}", title, text)
            return ret

        return _f

    def _get_text(parent=None, title="", label="", *a, **k):
        _record_modal("QInputDialog.getText", title, label)
        return ("", False)

    def _get_item(parent=None, title="", label="", items=(), *a, **k):
        _record_modal("QInputDialog.getItem", title, label)
        return ("", False)

    stack = ExitStack()
    stack.enter_context(patch.object(QMessageBox, "question", staticmethod(_question)))
    stack.enter_context(patch.object(QMessageBox, "information", staticmethod(_mk("information", buttons.Ok))))
    stack.enter_context(patch.object(QMessageBox, "warning", staticmethod(_mk("warning", buttons.Ok))))
    stack.enter_context(patch.object(QMessageBox, "critical", staticmethod(_mk("critical", buttons.Ok))))
    stack.enter_context(patch.object(QMessageBox, "about", staticmethod(_mk("about", None))))
    stack.enter_context(patch.object(QInputDialog, "getText", staticmethod(_get_text)))
    stack.enter_context(patch.object(QInputDialog, "getItem", staticmethod(_get_item)))

    import anki_miner.gui.utils.file_dialogs as fd

    canned = str(SCRATCH_HOME / "picked")
    # on_done is keyword-only on every wrapper, so the fakes can bind it by name
    # and swallow the positional args. They answer immediately, which is what
    # the atlas wants: the real picker no longer blocks the event loop, but it
    # would leave a live top-level window that keeps the app alive instead.
    stack.enter_context(patch.object(fd, "pick_open_file", lambda *a, on_done, **k: on_done(canned)))
    stack.enter_context(patch.object(fd, "pick_open_files", lambda *a, on_done, **k: on_done([canned])))
    stack.enter_context(patch.object(fd, "pick_save_file", lambda *a, on_done, **k: on_done(canned)))
    stack.enter_context(patch.object(fd, "pick_directory", lambda *a, on_done, **k: on_done(canned)))

    try:
        yield
    finally:
        # Belt and braces: anything that slipped past the patches must not
        # outlive the sweep as a live window.
        fd.cancel_all_pickers()
        stack.close()


@contextmanager
def patched_destructive_boot():
    """No-op the destructive startup GC at ``app.py``'s OWN binding, and prove it fired."""
    import anki_miner.gui.app as app_mod

    def _noop(*a, **k):
        _STORE_RECOVERY_FIRED["value"] = True
        return None

    if not hasattr(app_mod, "run_startup_store_recovery"):
        raise AssertionError("app.py no longer binds run_startup_store_recovery — re-check the patch target")
    with patch.object(app_mod, "run_startup_store_recovery", _noop):
        yield


def store_recovery_fired() -> bool:
    return _STORE_RECOVERY_FIRED["value"]


@contextmanager
def patched_gl_widget():
    """Stub the mpv/OpenGL surface, which grabs solid black.

    Patched at ``subtitle_player_widget``'s own binding: it imports the name at
    module import and constructs later, so patching ``mpv_video_widget`` is inert
    once ``subtitle_player_widget`` has been imported. The live surface is never
    reparented or animated by this harness — it is replaced before construction.
    """
    from PyQt6.QtWidgets import QLabel

    import anki_miner.gui.widgets.subtitle_player_widget as spw

    class _StubVideo(QLabel):
        def __init__(self, *a, **k):
            super().__init__("[video pane stubbed for capture]")
            self.setMinimumSize(320, 180)

        def __getattr__(self, name):  # tolerate the mpv API surface
            return lambda *a, **k: None

    if not hasattr(spw, "MpvVideoWidget"):
        raise AssertionError("subtitle_player_widget no longer binds MpvVideoWidget")
    with patch.object(spw, "MpvVideoWidget", _StubVideo):
        yield


@contextmanager
def patched_background_work():
    """Silence the startup work that is not part of a static screen capture.

    Validation spins a worker whose completion rewrites status text mid-capture;
    the shortcut service writes into ``~/.local/share/applications``.
    """
    from anki_miner.gui.controllers.background_tasks import BackgroundTaskController
    from anki_miner.services.shortcut_service import ShortcutService

    with ExitStack() as stack:
        stack.enter_context(patch.object(BackgroundTaskController, "start_validation", lambda *a, **k: False))
        if hasattr(ShortcutService, "create_shortcut"):
            stack.enter_context(patch.object(ShortcutService, "create_shortcut", lambda *a, **k: None))
        yield


def prepared_config(*, language: str = "en", font_scale: float = 1.0, first_run: bool = False):
    """Load, redirect, isolate and persist the harness config. Returns the fake server.

    ``font_scale`` is written to config rather than applied live: ``gui/utils/fonts.py``
    bakes ``pixel_size * Theme.get_font_scale()`` at widget *construction*, and text
    size is restart-to-apply by decision (D39b), so a live ``set_font_scale`` after
    the window exists would leave every already-built widget at the old size and
    measure a cell nobody can reach.

    The saved UI session state is removed first, so a cell's geometry and route are
    the cell's own and not the previous run's.

    The caller must ``stop()`` the returned fake AnkiConnect.
    """
    import dataclasses
    from urllib.parse import urlparse

    from anki_miner.gui.utils import session_state
    from anki_miner.gui.utils.config_manager import GUIConfigManager
    from tests.e2e.app_driver import _disabling_gui_config
    from tests.e2e.fake_ankiconnect import FakeAnkiConnect

    cfg = GUIConfigManager.load_config_with_provenance()[0]
    cfg = redirect_config_paths(cfg, SCRATCH_HOME)
    assert_isolated(cfg, SCRATCH_HOME)

    fake = FakeAnkiConnect()
    fake.start()
    cfg = dataclasses.replace(cfg, ankiconnect_url=fake.url)
    if urlparse(cfg.ankiconnect_url).port == 8765:
        fake.stop()
        raise AssertionError("ankiconnect_url still points at the REAL Anki")

    cfg = _disabling_gui_config(cfg)
    cfg = dataclasses.replace(cfg, ui_language=language, ui_font_scale=font_scale)
    if first_run:
        cfg = dataclasses.replace(cfg, first_run_setup_done=False)
    GUIConfigManager.save_config(cfg)

    state = session_state.state_file()
    if state.exists():
        state.unlink()
    return fake
