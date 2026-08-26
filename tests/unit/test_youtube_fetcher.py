"""Tests for YouTubeFetcherService (probe_metadata + fetch_video)."""

from __future__ import annotations

import collections
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import psutil
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    DubAudioUnavailableError,
    FfmpegNotFoundError,
    NoJapaneseSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

_REAL_KILLPG = os.killpg if sys.platform != "win32" else None

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
    """Build an object accepted by subprocess and supervisor call sites."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    proc.state = SupervisedState.COMPLETED if returncode == 0 else SupervisedState.FAILED
    proc.error = None
    return proc


def _pid_is_live(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _probe_tree_helper(tmp_path: Path) -> tuple[Path, Path, Path]:
    parent_pid_path = tmp_path / "probe-parent.pid"
    child_pid_path = tmp_path / "probe-child.pid"
    body = "\n".join(
        [
            "import os",
            "import pathlib",
            "import subprocess",
            "import sys",
            "import time",
            f"pathlib.Path({str(parent_pid_path)!r}).write_text(str(os.getpid()))",
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
            f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid))",
            "time.sleep(60)",
        ]
    )
    if sys.platform == "win32":
        script = tmp_path / "yt-dlp-probe-helper.py"
        script.write_text(body, encoding="utf-8")
        helper = tmp_path / "yt-dlp-probe-helper.cmd"
        helper.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
    else:
        helper = tmp_path / "yt-dlp-probe-helper"
        helper.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        helper.chmod(0o755)
    return helper, parent_pid_path, child_pid_path


class _FakePopen:
    """Minimal stand-in for subprocess.Popen with a scripted stdout stream."""

    def __init__(self, lines: list[str], returncode: int = 0) -> None:
        output = "".join(line if line.endswith("\n") else f"{line}\n" for line in lines)
        self.stdout = io.BytesIO(output.encode("utf-8"))
        self.stderr = None
        self._returncode = returncode
        self.returncode: int | None = returncode
        self.pid = 4242
        self.wait_called = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_called += 1
        self.returncode = self._returncode
        return self._returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


@pytest.fixture
def yt_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        media_temp_folder=tmp_path / "media",
        jmdict_path=tmp_path / "JMdict_e",
        youtube_max_duration_s=3600,
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
    from anki_miner.services import ytdlp_invocation as yf

    real = yf.ytdlp_supports_js_runtimes  # the lru_cache-wrapped function
    real.cache_clear()
    if "real_ytdlp" not in request.keywords:
        monkeypatch.setattr(yf, "ytdlp_supports_js_runtimes", lambda _path: False)
    yield
    real.cache_clear()


@pytest.fixture(autouse=True)
def _remote_component_capability(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Default the remote-components capability probe OFF for every test.

    Mirrors ``_js_runtime_capability``: keeps command-construction tests
    deterministic and off a real ``yt-dlp --help``. ``real_ytdlp``-marked tests
    opt out and manage the cache themselves. Issue #64.
    """
    from anki_miner.services import ytdlp_invocation as yf

    real = yf.ytdlp_supports_remote_components  # the lru_cache-wrapped function
    real.cache_clear()
    if "real_ytdlp" not in request.keywords:
        monkeypatch.setattr(yf, "ytdlp_supports_remote_components", lambda _path: False)
    yield
    real.cache_clear()


@pytest.fixture(autouse=True)
def _stub_supervisor_killpg() -> Any:
    with patch("anki_miner.utils.process_supervisor.os.killpg"):
        yield


# ---------------------------------------------------------------------------
# probe_metadata
# ---------------------------------------------------------------------------


