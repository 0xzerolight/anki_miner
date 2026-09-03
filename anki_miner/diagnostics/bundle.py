"""Create inert diagnostic ZIP files without importing Qt.

The bundle intentionally preserves configured paths, media file names, deck
names, and note-type names. Those values are often the evidence for path,
Unicode, and integration failures. Regex redaction would destroy that evidence;
the mitigation is an inert archive plus a clear privacy warning before upload.
The personal stores (``known_words.db``, ``stats.db``, ``recent_files.json``)
are never shipped as contents: the word lists themselves add no diagnostic value
beyond the logs. Their aggregates are, because "no words were mined" is usually
a store that is empty, missing, or locked, and only a row count says which.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import sqlite3
import sys
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
from anki_miner.utils.bounded_reader import read_json_bounded
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

# The four index-backed resource families and the config field naming each root
# (services/_sqlite_index.py owns the ``<root>/<id>/index.sqlite`` layout).
_RESOURCE_FAMILIES = (
    ("dicts", "dicts_root"),
    ("freqs", "freqs_root"),
    ("pitch", "pitch_root"),
    ("audio_packs", "audio_packs_root"),
)

# A user with hundreds of slots turns the inventory into the bundle. Report the
# first slots and the count of the rest.
_MAX_RESOURCE_SLOTS = 200

# A slot holds an index, a sidecar, and (for a broken import) whatever staging
# debris survived. Stop counting rather than walk an accidentally-huge tree.
_MAX_SLOT_FILES = 5000

_META_SIDECAR_MAX_BYTES = 1 * 1024 * 1024

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


def _unavailable(exc: BaseException) -> str:
    """Render one failed probe so the reader sees which failure it was."""
    return f"<unavailable: {type(exc).__name__}: {exc}>"


def _slot_size(root: Path) -> tuple[int, int]:
    """Count files and bytes under one resource slot, bounded."""
    files = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if files >= _MAX_SLOT_FILES:
                return files, total
            files += 1
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return files, total


def collect_resource_lines(config) -> list[str]:
    """Inventory every installed dictionary, frequency, pitch, and audio slot.

    "It is installed but lookups come back empty" is answered here and nowhere
    else: a stale ``schema_version``, an ``entry_count`` of zero, or a slot whose
    files are gone all look identical from the chain editor.
    """
    lines: list[str] = []
    for family, attribute in _RESOURCE_FAMILIES:
        root = getattr(config, attribute, None)
        if root is None:
            continue
        root = Path(root)
        try:
            slots = sorted(child for child in root.iterdir() if child.is_dir())
        except OSError as exc:
            lines.append(f"{family}: {_unavailable(exc)} root={root}")
            continue
        if not slots:
            lines.append(f"{family}: none root={root}")
            continue
        for slot in slots[:_MAX_RESOURCE_SLOTS]:
            sidecar = slot / "meta.json"
            raw: Any = (
                read_json_bounded(sidecar, _META_SIDECAR_MAX_BYTES, None, "resource meta")
                if sidecar.is_file()
                else None
            )
            meta: dict[str, Any] = raw if isinstance(raw, dict) else {}
            files, size = _slot_size(slot)
            lines.append(
                f"{family}/{slot.name}: "
                f"schema_version={meta.get('schema_version', '-')} "
                f"entry_count={meta.get('entry_count', '-')} "
                f"language={meta.get('language', '-')} "
                f"files={files} bytes={size}"
            )
        if len(slots) > _MAX_RESOURCE_SLOTS:
            lines.append(f"{family}: +{len(slots) - _MAX_RESOURCE_SLOTS} more slots")
    return lines or ["no resource roots configured"]


def _grouped_counts(path: Path, query: str, prefix: str) -> str:
    """Run one read-only GROUP BY and render it, or say why it could not run.

    A read-only URI plus a one-second timeout: a store the running app holds
    open must never turn an export into a hang, and a locked or absent file is
    itself the diagnosis.
    """
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1)
    except (sqlite3.Error, OSError) as exc:
        return _unavailable(exc)
    try:
        rows = conn.execute(query).fetchall()
    except (sqlite3.Error, OSError) as exc:
        return _unavailable(exc)
    finally:
        conn.close()
    total = sum(int(count) for _key, count in rows)
    groups = " ".join(f"{prefix}.{key or '-'}={int(count)}" for key, count in rows)
    return f"rows={total} {groups}".rstrip()


def collect_store_lines(config) -> list[str]:
    """Report row counts for the personal stores, never their rows.

    ``known_words*.db`` is globbed rather than derived: the per-language sibling
    rule lives in ``gui/utils/service_factory.py``, which imports Qt, and every
    language's cache is evidence here.
    """
    lines: list[str] = []
    known_db = Path(config.known_words_db_path)
    try:
        known_paths = sorted(known_db.parent.glob(f"{known_db.stem}*.db"))
    except OSError as exc:
        known_paths = []
        lines.append(f"{known_db.name}: {_unavailable(exc)}")
    if not known_paths:
        known_paths = [known_db]
    for path in known_paths:
        lines.append(
            f"{path.name}: {_grouped_counts(path, 'SELECT source, COUNT(*) FROM known_words GROUP BY source', 'source')}"
        )
    stats_db = Path(config.stats_db_path)
    lines.append(
        f"{stats_db.name}: "
        f"{_grouped_counts(stats_db, 'SELECT language, COUNT(*) FROM mining_sessions GROUP BY language', 'language')}"
    )
    return lines


def _disk_line(label: str, path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return f"{label}: {_unavailable(exc)} path={path}"
    return f"{label}: path={path} total_bytes={usage.total} used_bytes={usage.used} free_bytes={usage.free}"


def collect_disk_lines() -> list[str]:
    """Report free space on the two volumes a run writes to.

    A full disk surfaces as a dozen unrelated write failures; the free-byte
    count is what turns them back into one cause.
    """
    return [
        _disk_line("home", paths.ANKI_MINER_HOME),
        _disk_line("tempdir", Path(tempfile.gettempdir())),
        f"filesystem_encoding={sys.getfilesystemencoding()}",
    ]


def _ui_fact_lines(ui_facts: Mapping[str, str] | None) -> list[str]:
    """Render the GUI-thread display facts, in the order they were collected."""
    if not ui_facts:
        return ["<unavailable: no GUI facts were collected for this export>"]
    return [f"{key}: {value}" for key, value in ui_facts.items()]


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
    ui_facts: Mapping[str, str] | None = None,
) -> BundleResult:
    """Atomically write a diagnostics ZIP and return its inventory.

    ``ui_facts`` is a parameter rather than something this module reads because
    the screen and theme facts only exist on the GUI thread, and this module
    must stay importable without Qt.
    """
    log_members, log_missing = collect_log_members()
    state_members, state_missing = collect_state_members()
    missing = [*log_missing, *state_missing]
    effective_health = health_lines or [_UNCHECKED_HEALTH]
    generated_members = [
        ("environment.txt", _text_bytes(format_environment_lines(snapshot))),
        ("health.txt", _text_bytes(effective_health)),
        ("settings.json", _settings_bytes(config)),
        ("resources.txt", _text_bytes(collect_resource_lines(config))),
        ("stores.txt", _text_bytes(collect_store_lines(config))),
        ("disk.txt", _text_bytes(collect_disk_lines())),
        ("screens.txt", _text_bytes(_ui_fact_lines(ui_facts))),
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
