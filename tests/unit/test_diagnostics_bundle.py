from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator
from zipfile import ZipFile

import pytest

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.diagnostics import bundle
from anki_miner.diagnostics.bundle import collect_log_members, default_bundle_name, write_diagnostics_bundle
from anki_miner.diagnostics.environment import EnvironmentSnapshot


class SpySinkHandler(RotatingFileHandler):
    def __init__(self, path: Path) -> None:
        self.lock_events: list[str] = []
        super().__init__(path, maxBytes=1024, backupCount=5, encoding="utf-8")
        self._anki_miner_sink = True

    def acquire(self) -> None:
        self.lock_events.append("acquire")
        super().acquire()

    def release(self) -> None:
        self.lock_events.append("release")
        super().release()


@contextmanager
def _installed_sink(path: Path) -> Iterator[SpySinkHandler]:
    root = logging.getLogger()
    handler = SpySinkHandler(path)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        handler.close()


def _snapshot(tmp_path: Path) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        app_version="2.9.0",
        python="3.11.9",
        qt="<unavailable: ImportError>",
        platform="TestOS-1",
        frozen=False,
        meipass=None,
        executable=str(tmp_path / "python"),
        home=str(tmp_path),
        log_path=str(tmp_path / "anki_miner.log"),
        log_ring="1024 bytes x 5 backups",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        ytdlp="yt-dlp",
        alass="alass",
        dictionary_chain=("indexed:test-dictionary enabled",),
        frequency_chain=(),
        pitch_chain=(),
        audio_chain=("jpod101 enabled",),
        ankiconnect_url="http://127.0.0.1:8765",
        deck="Test Deck",
        note_type="Test Note",
    )


def _disable_early_crash_member(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(bundle, "_early_crash_path", lambda: tmp_path / "absent-early-crash.log")


def test_collect_log_members_includes_existing_rotations_and_locks_each_read(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active")
    Path(f"{active}.1").write_bytes(b"one")
    Path(f"{active}.3").write_bytes(b"three")
    _disable_early_crash_member(monkeypatch, tmp_path)

    with _installed_sink(active) as handler:
        members, missing = collect_log_members()
        events = list(handler.lock_events)

    assert members == [
        ("anki_miner.log", b"active"),
        ("anki_miner.log.1", b"one"),
        ("anki_miner.log.3", b"three"),
    ]
    assert missing == []
    assert events == ["acquire", "release"] * len(members)


def test_collect_log_members_adds_distinct_early_crash_fallback(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "anki_miner.log"
    active.write_bytes(b"active")
    early = tmp_path / "AnkiMiner-early-crash.log"
    early.write_bytes(b"early")
    monkeypatch.setattr(bundle, "_early_crash_path", lambda: early)

    with _installed_sink(active):
        members, missing = collect_log_members()

    assert ("early-crash.log", b"early") in members
    assert missing == []


def test_bundle_contains_logs_health_and_full_settings(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active log bytes")
    Path(f"{active}.1").write_bytes(b"rotated log bytes")
    _disable_early_crash_member(monkeypatch, tmp_path)
    config = AnkiMinerConfig(
        asr_device="cpu",
        blacklist_path=tmp_path / "lists" / "blacklist.txt",
        dictionary_chain=(ChainEntry(kind="indexed", dict_id="private-dictionary", enabled=True),),
        log_path=active,
    )
    target = tmp_path / "diagnostics.zip"

    with _installed_sink(active):
        result = write_diagnostics_bundle(
            target,
            config=config,
            snapshot=_snapshot(tmp_path),
            health_lines=["tools.ffmpeg: state=ok detail=ffmpeg 7.1 checked_at=2026-08-04T12:30:45"],
        )

    with ZipFile(target) as archive:
        names = archive.namelist()
        settings = json.loads(archive.read("settings.json"))
        health = archive.read("health.txt").decode("utf-8")
        total_bytes = sum(info.file_size for info in archive.infolist())

    assert names == list(result.members)
    assert "anki_miner.log" in names
    assert "anki_miner.log.1" in names
    assert "anki_miner.log.2" not in names
    assert settings["asr_device"] == "cpu"
    assert settings["blacklist_path"] == str(tmp_path / "lists" / "blacklist.txt")
    assert settings["dictionary_chain"] == [{"kind": "indexed", "dict_id": "private-dictionary", "enabled": True}]
    assert "tools.ffmpeg: state=ok" in health
    assert result.total_bytes == total_bytes


def test_permission_error_is_reported_in_result_and_readme(tmp_path: Path, monkeypatch) -> None:
    active = tmp_path / "custom.log"
    active.write_bytes(b"active")
    unreadable = Path(f"{active}.1")
    unreadable.write_bytes(b"private")
    _disable_early_crash_member(monkeypatch, tmp_path)
    real_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == unreadable:
            raise PermissionError("private path")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    target = tmp_path / "diagnostics.zip"

    with _installed_sink(active):
        result = write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(log_path=active),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    with ZipFile(target) as archive:
        readme = archive.read("README.txt").decode("utf-8")
        health = archive.read("health.txt").decode("utf-8")

    assert "anki_miner.log.1" in result.missing
    assert "missing: anki_miner.log.1" in readme
    assert "System Health has not been checked this session." in health


def test_write_failure_leaves_no_target_or_staging_file(tmp_path: Path, monkeypatch) -> None:
    _disable_early_crash_member(monkeypatch, tmp_path)
    target = tmp_path / "diagnostics.zip"

    class FailingZipFile:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

        def writestr(self, *_args, **_kwargs) -> None:
            raise OSError("zip write failed")

    monkeypatch.setattr(bundle.zipfile, "ZipFile", FailingZipFile)

    with pytest.raises(OSError, match="zip write failed"):
        write_diagnostics_bundle(
            target,
            config=AnkiMinerConfig(),
            snapshot=_snapshot(tmp_path),
            health_lines=[],
        )

    assert not target.exists()
    assert list(tmp_path.glob(".diagnostics-*.zip")) == []


def test_default_bundle_name_uses_passed_datetime() -> None:
    assert default_bundle_name(datetime(2026, 8, 4, 12, 34, 56)) == "anki-miner-diagnostics-20260804-123456.zip"
