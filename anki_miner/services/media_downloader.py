"""Generic yt-dlp downloader service for the Utilities → Download tool.

Downloads media from any yt-dlp-supported site into a user-chosen destination
folder. Deliberately separate from :class:`YouTubeFetcherService`, which is
mining-specific (720p cap, Japanese-subtitles-or-fail, temp workspaces); this
service has no subtitle requirement and never deletes what it downloaded.

Command-building idioms (``--ignore-config``, ``--paths home:`` + bare
``--output`` template, ``--`` end-of-options hardening, progress template,
cookie/JS-runtime/ffmpeg flags) are shared with ``youtube_fetcher.py`` via
``ytdlp_invocation.py``.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.base import AnkiMinerException
from anki_miner.exceptions.cancel import OperationCancelled
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    YtdlpNotFoundError,
)
from anki_miner.services import ytdlp_invocation
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.subprocess_log import log_command
from anki_miner.utils.ytdlp_resolver import resolve_ytdlp, ytdlp_generation_lock

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_S = 3 * 60 * 60
#: One probe's subprocess budget. Public: the probe workers take it as their
#: default, so the service and the GUI cannot drift apart.
PROBE_TIMEOUT_S = 120.0

#: How many playlist entries one probe fetches. The picker dialog shows what
#: came back and says so when the playlist is longer; a cap keeps a 5,000-video
#: channel from turning one button press into a multi-minute probe.
PLAYLIST_PROBE_MAX = 500

# Human-readable output name; the id suffix disambiguates same-titled videos.
_OUTPUT_TEMPLATE = "%(title)s [%(id)s].%(ext)s"

#: preset key -> (yt-dlp --format selector, -x --audio-format value or None).
#: Keys are persisted in ``config.downloader_format_preset``; the height-capped
#: selectors mirror the mining fetcher's shape, with a ``/best`` fallback for
#: muxed-only sites.
FORMAT_PRESETS: dict[str, tuple[str, str | None]] = {
    "best": ("bestvideo*+bestaudio/best", None),
    "1440p": ("bestvideo[height<=1440]+bestaudio/best[height<=1440]", None),
    "1080p": ("bestvideo[height<=1080]+bestaudio/best[height<=1080]", None),
    "720p": ("bestvideo[height<=720]+bestaudio/best[height<=720]", None),
    "audio_mp3": ("bestaudio/best", "mp3"),
    "audio_m4a": ("bestaudio/best", "m4a"),
}

# Final-filename discovery from yt-dlp's own output lines (version-stable
# phrasings; --print would change quiet-mode semantics). Last match wins, so a
# merged/extracted output supersedes the per-stream destinations.
_FILENAME_RES = (
    re.compile(r"^\[download\] Destination: (.+)$"),
    re.compile(r"^\[Merger\] Merging formats into \"(.+)\"$"),
    re.compile(r"^\[ExtractAudio\] Destination: (.+)$"),
)
_ALREADY_RE = re.compile(r"^\[download\] (.+) has already been downloaded")

#: A language code safe to interpolate into a yt-dlp format filter. Anything
#: else is dropped rather than escaped — the picker only ever produces codes, so
#: a non-matching value is a bug or an injection attempt, not a preference.
_AUDIO_LANG_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")


def apply_audio_language(selector: str, lang: str) -> str:
    """Return *selector* with an audio-language-preferring tier in front.

    ``("bestvideo*+bestaudio/best", "ja")`` becomes
    ``"bestvideo*+bestaudio[language~='^ja(-|$)']/bestvideo*+bestaudio/best"``:
    a video with a Japanese audio track gets it, and one without still downloads
    at the same quality it would have without any preference.

    Only the selector's FIRST alternative is filtered. Filtering the whole
    string would leave the original's later, better alternatives shadowed by the
    filtered copy's muxed fallback — a video with no matching audio would then
    land on ``best`` instead of ``bestvideo*+bestaudio``.

    The regex form matches the mining fetcher's ``bestaudio[language~='^ja(-|$)']``
    so ``ja`` selects ``ja`` and ``ja-JP`` but never ``jav``.
    """
    code = lang.strip()
    if not code or not _AUDIO_LANG_RE.match(code):
        return selector
    preferred, _, _ = selector.partition("/")
    if "bestaudio" not in preferred:
        # A muxed-only or otherwise hand-written selector has no separate audio
        # stream to filter; the user's string is left exactly as written.
        return selector
    filtered = preferred.replace("bestaudio", f"bestaudio[language~='^{code}(-|$)']")
    return f"{filtered}/{selector}"


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
    audio_lang: str = ""  # preferred audio-track language; "" = whatever the site serves


@dataclass(frozen=True)
class DownloadResult:
    status: DownloadStatus
    filepath: Path | None


@dataclass(frozen=True)
class UrlTracks:
    """What a URL actually offers, as reported by one metadata probe.

    Populates the Download tool's language pickers so the user chooses from the
    site's real tags instead of guessing between ``zh-Hans``, ``zh-CN`` and
    ``zh``. Deliberately separate from ``YouTubeFetcherService.probe_metadata``,
    which throws these maps away and keeps only per-language booleans for the
    mining gate.
    """

    title: str
    manual_sub_langs: tuple[str, ...]
    auto_sub_langs: tuple[str, ...]
    audio_langs: tuple[str, ...]
    has_auto_translations: bool


@dataclass(frozen=True)
class DownloadPlaylistEntry:
    """One playlist entry, as seen by flat extraction.

    ``index`` is the position among the *usable* entries, counted from the
    probe's ``start`` — what the picker's range expression selects on, so the
    numbers the user sees agree with the ones they type on every page.
    """

    index: int
    title: str
    url: str
    duration_s: int | None


@dataclass(frozen=True)
class DownloadPlaylist:
    title: str
    entries: tuple[DownloadPlaylistEntry, ...]
    total_count: int | None
    #: Entries exist past ``entries[-1]``: the probe got ``limit + 1`` back, or
    #: the extractor's count says so. Decided here because only the probe knows
    #: it asked for one more than it returned.
    truncated: bool = False


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
            MediaDownloadError: ffmpeg preflight failure (merge, audio-extract,
                or thumbnail/metadata embedding), timeout, or any other
                non-zero exit.
        """
        logger.info("media download starting: %s -> %s", redact_url_for_log(url), dest_dir)

        needs_ffmpeg = (
            "+" in options.format_selector
            or options.extract_audio_format is not None
            or options.embed_thumbnail
            or options.embed_metadata
        )
        if needs_ffmpeg and not self._ffmpeg_reachable():
            raise MediaDownloadError(
                "This preset needs ffmpeg (merging video+audio or extracting "
                "audio), but no ffmpeg executable was found. Install ffmpeg or "
                "set its location in Settings → YouTube."
            )

        tail: collections.deque[str] = collections.deque(maxlen=50)
        captured: dict[str, Path | None] = {"filepath": None}
        already = {"seen": False}
        postprocessing_seen = False

        def handle_line(line: str) -> None:
            nonlocal postprocessing_seen
            # A pure progress line is not a diagnostic; keeping it would evict
            # the yt-dlp error this tail exists to carry. See is_progress_only.
            if not ytdlp_invocation.is_progress_only(line):
                tail.append(line)
            m = ytdlp_invocation.PROGRESS_RE.search(line)
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
            if not postprocessing_seen and any(marker in line for marker in ytdlp_invocation.POSTPROCESS_MARKERS):
                postprocessing_seen = True
                if progress_cb is not None:
                    progress_cb(QCoreApplication.translate("MediaDownloader", "Processing"), None)

        with ytdlp_generation_lock() as release_unless_managed:
            cmd = self._build_cmd(url, dest_dir, options)
            # INFO: the format selector and the cookie source are what a failed
            # download is diagnosed from, and the user has not enabled debug logging.
            log_command(logger, "yt-dlp download", cmd, timeout_s=_DOWNLOAD_TIMEOUT_S, level=logging.INFO)
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
                op="yt-dlp download",
                # Without this the supervisor's failure tail is 20 progress ticks
                # and the yt-dlp error that caused the failure is evicted.
                noise_filter=ytdlp_invocation.is_progress_only,
            )

        if isinstance(result.error, FileNotFoundError):
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from result.error
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
    # Probes
    # ------------------------------------------------------------------

    def probe_tracks(
        self, url: str, timeout_s: float = PROBE_TIMEOUT_S, *, cancel_event: threading.Event | None = None
    ) -> UrlTracks:
        """Return the subtitle and audio-track languages *url* offers.

        One ``--dump-single-json`` call, no download.

        Raises:
            YtdlpNotFoundError: the yt-dlp executable cannot be located/run.
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the output tail.
            OperationCancelled: *cancel_event* was set before the probe finished.
            MediaDownloadError: timeout, non-JSON output, or any other non-zero
                exit.
        """
        cmd: list[str] = [
            self._ytdlp(),
            "--ignore-config",
            "--skip-download",
            "--dump-single-json",
            "--no-playlist",
        ]
        data = self._probe_json(cmd, url, timeout_s, op="yt-dlp track probe", cancel_event=cancel_event)

        subtitles = data.get("subtitles") or {}
        automatic = data.get("automatic_captions") or {}
        manual = sorted(k for k in subtitles if isinstance(k, str) and k != "live_chat")

        # YouTube publishes its one real auto track as "<lang>-orig" and then
        # machine-translates it into ~200 more, all of which land in
        # automatic_captions. Listing them all would bury the useful entry, so
        # the "-orig" keys win whenever any exist; a site with no such marker
        # (every non-YouTube extractor) has a short honest list instead.
        orig = sorted(k[: -len("-orig")] for k in automatic if isinstance(k, str) and k.endswith("-orig"))
        auto = tuple(orig) if orig else tuple(sorted(k for k in automatic if isinstance(k, str)))
        has_translations = bool(orig) and len(automatic) > len(orig)

        audio_langs = sorted(
            {
                str(fmt["language"])
                for fmt in (data.get("formats") or [])
                if isinstance(fmt, dict) and fmt.get("language")
            }
        )
        return UrlTracks(
            title=str(data.get("title") or ""),
            manual_sub_langs=tuple(manual),
            auto_sub_langs=auto,
            audio_langs=tuple(audio_langs),
            has_auto_translations=has_translations,
        )

    def probe_playlist(
        self,
        url: str,
        limit: int = PLAYLIST_PROBE_MAX,
        timeout_s: float = PROBE_TIMEOUT_S,
        *,
        start: int = 1,
        cancel_event: threading.Event | None = None,
    ) -> DownloadPlaylist:
        """List up to *limit* entries of the playlist at *url*, from position *start*.

        Site-agnostic on purpose: unlike ``YouTubeFetcherService.probe_playlist``,
        which requires each entry id to match an 11-char YouTube video-id regex,
        an entry here needs only a resolvable URL — so Bilibili collections,
        Vimeo showcases and SoundCloud sets all expand.

        Asks yt-dlp for ``limit + 1`` entries so a longer playlist is detected
        even when the extractor omits ``playlist_count`` (many do); the extra
        entry is dropped and ``truncated`` set. Entries are numbered from
        *start*, so the second page reads 501, 502, … in the picker.

        Raises:
            YtdlpNotFoundError: the yt-dlp executable cannot be located/run.
            OperationCancelled: *cancel_event* was set before the probe finished.
            MediaDownloadError: *url* is not a playlist, is empty, holds no
                usable entry, or the probe itself failed.
        """
        cmd: list[str] = [
            self._ytdlp(),
            "--ignore-config",
            "--skip-download",
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-items",
            f"{start}:{start + limit}",
        ]
        data = self._probe_json(cmd, url, timeout_s, op="yt-dlp playlist probe", cancel_event=cancel_event)

        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise MediaDownloadError(
                "That URL is not a playlist (yt-dlp reported no entries). " "Paste a playlist, channel or album URL."
            )
        if not raw_entries:
            raise MediaDownloadError("The playlist is empty.")
        # Truncation is decided on the RAW window, before unusable entries are
        # dropped: one private video inside a full window must not hide the
        # rest of the playlist. The tab advances by ``limit`` raw positions.
        over_cap = len(raw_entries) > limit
        raw_entries = raw_entries[:limit]

        entries: list[DownloadPlaylistEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            entry_url = raw.get("url") or raw.get("webpage_url")
            if not entry_url:
                logger.debug("playlist probe: skipping entry with no URL")
                continue
            raw_duration = raw.get("duration")
            entries.append(
                DownloadPlaylistEntry(
                    index=start + len(entries),
                    title=str(raw.get("title") or entry_url),
                    url=str(entry_url),
                    duration_s=int(raw_duration) if isinstance(raw_duration, (int, float)) else None,
                )
            )

        if not entries:
            raise MediaDownloadError(
                "The playlist has no usable entries — every one is private, deleted, or unreachable."
            )

        raw_count = data.get("playlist_count")
        total_count = int(raw_count) if isinstance(raw_count, (int, float)) else None
        truncated = over_cap or (total_count is not None and total_count > start - 1 + len(raw_entries))
        logger.info(
            "playlist probe ok: entries=%d start=%d total=%s truncated=%s",
            len(entries),
            start,
            total_count,
            truncated,
        )
        return DownloadPlaylist(
            title=str(data.get("title") or "Playlist"),
            entries=tuple(entries),
            total_count=total_count,
            truncated=truncated,
        )

    def _probe_json(
        self,
        cmd: list[str],
        url: str,
        timeout_s: float,
        *,
        op: str,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Finish *cmd* with the shared flags, run it, and parse its JSON.

        Shared by both probes so cookie/JS-runtime/end-of-options handling and
        error classification cannot drift between them.
        """
        with ytdlp_generation_lock() as release_unless_managed:
            cmd.extend(ytdlp_invocation.cookie_args(self._config))
            cmd.extend(ytdlp_invocation.js_runtime_args(self._config, self._ytdlp()))
            cmd.extend(ytdlp_invocation.remote_component_args(self._config, self._ytdlp()))
            # End-of-options separator: a '-'-leading URL must never be parsed
            # as a yt-dlp option. T-34.
            cmd.append("--")
            cmd.append(url)
            log_command(logger, op, cmd, timeout_s=timeout_s, level=logging.INFO)
            release_unless_managed(cmd[0])
            proc = run_supervised(cmd, timeout_s=timeout_s, cancel=cancel_event, op=op)

        if isinstance(proc.error, FileNotFoundError):
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from proc.error
        if proc.state is SupervisedState.CANCELLED:
            # Typed cancel: report_failure logs it at INFO and raises no screen issue.
            raise OperationCancelled(f"{op} cancelled")
        if proc.state is SupervisedState.TIMED_OUT:
            raise MediaDownloadError(f"{op} timed out after {timeout_s}s")
        if proc.state is SupervisedState.FAILED:
            if proc.returncode is None and proc.error is not None:
                raise MediaDownloadError(f"{op} failed: {proc.error}") from proc.error
            self._raise_for_error(collections.deque((proc.stderr or "").splitlines(), maxlen=50))

        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise MediaDownloadError("yt-dlp returned non-JSON output — the site or yt-dlp may have broken.") from exc
        if not isinstance(parsed, dict):
            raise MediaDownloadError("yt-dlp returned non-JSON output — expected an object.")
        return parsed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ffmpeg_reachable(self) -> bool:
        """Mirror ``YouTubeFetcherService._preflight_ffmpeg``'s precedence, as a
        predicate: config override, then the resolved absolute path, then PATH.

        ``resolve_ffmpeg`` never signals "missing" via ``None`` in production —
        it always returns a usable path or the bare ``"ffmpeg"`` PATH-fallback
        literal (see ``ffmpeg_resolver.py``) — but ``None`` is still handled
        defensively for robustness against a monkeypatched/misbehaving resolver.
        """
        loc = self._config.youtube_ffmpeg_location
        if loc is not None:
            return Path(loc).is_file()
        resolved = resolve_ffmpeg(self._config)
        if resolved is None:
            return False
        if resolved != "ffmpeg":
            return Path(resolved).is_file()
        return shutil.which("ffmpeg") is not None

    def _ytdlp(self) -> str:
        try:
            return resolve_ytdlp(self._config)
        except FileNotFoundError as exc:
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from exc

    def _build_cmd(self, url: str, dest_dir: Path, options: DownloadOptions) -> list[str]:
        cmd: list[str] = [
            self._ytdlp(),
            "--ignore-config",
            "--no-playlist",
            "--format",
            apply_audio_language(options.format_selector, options.audio_lang),
        ]
        if options.extract_audio_format:
            cmd.extend(["-x", "--audio-format", options.extract_audio_format])
        if options.write_subtitles:
            # Both flags: yt-dlp loads manual subs first and lets auto captions
            # only fill languages not already present — manual-preferred fallback.
            #
            # --sub-format is stated rather than left to yt-dlp's "best" default,
            # which resolves to the LAST entry of the extractor's format list — on
            # YouTube that tuple ends in vtt, so every download here used to write
            # vtt purely by tuple position. YouTube serves srt directly, so the srt
            # tier costs nothing and needs no ffmpeg postprocessor. "/best" is the
            # load-bearing half: this tab takes any yt-dlp-supported site, and one
            # that offers no srt must still get its subtitle instead of falling
            # into yt-dlp's "no subtitle format found matching" warning path.
            cmd.extend(
                [
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    options.subtitle_langs,
                    "--sub-format",
                    "srt/best",
                ]
            )
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
        cmd.extend(ytdlp_invocation.cookie_args(self._config))
        cmd.extend(ytdlp_invocation.js_runtime_args(self._config, self._ytdlp()))
        cmd.extend(ytdlp_invocation.remote_component_args(self._config, self._ytdlp()))
        ffmpeg_location = ytdlp_invocation.effective_ffmpeg_location(self._config)
        if ffmpeg_location is not None:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])
        # End-of-options separator: a '-'-leading URL must never be parsed as a
        # yt-dlp option. T-34.
        cmd.append("--")
        cmd.append(url)
        return cmd

    def _raise_for_error(self, tail: collections.deque[str]) -> None:
        joined_lower = "\n".join(tail).lower()
        classification = ytdlp_invocation.classify_error_tail(joined_lower)

        if classification == "bot":
            raise BotDetectionError(
                "The site requires login. In Settings → YouTube, set Cookies from "
                "browser, or point Cookies file at an exported cookies.txt, then retry."
            )

        if classification in ytdlp_invocation.COOKIE_TAGS:
            # Shares the YouTube path's wording (and its per-tag remedies) —
            # both services read the same cookie source, so a user who hits this
            # in Download must not be told something different than in YouTube.
            raise CookieDatabaseLockedError(
                ytdlp_invocation.cookie_failure_message(
                    str(classification),
                    self._config.youtube_cookies_from_browser,
                    joined_lower,
                    platform=sys.platform,
                )
            )

        if classification == "format_missing":
            # A generic downloader with a raw-format field cannot blame extractor
            # staleness alone — name both remedies.
            raise MediaDownloadError(
                "The site served no matching format. Update yt-dlp (Settings → "
                "YouTube → Update yt-dlp now) or, if you set a custom format "
                f"string, fix it. yt-dlp said: {ytdlp_invocation.tail_lines(tail, 5)}"
            )

        raise MediaDownloadError(f"yt-dlp exited non-zero: {ytdlp_invocation.tail_lines(tail, 20)}")
