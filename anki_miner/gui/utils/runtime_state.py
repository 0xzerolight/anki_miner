"""Machine-local runtime recovery state: partial downloads and queue contents.

D16-C says a partial download and an assembled queue survive quitting. What
makes that safe is *where* the state lives. It is not a setting, so it is not in
``gui_config.json``; it is not portable, so it must never reach a settings export
or a profile sidecar. Both guarantees are structural rather than an exclusion
list someone has to remember to extend:
:meth:`~anki_miner.gui.utils.config_manager.GUIConfigManager.export_config` and
:mod:`~anki_miner.gui.utils.profile_store` only ever serialise
:class:`~anki_miner.config.config.AnkiMinerConfig`, and nothing here is part of
it. ``tests/unit/test_runtime_state.py`` asserts that directly.

Layout::

    ~/.anki_miner/
      gui_config.json        # settings — exported, profiled
      profiles/              # profile sidecars
      runtime_state/         # NEITHER of the above
        downloads/           # <key>.part + <key>.json resume manifests
        queues/              # <key>.json queue snapshots

Every path is derived from ``GUIConfigManager.CONFIG_FILE`` **at call time** and
never snapshotted at import: ``tests/_home_isolation.py`` retargets that class
attribute per test, so a module-level snapshot would keep writing into the user's
real ``~/.anki_miner`` and trip the ``guard_real_home`` tripwire.

Qt-free on purpose — the download half is consumed by ``anki_miner.services``.
"""

from __future__ import annotations

from pathlib import Path

from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services.download_resume import DOWNLOADS_DIRNAME, RUNTIME_STATE_DIRNAME, safe_key

DIRNAME = RUNTIME_STATE_DIRNAME
_QUEUES_DIRNAME = "queues"

#: Re-exported so GUI callers validate keys against the one pattern the
#: download half already enforces.
validate_key = safe_key


def runtime_state_root() -> Path:
    """Return the runtime-state directory, computed fresh on every call."""
    return GUIConfigManager.CONFIG_FILE.parent / DIRNAME


def download_resume_root() -> Path:
    """Return the directory holding ``.part`` bodies and resume manifests.

    Equal by construction to
    :func:`anki_miner.services.download_resume.default_resume_root`, which the
    service layer uses because it may not import the GUI package.
    ``tests/unit/test_runtime_state.py`` asserts the two agree.
    """
    return runtime_state_root() / DOWNLOADS_DIRNAME


def queue_state_root() -> Path:
    """Return the directory holding persisted queue snapshots."""
    return runtime_state_root() / _QUEUES_DIRNAME


def is_within(path: Path, root: Path) -> bool:
    """Whether ``path`` resolves to ``root`` itself or something beneath it.

    Used before any deletion: Discard removes only resolved paths beneath the
    runtime-state roots, never a target a symlink or a hand-edited snapshot
    pointed somewhere else.
    """
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return False
    return resolved == base or base in resolved.parents
