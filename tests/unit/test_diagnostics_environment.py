from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, create_default_config
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


class TestDisplayAndGlFields:
    """The fields added after a field report the bundle could not explain.

    An AppImage aborted inside QOpenGLWidget's constructor on every video mine
    and the bundle named neither the driver, nor the session type, nor what the
    loader was searching — so the cause stayed a hypothesis.
    """

    def test_platform_name_is_passed_in_not_probed(self):
        """QGuiApplication.platformName() is GUI-thread only and this runs on a
        worker, so it is captured at the call site and handed over."""
        snapshot = collect_environment(create_default_config(), platform_name="wayland")
        assert snapshot.platform_name == "wayland"

    def test_platform_name_defaults_without_a_qapplication(self):
        assert collect_environment(create_default_config()).platform_name == "-"

    def test_session_and_qt_env_read_the_environment(self, monkeypatch):
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
        snapshot = collect_environment(create_default_config())
        assert "XDG_SESSION_TYPE=wayland" in snapshot.session_type
        assert "QT_QPA_PLATFORM=xcb" in snapshot.qt_env

    def test_unset_env_renders_as_a_dash_not_an_empty_string(self, monkeypatch):
        for name in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
            monkeypatch.delenv(name, raising=False)
        assert collect_environment(create_default_config()).session_type == "-"

    def test_gpu_drivers_never_lists_connectors(self):
        """/sys/class/drm/card[0-9]* also matches card0-DP-1 and friends, which
        have no driver and would bury the two lines that matter."""
        value = collect_environment(create_default_config()).gpu_drivers
        assert "-DP-" not in value
        assert "-HDMI-" not in value

    def test_every_new_field_is_rendered(self):
        lines = format_environment_lines(collect_environment(create_default_config()))
        rendered = {line.split(":", 1)[0] for line in lines}
        assert {
            "platform_name",
            "session_type",
            "qt_env",
            "gpu_drivers",
            "ld_library_path",
            "bundled_cxx_runtime",
            "libmpv_source",
            "video_preview",
        } <= rendered

    def test_libmpv_is_reported_but_never_loaded(self, monkeypatch):
        """A diagnostics probe that dlopened libmpv would change program state
        and risk the very abort it is trying to describe."""
        from anki_miner.utils import mpv_loader

        def explode():
            raise AssertionError("collect_environment must not load libmpv")

        monkeypatch.setattr(mpv_loader, "load_mpv", explode)
        snapshot = collect_environment(create_default_config())
        assert "resolved=" in snapshot.libmpv_source

    def test_still_never_raises_when_every_new_probe_fails(self, monkeypatch):
        from anki_miner.diagnostics import environment as env_module

        for name in ("_session_type", "_gpu_drivers", "_bundled_cxx_runtime", "_libmpv_source"):
            monkeypatch.setattr(env_module, name, _boom)
        snapshot = collect_environment(create_default_config())
        assert snapshot.gpu_drivers.startswith("<unavailable:")


class TestVersionFields:
    """Package + external-tool versions.

    Diagnoses a stale yt-dlp, a bundle missing ``unidic-lite``, an ffmpeg build
    without an encoder, and mismatched ASR version pairs — none of which the
    resolved *paths* alone can distinguish.
    """

    def test_absent_packages_render_absent(self, monkeypatch):
        from importlib.metadata import PackageNotFoundError

        from anki_miner.diagnostics import environment as env_module

        monkeypatch.setattr(env_module.metadata, "version", _raising(PackageNotFoundError("nope")))
        packages = collect_environment(create_default_config()).packages
        for name in env_module._PACKAGE_NAMES:
            assert f"{name}=<absent>" in packages

    def test_package_probe_failure_renders_unavailable(self, monkeypatch):
        from anki_miner.diagnostics import environment as env_module

        monkeypatch.setattr(env_module.metadata, "version", _raising(OSError("metadata unreadable")))
        packages = collect_environment(create_default_config()).packages
        assert "PyQt6=<unavailable: OSError>" in packages

    def test_tool_version_timeout_is_recorded_not_raised(self, monkeypatch):
        from anki_miner.diagnostics import environment as env_module

        monkeypatch.setattr(env_module, "resolve_ffmpeg", lambda _config: "/usr/bin/ffmpeg")
        monkeypatch.setattr(env_module, "resolve_ffprobe", lambda _config: "/usr/bin/ffprobe")
        monkeypatch.setattr(env_module, "resolve_ytdlp", lambda _config: "/usr/bin/yt-dlp")
        monkeypatch.setattr(env_module, "resolve_alass", lambda _config: "/usr/bin/alass")
        monkeypatch.setattr(
            env_module.subprocess,
            "run",
            _raising(subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)),
        )

        snapshot = collect_environment(create_default_config())

        assert snapshot.ffmpeg_version == "<unavailable: TimeoutExpired>"
        assert snapshot.ffprobe_version == "<unavailable: TimeoutExpired>"
        assert snapshot.ytdlp_version == "<unavailable: TimeoutExpired>"
        assert snapshot.alass_version == "<unavailable: TimeoutExpired>"

    def test_tool_version_takes_the_first_non_empty_output_line(self, monkeypatch):
        from anki_miner.diagnostics import environment as env_module

        monkeypatch.setattr(env_module, "resolve_ffmpeg", lambda _config: "/usr/bin/ffmpeg")
        monkeypatch.setattr(env_module, "resolve_ytdlp", lambda _config: "/usr/bin/yt-dlp")
        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(list(argv))
            if argv[0].endswith("yt-dlp"):
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="\n2025.09.01\n")
            return subprocess.CompletedProcess(argv, 0, stdout="\nffmpeg version 7.1\nbuilt with gcc\n", stderr="")

        monkeypatch.setattr(env_module.subprocess, "run", fake_run)
        snapshot = collect_environment(create_default_config())

        assert snapshot.ffmpeg_version == "ffmpeg version 7.1"
        assert snapshot.ytdlp_version == "2025.09.01"
        assert ["/usr/bin/ffmpeg", "-version"] in calls
        assert ["/usr/bin/yt-dlp", "--version"] in calls

    def test_unresolved_tool_is_never_spawned(self, monkeypatch):
        from anki_miner.diagnostics import environment as env_module

        monkeypatch.setattr(env_module, "resolve_ffmpeg", lambda _config: "")
        monkeypatch.setattr(env_module, "resolve_ffprobe", _raising(FileNotFoundError("no ffprobe")))
        monkeypatch.setattr(env_module, "resolve_ytdlp", lambda _config: "")
        monkeypatch.setattr(env_module, "resolve_alass", lambda _config: "")
        monkeypatch.setattr(env_module.subprocess, "run", _raising(AssertionError("must not spawn")))

        snapshot = collect_environment(create_default_config())

        assert snapshot.ffmpeg_version == ""
        assert snapshot.ffprobe_version == ""

    def test_version_fields_are_rendered(self, monkeypatch):
        monkeypatch.setattr("anki_miner.diagnostics.environment.subprocess.run", _raising(OSError("no spawn")))
        lines = format_environment_lines(collect_environment(create_default_config()))
        rendered = {line.split(":", 1)[0] for line in lines}
        assert {"packages", "ffmpeg_version", "ffprobe_version", "ytdlp_version", "alass_version"} <= rendered


def _boom():
    raise RuntimeError("probe failed")
