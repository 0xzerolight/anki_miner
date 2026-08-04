"""Create inert diagnostic ZIP files without importing Qt.

The bundle intentionally preserves configured paths, media file names, deck
names, and note-type names. Those values are often the evidence for path,
Unicode, and integration failures. Regex redaction would destroy that evidence;
the mitigation is an inert archive plus a clear privacy warning before upload.
Pure personal stores (``known_words.db``, ``stats.db``, and
``recent_files.json``) are deliberately excluded because they add no diagnostic
value beyond the logs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from anki_miner.config import paths
from anki_miner.diagnostics.environment import EnvironmentSnapshot, format_environment_lines
from anki_miner.utils.atomic_io import atomic_write_path

DIAGNOSTICS_ZIP_SUFFIX = ".zip"

_GITHUB_ISSUES_URL = "https://github.com/0xzerolight/anki_miner/issues"
_PRIVACY_SENTENCE = "This bundle contains file paths and file names from your computer. Review it before uploading."
_UNCHECKED_HEALTH = "System Health has not been checked this session."


@dataclass(frozen=True)
class BundleResult:
    path: Path
    members: tuple[str, ...]
    total_bytes: int
    missing: tuple[str, ...]


def default_bundle_name(now: datetime | None = None) -> str:
    """Return the timestamped default diagnostics ZIP name."""
    timestamp = now or datetime.now()
    return f"anki-miner-diagnostics-{timestamp:%Y%m%d-%H%M%S}{DIAGNOSTICS_ZIP_SUFFIX}"


def _early_crash_path() -> Path:
    return Path(tempfile.gettempdir()) / "AnkiMiner-early-crash.log"


def _read_sink_locked(handler: logging.Handler, path: Path) -> bytes | None:
    handler.acquire()
    try:
        handler.flush()
        return path.read_bytes()
    except OSError:
        return None
    finally:
        handler.release()


def _read_plain(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _sink_handlers() -> list[tuple[logging.Handler, Path]]:
    sinks: list[tuple[logging.Handler, Path]] = []
    for handler in logging.getLogger().handlers:
        if not getattr(handler, "_anki_miner_sink", False):
            continue
        base_filename = getattr(handler, "baseFilename", None)
        if base_filename is not None:
            sinks.append((handler, Path(base_filename)))
    return sinks


def collect_log_members() -> tuple[list[tuple[str, bytes]], list[str]]:
    """Read available active, rotated, and distinct early-crash log files."""
    sinks = _sink_handlers()
    if sinks:
        active_handler, active_path = sinks[-1]
    else:
        active_handler = None
        active_path = paths.ANKI_MINER_HOME / "anki_miner.log"
    handlers_by_path = {path: handler for handler, path in sinks}

    members: list[tuple[str, bytes]] = []
    missing: list[str] = []

    def collect(name: str, path: Path, handler: logging.Handler | None) -> None:
        if not path.is_file():
            return
        # Lock one file at a time. A rollover between files may duplicate or
        # omit one generation, but holding the lock across the whole ring would
        # stall logging for hundreds of milliseconds. A raced read is reported
        # through ``missing`` instead.
        content = _read_sink_locked(handler, path) if handler is not None else _read_plain(path)
        if content is None:
            missing.append(name)
        else:
            members.append((name, content))

    collect("anki_miner.log", active_path, active_handler)
    for index in range(1, 6):
        rotated = Path(f"{active_path}.{index}")
        collect(f"anki_miner.log.{index}", rotated, active_handler)

    early_path = _early_crash_path()
    if early_path != active_path:
        collect("early-crash.log", early_path, handlers_by_path.get(early_path))
    return members, missing


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_serializable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {key: _to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_serializable(item) for item in value]
    return value


def _text_bytes(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _settings_bytes(config: Any) -> bytes:
    serialized = _to_serializable(config)
    return (json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _readme_bytes(member_names: list[str], missing: list[str]) -> bytes:
    inventory = "\n".join(f"- {name}" for name in member_names)
    missing_text = ", ".join(missing) or "-"
    return (
        "Anki Miner diagnostics bundle\n\n"
        f"Contents:\n{inventory}\n\n"
        f"{_PRIVACY_SENTENCE}\n"
        f"GitHub issues: {_GITHUB_ISSUES_URL}\n"
        f"missing: {missing_text}\n"
    ).encode()


def write_diagnostics_bundle(
    target: Path,
    *,
    config,
    snapshot: EnvironmentSnapshot,
    health_lines: list[str],
) -> BundleResult:
    """Atomically write a diagnostics ZIP and return its inventory."""
    log_members, missing = collect_log_members()
    effective_health = health_lines or [_UNCHECKED_HEALTH]
    body_members = [
        ("environment.txt", _text_bytes(format_environment_lines(snapshot))),
        ("health.txt", _text_bytes(effective_health)),
        ("settings.json", _settings_bytes(config)),
        *log_members,
    ]
    member_names = ["README.txt", *(name for name, _content in body_members)]
    payloads = [("README.txt", _readme_bytes(member_names, missing)), *body_members]

    with atomic_write_path(target) as staged, zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in payloads:
            archive.writestr(name, content)

    return BundleResult(
        path=target,
        members=tuple(name for name, _content in payloads),
        total_bytes=sum(len(content) for _name, content in payloads),
        missing=tuple(missing),
    )
