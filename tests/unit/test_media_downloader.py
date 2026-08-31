"""Unit tests for MediaDownloaderService (generic yt-dlp downloads).

yt-dlp is never spawned: ``run_supervised`` is patched at the
``anki_miner.services.media_downloader`` module, mirroring
``tests/unit/test_youtube_fetcher.py``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    YtdlpNotFoundError,
)
from anki_miner.services import media_downloader as md
from anki_miner.services import ytdlp_invocation
from anki_miner.services.media_downloader import (
    FORMAT_PRESETS,
    DownloadOptions,
    DownloadStatus,
    MediaDownloadError,
    MediaDownloaderService,
)
from anki_miner.utils import ytdlp_resolver
from anki_miner.utils.process_supervisor import SupervisedState

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _fake_result(
    returncode: int = 0,
    state: SupervisedState | None = None,
    error: BaseException | None = None,
) -> Any:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = ""
    proc.stderr = ""
    if state is None:
        state = SupervisedState.COMPLETED if returncode == 0 else SupervisedState.FAILED
    proc.state = state
    proc.error = error
    return proc


def _scripted_run(
    lines: list[str],
    returncode: int = 0,
    state: SupervisedState | None = None,
) -> tuple[MagicMock, Callable[..., Any]]:
    """Return (recorder, fake_run) where fake_run feeds *lines* to line_callback."""
    recorder = MagicMock()

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        recorder(cmd, **kwargs)
        cb = kwargs.get("line_callback")
        if cb is not None:
            for line in lines:
                cb(line)
        return _fake_result(returncode, state)

    return recorder, fake_run


def _opts(**kw: Any) -> DownloadOptions:
    kw.setdefault("format_selector", "bestvideo*+bestaudio/best")
    return DownloadOptions(**kw)


@pytest.fixture
def dl_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(
        media_temp_folder=tmp_path / "media",
        jmdict_path=tmp_path / "JMdict_e",
        youtube_cookies_from_browser=None,
        youtube_cookies_file=None,
        youtube_ffmpeg_location=None,
    )


@pytest.fixture
def service(dl_config: AnkiMinerConfig) -> MediaDownloaderService:
    return MediaDownloaderService(dl_config)


@pytest.fixture(autouse=True)
def _deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin resolver + capability probes so no test shells out."""
    monkeypatch.setattr(md, "resolve_ytdlp", lambda _config: "/fake/yt-dlp")
    monkeypatch.setattr(ytdlp_invocation, "ytdlp_supports_js_runtimes", lambda _path: False)
    monkeypatch.setattr(ytdlp_invocation, "ytdlp_supports_remote_components", lambda _path: False)
    monkeypatch.setattr(ytdlp_invocation, "resolve_ffmpeg", lambda _config: "ffmpeg")
    # ffmpeg preflight (Task 11): default presets in this file use "+" format
    # selectors, so pin the resolver + PATH probe to "found" here; individual
    # preflight tests below override one or both to simulate "missing".
    monkeypatch.setattr(md, "resolve_ffmpeg", lambda _config: "ffmpeg")
    monkeypatch.setattr(md.shutil, "which", lambda _name: "/usr/bin/ffmpeg")


def _run_download(
    monkeypatch: pytest.MonkeyPatch,
    service: MediaDownloaderService,
    dest: Path,
    options: DownloadOptions,
    *,
    lines: list[str] | None = None,
    returncode: int = 0,
    state: SupervisedState | None = None,
    progress_cb: Callable[[str, float | None], None] | None = None,
) -> tuple[MagicMock, Any]:
    recorder, fake_run = _scripted_run(lines or [], returncode, state)
    monkeypatch.setattr(md, "run_supervised", fake_run)
    result = service.download(
        "https://example.com/v",
        dest,
        options,
        progress_cb=progress_cb,
    )
    return recorder, result


def _cmd(recorder: MagicMock) -> list[str]:
    return recorder.call_args[0][0]


# ---------------------------------------------------------------------------
# Preset table
# ---------------------------------------------------------------------------


