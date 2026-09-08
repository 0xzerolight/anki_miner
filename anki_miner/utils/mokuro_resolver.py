"""Central resolver for the mokuro executable.

Resolution order (first hit wins):

1. **Config override** — ``config.mokuro_location`` when set and runnable.
2. **Managed** — the console script inside the in-app uv environment,
   ``config.uv_root/mokuro/bin/mokuro`` (``Scripts\\mokuro.exe`` on Windows),
   written by ``services.mokuro_installer``.
3. **PATH fallback** — the bare literal ``"mokuro"`` (a user's own pip/pipx install).

No bundled tier: mokuro (torch + models) is never shipped inside the app.

The module cache mirrors ``alass_resolver``: keyed on the inputs, cleared by
the install wiring (``app._connect_mokuro_install``) once an install lands.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from anki_miner.utils.resolver_log import log_resolution, log_resolution_refused

__all__ = ["managed_mokuro_path", "managed_python_path", "mokuro_available", "mokuro_env_dir", "resolve_mokuro"]

logger = logging.getLogger(__name__)

_CACHE: dict[tuple[str | None, str | None], str] = {}


def _clear_cache() -> None:
    """Reset the module-level cache (test helper + post-install hook)."""
    _CACHE.clear()


def mokuro_env_dir(uv_root: Path) -> Path:
    """The uv virtual environment the installer creates for mokuro."""
    return Path(uv_root) / "mokuro"


def managed_python_path(uv_root: Path) -> Path:
    env = mokuro_env_dir(uv_root)
    return env / "Scripts" / "python.exe" if sys.platform == "win32" else env / "bin" / "python"


def managed_mokuro_path(uv_root: Path) -> Path:
    env = mokuro_env_dir(uv_root)
    return env / "Scripts" / "mokuro.exe" if sys.platform == "win32" else env / "bin" / "mokuro"


def _executable_file(path: Path) -> bool:
    return path.is_file() and (sys.platform == "win32" or os.access(path, os.X_OK))


def _compute(override: Any, uv_root: Any) -> str:
    if override:
        override_path = Path(override)
        if _executable_file(override_path):
            log_resolution(logger, "mokuro", "override", str(override_path))
            return str(override_path)
        if override_path.exists():
            log_resolution_refused(logger, "mokuro", "override_not_executable", override=override_path)
    if uv_root:
        managed = managed_mokuro_path(Path(uv_root))
        if _executable_file(managed):
            log_resolution(logger, "mokuro", "managed", str(managed))
            return str(managed)
        if managed.exists():
            log_resolution_refused(logger, "mokuro", "managed_not_executable", managed=managed)
    log_resolution(logger, "mokuro", "literal", "mokuro")
    return "mokuro"


def _resolve(override: Any, uv_root: Any) -> str:
    key = (str(override) if override else None, str(uv_root) if uv_root else None)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    resolved = _compute(override, uv_root)
    _CACHE[key] = resolved
    return resolved


def resolve_mokuro(config: Any) -> str:
    """Resolve the mokuro executable path/literal for *config*."""
    return _resolve(getattr(config, "mokuro_location", None), getattr(config, "uv_root", None))


def mokuro_available(mokuro_location: Any, uv_root: Any) -> bool:
    """Whether mokuro is reachable — never spawns it (torch import is seconds)."""
    resolved = _resolve(mokuro_location, uv_root)
    if resolved == "mokuro":
        return shutil.which("mokuro") is not None
    return _executable_file(Path(resolved))
