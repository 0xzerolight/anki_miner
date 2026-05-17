"""Tests for YouTubeFetcherService (probe_metadata + fetch_video)."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psutil  # type: ignore[import-untyped]
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    VideoTooLongError,
    YouTubeFetchError,
)
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_metadata(**overrides: Any) -> dict[str, Any]:
    """Build a plausible yt-dlp --dump-single-json payload."""
    base: dict[str, Any] = {
        "id": "dQw4w9WgXcQ",
        "title": "Test Video",
        "duration": 120,
        "uploader": "TestChannel",
        "thumbnail": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg",
        "age_limit": 0,
        "is_live": False,
        "language": "ja",
        "subtitles": {},
        "automatic_captions": {},
    }
    base.update(overrides)
    return base


def _fake_run(returncode: int, stdout: str = "", stderr: str = "") -> Any:
    """Build a subprocess.CompletedProcess-like object."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class _FakePopen:
    """Minimal stand-in for subprocess.Popen with a scripted stdout stream."""

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        # Terminate each line with \n to match Popen(text=True) behavior.
        self.stdout = iter(f"{line}\n" for line in lines)
        self._returncode = returncode
        self.pid = 4242
        self.wait_called = 0

    def wait(self) -> int:
        self.wait_called += 1
        return self._returncode


@pytest.fixture
def yt_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        media_temp_folder=tmp_path / "media",
        jmdict_path=tmp_path / "JMdict_e",
        youtube_max_duration_s=3600,
        youtube_max_height=720,
        youtube_cookies_from_browser=None,
        youtube_ffmpeg_location=None,
    )


@pytest.fixture
def service(yt_config: AnkiMinerConfig) -> YouTubeFetcherService:
    return YouTubeFetcherService(yt_config)


# ---------------------------------------------------------------------------
# probe_metadata
# ---------------------------------------------------------------------------


