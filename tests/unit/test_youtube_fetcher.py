"""Tests for YouTubeFetcherService (probe_metadata + fetch_video)."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
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
        youtube_cookies_file=None,
        youtube_ffmpeg_location=None,
    )


@pytest.fixture
def service(yt_config: AnkiMinerConfig) -> YouTubeFetcherService:
    return YouTubeFetcherService(yt_config)


@pytest.fixture(autouse=True)
def _js_runtime_capability(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Default the JS-runtime capability probe OFF for every test.

    Keeps the existing command-construction tests deterministic and stops them
    shelling out to a real ``yt-dlp --help``. Tests marked ``real_ytdlp`` opt out
    to exercise the real function and manage the cache themselves. Issue #64.
    """
    from anki_miner.services import youtube_fetcher as yf

    real = yf._ytdlp_supports_js_runtimes  # the lru_cache-wrapped function
    real.cache_clear()
    if "real_ytdlp" not in request.keywords:
        monkeypatch.setattr(yf, "_ytdlp_supports_js_runtimes", lambda: False)
    yield
    real.cache_clear()


@pytest.fixture(autouse=True)
def _remote_component_capability(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Default the remote-components capability probe OFF for every test.

    Mirrors ``_js_runtime_capability``: keeps command-construction tests
    deterministic and off a real ``yt-dlp --help``. ``real_ytdlp``-marked tests
    opt out and manage the cache themselves. Issue #64.
    """
    from anki_miner.services import youtube_fetcher as yf

    real = yf._ytdlp_supports_remote_components  # the lru_cache-wrapped function
    real.cache_clear()
    if "real_ytdlp" not in request.keywords:
        monkeypatch.setattr(yf, "_ytdlp_supports_remote_components", lambda: False)
    yield
    real.cache_clear()


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

    def test_live_stream_null_duration_builds_video_info(self, service: YouTubeFetcherService) -> None:
        """Live streams report ``duration: null`` (T-28).

        The key exists, so the KeyError guard passes; ``int(None)`` then
        raised an uncaught TypeError that bypassed the is_live rejection.
        A null duration must instead yield a VideoInfo (duration 0) so the
        caller's "Live streams not supported" branch can fire.
        """
        payload = _make_metadata(duration=None, is_live=True)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.is_live is True
        assert info.duration_s == 0

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

    def test_probe_uses_cookies_file(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies)
        svc = YouTubeFetcherService(cfg)
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            svc.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)

    def test_probe_cookies_file_takes_precedence_over_browser(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies, youtube_cookies_from_browser="firefox")
        svc = YouTubeFetcherService(cfg)
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            svc.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)
        assert "--cookies-from-browser" not in cmd

    def test_probe_no_cookie_flags_when_unset(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies" not in cmd
        assert "--cookies-from-browser" not in cmd


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

    def test_cookies_file_in_cmd(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies)
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
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)

    def test_cookies_file_takes_precedence_over_browser_in_cmd(
        self, yt_config: AnkiMinerConfig, tmp_path: Path
    ) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies, youtube_cookies_from_browser="firefox")
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
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)
        assert "--cookies-from-browser" not in cmd

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


class TestUrlArgumentSeparator:
    """yt-dlp argument-injection guard (T-34).

    The user-controlled URL must be the final argv token AND be immediately
    preceded by a literal ``--`` end-of-options separator in every command
    builder. Otherwise a ``-``/``--``-leading "URL" (e.g. ``--update-to=...``
    or ``--config-location=<planted file>``) is parsed as a yt-dlp option ->
    binary self-replacement / RCE on the probe alone.
    """

    # A hostile "URL" that, absent ``--``, yt-dlp would treat as an option.
    _HOSTILE = "--update-to=evil/fork@tag"

    @staticmethod
    def _assert_sep_then_url(cmd: list[str], url: str) -> None:
        assert cmd[-1] == url, f"URL must be the final token, got {cmd[-1]!r}"
        assert cmd[-2] == "--", f"a literal '--' must immediately precede the URL, got {cmd[-2]!r}"

    def test_probe_metadata_inserts_separator(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata(self._HOSTILE)
        cmd = mrun.call_args.args[0]
        self._assert_sep_then_url(cmd, self._HOSTILE)

    def test_probe_playlist_inserts_separator(self, service: YouTubeFetcherService) -> None:
        payload = {
            "id": "PLxxxxxxxxxxxx",
            "title": "List",
            "playlist_count": 1,
            "entries": [{"id": "dQw4w9WgXcQ", "title": "V", "duration": 10}],
        }
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist(self._HOSTILE, limit=5)
        cmd = mrun.call_args.args[0]
        self._assert_sep_then_url(cmd, self._HOSTILE)

    def test_build_fetch_cmd_inserts_separator(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        cmd = service._build_fetch_cmd(self._HOSTILE, tmp_path, "manual_only")
        self._assert_sep_then_url(cmd, self._HOSTILE)


class TestFetchVideoResolverFallback:
    """When ``youtube_ffmpeg_location`` is unset, the fetcher falls back to
    ``resolve_ffmpeg`` so frozen builds use the bundled binary instead of
    relying on yt-dlp's PATH lookup."""

    @staticmethod
    def _capture_popen(captured: dict[str, Any]) -> Any:
        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        return fake_popen

    def test_resolver_absolute_file_used_without_path_ffmpeg(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        # youtube_ffmpeg_location unset, but ffmpeg_location override resolves to a
        # real file. Preflight must pass with NO ffmpeg on PATH, and the resolved
        # path must be passed to yt-dlp.
        from anki_miner.utils import ffmpeg_resolver

        resolved_ffmpeg = tmp_path / "bundled-ffmpeg"
        resolved_ffmpeg.write_text("#!/bin/sh\n")
        cfg = replace(yt_config, youtube_ffmpeg_location=None, ffmpeg_location=resolved_ffmpeg)
        svc = YouTubeFetcherService(cfg)
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        ffmpeg_resolver._clear_cache()
        try:
            with (
                patch("anki_miner.services.youtube_fetcher.shutil.which", return_value=None),
                patch("subprocess.Popen", side_effect=self._capture_popen(captured)),
            ):
                svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        finally:
            ffmpeg_resolver._clear_cache()

        cmd = captured["cmd"]
        assert "--ffmpeg-location" in cmd
        assert cmd[cmd.index("--ffmpeg-location") + 1] == str(resolved_ffmpeg)

    def test_resolver_bare_literal_path_missing_raises(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # Resolver returns bare "ffmpeg" (no override, not frozen) and PATH has no
        # ffmpeg -> preflight raises, mirroring the historical behavior.
        from anki_miner.utils import ffmpeg_resolver

        ffmpeg_resolver._clear_cache()
        try:
            with (
                patch("anki_miner.services.youtube_fetcher.shutil.which", return_value=None),
                pytest.raises(FfmpegNotFoundError),
            ):
                service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        finally:
            ffmpeg_resolver._clear_cache()

    def test_resolver_bare_literal_no_ffmpeg_location_flag(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        # Resolver returns bare "ffmpeg" but PATH has ffmpeg -> preflight OK and NO
        # --ffmpeg-location is added (yt-dlp uses PATH as before).
        from anki_miner.utils import ffmpeg_resolver

        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        ffmpeg_resolver._clear_cache()
        try:
            with (
                patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
                patch("subprocess.Popen", side_effect=self._capture_popen(captured)),
            ):
                service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        finally:
            ffmpeg_resolver._clear_cache()

        assert "--ffmpeg-location" not in captured["cmd"]


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

    def test_cancel_with_no_stdout_lines_reports_cancelled(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """A cancel invisible to the line loop must not be dropped (T-02).

        The only historical check lived inside the stdout line loop; a fetch
        that produced no further lines (cancel after the last line) exited
        the loop normally and completed as success — outputs resolved, cards
        mined after Stop.
        """
        _make_happy_outputs(tmp_path)  # success would be possible if the bug returns
        cancel = threading.Event()
        cancel.set()

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            # The watchdog may race in and kill an "already exited" process.
            patch(
                "anki_miner.services.youtube_fetcher.psutil.Process",
                side_effect=psutil.NoSuchProcess(4242),
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

    def test_cancel_landing_after_last_line_reports_cancelled(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """Cancel set between the final stdout line and process exit must raise."""
        _make_happy_outputs(tmp_path)
        cancel = threading.Event()

        class _CancelDuringWaitPopen(_FakePopen):
            def wait(self) -> int:
                cancel.set()  # cancel lands after stdout drained, before exit
                return super().wait()

        popen = _CancelDuringWaitPopen(["[ankimine_dl] 1024 1024"], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=popen),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.Process",
                side_effect=psutil.NoSuchProcess(4242),
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


class _BlockedStdoutPopen:
    """Popen stand-in whose stdout read blocks until the process is 'killed'.

    Models yt-dlp's silent phases (the [Merger] ffmpeg post-process, stalled
    reads, retry backoff): the reader thread is parked inside
    ``for raw in popen.stdout`` and prints nothing.
    """

    def __init__(self) -> None:
        self.pid = 4242
        self._dead = threading.Event()
        self.stdout = self._stream()

    def _stream(self):
        # Bounded block so an unfixed implementation fails the test instead
        # of wedging the suite; a 'killed' process ends the stream early.
        self._dead.wait(timeout=8.0)
        return
        yield  # pragma: no cover - makes this function a generator

    def kill_from_watchdog(self) -> None:
        """Simulate process-tree death closing stdout."""
        self._dead.set()

    def wait(self) -> int:
        return 1  # killed -> non-zero exit


class TestFetchVideoCancelDuringSilentPhase:
    def test_cancel_during_blocked_read_kills_within_watchdog_interval(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """Cancel must reach yt-dlp even when stdout is silent (T-02).

        The in-loop check only runs when yt-dlp prints; with the reader
        blocked, only an out-of-band path (watchdog) can call _kill_tree.
        """
        cancel = threading.Event()
        popen = _BlockedStdoutPopen()

        fake_parent = MagicMock()
        fake_parent.children.return_value = []
        fake_parent.terminate.side_effect = popen.kill_from_watchdog

        errors: list[BaseException] = []

        def _run_fetch() -> None:
            try:
                service.fetch_video(
                    "https://youtu.be/abc123",
                    "abc123",
                    tmp_path,
                    "manual_only",
                    cancel_event=cancel,
                )
            except BaseException as e:  # noqa: BLE001 - capture for the main thread
                errors.append(e)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=popen),
            patch("anki_miner.services.youtube_fetcher.psutil.Process", return_value=fake_parent),
            patch(
                "anki_miner.services.youtube_fetcher.psutil.wait_procs",
                return_value=([fake_parent], []),
            ),
        ):
            t = threading.Thread(target=_run_fetch, daemon=True)
            t.start()
            time.sleep(0.2)  # let the reader park in the blocked stdout
            assert t.is_alive()
            cancel.set()  # Stop All during the silent [Merger] phase
            t.join(timeout=5.0)
            assert not t.is_alive(), "fetch_video never noticed the cancel (no out-of-band kill path)"

        fake_parent.terminate.assert_called()
        assert len(errors) == 1
        assert isinstance(errors[0], YouTubeFetchError)
        assert "cancel" in str(errors[0]).lower()


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


# ---------------------------------------------------------------------------
# JS runtime auto-detection (Issue #64)
# ---------------------------------------------------------------------------


def _which_factory(available: set[str]):
    """Build a shutil.which side_effect: returns a path only for ``available``."""

    def _which(name: str, *args: Any, **kwargs: Any) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    return _which


class TestJsRuntimeArgs:
    """``_js_runtime_args`` auto-enables an available JS runtime so yt-dlp can
    solve YouTube's n-challenge (the n-challenge fails when only node is present,
    since yt-dlp's --js-runtimes defaults to deno)."""

    def _enable_capability(self, monkeypatch: pytest.MonkeyPatch, supported: bool) -> None:
        from anki_miner.services import youtube_fetcher as yf

        monkeypatch.setattr(yf, "_ytdlp_supports_js_runtimes", lambda: supported)

    def test_probe_adds_js_runtime_node(self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch) -> None:
        self._enable_capability(monkeypatch, True)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        cmd = mrun.call_args[0][0]
        assert "--js-runtimes" in cmd
        assert cmd[cmd.index("--js-runtimes") + 1] == "node"

    def test_fetch_adds_js_runtime_node(
        self, yt_config: AnkiMinerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        ffmpeg = tmp_path / "ffmpeg"
        ffmpeg.write_text("#!/bin/sh\n")
        svc = YouTubeFetcherService(replace(yt_config, youtube_ffmpeg_location=ffmpeg))
        _make_happy_outputs(tmp_path)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with patch("subprocess.Popen", side_effect=fake_popen):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert "--js-runtimes" in cmd
        assert cmd[cmd.index("--js-runtimes") + 1] == "node"

    def test_fetch_prefers_node_then_falls_back_to_quickjs(
        self, yt_config: AnkiMinerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        ffmpeg = tmp_path / "ffmpeg"
        ffmpeg.write_text("#!/bin/sh\n")
        svc = YouTubeFetcherService(replace(yt_config, youtube_ffmpeg_location=ffmpeg))
        _make_happy_outputs(tmp_path)
        # Only quickjs available (no node, no bun).
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"quickjs"}))
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with patch("subprocess.Popen", side_effect=fake_popen):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert cmd[cmd.index("--js-runtimes") + 1] == "quickjs"

    def test_no_runtime_on_path_omits_flag(
        self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        # deno is yt-dlp's default and is intentionally not searched; nothing else
        # is present, so no flag is added.
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory(set()))
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--js-runtimes" not in mrun.call_args[0][0]

    def test_unsupported_ytdlp_omits_flag(self, service: YouTubeFetcherService) -> None:
        # autouse fixture defaults the capability probe to False (old yt-dlp).
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--js-runtimes" not in mrun.call_args[0][0]

    @pytest.mark.real_ytdlp
    def test_capability_probe_true_when_help_lists_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_js_runtimes.cache_clear()
        help_text = "Usage: yt-dlp [OPTIONS] URL\n  --js-runtimes RUNTIME[:PATH]  ...\n"
        with patch("subprocess.run", return_value=_fake_run(0, help_text)):
            assert yf._ytdlp_supports_js_runtimes() is True

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_flag_absent(self) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_js_runtimes.cache_clear()
        with patch("subprocess.run", return_value=_fake_run(0, "Usage: yt-dlp [OPTIONS] URL\n  --version\n")):
            assert yf._ytdlp_supports_js_runtimes() is False

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_ytdlp_missing(self) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_js_runtimes.cache_clear()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert yf._ytdlp_supports_js_runtimes() is False


class TestRemoteComponentArgs:
    """``_remote_component_args`` lets yt-dlp fetch the EJS challenge-solver
    script. A JS runtime alone is not enough (Issue #64): yt-dlp split YouTube
    challenge solving into a runtime plus the EJS solver script, which it no
    longer auto-downloads."""

    def _enable_capability(self, monkeypatch: pytest.MonkeyPatch, supported: bool) -> None:
        from anki_miner.services import youtube_fetcher as yf

        monkeypatch.setattr(yf, "_ytdlp_supports_remote_components", lambda: supported)

    def test_probe_adds_remote_components(
        self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        cmd = mrun.call_args[0][0]
        assert "--remote-components" in cmd
        assert cmd[cmd.index("--remote-components") + 1] == "ejs:github"

    def test_fetch_adds_remote_components(
        self, yt_config: AnkiMinerConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        ffmpeg = tmp_path / "ffmpeg"
        ffmpeg.write_text("#!/bin/sh\n")
        svc = YouTubeFetcherService(replace(yt_config, youtube_ffmpeg_location=ffmpeg))
        _make_happy_outputs(tmp_path)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with patch("subprocess.Popen", side_effect=fake_popen):
            svc.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        cmd = captured["cmd"]
        assert "--remote-components" in cmd
        assert cmd[cmd.index("--remote-components") + 1] == "ejs:github"

    def test_deno_only_still_gets_ejs_flag(
        self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No supported runtime on PATH -> _js_runtime_args omits --js-runtimes
        # (deno is yt-dlp's default and intentionally not searched). The EJS flag
        # must still be added: deno-only users need the solver script too.
        self._enable_capability(monkeypatch, True)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory(set()))
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        cmd = mrun.call_args[0][0]
        assert "--js-runtimes" not in cmd
        assert "--remote-components" in cmd
        assert cmd[cmd.index("--remote-components") + 1] == "ejs:github"

    def test_unsupported_ytdlp_omits_flag(self, service: YouTubeFetcherService) -> None:
        # autouse fixture defaults the capability probe to False (old yt-dlp).
        payload = _make_metadata()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--remote-components" not in mrun.call_args[0][0]

    @pytest.mark.real_ytdlp
    def test_capability_probe_true_when_help_lists_flag(self) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_remote_components.cache_clear()
        help_text = "Usage: yt-dlp [OPTIONS] URL\n  --remote-components COMPONENT  ...\n"
        with patch("subprocess.run", return_value=_fake_run(0, help_text)):
            assert yf._ytdlp_supports_remote_components() is True

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_flag_absent(self) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_remote_components.cache_clear()
        with patch("subprocess.run", return_value=_fake_run(0, "Usage: yt-dlp [OPTIONS] URL\n  --version\n")):
            assert yf._ytdlp_supports_remote_components() is False

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_ytdlp_missing(self) -> None:
        from anki_miner.services import youtube_fetcher as yf

        yf._ytdlp_supports_remote_components.cache_clear()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert yf._ytdlp_supports_remote_components() is False


# ---------------------------------------------------------------------------
# probe_playlist
# ---------------------------------------------------------------------------


def _make_playlist_entry(
    video_id: str = "dQw4w9WgXcQ",
    title: str = "Test Video",
    duration: int | None = 120,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a plausible yt-dlp flat-playlist entry."""
    base: dict[str, Any] = {
        "id": video_id,
        "title": title,
        "duration": duration,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }
    base.update(overrides)
    return base


def _make_playlist_payload(
    title: str = "Test Playlist",
    playlist_id: str = "PLtest123456789",
    playlist_count: int | None = 3,
    entries: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Build a plausible yt-dlp --flat-playlist --dump-single-json payload."""
    if entries is None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "Video 1", 60),
            _make_playlist_entry("bbbbbbbbbbb", "Video 2", 90),
            _make_playlist_entry("ccccccccccc", "Video 3", 120),
        ]
    base: dict[str, Any] = {
        "title": title,
        "id": playlist_id,
        "entries": entries,
    }
    if playlist_count is not None:
        base["playlist_count"] = playlist_count
    base.update(overrides)
    return base


class TestProbePlaylist:
    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_happy_path_entries_parsed_in_order(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.title == "Test Playlist"
        assert info.playlist_id == "PLtest123456789"
        assert info.total_count == 3
        assert len(info.entries) == 3
        assert info.entries[0].video_id == "aaaaaaaaaaa"
        assert info.entries[1].video_id == "bbbbbbbbbbb"
        assert info.entries[2].video_id == "ccccccccccc"
        # Order preserved
        assert [e.title for e in info.entries] == ["Video 1", "Video 2", "Video 3"]

    def test_canonical_urls_built_from_video_id(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(entries=[_make_playlist_entry("aaaaaaaaaaa", url="https://some-other-url")])
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        # URL must be canonical, NOT from entry's own url field
        assert info.entries[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"

    def test_duration_parsed_as_int(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(entries=[_make_playlist_entry("aaaaaaaaaaa", duration=183)])
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].duration_s == 183

    def test_missing_duration_yields_none(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(entries=[_make_playlist_entry("aaaaaaaaaaa", duration=None)])
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].duration_s is None

    def test_missing_playlist_count_yields_none(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(playlist_count=None)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.total_count is None

    def test_missing_title_defaults_to_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        del payload["title"]
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.title == "Playlist"

    def test_empty_title_defaults_to_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(title="")
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.title == "Playlist"

    def test_missing_id_yields_none_playlist_id(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        del payload["id"]
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.playlist_id is None

    # ------------------------------------------------------------------
    # Command shape asserts
    # ------------------------------------------------------------------

    def test_command_contains_flat_playlist_flags(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--flat-playlist" in cmd
        assert "--skip-download" in cmd
        assert "--dump-single-json" in cmd

    def test_command_contains_playlist_items_limit_plus_one(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--playlist-items" in cmd
        assert cmd[cmd.index("--playlist-items") + 1] == "1:11"  # limit+1 = 11

    def test_command_does_not_contain_no_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--no-playlist" not in cmd

    def test_command_appends_url_last(self, service: YouTubeFetcherService) -> None:
        url = "https://www.youtube.com/playlist?list=PLtest123456789"
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist(url, limit=5)
        cmd = mrun.call_args[0][0]
        assert cmd[-1] == url

    def test_command_contains_cookies_from_browser(self, yt_config: AnkiMinerConfig) -> None:
        cfg = replace(yt_config, youtube_cookies_from_browser="chrome")
        svc = YouTubeFetcherService(cfg)
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            svc.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=5)
        cmd = mrun.call_args[0][0]
        assert "--cookies-from-browser" in cmd
        assert cmd[cmd.index("--cookies-from-browser") + 1] == "chrome"

    def test_command_contains_cookies_file(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies)
        svc = YouTubeFetcherService(cfg)
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            svc.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=5)
        cmd = mrun.call_args[0][0]
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)

    def test_command_no_cookie_flags_when_unset(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=5)
        cmd = mrun.call_args[0][0]
        assert "--cookies" not in cmd
        assert "--cookies-from-browser" not in cmd

    # ------------------------------------------------------------------
    # Over-cap / limit+1 detection
    # ------------------------------------------------------------------

    def test_returns_limit_plus_one_entries_when_playlist_bigger(self, service: YouTubeFetcherService) -> None:
        # 11 entries returned for limit=10; fetcher must NOT truncate
        # Use fixed-width IDs: 'a' * 10 + hex digit -> exactly 11 chars, all valid
        hex_chars = "0123456789abcde"
        entries = [_make_playlist_entry(f"{'a' * 10}{hex_chars[i]}", f"Video {i}", 60) for i in range(11)]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        assert len(info.entries) == 11  # all limit+1 entries returned (no truncation)

    # ------------------------------------------------------------------
    # Skipping logic
    # ------------------------------------------------------------------

    def test_private_video_entry_skipped(self, service: YouTubeFetcherService) -> None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "[Private video]"),
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_deleted_video_entry_skipped(self, service: YouTubeFetcherService) -> None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "[Deleted video]"),
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_null_entry_skipped(self, service: YouTubeFetcherService) -> None:
        entries: list[Any] = [
            None,
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_entry_missing_id_skipped(self, service: YouTubeFetcherService) -> None:
        entry_no_id: dict[str, Any] = {"title": "No ID", "duration": 60}
        entries = [
            entry_no_id,
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1

    def test_entry_bad_video_id_skipped(self, service: YouTubeFetcherService) -> None:
        bad_entry = _make_playlist_entry("NOT-A-VALID-ID!!", "Bad ID video")
        entries = [
            bad_entry,
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_missing_title_on_entry_defaults_to_video_id(self, service: YouTubeFetcherService) -> None:
        entry: dict[str, Any] = {"id": "aaaaaaaaaaa", "duration": 60}
        payload = _make_playlist_payload(entries=[entry])
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].title == "aaaaaaaaaaa"

    def test_empty_title_on_entry_defaults_to_video_id(self, service: YouTubeFetcherService) -> None:
        entry = _make_playlist_entry("aaaaaaaaaaa", title="")
        payload = _make_playlist_payload(entries=[entry])
        with patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].title == "aaaaaaaaaaa"

    def test_all_entries_unusable_raises(self, service: YouTubeFetcherService) -> None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "[Private video]"),
            _make_playlist_entry("bbbbbbbbbbb", "[Deleted video]"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with (
            patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(YouTubeFetchError, match="no accessible videos"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_missing_entries_key_raises(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        del payload["entries"]
        with (
            patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(YouTubeFetchError, match="not a playlist"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_entries_not_a_list_raises(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        payload["entries"] = "not a list"
        with (
            patch("subprocess.run", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(YouTubeFetchError, match="not a playlist"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_non_zero_exit_raises_with_stderr_tail(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "subprocess.run",
                return_value=_fake_run(1, stdout="", stderr="ERROR: Playlist unavailable"),
            ),
            pytest.raises(YouTubeFetchError, match="exit 1"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_timeout_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=120),
            ),
            pytest.raises(YouTubeFetchError, match="timed out"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_ytdlp_missing_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch("subprocess.run", side_effect=FileNotFoundError()),
            pytest.raises(YouTubeFetchError, match="yt-dlp executable not found"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_non_json_output_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch("subprocess.run", return_value=_fake_run(0, "not-json-output", stderr="some warn")),
            pytest.raises(YouTubeFetchError, match="non-JSON"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
