"""Generic yt-dlp downloader service for the Utilities → Download tool.

Downloads media from any yt-dlp-supported site into a user-chosen destination
folder. Deliberately separate from :class:`YouTubeFetcherService`, which is
mining-specific (720p cap, Japanese-subtitles-or-fail, temp workspaces); this
service has no subtitle requirement and never deletes what it downloaded.

Command-building idioms (``--ignore-config``, ``--paths home:`` + bare
``--output`` template, ``--`` end-of-options hardening, progress template,
cookie/JS-runtime/ffmpeg flags) mirror ``youtube_fetcher.py`` — keep the two
in sync when yt-dlp semantics change.
"""

from __future__ import annotations

import collections
import logging
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.base import AnkiMinerException
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    YtdlpNotFoundError,
)
from anki_miner.services.audio_fetch_common import redact_url_for_log

# Shared capability probes: module-level functools.cache keyed on the resolved
# binary path, so importing them here means the 30s `--help` probe runs once
# per binary across both services instead of twice.
from anki_miner.services.youtube_fetcher import (
    _JS_RUNTIMES,
    _ytdlp_supports_js_runtimes,
    _ytdlp_supports_remote_components,
)
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.ytdlp_resolver import resolve_ytdlp, ytdlp_generation_lock

logger = logging.getLogger(__name__)

_YTDLP_MISSING_HINT = "yt-dlp executable not found. Use Settings → YouTube → Update yt-dlp now, then retry."

_DOWNLOAD_TIMEOUT_S = 3 * 60 * 60

# Human-readable output name; the id suffix disambiguates same-titled videos.
_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

#: preset key -> (yt-dlp --format selector, -x --audio-format value or None).
#: Keys are persisted in ``config.downloader_format_preset``; the height-capped
#: selectors mirror the mining fetcher's shape, with a ``/best`` fallback for
#: muxed-only sites.
FORMAT_PRESETS: dict[str, tuple[str, str | None]] = {
    "best": ("bestvideo*+bestaudio/best", None),
    "1080p": ("bestvideo[height<=1080]+bestaudio/best[height<=1080]", None),
    "720p": ("bestvideo[height<=720]+bestaudio/best[height<=720]", None),
    "audio_mp3": ("bestaudio/best", "mp3"),
    "audio_m4a": ("bestaudio/best", "m4a"),
}

_PROGRESS_RE = re.compile(r"\[ankimine_dl\] (\S+) (\S+)")
_POSTPROCESS_MARKERS = (
    "[Merger]",
    "[ExtractAudio]",
    "[EmbedThumbnail]",
    "[Metadata]",
    "[SubtitleConvertor]",
    "[ThumbnailsConvertor]",
    "[FixupM3u8]",
)

# Final-filename discovery from yt-dlp's own output lines (version-stable
# phrasings; --print would change quiet-mode semantics). Last match wins, so a
# merged/extracted output supersedes the per-stream destinations.
_FILENAME_RES = (
    re.compile(r"^\[download\] Destination: (.+)$"),
    re.compile(r"^\[Merger\] Merging formats into \"(.+)\"$"),
    re.compile(r"^\[ExtractAudio\] Destination: (.+)$"),
)
_ALREADY_RE = re.compile(r"^\[download\] (.+) has already been downloaded")


class MediaDownloadError(AnkiMinerException):
    """A generic-site download failed (nonzero exit, timeout, bad output)."""


class DownloadStatus(Enum):
    DONE = "done"
    ALREADY_DOWNLOADED = "already"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class DownloadOptions:
    """One download's resolved options; the tab maps presets/config to this."""

    format_selector: str
    extract_audio_format: str | None = None
    write_subtitles: bool = False
    subtitle_langs: str = "ja"
    embed_thumbnail: bool = False
    embed_metadata: bool = False


@dataclass(frozen=True)
class DownloadResult:
    status: DownloadStatus
    filepath: Path | None