class TestProbeMetadata:
    def test_happy_path_manual_subs(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(subtitles={"ja": [{"ext": "vtt"}]})
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.video_id == "dQw4w9WgXcQ"
        assert info.title == "Test Video"
        assert info.duration_s == 120
        assert info.has_manual_ja_subs is True
        assert info.has_auto_ja_subs is False
        assert info.thumbnail_url.endswith(".jpg")
        assert info.uploader == "TestChannel"
        assert info.is_live is False
        assert info.is_age_restricted is False

    def test_happy_path_native_auto_only(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            subtitles={},
            automatic_captions={"ja": [{"name": "Japanese"}]},
            language="ja",
        )
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is False
        assert info.has_auto_ja_subs is True

    def test_translated_from_english_auto_ja_rejected(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese (from English)"}]},
            language="en",
        )
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False

    def test_non_ja_language_with_auto_ja_rejected(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese"}]},
            language="en",
        )
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False

    def test_missing_required_key_raises(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        del payload["id"]
        with (
            patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload), stderr="some warn")),
            pytest.raises(YouTubeFetchError, match="incomplete metadata"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_missing_optional_keys_ok(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        del payload["thumbnail"]
        del payload["uploader"]
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.thumbnail_url is None
        assert info.uploader is None

    def test_video_too_long(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(duration=99999)
        with (
            patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(VideoTooLongError),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_empty_subtitles_dict(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(subtitles={})
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is False

    def test_age_restricted(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(age_limit=18)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.is_age_restricted is True

    def test_is_live(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(is_live=True)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.is_live is True

    def test_non_zero_exit_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "subprocess.run",
                return_value=_fake_run(1, stdout="", stderr="ERROR: Video unavailable"),
            ),
            pytest.raises(YouTubeFetchError, match="exit 1"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_probe_uses_cookies_from_browser(self, yt_config: AnkiMinerConfig) -> None:
        cfg = replace(yt_config, youtube_cookies_from_browser="firefox")
        svc = YouTubeFetcherService(cfg)
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            svc.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies-from-browser" in cmd
        assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"


# ---------------------------------------------------------------------------
# _has_native_auto_ja (helper unit tests)
# ---------------------------------------------------------------------------


class TestHasNativeAutoJa:
    def _call(self, data: dict[str, Any]) -> bool:
        return YouTubeFetcherService._has_native_auto_ja(data)

    def test_no_automatic_captions(self) -> None:
        assert self._call({}) is False

    def test_ja_key_missing(self) -> None:
        assert self._call({"automatic_captions": {"en": [{}]}}) is False

    def test_ja_empty_list(self) -> None:
        assert self._call({"automatic_captions": {"ja": []}}) is False

    def test_non_ja_language(self) -> None:
        data = {
            "automatic_captions": {"ja": [{"name": "Japanese"}]},
            "language": "en",
        }
        assert self._call(data) is False

    def test_translated_track_name(self) -> None:
        data = {
            "automatic_captions": {"ja": [{"name": "Japanese (from English)"}]},
            "language": "ja",
        }
        assert self._call(data) is False

    def test_native_ja_track(self) -> None:
        data = {
            "automatic_captions": {"ja": [{"name": "Japanese"}]},
            "language": "ja",
        }
        assert self._call(data) is True

    def test_language_missing_defaults_ok(self) -> None:
        # No language key and no translated name -> treat as native.
        data = {"automatic_captions": {"ja": [{"name": "Japanese"}]}}
        assert self._call(data) is True


# ---------------------------------------------------------------------------
# fetch_video
# ---------------------------------------------------------------------------


def _touch(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_happy_outputs(workspace: Path, video_id: str = "abc123") -> tuple[Path, Path]:
    video = workspace / f"{video_id}.mp4"
    sub = workspace / f"{video_id}.ja.srt"
    _touch(video, b"fake-mp4")
    _touch(sub, b"1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    return video, sub


class TestFetchVideoPreflight:
    def test_no_ffmpeg_on_path_raises(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value=None),
            pytest.raises(FfmpegNotFoundError),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_configured_ffmpeg_missing_raises(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cfg = replace(yt_config, youtube_ffmpeg_location=tmp_path / "no-such-ffmpeg")
        svc = YouTubeFetcherService(cfg)
        with pytest.raises(FfmpegNotFoundError):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")


class TestFetchVideoCommand:
    def test_manual_only_adds_write_sub(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert "--write-sub" in cmd
        assert "--write-auto-sub" not in cmd
        assert "--sub-lang" in cmd and cmd[cmd.index("--sub-lang") + 1] == "ja"
        assert "--convert-subs" in cmd and cmd[cmd.index("--convert-subs") + 1] == "srt"

    def test_auto_only_adds_write_auto_sub(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "auto_only")
        cmd = captured["cmd"]
        assert "--write-auto-sub" in cmd
        assert "--write-sub" not in cmd

    def test_cookies_from_browser_in_cmd(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cfg = replace(yt_config, youtube_cookies_from_browser="firefox")
        svc = YouTubeFetcherService(cfg)
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert "--cookies-from-browser" in cmd
        assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"

    def test_ffmpeg_location_in_cmd(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        fake_ffmpeg = tmp_path / "my-ffmpeg"
        fake_ffmpeg.write_text("#!/bin/sh\n")
        cfg = replace(yt_config, youtube_ffmpeg_location=fake_ffmpeg)
        svc = YouTubeFetcherService(cfg)
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with patch("subprocess.Popen", side_effect=fake_popen):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert "--ffmpeg-location" in cmd
        assert cmd[cmd.index("--ffmpeg-location") + 1] == str(fake_ffmpeg)


class TestFetchVideoProgress:
    def test_progress_parse_with_total(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        lines = [
            "[ankimine_dl] 512 1024",
            "[ankimine_dl] 1024 1024",
        ]
        calls: list[tuple[str, float | None]] = []

        def cb(label: str, frac: float | None) -> None:
            calls.append((label, frac))

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=cb,
            )
        assert calls == [("Downloading video", 0.5), ("Downloading video", 1.0)]

    def test_progress_parse_with_na_total(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        lines = ["[ankimine_dl] 1024 NA"]
        calls: list[tuple[str, float | None]] = []

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=lambda label, frac: calls.append((label, frac)),
            )
        assert calls == [("Downloading video", None)]

    def test_warning_prefixed_progress_line_still_parses(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        lines = ["WARNING: whatever [ankimine_dl] 1024 2048"]
        calls: list[tuple[str, float | None]] = []

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=lambda label, frac: calls.append((label, frac)),
            )
        assert calls == [("Downloading video", 0.5)]

    def test_postprocess_detection_fires_once(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        lines = [
            "[ankimine_dl] 512 1024",
            "[Merger] Merging formats into 'abc123.mp4'",
            "[SubtitleConvertor] Converting subtitles",
            "[Merger] Another merger line",
        ]
        calls: list[tuple[str, float | None]] = []

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=lambda label, frac: calls.append((label, frac)),
            )
        merging_calls = [c for c in calls if c == ("Merging", None)]
        assert len(merging_calls) == 1


class TestFetchVideoErrors:
    def test_bot_detection(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        lines = ["ERROR: Sign in to confirm you're not a bot"]
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=1)),
            pytest.raises(BotDetectionError),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_cookie_database_locked(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        lines = ["ERROR: could not decrypt cookies: database is locked"]
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=1)),
            pytest.raises(CookieDatabaseLockedError),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_generic_failure(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        lines = ["ERROR: Video unavailable"]
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=1)),
            pytest.raises(YouTubeFetchError) as exc,
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        # Not the bot/cookies subclasses.
        assert not isinstance(exc.value, (BotDetectionError, CookieDatabaseLockedError))

    def test_missing_output_after_exit_zero(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # No files created in workspace.
        lines: list[str] = []
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
            pytest.raises(YouTubeFetchError, match="expected output files are missing"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_zero_byte_subtitle_after_exit_zero(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        video = tmp_path / "abc123.mp4"
        sub = tmp_path / "abc123.ja.srt"
        _touch(video, b"fake-mp4")
        sub.write_bytes(b"")  # zero-byte

        lines: list[str] = []
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
            pytest.raises(YouTubeFetchError, match="zero-byte subtitle"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")


class TestFetchVideoCancel:
    def test_cancel_event_triggers_kill_tree(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        lines = ["[ankimine_dl] 100 1000", "[ankimine_dl] 200 1000"]
        cancel = threading.Event()
        cancel.set()  # pre-set; the first line iteration will notice.

        fake_parent = MagicMock()
        fake_parent.children.return_value = []
        fake_parent.terminate = MagicMock()

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.Process",
                return_value=fake_parent,
            ),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.wait_procs",
                return_value=([fake_parent], []),
            ),
            pytest.raises(YouTubeFetchError, match="Cancelled by user"),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                cancel_event=cancel,
            )
        fake_parent.terminate.assert_called_once()


class TestFetchVideoHappyPath:
    def test_returns_fetched_media_manual(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        video, sub = _make_happy_outputs(tmp_path)
        lines = ["[ankimine_dl] 1024 1024"]
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            out = service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert out.video_file == video
        assert out.subtitle_file == sub
        assert out.sub_source == "manual"

    def test_returns_fetched_media_auto(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
        ):
            out = service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "auto_only")
        assert out.sub_source == "auto"


def test_probe_metadata_timeout_wrapped(service: YouTubeFetcherService) -> None:
    with (
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=60),
        ),
        pytest.raises(YouTubeFetchError, match="timed out"),
    ):
        service.probe_metadata("https://youtu.be/abc123")


def test_probe_metadata_missing_yt_dlp(service: YouTubeFetcherService) -> None:
    with (
        patch("subprocess.run", side_effect=FileNotFoundError()),
        pytest.raises(YouTubeFetchError, match="yt-dlp executable not found"),
    ):
        service.probe_metadata("https://youtu.be/abc123")


# ---------------------------------------------------------------------------
# H4 — Flatpak/Snap Firefox cookie guidance (platform-specific)
# ---------------------------------------------------------------------------


class TestFlatpakSnapCookieGuidance:
    """The cookie-locked error message gains Flatpak/Snap guidance only on Linux
    when stderr also mentions a missing profile."""

    _PROFILE_NOT_FOUND_LINES = [
        "ERROR: could not decrypt cookies: database is locked",
        "ERROR: Profile default-release not found",
    ]
    _GUIDANCE_SUBSTR = "Flatpak or Snap"

    def test_linux_profile_not_found_adds_guidance(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(self._PROFILE_NOT_FOUND_LINES, 1)),
            patch("anki_miner.services.youtube_fetcher.sys.platform", "linux"),
            pytest.raises(CookieDatabaseLockedError) as exc,
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert self._GUIDANCE_SUBSTR in str(exc.value)

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_non_linux_omits_guidance(
        self,
        service: YouTubeFetcherService,
        tmp_path: Path,
        platform: str,
    ) -> None:
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(self._PROFILE_NOT_FOUND_LINES, 1)),
            patch("anki_miner.services.youtube_fetcher.sys.platform", platform),
            pytest.raises(CookieDatabaseLockedError) as exc,
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert self._GUIDANCE_SUBSTR not in str(exc.value)


# ---------------------------------------------------------------------------
# H5 — _kill_tree psutil edge cases
# ---------------------------------------------------------------------------


class TestKillTreeEdgeCases:
    """Cancel path resilience against races between terminate() and process exit."""

    def _run_cancel(
        self,
        service: YouTubeFetcherService,
        tmp_path: Path,
        process_side_effect: Any = None,
        process_return: Any = None,
        wait_procs_return: Any = ([], []),
    ) -> None:
        """Run fetch_video with a pre-set cancel_event and the given psutil mocks."""
        cancel = threading.Event()
        cancel.set()
        lines = ["[ankimine_dl] 100 1000"]

        process_patch_kwargs: dict[str, Any] = {}
        if process_side_effect is not None:
            process_patch_kwargs["side_effect"] = process_side_effect
        else:
            process_patch_kwargs["return_value"] = process_return

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.Process",
                **process_patch_kwargs,
            ),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.wait_procs",
                return_value=wait_procs_return,
            ),
            pytest.raises(YouTubeFetchError, match="Cancelled by user"),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                cancel_event=cancel,
            )

    def test_parent_vanished_before_children(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # psutil.Process(pid) raises NoSuchProcess -> cancel path must exit cleanly.
        self._run_cancel(
            service,
            tmp_path,
            process_side_effect=psutil.NoSuchProcess(4242),
        )

    def test_child_vanished_mid_iteration(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # One child whose terminate() raises NoSuchProcess. Parent survives.
        dead_child = MagicMock()
        dead_child.terminate.side_effect = psutil.NoSuchProcess(9999)

        parent = MagicMock()
        parent.children.return_value = [dead_child]
        parent.terminate = MagicMock()

        self._run_cancel(
            service,
            tmp_path,
            process_return=parent,
            wait_procs_return=([parent, dead_child], []),
        )

        # Parent still got terminated even though the child went away first.
        parent.terminate.assert_called_once()
        dead_child.terminate.assert_called_once()

    def test_sigkill_escalation_when_wait_procs_times_out(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # wait_procs reports the parent still alive after SIGTERM -> .kill() fires.
        parent = MagicMock()
        parent.children.return_value = []
        parent.terminate = MagicMock()
        parent.kill = MagicMock()

        self._run_cancel(
            service,
            tmp_path,
            process_return=parent,
            wait_procs_return=([], [parent]),
        )

        parent.terminate.assert_called_once()
        parent.kill.assert_called_once()


# ---------------------------------------------------------------------------
# M2 — Presenter exception suppression
# ---------------------------------------------------------------------------


def test_presenter_show_info_exception_is_swallowed(
    yt_config: AnkiMinerConfig,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A raising presenter must not abort the fetch loop; we log and continue."""
    _make_happy_outputs(tmp_path)
    presenter = MagicMock()
    presenter.show_info.side_effect = RuntimeError("boom")
    svc = YouTubeFetcherService(yt_config, presenter=presenter)

    # A line that is not progress and not post-process -> forwarded to presenter.
    lines = ["[youtube] abc123: Downloading webpage"]

    with (
        patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        caplog.at_level(logging.DEBUG, logger="anki_miner.services.youtube_fetcher"),
    ):
        out = svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    # Presenter was attempted and raised.
    presenter.show_info.assert_called()
    # Error was logged at debug, not propagated.
    assert any("presenter.show_info raised" in rec.message for rec in caplog.records)
    # Fetch still completed normally.
    assert out.sub_source == "manual"


# ---------------------------------------------------------------------------
# M3 — Progress regex miss
# ---------------------------------------------------------------------------


class TestProgressRegexMiss:
    """Lines that do not match _PROGRESS_RE must not call progress_cb and must
    not crash the loop."""

    def test_non_matching_lines_do_not_invoke_cb(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        # None of these carry the ankimine_dl sentinel -> _PROGRESS_RE.search misses.
        lines = [
            "[download] 50% of 10MiB at 1.23MiB/s ETA 00:05",
            "random noise line",
            "[info] abc123: some metadata",
        ]
        calls: list[tuple[str, float | None]] = []

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            out = service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=lambda label, frac: calls.append((label, frac)),
            )

        # No Downloading-video progress entries from non-matching lines.
        assert [c for c in calls if c[0] == "Downloading video"] == []
        assert out.sub_source == "manual"

    def test_mixed_miss_then_match_still_reports_match(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        lines = [
            "random noise",
            "[download] 10% of 10MiB",
            "[ankimine_dl] 512 1024",  # this one matches
            "[download] Destination: abc123.mp4",
        ]
        calls: list[tuple[str, float | None]] = []

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=0)),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                progress_cb=lambda label, frac: calls.append((label, frac)),
            )

        # Exactly one progress entry despite three non-matching lines around it.
        assert calls == [("Downloading video", 0.5)]
