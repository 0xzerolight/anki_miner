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
from urllib.parse import unquote, urlsplit, urlunsplit

from anki_miner.config import AudioSourceEntry, paths
from anki_miner.diagnostics.environment import EnvironmentSnapshot, format_environment_lines
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.logging_ext import capped, log_summary

logger = logging.getLogger(__name__)

DIAGNOSTICS_ZIP_SUFFIX = ".zip"

# Bumped whenever the member layout changes, so a report can be read against the
# expectations it was written for. 1 = logs + environment + health + settings;
# 2 adds the on-disk config/state members and the inventories.
BUNDLE_FORMAT = 2

# Per-member ceiling for everything except the logs. A single runaway artifact
# (a corrupt ui_state.ini, a queue snapshot that grew unbounded) must not push
# the log ring out of an attachable ZIP. The logs are exempt: they are the
# evidence the bundle exists for, and the ring already bounds them.
_MEMBER_MAX_BYTES = 2 * 1024 * 1024

# Written beside the active log by the child-process launcher
# (gui/launch.py CHILD_LOG_NAME). Spelled out rather than imported: that module
# pulls in Qt and this one must stay importable without it.
_CHILD_LOG_NAME = "anki_miner.child.log"

# gui/utils/session_state.py FILENAME. Same reason as the child log: no Qt here.
_UI_STATE_NAME = "ui_state.ini"

_GITHUB_ISSUES_URL = "https://github.com/0xzerolight/anki_miner/issues"
_PRIVACY_SENTENCE = "This bundle contains file paths and file names from your computer. Review it before uploading."
_UNCHECKED_HEALTH = "System Health has not been checked this session."
_LEGACY_AUDIO_FAILURE_MARKERS = (b"audio download failed for ", b"custom_json fetch failed for ")


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


def _redact_legacy_audio_failure_lines(content: bytes) -> bytes:
    """Fail closed for custom-audio failure records written before URL redaction."""
    redacted: list[bytes] = []
    for line in content.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        ending = line[len(body) :]
        for marker in _LEGACY_AUDIO_FAILURE_MARKERS:
            marker_index = body.find(marker)
            if marker_index >= 0:
                body = body[: marker_index + len(marker)] + b"<redacted-url>"
                break
        redacted.append(body + ending)
    return b"".join(redacted)


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
            members.append((name, _redact_legacy_audio_failure_lines(content)))

    collect("anki_miner.log", active_path, active_handler)
    for index in range(1, 6):
        rotated = Path(f"{active_path}.{index}")
        collect(f"anki_miner.log.{index}", rotated, active_handler)

    early_path = _early_crash_path()
    if early_path != active_path:
        collect("early-crash.log", early_path, handlers_by_path.get(early_path))

    # Native crashes (SIGSEGV/SIGABRT/...) never reach the logging module, so
    # faulthandler writes them here instead. Plain reads: no logging handler
    # owns this file. See gui/app.py:_enable_faulthandler.
    crash_path = paths.ANKI_MINER_HOME / "anki_miner.crash"
    collect("anki_miner.crash", crash_path, None)
    collect("anki_miner.crash.1", crash_path.with_suffix(".crash.1"), None)

    # The child process writes its own stderr sink beside the active log, so a
    # crash that never reached this process still has a record here.
    collect(_CHILD_LOG_NAME, active_path.with_name(_CHILD_LOG_NAME), None)
    return members, missing


def _capped_bytes(content: bytes) -> bytes:
    """Trim one non-log member to ``_MEMBER_MAX_BYTES`` and say how much was cut."""
    if len(content) <= _MEMBER_MAX_BYTES:
        return content
    omitted = len(content) - _MEMBER_MAX_BYTES
    return content[:_MEMBER_MAX_BYTES] + f"\n<truncated: {omitted} bytes omitted>\n".encode()


def _globbed(directory: Path, pattern: str) -> list[Path]:
    """Sorted files matching ``pattern``; an unreadable or absent dir yields none."""
    try:
        return sorted(path for path in directory.glob(pattern) if path.is_file())
    except OSError:
        return []