def _tail(buf: collections.deque[str], n: int = 20) -> str:
    return "\n".join(list(buf)[-n:])


class MediaDownloaderService:
    """Download one URL via yt-dlp into a destination folder. Stateless."""

    def __init__(self, config: AnkiMinerConfig) -> None:
        self._config = config

    def download(
        self,
        url: str,
        dest_dir: Path,
        options: DownloadOptions,
        progress_cb: Callable[[str, float | None], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadResult:
        """Download *url* into *dest_dir* per *options*.

        Raises:
            YtdlpNotFoundError: the yt-dlp executable cannot be located/run.
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the output tail.
            MediaDownloadError: timeout or any other non-zero exit.
        """
        logger.info("media download starting: %s -> %s", redact_url_for_log(url), dest_dir)

        tail: collections.deque[str] = collections.deque(maxlen=50)
        captured: dict[str, Path | None] = {"filepath": None}
        already = {"seen": False}
        postprocessing_seen = False

        def handle_line(line: str) -> None:
            nonlocal postprocessing_seen
            tail.append(line)
            m = _PROGRESS_RE.search(line)
            if m is not None:
                if progress_cb is not None:
                    downloaded_s, total_s = m.group(1), m.group(2)
                    frac: float | None = None
                    if total_s != "NA":
                        try:
                            total = float(total_s)
                            frac = float(downloaded_s) / total if total > 0 else None
                        except ValueError:
                            frac = None
                    progress_cb(QCoreApplication.translate("MediaDownloader", "Downloading"), frac)
                return
            already_m = _ALREADY_RE.match(line)
            if already_m is not None:
                already["seen"] = True
                captured["filepath"] = Path(already_m.group(1))
                return
            # A line can be both a filename source and a postprocess marker
            # ("[Merger] Merging formats into ...") — never early-return between
            # the two checks.
            for filename_re in _FILENAME_RES:
                name_m = filename_re.match(line)
                if name_m is not None:
                    captured["filepath"] = Path(name_m.group(1))
                    break
            if not postprocessing_seen and any(marker in line for marker in _POSTPROCESS_MARKERS):
                postprocessing_seen = True
                if progress_cb is not None:
                    progress_cb(QCoreApplication.translate("MediaDownloader", "Processing"), None)

        with ytdlp_generation_lock() as release_unless_managed:
            cmd = self._build_cmd(url, dest_dir, options)
            # A transfer runs for as long as the file takes, so only the managed slot
            # keeps the lock across it; see ytdlp_generation_lock. Must stay the last
            # statement before the spawn.
            release_unless_managed(cmd[0])
            result = run_supervised(
                cmd,
                timeout_s=_DOWNLOAD_TIMEOUT_S,
                cancel=cancel_event,
                line_callback=handle_line,
                combine_stderr=True,
                retain_output=False,
            )

        if isinstance(result.error, FileNotFoundError):
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from result.error
        if result.state is SupervisedState.CANCELLED:
            return DownloadResult(DownloadStatus.CANCELLED, None)
        if result.state is SupervisedState.TIMED_OUT:
            raise MediaDownloadError(f"yt-dlp download timed out after {_DOWNLOAD_TIMEOUT_S}s")
        if result.state is SupervisedState.FAILED:
            if result.returncode is None and result.error is not None:
                raise MediaDownloadError(f"yt-dlp process failed: {result.error}") from result.error
            self._raise_for_error(tail)

        status = DownloadStatus.ALREADY_DOWNLOADED if already["seen"] else DownloadStatus.DONE
        logger.info("media download complete: status=%s file=%s", status.value, captured["filepath"])
        return DownloadResult(status, captured["filepath"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ytdlp(self) -> str:
        try:
            return resolve_ytdlp(self._config)
        except FileNotFoundError as exc:
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from exc

    def _build_cmd(self, url: str, dest_dir: Path, options: DownloadOptions) -> list[str]:
        cmd: list[str] = [
            self._ytdlp(),
            "--ignore-config",
            "--no-playlist",
            "--format",
            options.format_selector,
        ]
        if options.extract_audio_format:
            cmd.extend(["-x", "--audio-format", options.extract_audio_format])
        if options.write_subtitles:
            # Both flags: yt-dlp loads manual subs first and lets auto captions
            # only fill languages not already present — manual-preferred fallback.
            cmd.extend(["--write-subs", "--write-auto-subs", "--sub-langs", options.subtitle_langs])
        if options.embed_thumbnail:
            cmd.append("--embed-thumbnail")
        if options.embed_metadata:
            cmd.append("--embed-metadata")
        cmd.extend(
            [
                # "home:" prefix so a Windows drive letter is never read as a
                # --paths TYPE; bare -o template so '%' in the folder name is
                # never a template metacharacter (mirrors youtube_fetcher).
                "--paths",
                f"home:{dest_dir}",
                "--output",
                _OUTPUT_TEMPLATE,
                "--newline",
                "--progress-template",
                "download:[ankimine_dl] %(progress.downloaded_bytes)s %(progress.total_bytes)s",
                "--retries",
                "3",
                "--fragment-retries",
                "3",
                "--socket-timeout",
                "30",
            ]
        )
        cmd.extend(self._cookie_args())
        cmd.extend(self._js_runtime_args())
        cmd.extend(self._remote_component_args())
        ffmpeg_location = self._effective_ffmpeg_location()
        if ffmpeg_location is not None:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])
        # End-of-options separator: a '-'-leading URL must never be parsed as a
        # yt-dlp option. T-34.
        cmd.append("--")
        cmd.append(url)
        return cmd

    def _cookie_args(self) -> list[str]:
        if self._config.youtube_cookies_file:
            return ["--cookies", str(self._config.youtube_cookies_file)]
        if self._config.youtube_cookies_from_browser:
            return ["--cookies-from-browser", self._config.youtube_cookies_from_browser]
        return []

    def _js_runtime_args(self) -> list[str]:
        if not _ytdlp_supports_js_runtimes(self._ytdlp()):
            return []
        for runtime in _JS_RUNTIMES:
            if shutil.which(runtime):
                return ["--js-runtimes", runtime]
        return []

    def _remote_component_args(self) -> list[str]:
        if not _ytdlp_supports_remote_components(self._ytdlp()):
            return []
        return ["--remote-components", "ejs:github"]

    def _effective_ffmpeg_location(self) -> str | None:
        loc = self._config.youtube_ffmpeg_location
        if loc is not None:
            return str(loc)
        resolved = resolve_ffmpeg(self._config)
        if resolved != "ffmpeg" and Path(resolved).is_file():
            return resolved
        return None

    @staticmethod
    def _raise_for_error(tail: collections.deque[str]) -> None:
        joined_lower = "\n".join(tail).lower()

        if ("sign in" in joined_lower and "confirm" in joined_lower) or ("sign in to confirm" in joined_lower):
            raise BotDetectionError(
                "The site requires login. In Settings → YouTube, set Cookies from "
                "browser, or point Cookies file at an exported cookies.txt, then retry."
            )

        if "database is locked" in joined_lower or "database locked" in joined_lower:
            raise CookieDatabaseLockedError(
                "Cookie database is locked. Close the browser and retry, or set Cookies → Browser to None."
            )

        if "requested format is not available" in joined_lower:
            # A generic downloader with a raw-format field cannot blame extractor
            # staleness alone — name both remedies.
            raise MediaDownloadError(
                "The site served no matching format. Update yt-dlp (Settings → "
                "YouTube → Update yt-dlp now) or, if you set a custom format "
                f"string, fix it. yt-dlp said: {_tail(tail, 5)}"
            )

        raise MediaDownloadError(f"yt-dlp exited non-zero: {_tail(tail, 20)}")