class TestProbeMetadata:
    def test_happy_path_manual_subs(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(subtitles={"ja": [{"ext": "vtt"}]})
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.video_id == "dQw4w9WgXcQ"
        assert info.title == "Test Video"
        assert info.duration_s == 120
        assert info.has_manual_ja_subs is True
        assert info.has_auto_ja_subs is False
        assert info.is_live is False
        assert info.is_age_restricted is False

    def test_manual_ja_is_not_name_filtered(self, service: YouTubeFetcherService) -> None:
        """``subtitles["ja"]`` is always the genuine manual Japanese track.

        yt-dlp files manual *translations* under ``ja-<origlang>``, not ``ja``
        (``_video.py``: ``trans_code += f"-{lang_code}"`` alongside the
        ``" from %s"`` name suffix). So a name filter on this branch could only ever
        reject a track whose uploader-chosen title happens to contain "from" — which
        is why the manual branch deliberately does not filter on names.
        """
        payload = _make_metadata(subtitles={"ja": [{"ext": "vtt", "name": "Japanese (from the manga)"}]})
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is True

    def test_manual_translation_key_is_not_mistaken_for_native(self, service: YouTubeFetcherService) -> None:
        """A ``ja-en`` manual translation must not register as manual Japanese."""
        payload = _make_metadata(subtitles={"ja-en": [{"ext": "vtt", "name": "Japanese from English"}]})
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is False

    def test_happy_path_native_auto_only(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            subtitles={},
            automatic_captions={"ja": [{"name": "Japanese"}]},
            language="ja",
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is False
        assert info.has_auto_ja_subs is True

    def test_translated_from_english_auto_ja_rejected(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese (from English)"}]},
            language="en",
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False

    def test_dubbed_video_sets_dub_flag(self, service: YouTubeFetcherService) -> None:
        """EN original with a JA auto-dub: MT ja captions + ja audio track -> dub route."""
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese"}], "en-orig": [{"name": "English (Original)"}]},
            language="en",
            formats=[
                {"vcodec": "avc1", "acodec": "none", "language": None},
                {"vcodec": "none", "acodec": "opus", "language": "en-US"},
                {"vcodec": "none", "acodec": "opus", "language": "ja"},
            ],
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False
        assert info.has_dub_ja_subs is True

    def test_mt_captions_without_dub_audio_stay_rejected(self, service: YouTubeFetcherService) -> None:
        """The original MT-caption rejection is intact when no JA audio exists."""
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese"}], "en-orig": [{"name": "English (Original)"}]},
            language="en",
            formats=[{"vcodec": "none", "acodec": "opus", "language": "en-US"}],
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False
        assert info.has_dub_ja_subs is False

    def test_native_auto_video_does_not_set_dub_flag(self, service: YouTubeFetcherService) -> None:
        """Exclusivity: a native-auto video takes the auto_only route, never auto_dub."""
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese"}], "ja-orig": [{"name": "Japanese (Original)"}]},
            language="ja",
            formats=[{"vcodec": "none", "acodec": "opus", "language": "ja"}],
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is True
        assert info.has_dub_ja_subs is False

    def test_no_ja_captions_no_dub_flag(self, service: YouTubeFetcherService) -> None:
        """A JA audio track alone is not mineable — captions are still required."""
        payload = _make_metadata(
            automatic_captions={"en-orig": [{"name": "English (Original)"}]},
            language="en",
            formats=[{"vcodec": "none", "acodec": "opus", "language": "ja"}],
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_dub_ja_subs is False

    def test_non_ja_language_with_auto_ja_rejected(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(
            automatic_captions={"ja": [{"name": "Japanese"}]},
            language="en",
        )
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_auto_ja_subs is False

    def test_missing_required_key_raises(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        del payload["id"]
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(0, json.dumps(payload), stderr="some warn"),
            ),
            pytest.raises(YouTubeFetchError, match="incomplete metadata"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_missing_optional_keys_ok(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        del payload["thumbnail"]
        del payload["uploader"]
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.video_id == "dQw4w9WgXcQ"

    def test_video_too_long(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(duration=99999)
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(VideoTooLongError),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_empty_subtitles_dict(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(subtitles={})
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.has_manual_ja_subs is False

    def test_age_restricted(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(age_limit=18)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.is_age_restricted is True

    def test_is_live(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata(is_live=True)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_metadata("https://youtu.be/abc123")
        assert info.is_live is True
        assert info.duration_s == 0

    def test_non_zero_exit_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, stdout="", stderr="ERROR: Video unavailable"),
            ),
            pytest.raises(YouTubeFetchError, match="exit 1"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_probe_uses_cookies_from_browser(self, yt_config: AnkiMinerConfig) -> None:
        cfg = replace(yt_config, youtube_cookies_from_browser="firefox")
        svc = YouTubeFetcherService(cfg)
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            svc.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)
        assert "--cookies-from-browser" not in cmd

    def test_probe_no_cookie_flags_when_unset(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        args, _ = mrun.call_args
        cmd = args[0]
        assert "--cookies" not in cmd
        assert "--cookies-from-browser" not in cmd

    def test_non_json_output_raises(self, service: YouTubeFetcherService) -> None:
        """Exit 0 but unparseable stdout (the site or yt-dlp broke) wraps the
        JSONDecodeError into a YouTubeFetchError instead of leaking it."""
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(0, "not-json", stderr="some warn"),
            ),
            pytest.raises(YouTubeFetchError, match="non-JSON"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_empty_stdout_raises_non_json(self, service: YouTubeFetcherService) -> None:
        """Exit 0 with empty stdout is also non-JSON, not an empty VideoInfo."""
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, "", stderr="")),
            pytest.raises(YouTubeFetchError, match="non-JSON"),
        ):
            service.probe_metadata("https://youtu.be/abc123")


class TestProbeClassifiesLikeFetch:
    """A probe must recognize the same failures a fetch does (Issue #119).

    The probe paths used to raise the raw stderr tail unconditionally, so a user
    whose cookie source was unreadable got yt-dlp's own text and a link to
    yt-dlp's issue tracker in a queue-row tooltip — while this app's remedy for
    that exact failure sat unused on the fetch path. Both probes now run the
    shared classifier; the raw tail stays only as the unrecognized fallback.

    Stderr strings here are verbatim yt-dlp output — see
    ``tests/unit/test_ytdlp_invocation.py`` for why that matters.
    """

    #: cookies.py:363 — the failure in Issue #119's screenshot, doubled the way
    #: yt-dlp really emits it (logger.error, then YoutubeDL re-reports the cause).
    CHROME_COPY_FAILED = (
        "ERROR: Could not copy Chrome cookie database. See  "
        "https://github.com/yt-dlp/yt-dlp/issues/7271  for more info\n"
        "ERROR: Could not copy Chrome cookie database. See  "
        "https://github.com/yt-dlp/yt-dlp/issues/7271  for more info"
    )

    @staticmethod
    def _service_with_chrome_cookies(yt_config: AnkiMinerConfig) -> YouTubeFetcherService:
        return YouTubeFetcherService(replace(yt_config, youtube_cookies_from_browser="chrome"))

    def test_metadata_probe_maps_cookie_copy_failure(self, yt_config: AnkiMinerConfig) -> None:
        service = self._service_with_chrome_cookies(yt_config)
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=self.CHROME_COPY_FAILED),
            ),
            pytest.raises(CookieDatabaseLockedError) as exc,
        ):
            service.probe_metadata("https://youtu.be/abc123")
        assert "Close chrome" in str(exc.value)
        # The whole point: yt-dlp's text and issue link stop here.
        assert "Could not copy" not in str(exc.value)
        assert "github.com" not in str(exc.value)

    def test_playlist_probe_maps_cookie_copy_failure(self, yt_config: AnkiMinerConfig) -> None:
        service = self._service_with_chrome_cookies(yt_config)
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=self.CHROME_COPY_FAILED),
            ),
            pytest.raises(CookieDatabaseLockedError) as exc,
        ):
            service.probe_playlist("https://youtube.com/playlist?list=PL1", limit=10)
        assert "Close chrome" in str(exc.value)

    def test_metadata_probe_maps_missing_cookie_db(self, yt_config: AnkiMinerConfig) -> None:
        """cookies.py:318 — reproducible locally on a box with no Chrome installed."""
        service = self._service_with_chrome_cookies(yt_config)
        stderr = 'ERROR: could not find chrome cookies database in "/home/u/.config/google-chrome"'
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=stderr),
            ),
            pytest.raises(CookieDatabaseLockedError) as exc,
        ):
            service.probe_metadata("https://youtu.be/abc123")
        assert "No cookie database found for chrome" in str(exc.value)
        # Closing a browser that was never there cannot help.
        assert "Close chrome" not in str(exc.value)

    def test_metadata_probe_maps_bot_wall(self, service: YouTubeFetcherService) -> None:
        stderr = "ERROR: [youtube] abc123: Sign in to confirm you're not a bot."
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=stderr),
            ),
            pytest.raises(BotDetectionError),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_metadata_probe_maps_stale_extractor(self, service: YouTubeFetcherService) -> None:
        """An images-only listing at probe time means the same stale yt-dlp it
        means at fetch time, and wants the same "update yt-dlp" remedy."""
        stderr = "ERROR: Only images are available for download, use --list-formats to see them"
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=stderr),
            ),
            pytest.raises(YouTubeFetchError, match="Update yt-dlp now"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_probe_never_raises_the_dub_route_error(self, service: YouTubeFetcherService) -> None:
        """The auto_dub branch is gated on sub_mode, which a probe never sets.

        A probe requests no format at all, so "no format" from a probe means a
        stale extractor, not a vanished dub track.
        """
        stderr = "ERROR: Requested format is not available"
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=stderr),
            ),
            pytest.raises(YouTubeFetchError) as exc,
        ):
            service.probe_metadata("https://youtu.be/abc123")
        assert not isinstance(exc.value, DubAudioUnavailableError)

    def test_unrecognized_probe_failure_keeps_the_verbatim_tail(self, service: YouTubeFetcherService) -> None:
        """The raw-tail fallback is deliberate — an unknown yt-dlp error is more
        use on screen in full than paraphrased."""
        stderr = "ERROR: unable to download video data: HTTP Error 403: Forbidden (attempts=3)."
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", stderr=stderr),
            ),
            pytest.raises(YouTubeFetchError) as exc,
        ):
            service.probe_metadata("https://youtu.be/abc123")
        message = str(exc.value)
        assert "metadata probe failed (exit 1)" in message
        assert "HTTP Error 403: Forbidden" in message
        assert not isinstance(exc.value, (BotDetectionError, CookieDatabaseLockedError))