def _state_sources(home: Path) -> list[tuple[str, Path]]:
    """Map every on-disk config/state artifact to its member name in the ZIP.

    ``gui_config*`` covers the live file plus the recovery artifacts the config
    manager writes beside it (``.bak`` one-overwrite rotation and the
    ``.from-schema-<N>.json`` archive; see gui/utils/config_manager.py). Both are
    the evidence for "my settings changed by themselves".

    Download manifests are included; the ``.part`` payloads beside them never
    are — they are the bytes, not the state.
    """
    runtime_state = home / "runtime_state"
    return [
        *((f"config/{path.name}", path) for path in _globbed(home, "gui_config*")),
        *((f"config/{path.name}", path) for path in _globbed(home, _UI_STATE_NAME)),
        *((f"config/profiles/{path.name}", path) for path in _globbed(home / "profiles", "*.json")),
        *((f"state/queues/{path.name}", path) for path in _globbed(runtime_state / "queues", "*.json")),
        *((f"state/downloads/{path.name}", path) for path in _globbed(runtime_state / "downloads", "*.json")),
    ]


def collect_state_members() -> tuple[list[tuple[str, bytes]], list[str]]:
    """Read the on-disk config and runtime state, best effort.

    The settings member records the config the session is *running*; these
    members record what is on disk. A divergence between the two is the whole
    diagnosis for a setting that did not survive a restart, a profile switch
    that half-applied, or a queue that came back empty.
    """
    members: list[tuple[str, bytes]] = []
    missing: list[str] = []
    for name, path in _state_sources(paths.ANKI_MINER_HOME):
        content = _read_plain(path)
        if content is None:
            missing.append(name)
        else:
            members.append((name, _capped_bytes(content)))
    return members, missing


def _redact_custom_audio_url(url: str) -> str:
    """Keep a useful URL shape without credentials or parse failures."""
    try:
        parts = urlsplit(url)
        if parts.username is not None or "@" in unquote(parts.netloc):
            return "<redacted-url>"
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme or hostname is None:
        return "<redacted-url>"
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "REDACTED", ""))


def _to_serializable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        serialized = {field.name: _to_serializable(getattr(value, field.name)) for field in dataclasses.fields(value)}
        if isinstance(value, AudioSourceEntry) and value.kind in ("custom", "custom_json") and value.url:
            serialized["url"] = _redact_custom_audio_url(value.url)
        return serialized
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
        f"bundle_format: {BUNDLE_FORMAT}\n\n"
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
    log_members, log_missing = collect_log_members()
    state_members, state_missing = collect_state_members()
    missing = [*log_missing, *state_missing]
    effective_health = health_lines or [_UNCHECKED_HEALTH]
    generated_members = [
        ("environment.txt", _text_bytes(format_environment_lines(snapshot))),
        ("health.txt", _text_bytes(effective_health)),
        ("settings.json", _settings_bytes(config)),
    ]
    # State members arrive already capped by their collector; the logs are
    # exempt by design. Capping here would double-stamp the truncation marker.
    body_members = [
        *((name, _capped_bytes(content)) for name, content in generated_members),
        *state_members,
        *log_members,
    ]
    member_names = ["README.txt", *(name for name, _content in body_members)]
    payloads = [("README.txt", _readme_bytes(member_names, missing)), *body_members]

    with atomic_write_path(target) as staged, zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in payloads:
            archive.writestr(name, content)

    total_bytes = sum(len(content) for _name, content in payloads)
    try:
        zip_bytes: int | None = target.stat().st_size
    except OSError:
        zip_bytes = None
    log_summary(
        logger,
        "Diagnostics exported",
        path=target,
        members=len(payloads),
        bytes=total_bytes,
        zip_bytes=zip_bytes,
        missing=capped(missing),
    )

    return BundleResult(
        path=target,
        members=tuple(name for name, _content in payloads),
        total_bytes=total_bytes,
        missing=tuple(missing),
    )
