"""Collect and format a Qt-free environment snapshot."""

from __future__ import annotations

import logging
import platform as platform_module
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from anki_miner import __version__
from anki_miner.config import paths
from anki_miner.utils.alass_resolver import resolve_alass
from anki_miner.utils.bundled_binary import frozen_state
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg, resolve_ffprobe
from anki_miner.utils.ytdlp_resolver import resolve_ytdlp

#: Reported when no rotating sink is installed (a CLI or test context). The
#: ring size is deliberately NOT duplicated as a constant here — it lives in
#: ``gui.app._configure_logging`` and a copy would silently go stale.
_NO_LOG_SINK = "no sink installed"
_CHAIN_FIELDS = ("dictionary_chain", "frequency_chain", "pitch_chain", "audio_chain")


@dataclass(frozen=True)
class EnvironmentSnapshot:
    app_version: str
    python: str
    qt: str
    platform: str
    frozen: bool
    meipass: str | None
    executable: str
    home: str
    log_path: str
    log_ring: str
    ffmpeg: str
    ffprobe: str
    ytdlp: str
    alass: str
    dictionary_chain: tuple[str, ...]
    frequency_chain: tuple[str, ...]
    pitch_chain: tuple[str, ...]
    audio_chain: tuple[str, ...]
    ankiconnect_url: str
    deck: str
    note_type: str


def _unavailable(exc: Exception) -> str:
    return f"<unavailable: {type(exc).__name__}>"


def _safe_string(probe: Callable[[], object]) -> str:
    try:
        return str(probe())
    except Exception as exc:
        return _unavailable(exc)


def _qt_versions() -> str:
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR  # noqa: PLC0415
    except ImportError:
        return "<unavailable: ImportError>"
    except Exception as exc:
        return _unavailable(exc)
    return f"Qt {QT_VERSION_STR} / PyQt {PYQT_VERSION_STR}"


def _chain_label(kind: object, identifier: object | None, enabled: object) -> str:
    source = str(kind) if identifier is None else f"{kind}:{identifier}"
    return f"{source} {'enabled' if bool(enabled) else 'disabled'}"


def _dictionary_chain(config: Any) -> tuple[str, ...]:
    return tuple(_chain_label(entry.kind, entry.dict_id, entry.enabled) for entry in config.dictionary_chain)


def _frequency_chain(config: Any) -> tuple[str, ...]:
    return tuple(_chain_label("indexed", entry.source_id, entry.enabled) for entry in config.frequency_chain)


def _pitch_chain(config: Any) -> tuple[str, ...]:
    return tuple(_chain_label("indexed", entry.source_id, entry.enabled) for entry in config.pitch_chain)


def _audio_chain(config: Any) -> tuple[str, ...]:
    return tuple(_chain_label(entry.kind, entry.pack_id, entry.enabled) for entry in config.expression_audio_chain)


def _safe_chain(probe: Callable[[], tuple[str, ...]]) -> tuple[str, ...]:
    try:
        return probe()
    except Exception as exc:
        return (_unavailable(exc),)


def _log_ring() -> str:
    for handler in reversed(logging.getLogger().handlers):
        if not getattr(handler, "_anki_miner_sink", False):
            continue
        max_bytes = getattr(handler, "maxBytes", None)
        backup_count = getattr(handler, "backupCount", None)
        if isinstance(max_bytes, int) and isinstance(backup_count, int):
            return f"{max_bytes} bytes x {backup_count} backups"
    return _NO_LOG_SINK


def collect_environment(config) -> EnvironmentSnapshot:
    """Collect a best-effort environment snapshot without probing AnkiConnect.

    This function is blocking and must run off the GUI thread. Binary resolution
    can scan a long PATH, and managed yt-dlp/alass probes may hash multi-megabyte
    executables. Every probe is isolated: resolver, import, or filesystem failures
    become ``<unavailable: ExceptionType>`` fields. This function never raises for
    an environment-probe failure.
    """
    try:
        frozen, meipass_value = frozen_state()
        meipass = str(meipass_value) if meipass_value is not None else None
    except Exception as exc:
        frozen = False
        meipass = _unavailable(exc)

    return EnvironmentSnapshot(
        app_version=__version__,
        python=_safe_string(platform_module.python_version),
        qt=_qt_versions(),
        platform=_safe_string(platform_module.platform),
        frozen=frozen,
        meipass=meipass,
        executable=_safe_string(lambda: sys.executable),
        home=_safe_string(lambda: paths.ANKI_MINER_HOME),
        log_path=_safe_string(lambda: config.log_path),
        log_ring=_safe_string(_log_ring),
        ffmpeg=_safe_string(lambda: resolve_ffmpeg(config)),
        ffprobe=_safe_string(lambda: resolve_ffprobe(config)),
        ytdlp=_safe_string(lambda: resolve_ytdlp(config)),
        alass=_safe_string(lambda: resolve_alass(config)),
        dictionary_chain=_safe_chain(lambda: _dictionary_chain(config)),
        frequency_chain=_safe_chain(lambda: _frequency_chain(config)),
        pitch_chain=_safe_chain(lambda: _pitch_chain(config)),
        audio_chain=_safe_chain(lambda: _audio_chain(config)),
        ankiconnect_url=_safe_string(lambda: config.ankiconnect_url),
        deck=_safe_string(lambda: config.anki_deck_name),
        note_type=_safe_string(lambda: config.anki_note_type),
    )


def format_environment_lines(snapshot: EnvironmentSnapshot) -> list[str]:
    """Render *snapshot* in deterministic field order."""
    lines: list[str] = []
    for name in EnvironmentSnapshot.__dataclass_fields__:
        value = getattr(snapshot, name)
        if name in _CHAIN_FIELDS:
            if not value:
                lines.append(f"{name}: -")
                continue
            lines.extend(f"{name}[{index}]: {entry}" for index, entry in enumerate(value))
            continue
        lines.append(f"{name}: {'-' if value is None or value == '' else value}")
    return lines


def format_health_lines(rows: Iterable[tuple[str, str, str, datetime | None]]) -> list[str]:
    """Render Qt-independent health tuples as deterministic plain-text rows."""
    lines: list[str] = []
    for key, state, detail, checked_at in rows:
        checked = checked_at.isoformat(timespec="seconds") if checked_at is not None else "-"
        lines.append(f"{key}: state={state} detail={detail or '-'} checked_at={checked}")
    return lines
