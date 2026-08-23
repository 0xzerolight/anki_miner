"""YouTube fetcher service: probe metadata and download video+subs via yt-dlp."""

from __future__ import annotations

import collections
import functools
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    NoJapaneseSubtitlesError,
    VideoTooLongError,
    YouTubeFetchError,
    YtdlpNotFoundError,
)
from anki_miner.models.youtube import FetchedMedia, PlaylistEntry, PlaylistInfo, SubMode, VideoInfo
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.ytdlp_resolver import managed_ytdlp_lock, resolve_ytdlp

# Message appended to YtdlpNotFoundError so the user can self-serve the fix.
_YTDLP_MISSING_HINT = "yt-dlp executable not found. Use Settings → YouTube → Update yt-dlp now, then retry."

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".webm", ".mkv"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}
_PROGRESS_RE = re.compile(r"\[ankimine_dl\] (\S+) (\S+)")
_POSTPROCESS_MARKERS = ("[Merger]", "[FixupM3u8]", "[SubtitleConvertor]", "[ExtractAudio]")

_YTDLP_FETCH_TIMEOUT_S = 3 * 60 * 60

# JS runtimes yt-dlp can solve YouTube's n-challenge with. "deno" is omitted: it is
# yt-dlp's built-in default, so when the user has deno nothing needs doing. Ordered
# by preference for the failing case (node is the common Windows setup). Issue #64.
_JS_RUNTIMES = ("node", "bun", "quickjs")

# Max video height (px) fetched from YouTube; the format selector caps both the
# video and best-fallback streams. Was the hidden `config.youtube_max_height`
# knob (ARC-004: inlined, never surfaced in any panel).
YOUTUBE_MAX_HEIGHT = 720


