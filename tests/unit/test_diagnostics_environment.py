from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.diagnostics.environment import (
    EnvironmentSnapshot,
    collect_environment,
    format_environment_lines,
    format_health_lines,
)


def _raising(exc: Exception):
    def raise_exception(*_args, **_kwargs):
        raise exc

    return raise_exception


def test_collect_environment_never_raises_when_resolvers_fail(tmp_path: Path, monkeypatch) -> None:
    from anki_miner.diagnostics import environment

    monkeypatch.setattr(environment, "frozen_state", _raising(ValueError("private meipass path")))
    monkeypatch.setattr(environment, "resolve_ffmpeg", _raising(FileNotFoundError("private ffmpeg path")))
    monkeypatch.setattr(environment, "resolve_ffprobe", _raising(PermissionError("private ffprobe path")))
    monkeypatch.setattr(environment, "resolve_ytdlp", _raising(RuntimeError("private yt-dlp path")))
    monkeypatch.setattr(environment, "resolve_alass", _raising(OSError("private alass path")))

    snapshot = collect_environment(AnkiMinerConfig(log_path=tmp_path / "private" / "anki_miner.log"))

    assert snapshot.frozen is False
    assert snapshot.meipass == "<unavailable: ValueError>"
    assert snapshot.ffmpeg == "<unavailable: FileNotFoundError>"
    assert snapshot.ffprobe == "<unavailable: PermissionError>"
    assert snapshot.ytdlp == "<unavailable: RuntimeError>"
    assert snapshot.alass == "<unavailable: OSError>"


def test_format_environment_lines_is_deterministic_and_expands_chains() -> None:
    snapshot = EnvironmentSnapshot(
        app_version="2.9.0",
        python="3.11.9",
        qt="Qt 6.8.0 / PyQt 6.8.0",
        platform="TestOS-1",
        frozen=False,
        meipass=None,
        executable="/opt/anki-miner/python",
        home="/Users/Ivan/.anki_miner",
        log_path="/Users/Ivan/.anki_miner/anki_miner.log",
        log_ring="2097152 bytes x 5 backups",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        ytdlp="yt-dlp",
        alass="alass",
        dictionary_chain=("indexed:jmdict-english enabled", "jisho disabled"),
        frequency_chain=("indexed:jpdb enabled",),
        pitch_chain=(),
        audio_chain=("pack:nhk enabled", "jpod101 enabled"),
        ankiconnect_url="http://127.0.0.1:8765",
        deck="Japanese",
        note_type="Lapis",
    )

    first = format_environment_lines(snapshot)
    second = format_environment_lines(snapshot)

    assert first == second
    assert first[0] == "app_version: 2.9.0"
    assert "dictionary_chain[0]: indexed:jmdict-english enabled" in first
    assert "pitch_chain: -" in first
    assert "deck: Japanese" in first
    assert "note_type: Lapis" in first
    assert not any(line.startswith("deck: /") or line.startswith("note_type: /") for line in first)


def test_format_health_lines_uses_stable_plain_rows() -> None:
    checked_at = datetime(2026, 8, 4, 12, 30, 45)
    rows = [
        ("tools.ffmpeg", "ok", "ffmpeg 7.1", checked_at),
        ("anki.deck", "unknown", "", None),
    ]

    assert format_health_lines(rows) == [
        "tools.ffmpeg: state=ok detail=ffmpeg 7.1 checked_at=2026-08-04T12:30:45",
        "anki.deck: state=unknown detail=- checked_at=-",
    ]


def test_importing_diagnostics_does_not_import_pyqt6() -> None:
    project_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import anki_miner.diagnostics, sys; assert 'PyQt6' not in sys.modules",
        ],
        cwd=project_root,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