def test_preset_table_exact() -> None:
    assert FORMAT_PRESETS == {
        "best": ("bestvideo*+bestaudio/best", None),
        "1440p": ("bestvideo[height<=1440]+bestaudio/best[height<=1440]", None),
        "1080p": ("bestvideo[height<=1080]+bestaudio/best[height<=1080]", None),
        "720p": ("bestvideo[height<=720]+bestaudio/best[height<=720]", None),
        "audio_mp3": ("bestaudio/best", "mp3"),
        "audio_m4a": ("bestaudio/best", "m4a"),
    }


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    def test_command_always_has_no_playlist_and_end_of_options(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert cmd[0] == "/fake/yt-dlp"
        assert "--ignore-config" in cmd
        assert "--no-playlist" in cmd
        assert cmd[-2:] == ["--", "https://example.com/v"]

    def test_format_selector_passed(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts(format_selector="best[height<=480]"))
        cmd = _cmd(recorder)
        assert cmd[cmd.index("--format") + 1] == "best[height<=480]"

    def test_audio_preset_appends_extract_flags(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(
            monkeypatch, service, tmp_path, _opts(format_selector="bestaudio/best", extract_audio_format="mp3")
        )
        cmd = _cmd(recorder)
        assert "-x" in cmd
        assert cmd[cmd.index("--audio-format") + 1] == "mp3"

    def test_no_extract_flags_without_audio_format(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert "-x" not in cmd
        assert "--audio-format" not in cmd

    def test_subtitle_flags_gated(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts(write_subtitles=True, subtitle_langs="ja,en"))
        cmd = _cmd(recorder)
        assert "--write-subs" in cmd
        assert "--write-auto-subs" in cmd
        assert cmd[cmd.index("--sub-langs") + 1] == "ja,en"

        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert "--write-subs" not in cmd
        assert "--write-auto-subs" not in cmd
        assert "--sub-langs" not in cmd
        assert "--sub-format" not in cmd

    def test_subtitle_format_prefers_srt_with_a_best_fallback(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        """srt first, "/best" second — and the fallback tier is load-bearing.

        Left unstated, yt-dlp's ``best`` default resolves to the LAST entry of the
        extractor's format list, which on YouTube is vtt; that is why every download
        used to write vtt. Tightening this to a bare ``srt`` would break every site
        that serves no srt, so the trailing ``/best`` is pinned, not incidental.
        """
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts(write_subtitles=True))
        cmd = _cmd(recorder)
        assert cmd[cmd.index("--sub-format") + 1] == "srt/best"
        # No ffmpeg postprocessor: a subtitle-only download must not need ffmpeg.
        assert "--convert-subs" not in cmd

    def test_embed_flags_gated(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts(embed_thumbnail=True, embed_metadata=True))
        cmd = _cmd(recorder)
        assert "--embed-thumbnail" in cmd
        assert "--embed-metadata" in cmd

        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert "--embed-thumbnail" not in cmd
        assert "--embed-metadata" not in cmd

    def test_paths_and_output_template(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert cmd[cmd.index("--paths") + 1] == f"home:{tmp_path}"
        assert cmd[cmd.index("--output") + 1] == "%(title)s [%(id)s].%(ext)s"
        assert "--newline" in cmd
        assert "--progress-template" in cmd

    def test_cookie_file_beats_browser(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dl_config: AnkiMinerConfig
    ) -> None:
        from dataclasses import replace

        config = replace(
            dl_config,
            youtube_cookies_file=tmp_path / "cookies.txt",
            youtube_cookies_from_browser="firefox",
        )
        service = MediaDownloaderService(config)
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert cmd[cmd.index("--cookies") + 1] == str(tmp_path / "cookies.txt")
        assert "--cookies-from-browser" not in cmd

    def test_ffmpeg_location_passed_when_resolvable(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        bundled = tmp_path / "ffmpeg-bundled"
        bundled.write_bytes(b"x")
        monkeypatch.setattr(ytdlp_invocation, "resolve_ffmpeg", lambda _config: str(bundled))
        recorder, _ = _run_download(monkeypatch, service, tmp_path, _opts())
        cmd = _cmd(recorder)
        assert cmd[cmd.index("--ffmpeg-location") + 1] == str(bundled)


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


class TestProgress:
    def test_progress_fraction_and_na(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        seen: list[tuple[str, float | None]] = []
        _run_download(
            monkeypatch,
            service,
            tmp_path,
            _opts(),
            lines=["[ankimine_dl] 500 1000", "[ankimine_dl] 750 NA"],
            progress_cb=lambda label, frac: seen.append((label, frac)),
        )
        assert seen[0][1] == 0.5
        assert seen[1][1] is None

    def test_postprocess_marker_reports_processing(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        seen: list[tuple[str, float | None]] = []
        _run_download(
            monkeypatch,
            service,
            tmp_path,
            _opts(),
            lines=['[Merger] Merging formats into "out.mp4"'],
            progress_cb=lambda label, frac: seen.append((label, frac)),
        )
        assert len(seen) == 1
        assert seen[0][1] is None


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------


class TestResultMapping:
    def test_filename_capture_last_match_wins(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        _, result = _run_download(
            monkeypatch,
            service,
            tmp_path,
            _opts(),
            lines=[
                "[download] Destination: video.f137.mp4",
                "[download] Destination: audio.f140.m4a",
                '[Merger] Merging formats into "final.mp4"',
            ],
        )
        assert result.status is DownloadStatus.DONE
        assert result.filepath == Path("final.mp4")

    def test_extract_audio_destination_captured(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        _, result = _run_download(
            monkeypatch,
            service,
            tmp_path,
            _opts(format_selector="bestaudio/best", extract_audio_format="mp3"),
            lines=[
                "[download] Destination: song.webm",
                "[ExtractAudio] Destination: song.mp3",
            ],
        )
        assert result.filepath == Path("song.mp3")

    def test_done_without_filename_lines(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        _, result = _run_download(monkeypatch, service, tmp_path, _opts())
        assert result.status is DownloadStatus.DONE
        assert result.filepath is None

    def test_already_downloaded_maps_to_skip_status(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        _, result = _run_download(
            monkeypatch,
            service,
            tmp_path,
            _opts(),
            lines=["[download] My Video [abc].mp4 has already been downloaded"],
        )
        assert result.status is DownloadStatus.ALREADY_DOWNLOADED
        assert result.filepath == Path("My Video [abc].mp4")

    def test_cancelled_state_returns_cancelled_result(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        _, result = _run_download(
            monkeypatch, service, tmp_path, _opts(), state=SupervisedState.CANCELLED, returncode=1
        )
        assert result.status is DownloadStatus.CANCELLED
        assert result.filepath is None

    def test_cancel_event_forwarded(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        recorder, fake_run = _scripted_run([])
        monkeypatch.setattr(md, "run_supervised", fake_run)
        event = threading.Event()
        service.download("https://example.com/v", tmp_path, _opts(), cancel_event=event)
        assert recorder.call_args.kwargs["cancel"] is event


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_resolver_miss_raises_ytdlp_not_found(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        def _boom(_config: AnkiMinerConfig) -> str:
            raise FileNotFoundError("no yt-dlp")

        monkeypatch.setattr(md, "resolve_ytdlp", _boom)
        with pytest.raises(YtdlpNotFoundError):
            service.download("https://example.com/v", tmp_path, _opts())

    def test_spawn_file_not_found_raises_ytdlp_not_found(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        result = _fake_result(returncode=1, state=SupervisedState.FAILED, error=FileNotFoundError("gone"))
        result.returncode = None
        monkeypatch.setattr(md, "run_supervised", lambda *a, **k: result)
        with pytest.raises(YtdlpNotFoundError):
            service.download("https://example.com/v", tmp_path, _opts())

    def test_timeout_raises_media_download_error(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "run_supervised", lambda *a, **k: _fake_result(1, SupervisedState.TIMED_OUT))
        with pytest.raises(MediaDownloadError, match="timed out"):
            service.download("https://example.com/v", tmp_path, _opts())

    def test_nonzero_exit_raises_media_download_error_with_tail(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        with pytest.raises(MediaDownloadError, match="something exploded"):
            _run_download(
                monkeypatch,
                service,
                tmp_path,
                _opts(),
                lines=["ERROR: something exploded"],
                returncode=1,
            )

    def test_bot_detection_marker(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        with pytest.raises(BotDetectionError):
            _run_download(
                monkeypatch,
                service,
                tmp_path,
                _opts(),
                lines=["ERROR: Sign in to confirm you're not a bot"],
                returncode=1,
            )

    def test_cookie_database_locked_marker(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        with pytest.raises(CookieDatabaseLockedError):
            _run_download(
                monkeypatch,
                service,
                tmp_path,
                _opts(),
                lines=["ERROR: could not copy cookies: database is locked"],
                returncode=1,
            )

    @pytest.mark.parametrize(
        ("stderr_line", "expected_phrase"),
        [
            # cookies.py:363 — the Issue #119 failure, Windows chromium.
            (
                "ERROR: Could not copy Chrome cookie database. See  "
                "https://github.com/yt-dlp/yt-dlp/issues/7271  for more info",
                "Close chrome",
            ),
            # cookies.py:1099 — DPAPI; a browser restart cannot fix it.
            (
                "ERROR: Failed to decrypt with DPAPI. See  "
                "https://github.com/yt-dlp/yt-dlp/issues/10927  for more info",
                "could not decrypt",
            ),
            # cookies.py:318 — no cookie DB under the browser's search root.
            (
                'ERROR: could not find chrome cookies database in "/home/u/.config/google-chrome"',
                "No cookie database found for chrome",
            ),
        ],
    )
    def test_real_cookie_failures_get_their_own_remedy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dl_config: AnkiMinerConfig,
        tmp_path: Path,
        stderr_line: str,
        expected_phrase: str,
    ) -> None:
        """Verbatim yt-dlp cookie errors, each mapped to the remedy that fits it.

        Download reads the same cookie source the YouTube path does, so it must
        say the same thing — which is why it shares ``cookie_failure_message``
        with ``youtube_fetcher`` rather than keeping its own copy.
        """
        from dataclasses import replace

        service = MediaDownloaderService(replace(dl_config, youtube_cookies_from_browser="chrome"))
        with pytest.raises(CookieDatabaseLockedError) as excinfo:
            _run_download(
                monkeypatch,
                service,
                tmp_path,
                _opts(),
                lines=[stderr_line],
                returncode=1,
            )
        assert expected_phrase in str(excinfo.value)
        assert "github.com" not in str(excinfo.value)

    def test_format_unavailable_names_both_remedies(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        with pytest.raises(MediaDownloadError) as excinfo:
            _run_download(
                monkeypatch,
                service,
                tmp_path,
                _opts(),
                lines=["ERROR: Requested format is not available"],
                returncode=1,
            )
        message = str(excinfo.value)
        assert "yt-dlp" in message
        assert "format" in message.lower()


# ---------------------------------------------------------------------------
# Generation-lock scope
# ---------------------------------------------------------------------------


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
    """A running download holds the generation lock only when it IS the managed binary.

    Holding it across a multi-hour transfer starved every other resolver caller
    (System Health validation, diagnostics, availability probes); the lock is only
    needed so the updater cannot swap the app-managed binary under a built argv.
    """

    @pytest.fixture(autouse=True)
    def _isolated_managed_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(ytdlp_resolver.paths, "ANKI_MINER_HOME", tmp_path / "home")

    @staticmethod
    def _lock_free_during_download(
        monkeypatch: pytest.MonkeyPatch,
        service: MediaDownloaderService,
        dest: Path,
        executable: str,
    ) -> bool:
        monkeypatch.setattr(md, "resolve_ytdlp", lambda _config: executable)
        spawned = threading.Event()
        finish = threading.Event()

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            assert cmd[0] == executable
            spawned.set()
            assert finish.wait(10)
            return _fake_result(returncode=0)

        monkeypatch.setattr(md, "run_supervised", fake_run)

        def worker() -> None:
            service.download("https://example.com/v", dest, _opts())

        outcome: list[bool] = []
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

    def test_non_managed_binary_frees_the_lock_mid_download(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        assert self._lock_free_during_download(monkeypatch, service, tmp_path, "/usr/bin/yt-dlp")

    def test_managed_binary_holds_the_lock_mid_download(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        assert not self._lock_free_during_download(monkeypatch, service, tmp_path, managed)

    def test_the_lock_is_released_after_a_managed_download(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        managed = str(tmp_path / "home" / "bin" / ytdlp_resolver.ytdlp_binary_name())
        monkeypatch.setattr(md, "resolve_ytdlp", lambda _config: managed)
        _run_download(monkeypatch, service, tmp_path, _opts())
        assert _lock_acquirable_from_another_thread()


# ---------------------------------------------------------------------------
# ffmpeg preflight (Task 11)
# ---------------------------------------------------------------------------


class TestFfmpegPreflight:
    def test_merge_preset_preflights_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        options = _opts(format_selector="bestvideo[height<=1080]+bestaudio")
        with pytest.raises(MediaDownloadError, match="ffmpeg"):
            service.download("https://example.com/v", tmp_path, options)

    def test_audio_extract_preset_preflights_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        options = _opts(format_selector="bestaudio/best", extract_audio_format="mp3")
        with pytest.raises(MediaDownloadError, match="ffmpeg"):
            service.download("https://example.com/v", tmp_path, options)

    def test_plain_best_preset_skips_ffmpeg_check(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        recorder, fake_run = _scripted_run([])
        monkeypatch.setattr(md, "run_supervised", fake_run)
        options = _opts(format_selector="best")
        service.download("https://example.com/v", tmp_path, options)  # must not raise
        recorder.assert_called_once()

    def test_best_preset_with_embed_thumbnail_preflights_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        spawned = MagicMock()
        monkeypatch.setattr(md, "run_supervised", spawned)
        options = _opts(format_selector="best", embed_thumbnail=True)
        with pytest.raises(MediaDownloadError, match="ffmpeg"):
            service.download("https://example.com/v", tmp_path, options)
        spawned.assert_not_called()

    def test_best_preset_without_embeds_skips_ffmpeg_check(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        recorder, fake_run = _scripted_run([])
        monkeypatch.setattr(md, "run_supervised", fake_run)
        options = _opts(format_selector="best", embed_thumbnail=False, embed_metadata=False)
        service.download("https://example.com/v", tmp_path, options)  # must not raise
        recorder.assert_called_once()

    def test_preflight_runs_before_lock_and_spawn(
        self, monkeypatch: pytest.MonkeyPatch, service: MediaDownloaderService, tmp_path: Path
    ) -> None:
        """No transfer cost on refusal: run_supervised must never be reached."""
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        spawned = MagicMock()
        monkeypatch.setattr(md, "run_supervised", spawned)
        options = _opts(format_selector="bestvideo*+bestaudio/best")
        with pytest.raises(MediaDownloadError, match="ffmpeg"):
            service.download("https://example.com/v", tmp_path, options)
        spawned.assert_not_called()

    def test_configured_ffmpeg_location_missing_raises(
        self, monkeypatch: pytest.MonkeyPatch, dl_config: AnkiMinerConfig, tmp_path: Path
    ) -> None:
        from dataclasses import replace

        config = replace(dl_config, youtube_ffmpeg_location=tmp_path / "nonexistent-ffmpeg")
        service = MediaDownloaderService(config)
        options = _opts(format_selector="bestvideo*+bestaudio/best")
        with pytest.raises(MediaDownloadError, match="ffmpeg"):
            service.download("https://example.com/v", tmp_path, options)

    def test_configured_ffmpeg_location_existing_skips_resolver(
        self, monkeypatch: pytest.MonkeyPatch, dl_config: AnkiMinerConfig, tmp_path: Path
    ) -> None:
        from dataclasses import replace

        configured = tmp_path / "my-ffmpeg"
        configured.write_bytes(b"x")
        config = replace(dl_config, youtube_ffmpeg_location=configured)
        service = MediaDownloaderService(config)
        # Resolver would report "missing" if consulted; the override must win
        # without ever calling it.
        monkeypatch.setattr(md, "resolve_ffmpeg", lambda *a, **k: None)
        recorder, fake_run = _scripted_run([])
        monkeypatch.setattr(md, "run_supervised", fake_run)
        options = _opts(format_selector="bestvideo*+bestaudio/best")
        service.download("https://example.com/v", tmp_path, options)  # must not raise
        recorder.assert_called_once()