# Keyed on the resolved yt-dlp path (unbounded cache), NOT a 1-entry cache: the
# resolved path changes after a self-update download, and a 1-entry cache keyed
# on nothing would then report the OLD binary's capabilities for the NEW one.
@functools.cache
def _ytdlp_supports_js_runtimes(ytdlp_path: str) -> bool:
    """True if the yt-dlp at *ytdlp_path* recognizes ``--js-runtimes``.

    Cached per resolved path. Guards against older yt-dlp that lacks the flag —
    passing an unknown option would break all YouTube mining. Any failure (yt-dlp
    missing, timeout) returns False -> behave as before.
    """
    try:
        with managed_ytdlp_lock(ytdlp_path):
            proc = subprocess.run(
                [ytdlp_path, "--ignore-config", "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "--js-runtimes" in (proc.stdout or "")


@functools.cache
def _ytdlp_supports_remote_components(ytdlp_path: str) -> bool:
    """True if the yt-dlp at *ytdlp_path* recognizes ``--remote-components``.

    Cached per resolved path (see ``_ytdlp_supports_js_runtimes`` for why the
    path is the cache key). Probed separately from ``--js-runtimes`` so an older
    yt-dlp that knows one flag but not the other still degrades safely. Any
    failure (yt-dlp missing, timeout) returns False -> behave as before. Issue #64.
    """
    try:
        with managed_ytdlp_lock(ytdlp_path):
            proc = subprocess.run(
                [ytdlp_path, "--ignore-config", "--help"],
                capture_output=True,
                text=True,
                timeout=30,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "--remote-components" in (proc.stdout or "")


def _tail(buf: collections.deque[str], n: int = 20) -> str:
    """Return the last *n* lines of *buf* joined by newlines."""
    lines = list(buf)[-n:]
    return "\n".join(lines)


class YouTubeFetcherService:
    """Probe and download YouTube video+subtitles via yt-dlp.

    This service does not keep any cross-call state.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
    ) -> None:
        self._config = config

    def _ytdlp(self) -> str:
        """Resolve the yt-dlp executable to invoke for this fetcher's config.

        Picks up a config override, the app-managed downloaded copy
        (~/.anki_miner/bin/), a bundled binary, or the bare literal on PATH.
        """
        try:
            return resolve_ytdlp(self._config)
        except FileNotFoundError as exc:
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from exc

    # ------------------------------------------------------------------
    # probe_metadata
    # ------------------------------------------------------------------

    def probe_metadata(self, url: str, timeout_s: float = 60.0) -> VideoInfo:
        """Run yt-dlp --dump-single-json and return a VideoInfo.

        Args:
            url: YouTube URL to probe.
            timeout_s: subprocess timeout in seconds. On timeout, yt-dlp is
                killed and YouTubeFetchError is raised.

        Raises:
            YouTubeFetchError: yt-dlp crashed, returned non-JSON, or omitted
                required keys.
            VideoTooLongError: video duration exceeds configured maximum.
        """
        logger.info("youtube probe starting: %s", redact_url_for_log(url))
        with managed_ytdlp_lock():
            cmd: list[str] = [
                self._ytdlp(),
                "--ignore-config",
                "--skip-download",
                "--dump-single-json",
                "--no-playlist",
            ]
            cmd.extend(self._cookie_args())
            cmd.extend(self._js_runtime_args())
            cmd.extend(self._remote_component_args())
            # End-of-options separator: a '-'/'--'-leading URL must not be parsed
            # as a yt-dlp option (e.g. --update-to self-replaces the binary on the
            # probe alone, --config-location loads a planted --exec config). T-34.
            cmd.append("--")
            cmd.append(url)
            proc = run_supervised(
                cmd,
                timeout_s=timeout_s,
            )

        if isinstance(proc.error, FileNotFoundError):
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from proc.error
        if proc.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp metadata probe timed out after {timeout_s}s")

        if proc.state is SupervisedState.FAILED:
            if proc.returncode is None and proc.error is not None:
                raise YouTubeFetchError(f"yt-dlp metadata probe failed: {proc.error}") from proc.error
            stderr_tail = (proc.stderr or "").strip().splitlines()[-20:]
            raise YouTubeFetchError(
                f"yt-dlp metadata probe failed (exit {proc.returncode}): {chr(10).join(stderr_tail)}"
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-10:]
            raise YouTubeFetchError(
                "yt-dlp returned non-JSON output — the site or yt-dlp may have "
                f"broken. stderr: {chr(10).join(stderr_tail)}"
            ) from e

        try:
            video_id = data["id"]
            title = data["title"]
            duration = data["duration"]
        except KeyError as e:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-10:]
            raise YouTubeFetchError(
                "yt-dlp returned incomplete metadata — the site or yt-dlp may "
                f"have broken. stderr: {chr(10).join(stderr_tail)}"
            ) from e

        if not isinstance(video_id, str) or not _VIDEO_ID_RE.match(video_id):
            raise YouTubeFetchError(f"Unexpected video id format: {video_id!r}")

        # Live streams report ``duration: null`` — the key is present so the
        # KeyError guard above passes, but ``int(None)`` would raise an
        # uncaught TypeError, bypassing the caller's "Live streams not
        # supported" rejection. Treat null as 0 so a VideoInfo is built and
        # the is_live branch fires (a finite 0 also can't trip max-duration). T-28.
        duration_s = 0 if duration is None else int(duration)
        if duration_s > self._config.youtube_max_duration_s:
            raise VideoTooLongError(
                f"Video duration {duration_s}s exceeds configured maximum {self._config.youtube_max_duration_s}s"
            )

        subs = data.get("subtitles") or {}
        auto_captions = data.get("automatic_captions") or {}
        has_manual_ja = bool(subs.get("ja"))
        has_auto_ja = self._has_native_auto_ja(data)
        # Auto-dub relaxation: machine-translated ja captions are normally
        # rejected because they do not match the audio — but when YouTube also
        # carries a Japanese (auto-dub) audio track, captions and dub come from
        # the same translation pipeline, so together they are mineable. The
        # fetch side requests that track fail-closed (see _build_fetch_cmd).
        has_dub_ja = (not has_auto_ja) and bool(auto_captions.get("ja")) and self._has_ja_audio_track(data)

        logger.info("youtube probe ok: id=%s duration=%s", video_id, duration_s)
        return VideoInfo(
            video_id=video_id,
            title=str(title),
            duration_s=duration_s,
            has_manual_ja_subs=has_manual_ja,
            has_auto_ja_subs=has_auto_ja,
            has_dub_ja_subs=has_dub_ja,
            is_live=bool(data.get("is_live")),
            is_age_restricted=int(data.get("age_limit") or 0) >= 18,
        )

    # ------------------------------------------------------------------
    # probe_playlist
    # ------------------------------------------------------------------

    def probe_playlist(self, url: str, limit: int, timeout_s: float = 120.0) -> PlaylistInfo:
        """Run yt-dlp --flat-playlist --dump-single-json and return a PlaylistInfo.

        Fetches up to ``limit + 1`` entries so callers can detect when the
        playlist exceeds the cap without an extra round-trip.  Truncation to
        ``limit`` is the caller's responsibility; this method returns all
        fetched entries.

        **Over-cap detection contract**

        Private, deleted, or otherwise unavailable entries are silently dropped
        from ``PlaylistInfo.entries`` while this method parses the yt-dlp
        output.  That means ``len(entries) == limit`` does not unambiguously signal
        "exactly at cap" — one of the fetched slots may have been an unusable
        entry, leaving fewer usable ones in the list.

        Callers should treat the playlist as over-cap when *either* of these
        conditions holds:

        * ``len(info.entries) > limit`` — the reliable entry-count signal; OR
        * ``info.total_count is not None and info.total_count > limit`` — the
          authoritative playlist-size signal when yt-dlp reports it.

        When ``total_count`` is ``None`` and unusable entries were silently
        skipped within the fetched window, over-cap detection may produce a
        false negative (caller sees ``len(entries) <= limit`` and concludes the
        playlist fits).  This is an acceptable trade-off: the worst case is
        that the caller queues up to ``limit`` videos without showing an
        over-cap confirmation.

        Args:
            url: YouTube playlist URL to probe.
            limit: maximum entries the caller wants; the command requests
                ``limit + 1`` from yt-dlp for over-cap detection.
            timeout_s: subprocess timeout in seconds.  On timeout, yt-dlp is
                killed and YouTubeFetchError is raised.

        Raises:
            YouTubeFetchError: yt-dlp crashed, returned non-JSON, the URL is
                not a playlist (missing / non-list ``entries`` key), or all
                entries were unusable (private / deleted / bad id).
        """
        logger.info(
            "youtube playlist probe starting: %s (limit=%s)",
            redact_url_for_log(url),
            limit,
        )
        with managed_ytdlp_lock():
            cmd: list[str] = [
                self._ytdlp(),
                "--ignore-config",
                "--skip-download",
                "--flat-playlist",
                "--dump-single-json",
                "--playlist-items",
                f"1:{limit + 1}",
            ]
            cmd.extend(self._cookie_args())
            cmd.extend(self._js_runtime_args())
            cmd.extend(self._remote_component_args())
            # End-of-options separator before the user URL — see probe_metadata. T-34.
            cmd.append("--")
            cmd.append(url)
            proc = run_supervised(
                cmd,
                timeout_s=timeout_s,
            )

        if isinstance(proc.error, FileNotFoundError):
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from proc.error
        if proc.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp playlist probe timed out after {timeout_s}s")

        if proc.state is SupervisedState.FAILED:
            if proc.returncode is None and proc.error is not None:
                raise YouTubeFetchError(f"yt-dlp playlist probe failed: {proc.error}") from proc.error
            stderr_tail = (proc.stderr or "").strip().splitlines()[-20:]
            raise YouTubeFetchError(
                f"yt-dlp playlist probe failed (exit {proc.returncode}): {chr(10).join(stderr_tail)}"
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-10:]
            raise YouTubeFetchError(
                "yt-dlp returned non-JSON output — the site or yt-dlp may have "
                f"broken. stderr: {chr(10).join(stderr_tail)}"
            ) from e

        raw_entries = data.get("entries")
        if not isinstance(raw_entries, list):
            raise YouTubeFetchError(
                "yt-dlp output is not a playlist (missing or non-list 'entries' key). "
                "Pass a playlist URL, not a single video URL."
            )

        playlist_id: str | None = data.get("id") or None
        raw_title = data.get("title") or ""
        title = str(raw_title) if raw_title else "Playlist"
        raw_count = data.get("playlist_count")
        total_count: int | None = int(raw_count) if raw_count is not None else None

        entries: list[PlaylistEntry] = []
        for raw in raw_entries:
            if raw is None:
                logger.debug("playlist probe: skipping null entry")
                continue

            video_id = raw.get("id")
            if not video_id or not _VIDEO_ID_RE.match(str(video_id)):
                logger.debug("playlist probe: skipping entry with bad/missing id: %r", video_id)
                continue

            video_id = str(video_id)
            entry_title_raw = raw.get("title") or ""
            entry_title = str(entry_title_raw) if entry_title_raw else video_id

            if entry_title in _PLAYLIST_UNAVAILABLE_TITLES:
                logger.debug("playlist probe: skipping unavailable entry: %r", entry_title)
                continue

            raw_duration = raw.get("duration")
            duration_s: int | None = int(raw_duration) if raw_duration is not None else None

            canonical_url = f"https://www.youtube.com/watch?v={video_id}"
            entries.append(
                PlaylistEntry(
                    video_id=video_id,
                    title=entry_title,
                    duration_s=duration_s,
                    url=canonical_url,
                )
            )

        if not entries:
            raise YouTubeFetchError("Playlist contains no accessible videos.")

        logger.info(
            "youtube playlist probe ok: id=%s title=%r entries=%s",
            playlist_id,
            title,
            len(entries),
        )
        return PlaylistInfo(
            playlist_id=playlist_id,
            title=title,
            entries=tuple(entries),
            total_count=total_count,
        )

    @staticmethod
    def _has_native_auto_ja(data: dict) -> bool:
        """Detect native Japanese auto-captions, ignoring auto-translated ones.

        The mere presence of ``automatic_captions.ja`` does NOT mean the video is
        Japanese: yt-dlp lists auto-*translated* tracks under the same key. Getting
        this wrong is user-visible in both directions — a false positive mines
        machine-translated Japanese, a false negative rejects a perfectly good video
        with "No Japanese subtitles available for this video."

        The reliable signal is the ``<lang>-orig`` key, not the ``language`` field:

        - yt-dlp registers ``automatic_captions["<code>-orig"]`` only for the ASR
          track's *own* language (``_video.py``: the ``lang_code == f"a-{code}"``
          branch, and the ``isTranslatable`` branch), and both of those branches call
          ``set_audio_lang_from_orig_subs_lang`` — the very function that derives the
          top-level ``language``.
        - ``language`` is therefore a *derivative*, and one that
          ``info_dict.update(best_format)`` later overwrites from the selected audio
          format. On a video with dubbed audio tracks it can name the dub, not the
          original, which is how genuinely Japanese videos got rejected.

        Verified against live YouTube: a Japanese video exposes both ``ja`` and
        ``ja-orig``; an English video exposes ``ja`` (machine-translated) plus
        ``en-orig`` and no ``ja-orig``.

        Three steps, in order:

        1. ``ja-orig`` present -> native.
        2. Some *other* ``<lang>-orig`` present -> not native. The ``-orig`` machinery
           ran and named a non-Japanese original, so the bare ``ja`` here is a
           translation.
        3. No ``*-orig`` key at all -> fall back to the ``language`` check. ``-orig``
           registration is conditional (it needs a non-empty ``translationLanguages``,
           which only web/mweb player responses carry, or an ``isTranslatable``
           track), so its absence proves nothing. Rejecting here would newly break
           genuinely native videos.

        The old per-track ``"from "`` / ``"translated"`` name check is deliberately
        gone: yt-dlp appends that marker only under ``if is_manual_subs``, so an
        auto-translated track is named plainly "Japanese" and the check was dead code
        for this dict. It still works for *manual* subs, which is why the manual
        branch in :meth:`probe_metadata` keeps it.
        """
        auto = data.get("automatic_captions") or {}
        if not auto.get("ja"):
            return False

        if auto.get("ja-orig"):
            return True

        if any(key.endswith("-orig") and value for key, value in auto.items()):
            return False

        lang = (data.get("language") or "").lower()
        return not lang or lang == "ja"

    @staticmethod
    def _has_ja_audio_track(data: dict) -> bool:
        """Detect a Japanese audio-only format among the probed formats.

        This is the fetch-side reachability check for the auto-dub route: the
        ``auto_dub`` format selector asks for ``bestaudio[language^=ja]``, which
        can only ever match an audio-only format, so that is what we require
        here. A muxed format's ``language`` names its container audio (the
        original), never a dub, and ``bestaudio`` cannot select it.

        On a genuinely Japanese video the original audio-only track also
        matches ("ja audio track" is the semantic, dub or not) — harmless,
        because ``_classify_probe_result`` only consults the dub flag after
        the native routes have already been ruled out.

        Matches ``ja`` exactly or a regional variant like ``ja-JP``; a plain
        prefix test would also admit unrelated codes (e.g. ``jav``), so the
        variant must be dash-separated.
        """
        for fmt in data.get("formats") or []:
            if fmt.get("vcodec") not in (None, "none"):
                continue
            lang = (fmt.get("language") or "").lower()
            if lang == "ja" or lang.startswith("ja-"):
                return True
        return False

    # ------------------------------------------------------------------
    # fetch_video
    # ------------------------------------------------------------------

    def fetch_video(
        self,
        url: str,
        video_id: str,
        workspace: Path,
        sub_mode: SubMode,
        progress_cb: Callable[[str, float | None], None] | None = None,
        cancel_event: threading.Event | None = None,
        *,
        fallback_allowed: bool = False,
    ) -> FetchedMedia:
        """Download the video + Japanese subtitles into *workspace*.

        Args:
            fallback_allowed: When *sub_mode* is ``"manual_only"``, also accept
                native auto-captions if the manual track turns out to be
                unavailable at download time. Callers pass the probe's
                ``has_auto_ja_subs`` so the fallback can only reach a track already
                certified native — never a machine translation.

        Raises:
            FfmpegNotFoundError: ffmpeg preflight failed.
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the tail of stderr.
            NoJapaneseSubtitlesError: yt-dlp succeeded but wrote no subtitle.
            YouTubeFetchError: any other non-zero exit, cancellation, or
                missing/zero-byte output file.
        """
        logger.info("youtube fetch starting: id=%s workspace=%s", video_id, workspace)
        self._preflight_ffmpeg()

        tail: collections.deque[str] = collections.deque(maxlen=50)
        postprocessing_seen = False

        def handle_line(line: str) -> None:
            nonlocal postprocessing_seen
            tail.append(line)
            m = _PROGRESS_RE.search(line)
            if m is not None:
                if progress_cb is not None:
                    downloaded_s, total_s = m.group(1), m.group(2)
                    if total_s == "NA":
                        progress_cb(QCoreApplication.translate("YouTubeFetcher", "Downloading video"), None)
                    else:
                        try:
                            downloaded = float(downloaded_s)
                            total = float(total_s)
                            frac = downloaded / total if total > 0 else None
                        except ValueError:
                            frac = None
                        progress_cb(QCoreApplication.translate("YouTubeFetcher", "Downloading video"), frac)
                return
            if not postprocessing_seen and self._is_postprocess_line(line):
                postprocessing_seen = True
                if progress_cb is not None:
                    progress_cb(QCoreApplication.translate("YouTubeFetcher", "Merging audio and video"), None)

        with managed_ytdlp_lock():
            cmd = self._build_fetch_cmd(url, workspace, sub_mode, fallback_allowed=fallback_allowed)
            process_result = run_supervised(
                cmd,
                timeout_s=_YTDLP_FETCH_TIMEOUT_S,
                cancel=cancel_event,
                line_callback=handle_line,
                combine_stderr=True,
            )
        if isinstance(process_result.error, FileNotFoundError):
            raise YtdlpNotFoundError(_YTDLP_MISSING_HINT) from process_result.error
        if process_result.state is SupervisedState.CANCELLED:
            raise YouTubeFetchError("Cancelled by user")
        if process_result.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp download timed out after {_YTDLP_FETCH_TIMEOUT_S}s")
        if process_result.state is SupervisedState.FAILED:
            if process_result.returncode is None and process_result.error is not None:
                raise YouTubeFetchError(f"yt-dlp process failed: {process_result.error}") from process_result.error
            self._raise_for_error(tail)

        # Success: locate output files by globbing on video_id.
        result = self._resolve_outputs(workspace, video_id, sub_mode)
        logger.info(
            "youtube fetch complete: video=%s subs=%s",
            result.video_file.name,
            result.subtitle_file.name,
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_ffmpeg_location(self) -> str | None:
        """Resolve the ffmpeg path to hand yt-dlp, or None to rely on PATH.

        Precedence:
        1. ``youtube_ffmpeg_location`` explicit override (existence is validated
           separately in :meth:`_preflight_ffmpeg`).
        2. ``resolve_ffmpeg(config)`` — picks up the bundled binary in frozen
           builds (or a ``ffmpeg_location`` override). Returned only when it is a
           real absolute file; the bare literal ``"ffmpeg"`` means "use PATH".

        Returns:
            An absolute file path string, or ``None`` to let yt-dlp do its own
            PATH lookup.
        """
        loc = self._config.youtube_ffmpeg_location
        if loc is not None:
            return str(loc)
        resolved = resolve_ffmpeg(self._config)
        if resolved != "ffmpeg" and Path(resolved).is_file():
            return resolved
        return None

    def _preflight_ffmpeg(self) -> None:
        loc = self._config.youtube_ffmpeg_location
        if loc is not None:
            p = Path(loc)
            if not (p.exists() and p.is_file()):
                raise FfmpegNotFoundError(f"Configured ffmpeg location does not exist: {p}")
            return
        # No explicit override: a bundled/resolved absolute binary satisfies the
        # preflight; otherwise fall back to the historical PATH check.
        if self._effective_ffmpeg_location() is not None:
            return
        if shutil.which("ffmpeg") is None:
            raise FfmpegNotFoundError(
                "ffmpeg not found on PATH. Install ffmpeg or set the 'youtube_ffmpeg_location' config option."
            )

    def _build_fetch_cmd(
        self,
        url: str,
        workspace: Path,
        sub_mode: SubMode,
        *,
        fallback_allowed: bool = False,
    ) -> list[str]:
        max_height = YOUTUBE_MAX_HEIGHT
        # Route the workspace directory through --paths (a literal path) and keep
        # -o a bare, relative template. Embedding the (user-configurable) temp
        # folder in the -o template treated any '%' in the path as a template
        # metacharacter, so a folder like "100% Japanese" produced an invalid
        # template and the fetch failed with a misleading "outputs are missing".
        output_tpl = "%(id)s.%(ext)s"
        fmt = f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"

        cmd: list[str] = [self._ytdlp(), "--ignore-config"]
        # yt-dlp already implements manual-preferred-with-auto-fallback: in
        # process_subtitles, manual subs load first and automatic_captions only fill
        # languages not already present, so passing both flags writes exactly one
        # file and prefers the manual track. No second invocation needed.
        #
        # The auto flag is gated on fallback_allowed rather than passed
        # unconditionally, because for a non-Japanese-audio video
        # automatic_captions["ja"] is a MACHINE TRANSLATION (yt-dlp requests it with
        # {'tlang': ...}). Ungated, a manual_only video whose manual track vanished
        # between probe and fetch would silently mine translated Japanese — exactly
        # the false positive _has_native_auto_ja exists to prevent. Callers pass the
        # probe's has_auto_ja_subs, so the fallback only fires where the auto track
        # was already certified native.
        if sub_mode == "manual_only":
            cmd.append("--write-sub")
            if fallback_allowed:
                cmd.append("--write-auto-sub")
        elif sub_mode == "auto_only":
            cmd.append("--write-auto-sub")
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"Unsupported sub_mode: {sub_mode!r}")

        cmd.extend(
            [
                "--no-playlist",
                "--sub-lang",
                "ja",
                "--sub-format",
                "vtt/best",
                "--convert-subs",
                "srt",
                "--format",
                fmt,
                # The "home:" prefix is explicit so a Windows drive letter in the
                # path (e.g. "C:\\...") is never mistaken for a --paths TYPE.
                "--paths",
                f"home:{workspace}",
                "--output",
                output_tpl,
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

        # End-of-options separator before the user URL — see probe_metadata. T-34.
        cmd.append("--")
        cmd.append(url)
        return cmd

    def _cookie_args(self) -> list[str]:
        """yt-dlp cookie flags.

        A cookies file (``--cookies``) takes precedence over the browser
        dropdown (``--cookies-from-browser``); the two flags are mutually
        exclusive — yt-dlp errors if both are passed.
        """
        if self._config.youtube_cookies_file:
            return ["--cookies", str(self._config.youtube_cookies_file)]
        if self._config.youtube_cookies_from_browser:
            return ["--cookies-from-browser", self._config.youtube_cookies_from_browser]
        return []

    def _js_runtime_args(self) -> list[str]:
        """Enable an available JS runtime so yt-dlp can solve the n-challenge.

        YouTube extraction needs a JavaScript runtime, but yt-dlp's
        ``--js-runtimes`` defaults to deno only. When the user has node (or bun /
        quickjs) but not deno, extraction fails with "n challenge solving failed".
        Auto-pass the first available runtime. No-op when the installed yt-dlp
        lacks the flag or no supported runtime is on PATH (deno, yt-dlp's default,
        needs no flag). Issue #64.
        """
        if not _ytdlp_supports_js_runtimes(self._ytdlp()):
            return []
        for runtime in _JS_RUNTIMES:
            if shutil.which(runtime):
                return ["--js-runtimes", runtime]
        return []

    def _remote_component_args(self) -> list[str]:
        """Allow yt-dlp to fetch the EJS challenge-solver script when needed.

        A JS runtime alone is not enough: yt-dlp (>= ~2026.03) split YouTube
        challenge solving into a runtime *plus* the EJS solver script (the
        ``yt-dlp-ejs`` component), which it no longer auto-downloads. Without it,
        signature / n-sig solving fails ("Remote component challenge solver
        script ... was skipped"). ``ejs:github`` enables fetching it on first use
        (then yt-dlp caches it); ``ejs:npm`` is Deno/Bun-only, so github is the
        node-safe choice.

        Not gated on a runtime being found: deno-only users (whom
        ``_js_runtime_args`` deliberately skips, deno being yt-dlp's default) need
        the solver script too. Harmless when EJS is already bundled or pip-installed
        — yt-dlp prefers a local copy and the flag only *allows* a fetch when one is
        missing. No-op when the installed yt-dlp lacks the flag. Issue #64.
        """
        if not _ytdlp_supports_remote_components(self._ytdlp()):
            return []
        return ["--remote-components", "ejs:github"]

    @staticmethod
    def _is_postprocess_line(line: str) -> bool:
        if any(marker in line for marker in _POSTPROCESS_MARKERS):
            return True
        return "[download] 100%" in line and "Deleting original file" in line

    def _raise_for_error(self, tail: collections.deque[str]) -> None:
        joined_lower = "\n".join(tail).lower()

        if ("sign in" in joined_lower and "confirm" in joined_lower) or ("sign in to confirm" in joined_lower):
            raise BotDetectionError(
                "YouTube requires login. In Settings → YouTube, set Cookies from "
                "browser, or point Cookies file at an exported cookies.txt, then retry."
            )

        if "database is locked" in joined_lower or "database locked" in joined_lower:
            browser = self._config.youtube_cookies_from_browser or "the browser"
            msg = f"Cookie database is locked. Close {browser} and retry, or set Cookies → Browser to None."
            if sys.platform.startswith("linux") and ("profile" in joined_lower and "not found" in joined_lower):
                msg += (
                    " If you installed Firefox via Flatpak or Snap, use the "
                    "system-package Firefox instead, or set Cookies file in "
                    "Settings → YouTube to an exported cookies.txt."
                )
            raise CookieDatabaseLockedError(msg)

        # Extractor-freshness failures. YouTube keeps rolling out DRM and SABR-only
        # streaming experiments per client, and an older yt-dlp then finds no usable
        # format at all. The raw stderr for this is "Requested format is not
        # available", which reads like a bad --format string rather than "your yt-dlp
        # is too old" — so name the actual remedy.
        stale_extractor_markers = (
            "requested format is not available",
            "only images are available",
            "drm protected",
        )
        if any(marker in joined_lower for marker in stale_extractor_markers):
            raise YouTubeFetchError(
                "YouTube served no downloadable format for this video, which usually "
                "means yt-dlp is out of date (YouTube's DRM/SABR experiments break "
                "older versions). Use Settings → YouTube → Update yt-dlp now, or "
                "enable 'Keep yt-dlp up to date automatically', then retry. "
                f"yt-dlp said: {_tail(tail, 5)}"
            )

        raise YouTubeFetchError(f"yt-dlp exited non-zero: {_tail(tail, 20)}")

    def _resolve_outputs(self, workspace: Path, video_id: str, sub_mode: SubMode) -> FetchedMedia:
        candidates = list(workspace.glob(f"{video_id}*"))
        video_candidates: list[Path] = []
        subtitle_candidates: list[Path] = []
        for c in candidates:
            # Normally "<id>.ja.srt" (--convert-subs srt). Accept the un-converted
            # "<id>.ja.vtt" too: --convert-subs runs as an ffmpeg postprocessor, so if
            # it is skipped or fails the vtt is all that survives — and pysubs2 parses
            # vtt natively, so refusing it threw away a perfectly usable subtitle and
            # reported "expected output files are missing" instead.
            #
            # No "ja-orig" handling here on purpose: yt-dlp matches --sub-lang with a
            # regex fullmatch, so "ja" can never select the "ja-orig" track and such a
            # file can never be written.
            if c.name.endswith(".ja.srt") or c.name.endswith(".ja.vtt"):
                subtitle_candidates.append(c)
                continue
            if c.suffix.lower() in _VIDEO_EXTS:
                video_candidates.append(c)

        if len(video_candidates) > 1:
            names = sorted(p.name for p in video_candidates)
            raise YouTubeFetchError(f"Multiple video outputs found in workspace: {names}")

        # Prefer srt when both survive (a kept-original vtt alongside the converted
        # srt is not an ambiguity), and only complain about a genuine tie.
        srt_candidates = [p for p in subtitle_candidates if p.name.endswith(".srt")]
        preferred = srt_candidates or subtitle_candidates
        if len(preferred) > 1:
            names = sorted(p.name for p in preferred)
            raise YouTubeFetchError(f"Multiple subtitle outputs found in workspace: {names}")

        video_file = video_candidates[0] if video_candidates else None
        subtitle_file = preferred[0] if preferred else None

        if video_file is not None and subtitle_file is None:
            # yt-dlp writes subtitles before the video and reports
            # "There are no subtitles for the requested languages" as an info line
            # while still exiting 0, so we only learn this after paying for the whole
            # download. Deterministic, so the queue worker must not retry it.
            raise NoJapaneseSubtitlesError(
                "yt-dlp downloaded the video but wrote no Japanese subtitle "
                f"(mode={sub_mode}). The track listed at probe time was not available "
                "at download time."
            )
        if video_file is None or subtitle_file is None:
            raise YouTubeFetchError(
                f"yt-dlp exited 0 but expected output files are missing (video={video_file}, subtitle={subtitle_file})"
            )
        try:
            video_size = video_file.stat().st_size
        except OSError as e:
            raise YouTubeFetchError(f"Video file unreadable after fetch: {video_file}") from e
        if video_size <= 0:
            raise YouTubeFetchError(f"yt-dlp produced a zero-byte video file: {video_file}")
        try:
            sub_size = subtitle_file.stat().st_size
        except OSError as e:
            raise YouTubeFetchError(f"Subtitle file unreadable after fetch: {subtitle_file}") from e
        if sub_size <= 0:
            raise YouTubeFetchError(f"yt-dlp produced a zero-byte subtitle file: {subtitle_file}")

        sub_source: Literal["manual", "auto"] = "manual" if sub_mode == "manual_only" else "auto"
        return FetchedMedia(
            video_file=video_file,
            subtitle_file=subtitle_file,
            sub_source=sub_source,
        )