class TestAppOwnedCommandIsolation:
    def test_metadata_probe_ignores_user_config(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert mrun.call_args.args[0][1] == "--ignore-config"

    def test_playlist_probe_ignores_user_config(self, service: YouTubeFetcherService) -> None:
        payload = {
            "id": "PLxxxxxxxxxxxx",
            "title": "List",
            "playlist_count": 1,
            "entries": [{"id": "dQw4w9WgXcQ", "title": "V", "duration": 10}],
        }
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=secret", limit=5)
        assert mrun.call_args.args[0][1] == "--ignore-config"

    def test_fetch_ignores_user_config(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        assert service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "manual_only")[1] == "--ignore-config"

    @pytest.mark.real_ytdlp
    def test_capability_probes_ignore_user_config(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_js_runtimes.cache_clear()
        yf.ytdlp_supports_remote_components.cache_clear()
        with patch("subprocess.run", return_value=_fake_run(0, "old help")) as mrun:
            yf.ytdlp_supports_js_runtimes("yt-dlp")
            yf.ytdlp_supports_remote_components("yt-dlp")
        assert [call.args[0][1] for call in mrun.call_args_list] == ["--ignore-config", "--ignore-config"]


@pytest.mark.parametrize("probe", ["metadata", "playlist"])
def test_probe_log_redacts_query_and_fragment(
    service: YouTubeFetcherService,
    caplog: pytest.LogCaptureFixture,
    probe: str,
) -> None:
    url = "https://www.youtube.com/watch?v=public&si=PRIVATE_TOKEN#PRIVATE_FRAGMENT"
    with (
        caplog.at_level("INFO", logger="anki_miner.services.youtube_fetcher"),
        patch.object(service, "_ytdlp", side_effect=YtdlpNotFoundError("stop after log")),
        pytest.raises(YtdlpNotFoundError),
    ):
        if probe == "metadata":
            service.probe_metadata(url)
        else:
            service.probe_playlist(url, limit=5)

    assert "https://www.youtube.com/watch" in caplog.text
    assert "PRIVATE_TOKEN" not in caplog.text
    assert "PRIVATE_FRAGMENT" not in caplog.text


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

    def test_native_ja_track(self) -> None:
        data = {
            "automatic_captions": {"ja": [{"name": "Japanese"}]},
            "language": "ja",
        }
        assert self._call(data) is True

    # -- step 1: ja-orig is the authoritative native signal -----------------

    def test_ja_orig_accepted_even_when_language_names_a_dub(self) -> None:
        """The reported false negative.

        ``language`` is derived from the *selected audio format*
        (``info_dict.update(best_format)``), so on a Japanese video carrying dubbed
        audio tracks it can name the dub. Keying on it rejected genuinely native
        Japanese videos with "No Japanese subtitles available for this video."
        ``ja-orig`` is registered only for the ASR track's own language, so it
        survives that.
        """
        data = {
            "automatic_captions": {
                "ja": [{"name": "Japanese"}],
                "ja-orig": [{"name": "Japanese (Original)"}],
            },
            "language": "en",
        }
        assert self._call(data) is True

    # -- step 2: another <lang>-orig proves the original is not Japanese ----

    def test_other_orig_key_rejects_translated_ja(self) -> None:
        """The false positive that used to slip through when ``language`` was absent.

        Verified live against an English video: it exposes ``ja`` (machine
        translated, named plainly "Japanese") plus ``en-orig``, and no ``ja-orig``.
        """
        data = {
            "automatic_captions": {
                "ja": [{"name": "Japanese"}],
                "en-orig": [{"name": "English (Original)"}],
            },
        }
        assert self._call(data) is False

    # -- step 3: no *-orig at all -> fall back to the language check --------

    def test_language_missing_and_no_orig_keys_defaults_ok(self) -> None:
        """Absence of ``*-orig`` proves nothing, so keep the old behavior here.

        ``-orig`` registration is conditional: it needs a non-empty
        ``translationLanguages`` (only web/mweb player responses carry it) or an
        ``isTranslatable`` track. Rejecting on a bare ``ja`` with no ``language``
        would newly break genuinely native videos — the exact symptom being fixed.
        """
        data = {"automatic_captions": {"ja": [{"name": "Japanese"}]}}
        assert self._call(data) is True

    def test_empty_orig_value_does_not_count_as_a_signal(self) -> None:
        data = {"automatic_captions": {"ja": [{"name": "Japanese"}], "en-orig": []}}
        assert self._call(data) is True

    def test_auto_translated_track_name_is_not_a_signal(self) -> None:
        """Pins that the auto branch does NOT filter on track names.

        yt-dlp appends the " from <lang>" suffix only under ``if is_manual_subs``
        (``_video.py``), so an auto-translated track is named plainly "Japanese" and a
        name check here was dead code. This input is not something yt-dlp can
        actually emit for ``automatic_captions``; the assertion exists so the check is
        not "restored" on the strength of a plausible-looking name.
        """
        data = {
            "automatic_captions": {"ja": [{"name": "Japanese (from English)"}]},
            "language": "ja",
        }
        assert self._call(data) is True


# ---------------------------------------------------------------------------
# _has_ja_audio_track (helper unit tests)
# ---------------------------------------------------------------------------


class TestHasJaAudioTrack:
    """_has_ja_audio_track: detect a selectable Japanese audio-only format."""

    @staticmethod
    def _call(data: dict[str, Any]) -> bool:
        return YouTubeFetcherService._has_ja_audio_track(data)

    def test_no_formats_key(self) -> None:
        assert self._call({}) is False

    def test_empty_formats(self) -> None:
        assert self._call({"formats": []}) is False

    def test_ja_audio_only_format(self) -> None:
        data = {"formats": [{"vcodec": "none", "acodec": "opus", "language": "ja"}]}
        assert self._call(data) is True

    def test_ja_regional_variant(self) -> None:
        data = {"formats": [{"vcodec": "none", "acodec": "opus", "language": "ja-JP"}]}
        assert self._call(data) is True

    def test_missing_vcodec_treated_as_audio_only(self) -> None:
        # yt-dlp sometimes omits vcodec instead of writing "none".
        data = {"formats": [{"acodec": "opus", "language": "ja"}]}
        assert self._call(data) is True

    def test_muxed_ja_format_ignored(self) -> None:
        # A muxed format's language names the container audio, not a dub track;
        # bestaudio[language~='^ja(-|$)'] could never select it anyway.
        data = {"formats": [{"vcodec": "avc1", "acodec": "mp4a", "language": "ja"}]}
        assert self._call(data) is False

    def test_non_ja_audio_only_ignored(self) -> None:
        data = {"formats": [{"vcodec": "none", "acodec": "opus", "language": "en-US"}]}
        assert self._call(data) is False

    def test_language_absent_ignored(self) -> None:
        data = {"formats": [{"vcodec": "none", "acodec": "opus"}]}
        assert self._call(data) is False

    def test_javanese_not_mistaken_for_japanese(self) -> None:
        # "jv" is Javanese; also guard the prefix match against bare startswith
        # false-positives — only "ja" exact or "ja-<region>" qualify.
        data = {"formats": [{"vcodec": "none", "acodec": "opus", "language": "jav"}]}
        assert self._call(data) is False


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
        # No fallback_allowed -> the auto flag stays off, so a non-native auto
        # track can never be substituted for the requested manual one.
        assert "--write-auto-sub" not in cmd
        assert "--sub-lang" in cmd and cmd[cmd.index("--sub-lang") + 1] == "ja"
        assert "--convert-subs" in cmd and cmd[cmd.index("--convert-subs") + 1] == "srt"

    def test_manual_only_with_fallback_allowed_adds_both_flags(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """Both flags = yt-dlp's own manual-preferred fallback.

        ``process_subtitles`` loads manual subs first and fills only the languages
        they do not cover from ``automatic_captions``, so both flags together write
        exactly one file and prefer the manual track. That is the whole fallback
        mechanism — no second yt-dlp invocation is needed.
        """
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                fallback_allowed=True,
            )
        cmd = captured["cmd"]
        assert "--write-sub" in cmd
        assert "--write-auto-sub" in cmd

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

    def test_auto_only_ignores_fallback_allowed(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """There is nothing to fall back *from* when auto is already the request."""
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["cmd"] = cmd
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "auto_only",
                fallback_allowed=True,
            )
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist(self._HOSTILE, limit=5)
        cmd = mrun.call_args.args[0]
        self._assert_sep_then_url(cmd, self._HOSTILE)

    def test_build_fetch_cmd_inserts_separator(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        cmd = service._build_fetch_cmd(self._HOSTILE, tmp_path, "manual_only")
        self._assert_sep_then_url(cmd, self._HOSTILE)


class TestBuildFetchCmdSocketTimeout:
    """OVH-039: _build_fetch_cmd must include --socket-timeout so stalled
    downloads fail fast into existing retry logic instead of hanging forever."""

    def test_socket_timeout_present(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "manual_only")
        assert "--socket-timeout" in cmd

    def test_socket_timeout_value_is_numeric(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "manual_only")
        idx = cmd.index("--socket-timeout")
        value = cmd[idx + 1]
        assert value.isdigit(), f"--socket-timeout value must be numeric, got {value!r}"

    def test_auto_sub_mode_also_has_socket_timeout(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "auto_only")
        assert "--socket-timeout" in cmd


class TestBuildFetchCmdPercentPath:
    """Bug Y5: a media_temp_folder containing '%' must not corrupt yt-dlp's
    output template. The directory goes via --paths (a literal path where % is
    not a metacharacter); -o stays a bare, un-prefixed template."""

    def test_percent_dir_not_embedded_in_output_template(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        workspace = tmp_path / "100% Japanese"
        workspace.mkdir()
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", workspace, "manual_only")

        template = cmd[cmd.index("--output") + 1]
        # The template is still a real yt-dlp template, but the workspace path
        # (with its stray %) is NOT embedded in it.
        assert template == "%(id)s.%(ext)s"
        assert str(workspace) not in template

    def test_percent_dir_carried_via_paths(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        workspace = tmp_path / "100% Japanese"
        workspace.mkdir()
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", workspace, "manual_only")

        assert "--paths" in cmd
        paths_value = cmd[cmd.index("--paths") + 1]
        assert str(workspace) in paths_value

    def test_resolution_still_finds_outputs_in_percent_dir(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "100% Japanese"
        workspace.mkdir()
        _touch(workspace / "abc123.mp4", b"v")
        _touch(workspace / "abc123.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        result = service._resolve_outputs(workspace, "abc123", "manual_only")
        assert result.video_file.name == "abc123.mp4"
        assert result.subtitle_file.name == "abc123.ja.srt"


class TestBuildFetchCmdAutoDub:
    """The auto-dub route's format selection and its failure diagnostics."""

    def test_build_fetch_cmd_auto_dub_requests_ja_audio_fail_closed(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """auto_dub must pin the JA audio track with no non-JA fallback: MT ja
        subs over foreign audio is the exact mismatch the caption gate exists
        to prevent, so an unavailable dub must fail the fetch, not degrade it."""
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "auto_dub")
        assert "--write-auto-sub" in cmd
        assert "--write-sub" not in cmd
        fmt = cmd[cmd.index("--format") + 1]
        assert fmt == "bestvideo[height<=720]+bestaudio[language~='^ja(-|$)']"
        assert "/" not in fmt  # no fallback alternative may reintroduce non-JA audio

    def test_auto_dub_selector_excludes_javanese(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """A bare [language^=ja] prefix test would also admit "jav" (Javanese);
        the selector must be regex-anchored the same way the probe is."""
        cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "auto_dub")
        fmt = cmd[cmd.index("--format") + 1]
        assert "language^=ja" not in fmt
        assert "bestaudio[language~=" in fmt

    def test_build_fetch_cmd_auto_only_format_unchanged(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """The two existing modes keep the historical selector byte-identical."""
        for mode in ("manual_only", "auto_only"):
            cmd = service._build_fetch_cmd("https://youtu.be/abc123", tmp_path, mode)
            fmt = cmd[cmd.index("--format") + 1]
            assert fmt == "bestvideo[height<=720]+bestaudio/best[height<=720]"

    def test_raise_for_error_names_missing_dub_track(self, service: YouTubeFetcherService) -> None:
        """'Requested format is not available' on the dub route means either
        side of the selector vanished between probe and fetch — saying 'update
        yt-dlp' would mislead — and must be typed as a deterministic failure."""
        tail = collections.deque(["ERROR: Requested format is not available"])
        with pytest.raises(DubAudioUnavailableError, match="Japanese-audio") as excinfo:
            service._raise_for_error(tail, "auto_dub")
        assert issubclass(DubAudioUnavailableError, YouTubeFetchError)
        assert "Japanese-audio" in str(excinfo.value)
        with pytest.raises(YouTubeFetchError, match="yt-dlp is out of date"):
            service._raise_for_error(tail, "auto_only")


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
        resolved_ffmpeg.chmod(0o755)
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
        merging_calls = [c for c in calls if c == ("Merging audio and video", None)]
        assert len(merging_calls) == 1


class TestStaleExtractorMapping:
    """Format-unavailable stderr must point at yt-dlp freshness, not at --format.

    YouTube keeps rolling out DRM and SABR-only experiments per client; an older
    yt-dlp then finds no usable format and says "Requested format is not available",
    which reads like a bad format selector rather than "your yt-dlp is too old".
    """

    @pytest.mark.parametrize(
        "stderr_line",
        [
            "ERROR: [youtube] abc123: Requested format is not available. Use --list-formats",
            "WARNING: Only images are available for download. use --list-formats to see them",
            "WARNING: This video is drm protected and only images are available for download",
        ],
    )
    def test_maps_to_an_actionable_message(
        self, service: YouTubeFetcherService, tmp_path: Path, stderr_line: str
    ) -> None:
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([stderr_line], returncode=1)),
            pytest.raises(YouTubeFetchError, match="Update yt-dlp now"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_unrelated_failure_keeps_the_generic_message(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """Do not blame yt-dlp's age for every non-zero exit."""
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(["ERROR: Video unavailable"], returncode=1)),
            pytest.raises(YouTubeFetchError, match="exited non-zero"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_routine_sabr_warning_does_not_mask_later_failure(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        lines = [
            "WARNING: Some web client https formats have been skipped; YouTube is forcing SABR streaming for this client.",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
        ]
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen(lines, returncode=1)),
            pytest.raises(YouTubeFetchError) as exc_info,
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

        message = str(exc_info.value)
        assert "HTTP Error 403" in message
        assert "Update yt-dlp now" not in message


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

    def test_video_without_subtitle_raises_no_japanese_subtitles(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """The video landed but no subtitle did — a deterministic failure.

        yt-dlp reports "There are no subtitles for the requested languages" as an
        info line and exits 0. It writes subtitles before the video, so by this point
        the whole video has already downloaded. The dedicated subclass is what stops
        the queue worker from retrying and paying for a second download.
        """
        _touch(tmp_path / "abc123.mp4", b"fake-mp4")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            pytest.raises(NoJapaneseSubtitlesError, match="wrote no Japanese subtitle"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_no_japanese_subtitles_error_is_a_youtube_fetch_error(self) -> None:
        """Subclass, not sibling.

        ``YouTubeFetchError`` is the documented catch-all for ``fetch_video`` and
        ``process_youtube_url``; a sibling (the ``FfmpegNotFoundError`` shape) would
        leak past every caller relying on it.
        """
        assert issubclass(NoJapaneseSubtitlesError, YouTubeFetchError)

    def test_vtt_subtitle_accepted_when_conversion_did_not_run(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """``--convert-subs srt`` is an ffmpeg postprocessor and can be skipped.

        pysubs2 parses vtt natively, so refusing the surviving vtt threw away a
        usable subtitle and reported "expected output files are missing" instead.
        """
        _touch(tmp_path / "abc123.mp4", b"fake-mp4")
        _touch(tmp_path / "abc123.ja.vtt", b"WEBVTT\n\n00:01.000 --> 00:02.000\nhello\n")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
        ):
            result = service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert result.subtitle_file.name == "abc123.ja.vtt"

    def test_srt_preferred_when_both_srt_and_vtt_survive(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """A kept original alongside the converted file is not an ambiguity."""
        _touch(tmp_path / "abc123.mp4", b"fake-mp4")
        _touch(tmp_path / "abc123.ja.vtt", b"WEBVTT\n")
        _touch(tmp_path / "abc123.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhello\n")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
        ):
            result = service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert result.subtitle_file.name == "abc123.ja.srt"


def test_ytdlp_hang_killed_by_deadline(service: YouTubeFetcherService, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def timed_out(_command: list[str], **kwargs: Any) -> SupervisedResult:
        captured.update(kwargs)
        return SupervisedResult(SupervisedState.TIMED_OUT, -signal.SIGKILL, "", "")

    with (
        patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("anki_miner.services.youtube_fetcher.run_supervised", side_effect=timed_out, create=True),
        patch("subprocess.Popen", side_effect=AssertionError("legacy Popen path used")),
        pytest.raises(YouTubeFetchError, match="timed out"),
    ):
        service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    assert captured["timeout_s"] == 3 * 60 * 60
    assert captured["combine_stderr"] is True


class TestFetchVideoCancel:
    def test_cancel_event_triggers_kill_tree(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        lines = ["[ankimine_dl] 100 1000", "[ankimine_dl] 200 1000"]
        cancel = threading.Event()
        cancel.set()  # pre-set; the first line iteration will notice.

        popen = _FakePopen(lines, returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=popen),
            patch("anki_miner.utils.process_supervisor.os.killpg") as killpg,
            pytest.raises(YouTubeFetchError, match="Cancelled by user"),
        ):
            service.fetch_video(
                "https://youtu.be/abc123",
                "abc123",
                tmp_path,
                "manual_only",
                cancel_event=cancel,
            )
        killpg.assert_any_call(popen.pid, signal.SIGTERM)
        killpg.assert_any_call(popen.pid, signal.SIGKILL)

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
            patch("anki_miner.utils.process_supervisor.os.killpg"),
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
            def wait(self, timeout: float | None = None) -> int:
                cancel.set()  # cancel lands after stdout drained, before exit
                return super().wait(timeout)

        popen = _CancelDuringWaitPopen(["[ankimine_dl] 1024 1024"], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=popen),
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
        self.stdout = self
        self.stderr = None
        self.returncode: int | None = None

    def read(self, _size: int) -> bytes:
        # Bounded block so an unfixed implementation fails the test instead
        # of wedging the suite; a 'killed' process ends the stream early.
        self._dead.wait(timeout=8.0)
        return b""

    def poll(self) -> int | None:
        return self.returncode

    def kill_from_supervisor(self, _pid: int, sig: int) -> None:
        """Simulate process-tree death closing stdout."""
        self.returncode = -sig
        self._dead.set()

    def wait(self, timeout: float | None = None) -> int:
        self._dead.wait(timeout)
        return 1  # killed -> non-zero exit


class TestFetchVideoCancelDuringSilentPhase:
    def test_cancel_during_blocked_read_kills_within_watchdog_interval(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        """Cancel must reach yt-dlp even when stdout is silent (T-02).

        The supervisor must observe cancellation independently of pipe output.
        """
        cancel = threading.Event()
        popen = _BlockedStdoutPopen()

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
            patch("anki_miner.utils.process_supervisor.os.killpg", side_effect=popen.kill_from_supervisor) as killpg,
        ):
            t = threading.Thread(target=_run_fetch, daemon=True)
            t.start()
            time.sleep(0.2)  # let the reader park in the blocked stdout
            assert t.is_alive()
            cancel.set()  # Stop All during the silent [Merger] phase
            t.join(timeout=5.0)
            assert not t.is_alive(), "fetch_video never noticed the cancel (no out-of-band kill path)"

        assert killpg.called
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


class TestResolveOutputsAmbiguity:
    """``_resolve_outputs`` must refuse to silently pick one when yt-dlp left
    more than one video or subtitle matching the id glob — an ambiguous
    workspace means the wrong file could be mined. Exercised through the public
    ``fetch_video`` (exit 0, globbing happens on success) so the wrapping at the
    fetch boundary is covered too.
    """

    def test_multiple_video_outputs_raises(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # Two video files + one subtitle for the same id.
        _touch(tmp_path / "abc123.mp4", b"v1")
        _touch(tmp_path / "abc123.webm", b"v2")
        _touch(tmp_path / "abc123.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            pytest.raises(YouTubeFetchError, match="Multiple video outputs"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_multiple_subtitle_outputs_raises(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        # One video + two .ja.srt subtitle files for the same id.
        _touch(tmp_path / "abc123.mp4", b"v1")
        _touch(tmp_path / "abc123.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        # A second subtitle whose stem still globs on "abc123*" and ends .ja.srt.
        _touch(tmp_path / "abc123.extra.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nyo\n")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            pytest.raises(YouTubeFetchError, match="Multiple subtitle outputs"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_video_ambiguity_reported_before_subtitle(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """Both ambiguous → the video check runs first, so its message wins."""
        _touch(tmp_path / "abc123.mp4", b"v1")
        _touch(tmp_path / "abc123.mkv", b"v2")
        _touch(tmp_path / "abc123.ja.srt", b"s1")
        _touch(tmp_path / "abc123.extra.ja.srt", b"s2")
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            pytest.raises(YouTubeFetchError, match="Multiple video outputs"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_subtitle_stat_oserror_wrapped(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        """An OSError statting the resolved subtitle (vanished / unreadable
        between glob and stat) wraps to YouTubeFetchError, not a raw OSError."""
        _make_happy_outputs(tmp_path)
        real_stat = Path.stat

        def fake_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.name.endswith(".ja.srt"):
                raise OSError("stat boom")
            return real_stat(self, *args, **kwargs)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", return_value=_FakePopen([], returncode=0)),
            patch.object(Path, "stat", fake_stat),
            pytest.raises(YouTubeFetchError, match="Subtitle file unreadable after fetch"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_zero_byte_video_rejected(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _touch(tmp_path / "abc123.mp4", b"")
        _touch(tmp_path / "abc123.ja.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")

        with pytest.raises(YouTubeFetchError, match="zero-byte video"):
            service._resolve_outputs(tmp_path, "abc123", "manual_only")


def test_probe_metadata_timeout_wrapped(service: YouTubeFetcherService) -> None:
    timed_out = SupervisedResult(SupervisedState.TIMED_OUT, None, "", "")
    with (
        patch(
            "anki_miner.services.youtube_fetcher.run_supervised",
            return_value=timed_out,
        ),
        pytest.raises(YouTubeFetchError, match="timed out"),
    ):
        service.probe_metadata("https://youtu.be/abc123")


def test_probe_metadata_uses_supervisor_timeout(service: YouTubeFetcherService) -> None:
    timed_out = SupervisedResult(SupervisedState.TIMED_OUT, None, "", "")
    with (
        patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=timed_out) as supervised,
        pytest.raises(YouTubeFetchError, match="timed out"),
    ):
        service.probe_metadata("https://youtu.be/abc123", timeout_s=0.1)
    assert supervised.call_args.kwargs["timeout_s"] == 0.1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group integration coverage")
@pytest.mark.parametrize("probe", ["metadata", "playlist"])
def test_probe_timeout_terminates_helper_tree(
    yt_config: AnkiMinerConfig,
    tmp_path: Path,
    probe: str,
) -> None:
    helper, parent_pid_path, child_pid_path = _probe_tree_helper(tmp_path)
    service = YouTubeFetcherService(replace(yt_config, ytdlp_location=helper))

    assert _REAL_KILLPG is not None
    with (
        patch("anki_miner.utils.process_supervisor.os.killpg", side_effect=_REAL_KILLPG),
        pytest.raises(YouTubeFetchError, match="timed out"),
    ):
        if probe == "metadata":
            service.probe_metadata("https://youtu.be/abc123", timeout_s=1.0)
        else:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest", limit=5, timeout_s=1.0)

    parent_pid = int(parent_pid_path.read_text())
    child_pid = int(child_pid_path.read_text())
    try:
        assert not _pid_is_live(parent_pid)
        assert not _pid_is_live(child_pid)
    finally:
        for pid in (parent_pid, child_pid):
            if _pid_is_live(pid):
                psutil.Process(pid).kill()


def _assert_windows_probe_timeout_terminates_helper_tree(
    yt_config: AnkiMinerConfig,
    tmp_path: Path,
    probe: str,
) -> None:
    helper, parent_pid_path, child_pid_path = _probe_tree_helper(tmp_path)
    service = YouTubeFetcherService(replace(yt_config, ytdlp_location=helper))
    started = time.monotonic()

    with pytest.raises(YouTubeFetchError, match="timed out"):
        if probe == "metadata":
            service.probe_metadata("https://youtu.be/abc123", timeout_s=2.0)
        else:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest", limit=5, timeout_s=2.0)

    elapsed = time.monotonic() - started
    assert elapsed < 5.0
    parent_pid = int(parent_pid_path.read_text())
    child_pid = int(child_pid_path.read_text())
    try:
        assert not _pid_is_live(parent_pid)
        assert not _pid_is_live(child_pid)
    finally:
        for pid in (parent_pid, child_pid):
            if _pid_is_live(pid):
                psutil.Process(pid).kill()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration coverage")
def test_probe_metadata_timeout_terminates_windows_js_runtime_tree(
    yt_config: AnkiMinerConfig,
    tmp_path: Path,
) -> None:
    _assert_windows_probe_timeout_terminates_helper_tree(yt_config, tmp_path, "metadata")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration coverage")
def test_probe_playlist_timeout_terminates_windows_js_runtime_tree(
    yt_config: AnkiMinerConfig,
    tmp_path: Path,
) -> None:
    _assert_windows_probe_timeout_terminates_helper_tree(yt_config, tmp_path, "playlist")


def test_probe_metadata_missing_yt_dlp(service: YouTubeFetcherService) -> None:
    missing = SupervisedResult(SupervisedState.FAILED, None, "", "", FileNotFoundError())
    with (
        patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=missing),
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
        from anki_miner.services import ytdlp_invocation as yf

        monkeypatch.setattr(yf, "ytdlp_supports_js_runtimes", lambda _path: supported)

    def test_probe_adds_js_runtime_node(self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch) -> None:
        self._enable_capability(monkeypatch, True)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--js-runtimes" not in mrun.call_args[0][0]

    def test_unsupported_ytdlp_omits_flag(self, service: YouTubeFetcherService) -> None:
        # autouse fixture defaults the capability probe to False (old yt-dlp).
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--js-runtimes" not in mrun.call_args[0][0]

    @pytest.mark.real_ytdlp
    def test_capability_probe_true_when_help_lists_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_js_runtimes.cache_clear()
        help_text = "Usage: yt-dlp [OPTIONS] URL\n  --js-runtimes RUNTIME[:PATH]  ...\n"
        with patch("subprocess.run", return_value=_fake_run(0, help_text)) as mock_run:
            assert yf.ytdlp_supports_js_runtimes("yt-dlp") is True
        assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_flag_absent(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_js_runtimes.cache_clear()
        with patch("subprocess.run", return_value=_fake_run(0, "Usage: yt-dlp [OPTIONS] URL\n  --version\n")):
            assert yf.ytdlp_supports_js_runtimes("yt-dlp") is False

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_ytdlp_missing(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_js_runtimes.cache_clear()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert yf.ytdlp_supports_js_runtimes("yt-dlp") is False


class TestRemoteComponentArgs:
    """``_remote_component_args`` lets yt-dlp fetch the EJS challenge-solver
    script. A JS runtime alone is not enough (Issue #64): yt-dlp split YouTube
    challenge solving into a runtime plus the EJS solver script, which it no
    longer auto-downloads."""

    def _enable_capability(self, monkeypatch: pytest.MonkeyPatch, supported: bool) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        monkeypatch.setattr(yf, "ytdlp_supports_remote_components", lambda _path: supported)

    def test_probe_adds_remote_components(
        self, service: YouTubeFetcherService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable_capability(monkeypatch, True)
        monkeypatch.setattr("anki_miner.services.youtube_fetcher.shutil.which", _which_factory({"node"}))
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        cmd = mrun.call_args[0][0]
        assert "--js-runtimes" not in cmd
        assert "--remote-components" in cmd
        assert cmd[cmd.index("--remote-components") + 1] == "ejs:github"

    def test_unsupported_ytdlp_omits_flag(self, service: YouTubeFetcherService) -> None:
        # autouse fixture defaults the capability probe to False (old yt-dlp).
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_metadata("https://youtu.be/abc123")
        assert "--remote-components" not in mrun.call_args[0][0]

    @pytest.mark.real_ytdlp
    def test_capability_probe_true_when_help_lists_flag(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_remote_components.cache_clear()
        help_text = "Usage: yt-dlp [OPTIONS] URL\n  --remote-components COMPONENT  ...\n"
        with patch("subprocess.run", return_value=_fake_run(0, help_text)) as mock_run:
            assert yf.ytdlp_supports_remote_components("yt-dlp") is True
        assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_flag_absent(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_remote_components.cache_clear()
        with patch("subprocess.run", return_value=_fake_run(0, "Usage: yt-dlp [OPTIONS] URL\n  --version\n")):
            assert yf.ytdlp_supports_remote_components("yt-dlp") is False

    @pytest.mark.real_ytdlp
    def test_capability_probe_false_when_ytdlp_missing(self) -> None:
        from anki_miner.services import ytdlp_invocation as yf

        yf.ytdlp_supports_remote_components.cache_clear()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert yf.ytdlp_supports_remote_components("yt-dlp") is False


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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        # URL must be canonical, NOT from entry's own url field
        assert info.entries[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"

    def test_duration_parsed_as_int(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(entries=[_make_playlist_entry("aaaaaaaaaaa", duration=183)])
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].duration_s == 183

    def test_missing_duration_yields_none(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(entries=[_make_playlist_entry("aaaaaaaaaaa", duration=None)])
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].duration_s is None

    def test_missing_playlist_count_yields_none(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(playlist_count=None)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.total_count is None

    def test_missing_title_defaults_to_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        del payload["title"]
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.title == "Playlist"

    def test_empty_title_defaults_to_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload(title="")
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.title == "Playlist"

    def test_missing_id_yields_none_playlist_id(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        del payload["id"]
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.playlist_id is None

    # ------------------------------------------------------------------
    # Command shape asserts
    # ------------------------------------------------------------------

    def test_command_contains_flat_playlist_flags(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--flat-playlist" in cmd
        assert "--skip-download" in cmd
        assert "--dump-single-json" in cmd

    def test_command_contains_playlist_items_limit_plus_one(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--playlist-items" in cmd
        assert cmd[cmd.index("--playlist-items") + 1] == "1:11"  # limit+1 = 11

    def test_command_does_not_contain_no_playlist(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=10)
        cmd = mrun.call_args[0][0]
        assert "--no-playlist" not in cmd

    def test_command_appends_url_last(self, service: YouTubeFetcherService) -> None:
        url = "https://www.youtube.com/playlist?list=PLtest123456789"
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            service.probe_playlist(url, limit=5)
        cmd = mrun.call_args[0][0]
        assert cmd[-1] == url

    def test_command_contains_cookies_from_browser(self, yt_config: AnkiMinerConfig) -> None:
        cfg = replace(yt_config, youtube_cookies_from_browser="chrome")
        svc = YouTubeFetcherService(cfg)
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            svc.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=5)
        cmd = mrun.call_args[0][0]
        assert "--cookies-from-browser" in cmd
        assert cmd[cmd.index("--cookies-from-browser") + 1] == "chrome"

    def test_command_contains_cookies_file(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        cfg = replace(yt_config, youtube_cookies_file=cookies)
        svc = YouTubeFetcherService(cfg)
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            svc.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=5)
        cmd = mrun.call_args[0][0]
        assert "--cookies" in cmd
        assert cmd[cmd.index("--cookies") + 1] == str(cookies)

    def test_command_no_cookie_flags_when_unset(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_deleted_video_entry_skipped(self, service: YouTubeFetcherService) -> None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "[Deleted video]"),
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_null_entry_skipped(self, service: YouTubeFetcherService) -> None:
        entries: list[Any] = [
            None,
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
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
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1

    def test_entry_bad_video_id_skipped(self, service: YouTubeFetcherService) -> None:
        bad_entry = _make_playlist_entry("NOT-A-VALID-ID!!", "Bad ID video")
        entries = [
            bad_entry,
            _make_playlist_entry("bbbbbbbbbbb", "Normal video"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert len(info.entries) == 1
        assert info.entries[0].video_id == "bbbbbbbbbbb"

    def test_missing_title_on_entry_defaults_to_video_id(self, service: YouTubeFetcherService) -> None:
        entry: dict[str, Any] = {"id": "aaaaaaaaaaa", "duration": 60}
        payload = _make_playlist_payload(entries=[entry])
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].title == "aaaaaaaaaaa"

    def test_empty_title_on_entry_defaults_to_video_id(self, service: YouTubeFetcherService) -> None:
        entry = _make_playlist_entry("aaaaaaaaaaa", title="")
        payload = _make_playlist_payload(entries=[entry])
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ):
            info = service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)
        assert info.entries[0].title == "aaaaaaaaaaa"

    def test_all_entries_unusable_raises(self, service: YouTubeFetcherService) -> None:
        entries = [
            _make_playlist_entry("aaaaaaaaaaa", "[Private video]"),
            _make_playlist_entry("bbbbbbbbbbb", "[Deleted video]"),
        ]
        payload = _make_playlist_payload(entries=entries)
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))),
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
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(YouTubeFetchError, match="not a playlist"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_entries_not_a_list_raises(self, service: YouTubeFetcherService) -> None:
        payload = _make_playlist_payload()
        payload["entries"] = "not a list"
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))),
            pytest.raises(YouTubeFetchError, match="not a playlist"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_non_zero_exit_raises_with_stderr_tail(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, stdout="", stderr="ERROR: Playlist unavailable"),
            ),
            pytest.raises(YouTubeFetchError, match="exit 1"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_timeout_raises(self, service: YouTubeFetcherService) -> None:
        timed_out = SupervisedResult(SupervisedState.TIMED_OUT, None, "", "")
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=timed_out,
            ),
            pytest.raises(YouTubeFetchError, match="timed out"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_uses_supervisor_timeout(self, service: YouTubeFetcherService) -> None:
        timed_out = SupervisedResult(SupervisedState.TIMED_OUT, None, "", "")
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=timed_out) as supervised,
            pytest.raises(YouTubeFetchError, match="timed out"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50, timeout_s=0.1)
        assert supervised.call_args.kwargs["timeout_s"] == 0.1

    def test_ytdlp_missing_raises(self, service: YouTubeFetcherService) -> None:
        missing = SupervisedResult(SupervisedState.FAILED, None, "", "", FileNotFoundError())
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=missing),
            pytest.raises(YouTubeFetchError, match="yt-dlp executable not found"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)

    def test_non_json_output_raises(self, service: YouTubeFetcherService) -> None:
        with (
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(0, "not-json-output", stderr="some warn"),
            ),
            pytest.raises(YouTubeFetchError, match="non-JSON"),
        ):
            service.probe_playlist("https://www.youtube.com/playlist?list=PLtest123456789", limit=50)


class TestNoWindowSpawn:
    """Issue #79: every yt-dlp spawn must suppress a Windows console."""

    def test_probe_metadata_routes_through_supervisor(self, service: YouTubeFetcherService) -> None:
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as supervised:
            service.probe_metadata("https://youtu.be/abc123")
        supervised.assert_called_once()

    def test_fetch_video_starts_new_session(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        _make_happy_outputs(tmp_path)
        captured: dict[str, Any] = {}

        def fake_popen(cmd: list[str], **kwargs: Any) -> _FakePopen:
            captured["kwargs"] = kwargs
            return _FakePopen(lines=[], returncode=0)

        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("anki_miner.utils.process_supervisor.sys.platform", "linux"),
            patch("subprocess.Popen", side_effect=fake_popen),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")
        assert captured["kwargs"].get("start_new_session") is True


# ---------------------------------------------------------------------------
# yt-dlp resolver integration + YtdlpNotFoundError
# ---------------------------------------------------------------------------


class TestYtdlpResolverIntegration:
    """The fetcher resolves the yt-dlp binary via ytdlp_resolver."""

    def test_default_command_uses_bare_literal(self, service: YouTubeFetcherService, no_sibling_ytdlp) -> None:
        """With nothing resolvable, cmd[0] falls through to the bare literal 'yt-dlp'.

        Patching ``shutil.which`` alone is not enough: yt-dlp is a hard runtime
        dependency, so its console script sits next to ``sys.executable`` and the
        resolver's interpreter-sibling tier finds ``.venv/bin/yt-dlp`` on every
        developer machine and in CI. ``no_sibling_ytdlp`` (tests/conftest.py) points
        ``sys.executable`` at an empty directory so this really tests the fallback.
        """
        payload = _make_metadata()
        with (
            patch("anki_miner.utils.ytdlp_resolver.shutil.which", return_value=None),
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
            ) as mrun,
        ):
            service.probe_metadata("https://youtu.be/abc123")
        assert mrun.call_args.args[0][0] == "yt-dlp"

    def test_resolved_path_flows_into_probe_metadata(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        binary = tmp_path / "managed-yt-dlp"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        cfg = replace(yt_config, ytdlp_location=binary)
        svc = YouTubeFetcherService(cfg)
        payload = _make_metadata()
        with patch(
            "anki_miner.services.youtube_fetcher.run_supervised", return_value=_fake_run(0, json.dumps(payload))
        ) as mrun:
            svc.probe_metadata("https://youtu.be/abc123")
        assert mrun.call_args.args[0][0] == str(binary)

    def test_resolved_path_flows_into_fetch_cmd(self, yt_config: AnkiMinerConfig, tmp_path: Path) -> None:
        binary = tmp_path / "managed-yt-dlp"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        cfg = replace(yt_config, ytdlp_location=binary)
        svc = YouTubeFetcherService(cfg)
        cmd = svc._build_fetch_cmd("https://youtu.be/abc123", tmp_path, "manual_only")
        assert cmd[0] == str(binary)


class TestYtdlpNotFound:
    """FileNotFoundError from yt-dlp must surface as YtdlpNotFoundError."""

    def test_probe_metadata_missing_binary(self, service: YouTubeFetcherService) -> None:
        missing = SupervisedResult(SupervisedState.FAILED, None, "", "", FileNotFoundError())
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=missing),
            pytest.raises(YtdlpNotFoundError, match="Update yt-dlp now"),
        ):
            service.probe_metadata("https://youtu.be/abc123")

    def test_probe_playlist_missing_binary(self, service: YouTubeFetcherService) -> None:
        missing = SupervisedResult(SupervisedState.FAILED, None, "", "", FileNotFoundError())
        with (
            patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=missing),
            pytest.raises(YtdlpNotFoundError, match="Update yt-dlp now"),
        ):
            service.probe_playlist("https://youtu.be/abc123", limit=5)

    def test_fetch_video_missing_binary(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        with (
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("subprocess.Popen", side_effect=FileNotFoundError()),
            pytest.raises(YtdlpNotFoundError, match="Update yt-dlp now"),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

    def test_ytdlp_not_found_is_youtube_fetch_error(self) -> None:
        assert issubclass(YtdlpNotFoundError, YouTubeFetchError)


def _lock_acquirable_from_another_thread() -> bool:
    """True when a foreign thread can take the generation lock right now.

    The lock is an RLock, so probing it on the thread that may still hold it would
    always succeed; the probe has to run somewhere else to mean anything.
    """
    seen: list[bool] = []

    def probe() -> None:
        with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
            seen.append(bool(acquired))

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    return seen == [True]


class TestGenerationLockScope:
    """A running yt-dlp only holds the generation lock when it IS the managed binary.

    The lock exists so the updater cannot swap the app-managed binary between argv
    construction and exec (c963c8a1). A user-supplied / PATH / bundled yt-dlp carries
    no such hazard, and holding the lock across a multi-hour download starved every
    other resolver caller (System Health validation, diagnostics, availability probes).
    """

    @pytest.fixture(autouse=True)
    def _isolated_managed_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path / "home")

    @staticmethod
    def _lock_free_during(call: Any, executable: str) -> bool:
        """Run *call* with a yt-dlp spawn parked mid-flight; report lock availability."""
        spawned = threading.Event()
        finish = threading.Event()

        def fake_run_supervised(cmd: list[str], **kwargs: Any) -> Any:
            assert cmd[0] == executable
            spawned.set()
            assert finish.wait(10)
            return _fake_run(1, "", "ERROR: stopped")

        outcome: list[bool] = []

        def worker() -> None:
            # The parked run always ends non-zero; the failure is not what is under test.
            with pytest.raises(YouTubeFetchError):
                call()

        with (
            patch("anki_miner.services.youtube_fetcher.resolve_ytdlp", return_value=executable),
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("anki_miner.services.youtube_fetcher.run_supervised", side_effect=fake_run_supervised),
        ):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                assert spawned.wait(10), "run_supervised was never reached"
                with ytdlp_resolver.managed_ytdlp_lock(blocking=False) as acquired:
                    outcome.append(bool(acquired))
            finally:
                finish.set()
                thread.join(10)
        assert not thread.is_alive()
        return outcome == [True]

    def test_fetch_video_with_a_non_managed_binary_frees_the_lock(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        assert self._lock_free_during(
            lambda: service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only"),
            "/usr/bin/yt-dlp",
        )

    def test_fetch_video_with_the_managed_binary_holds_the_lock(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        assert not self._lock_free_during(
            lambda: service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only"),
            managed,
        )

    def test_probe_metadata_with_a_non_managed_binary_frees_the_lock(self, service: YouTubeFetcherService) -> None:
        assert self._lock_free_during(
            lambda: service.probe_metadata("https://youtu.be/abc123"),
            "/usr/bin/yt-dlp",
        )

    def test_probe_metadata_with_the_managed_binary_holds_the_lock(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        assert not self._lock_free_during(lambda: service.probe_metadata("https://youtu.be/abc123"), managed)

    def test_probe_playlist_with_a_non_managed_binary_frees_the_lock(self, service: YouTubeFetcherService) -> None:
        assert self._lock_free_during(
            lambda: service.probe_playlist("https://youtu.be/playlist", limit=5),
            "/usr/bin/yt-dlp",
        )

    def test_probe_playlist_with_the_managed_binary_holds_the_lock(
        self, service: YouTubeFetcherService, tmp_path: Path
    ) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        assert not self._lock_free_during(lambda: service.probe_playlist("https://youtu.be/playlist", limit=5), managed)

    def test_the_lock_is_released_after_a_managed_run(self, service: YouTubeFetcherService, tmp_path: Path) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        with (
            patch("anki_miner.services.youtube_fetcher.resolve_ytdlp", return_value=managed),
            patch("anki_miner.services.youtube_fetcher.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "anki_miner.services.youtube_fetcher.run_supervised",
                return_value=_fake_run(1, "", "ERROR: stopped"),
            ),
            pytest.raises(YouTubeFetchError),
        ):
            service.fetch_video("https://youtu.be/abc123", "abc123", tmp_path, "manual_only")

        assert _lock_acquirable_from_another_thread()
