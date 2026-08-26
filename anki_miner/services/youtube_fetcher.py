"""YouTube fetcher service: probe metadata and download video+subs via yt-dlp."""

from __future__ import annotations

import collections
import json
import logging
import re
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NoReturn

from PyQt6.QtCore import QCoreApplication

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
from anki_miner.models.youtube import FetchedMedia, PlaylistEntry, PlaylistInfo, SubMode, VideoInfo
from anki_miner.services import ytdlp_invocation
from anki_miner.services.audio_fetch_common import redact_url_for_log
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised
from anki_miner.utils.ytdlp_resolver import resolve_ytdlp, ytdlp_generation_lock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".webm", ".mkv"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYLIST_UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}

_YTDLP_FETCH_TIMEOUT_S = 3 * 60 * 60

# Max video height (px) fetched from YouTube; the format selector caps both the
# video and best-fallback streams. Was the hidden `config.youtube_max_height`
# knob (ARC-004: inlined, never surfaced in any panel).
YOUTUBE_MAX_HEIGHT = 720


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
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from exc

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
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the tail of stderr. A probe passes the
                configured cookie flags, so it fails on an unreadable cookie
                source exactly as a fetch does — see :meth:`_classified_error`.
            YouTubeFetchError: yt-dlp crashed, returned non-JSON, or omitted
                required keys.
            VideoTooLongError: video duration exceeds configured maximum.
        """
        logger.info("youtube probe starting: %s", redact_url_for_log(url))
        with ytdlp_generation_lock() as release_unless_managed:
            cmd: list[str] = [
                self._ytdlp(),
                "--ignore-config",
                "--skip-download",
                "--dump-single-json",
                "--no-playlist",
            ]
            cmd.extend(ytdlp_invocation.cookie_args(self._config))
            cmd.extend(ytdlp_invocation.js_runtime_args(self._config, self._ytdlp()))
            cmd.extend(ytdlp_invocation.remote_component_args(self._config, self._ytdlp()))
            # End-of-options separator: a '-'/'--'-leading URL must not be parsed
            # as a yt-dlp option (e.g. --update-to self-replaces the binary on the
            # probe alone, --config-location loads a planted --exec config). T-34.
            cmd.append("--")
            cmd.append(url)
            # Only the managed slot keeps the lock across the run; see
            # ytdlp_generation_lock. Must stay the last statement before the spawn.
            release_unless_managed(cmd[0])
            proc = run_supervised(
                cmd,
                timeout_s=timeout_s,
            )

        if isinstance(proc.error, FileNotFoundError):
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from proc.error
        if proc.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp metadata probe timed out after {timeout_s}s")

        if proc.state is SupervisedState.FAILED:
            if proc.returncode is None and proc.error is not None:
                raise YouTubeFetchError(f"yt-dlp metadata probe failed: {proc.error}") from proc.error
            self._raise_for_probe_error("metadata", proc.returncode, proc.stderr)

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
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the tail of stderr — see
                :meth:`probe_metadata`.
            YouTubeFetchError: yt-dlp crashed, returned non-JSON, the URL is
                not a playlist (missing / non-list ``entries`` key), or all
                entries were unusable (private / deleted / bad id).
        """
        logger.info(
            "youtube playlist probe starting: %s (limit=%s)",
            redact_url_for_log(url),
            limit,
        )
        with ytdlp_generation_lock() as release_unless_managed:
            cmd: list[str] = [
                self._ytdlp(),
                "--ignore-config",
                "--skip-download",
                "--flat-playlist",
                "--dump-single-json",
                "--playlist-items",
                f"1:{limit + 1}",
            ]
            cmd.extend(ytdlp_invocation.cookie_args(self._config))
            cmd.extend(ytdlp_invocation.js_runtime_args(self._config, self._ytdlp()))
            cmd.extend(ytdlp_invocation.remote_component_args(self._config, self._ytdlp()))
            # End-of-options separator before the user URL — see probe_metadata. T-34.
            cmd.append("--")
            cmd.append(url)
            # Only the managed slot keeps the lock across the run; see
            # ytdlp_generation_lock. Must stay the last statement before the spawn.
            release_unless_managed(cmd[0])
            proc = run_supervised(
                cmd,
                timeout_s=timeout_s,
            )

        if isinstance(proc.error, FileNotFoundError):
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from proc.error
        if proc.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp playlist probe timed out after {timeout_s}s")

        if proc.state is SupervisedState.FAILED:
            if proc.returncode is None and proc.error is not None:
                raise YouTubeFetchError(f"yt-dlp playlist probe failed: {proc.error}") from proc.error
            self._raise_for_probe_error("playlist", proc.returncode, proc.stderr)

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
        ``auto_dub`` format selector asks for ``bestaudio[language~='^ja(-|$)']``,
        which can only ever match an audio-only format, so that is what we
        require here. A muxed format's ``language`` names its container audio
        (the original), never a dub, and ``bestaudio`` cannot select it.

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
                certified native — never a machine translation. Ignored for
                ``"auto_dub"``, which always fetches the machine-translated ja
                captions together with the Japanese auto-dub audio track.

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
            m = ytdlp_invocation.PROGRESS_RE.search(line)
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

        with ytdlp_generation_lock() as release_unless_managed:
            cmd = self._build_fetch_cmd(url, workspace, sub_mode, fallback_allowed=fallback_allowed)
            # A fetch runs for as long as the video takes, so only the managed slot
            # keeps the lock across it; see ytdlp_generation_lock. Must stay the
            # last statement before the spawn.
            release_unless_managed(cmd[0])
            process_result = run_supervised(
                cmd,
                timeout_s=_YTDLP_FETCH_TIMEOUT_S,
                cancel=cancel_event,
                line_callback=handle_line,
                combine_stderr=True,
                retain_output=False,
            )
        if isinstance(process_result.error, FileNotFoundError):
            raise YtdlpNotFoundError(ytdlp_invocation.YTDLP_MISSING_HINT) from process_result.error
        if process_result.state is SupervisedState.CANCELLED:
            raise YouTubeFetchError("Cancelled by user")
        if process_result.state is SupervisedState.TIMED_OUT:
            raise YouTubeFetchError(f"yt-dlp download timed out after {_YTDLP_FETCH_TIMEOUT_S}s")
        if process_result.state is SupervisedState.FAILED:
            if process_result.returncode is None and process_result.error is not None:
                raise YouTubeFetchError(f"yt-dlp process failed: {process_result.error}") from process_result.error
            self._raise_for_error(tail, sub_mode)

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

    def _preflight_ffmpeg(self) -> None:
        loc = self._config.youtube_ffmpeg_location
        if loc is not None:
            p = Path(loc)
            if not (p.exists() and p.is_file()):
                raise FfmpegNotFoundError(f"Configured ffmpeg location does not exist: {p}")
            return
        # No explicit override: a bundled/resolved absolute binary satisfies the
        # preflight; otherwise fall back to the historical PATH check.
        if ytdlp_invocation.effective_ffmpeg_location(self._config) is not None:
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
        if sub_mode == "auto_dub":
            # Auto-dub route: the ja captions are machine-translated, matching
            # the Japanese auto-dub audio track — so that exact track must be
            # fetched. language~='^ja(-|$)' is a regex-anchored match, same as
            # the probe's _has_ja_audio_track: it catches "ja" and regional
            # "ja-JP" but not an unrelated code that merely starts with "ja"
            # (e.g. "jav", Javanese) — a bare [language^=ja] prefix test would
            # admit that. The selector deliberately has NO "/bestaudio"
            # fallback, because falling back to the original (non-JA) audio
            # would silently mine MT subs against foreign audio. If the dub
            # vanished since the probe, the fetch fails and _raise_for_error
            # names the cause.
            fmt = f"bestvideo[height<={max_height}]+bestaudio[language~='^ja(-|$)']"

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
        elif sub_mode in ("auto_only", "auto_dub"):
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

        cmd.extend(ytdlp_invocation.cookie_args(self._config))
        cmd.extend(ytdlp_invocation.js_runtime_args(self._config, self._ytdlp()))
        cmd.extend(ytdlp_invocation.remote_component_args(self._config, self._ytdlp()))
        ffmpeg_location = ytdlp_invocation.effective_ffmpeg_location(self._config)
        if ffmpeg_location is not None:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])

        # End-of-options separator before the user URL — see probe_metadata. T-34.
        cmd.append("--")
        cmd.append(url)
        return cmd

    @staticmethod
    def _is_postprocess_line(line: str) -> bool:
        if any(marker in line for marker in ytdlp_invocation.POSTPROCESS_MARKERS):
            return True
        return "[download] 100%" in line and "Deleting original file" in line

    def _classified_error(
        self,
        tail: collections.deque[str],
        sub_mode: SubMode | None,
    ) -> YouTubeFetchError | None:
        """Return a typed error for a recognized yt-dlp failure, else None.

        Shared by every yt-dlp call site in this service — the two probes and
        the fetch — so a login wall, a cookie-source failure or a stale
        extractor reads the same whether it surfaces while checking a URL or
        while downloading it. Probes pass ``sub_mode=None``; the one branch that
        needs a mode guards on it explicitly.

        Returning (rather than raising) lets each caller keep its own wording
        for the unrecognized case, which differs: a probe says "probe failed",
        the fetch says "exited non-zero".
        """
        joined_lower = "\n".join(tail).lower()
        classification = ytdlp_invocation.classify_error_tail(joined_lower)

        if classification == "bot":
            return BotDetectionError(
                "YouTube requires login. In Settings → YouTube, set Cookies from "
                "browser, or point Cookies file at an exported cookies.txt, then retry."
            )

        if classification in ytdlp_invocation.COOKIE_TAGS:
            return CookieDatabaseLockedError(
                ytdlp_invocation.cookie_failure_message(
                    str(classification),
                    self._config.youtube_cookies_from_browser,
                    joined_lower,
                    platform=sys.platform,
                )
            )

        # Extractor-freshness failures. YouTube keeps rolling out DRM and SABR-only
        # streaming experiments per client, and an older yt-dlp then finds no usable
        # format at all. The raw stderr for this is "Requested format is not
        # available", which reads like a bad --format string rather than "your yt-dlp
        # is too old" — so name the actual remedy. (classify_error_tail's shared
        # "format_missing" covers the "requested format" marker; the two extra
        # markers below are YouTube-specific and stay local.)
        stale_extractor_markers = (
            "only images are available",
            "drm protected",
        )
        if classification == "format_missing" or any(marker in joined_lower for marker in stale_extractor_markers):
            if sub_mode == "auto_dub" and "requested format is not available" in joined_lower:
                # On this route the format selector pins the JA dub track with
                # no fallback, so "no format" almost always means either side of
                # the selector went missing between probe and fetch — the dub
                # track, or the video stream it is paired with — and "update
                # yt-dlp" would send the user to the wrong remedy. Deterministic:
                # a retry re-fetches the same selector against the same missing
                # format and fails identically, so this is typed to opt out of
                # the queue worker's automatic retry (_DETERMINISTIC_FETCH_ERRORS).
                #
                # Unreachable from a probe: probes pass sub_mode=None, and a probe
                # requests no format at all, so it cannot fail to match one.
                return DubAudioUnavailableError(
                    "No format matched the pinned Japanese-audio selector — the "
                    "auto-dub track listed at probe time is no longer available "
                    "(or no separate video stream exists), so this video cannot "
                    f"be mined via the dub route. yt-dlp said: {ytdlp_invocation.tail_lines(tail, 5)}"
                )
            return YouTubeFetchError(
                "YouTube served no downloadable format for this video, which usually "
                "means yt-dlp is out of date (YouTube's DRM/SABR experiments break "
                "older versions). Use Settings → YouTube → Update yt-dlp now, or "
                "enable 'Keep yt-dlp up to date automatically', then retry. "
                f"yt-dlp said: {ytdlp_invocation.tail_lines(tail, 5)}"
            )

        return None

    def _raise_for_error(self, tail: collections.deque[str], sub_mode: SubMode) -> None:
        """Raise the fetch-side failure for a non-zero yt-dlp exit."""
        error = self._classified_error(tail, sub_mode)
        if error is not None:
            raise error
        raise YouTubeFetchError(f"yt-dlp exited non-zero: {ytdlp_invocation.tail_lines(tail, 20)}")

    def _raise_for_probe_error(self, label: str, returncode: int | None, stderr: str | None) -> NoReturn:
        """Raise the probe-side failure for a non-zero yt-dlp exit.

        A probe fails for most of the same reasons a fetch does — the cookie
        source is unreadable, YouTube wants a login, the extractor is stale —
        so it runs the same classifier and only falls back to the verbatim tail
        when nothing matches. The verbatim fallback is deliberate: an
        unrecognized yt-dlp error is more useful on screen in full than
        paraphrased (pinned by test_long_multiline_probe_error_stays_off_the_row).
        """
        lines = (stderr or "").strip().splitlines()[-20:]
        error = self._classified_error(collections.deque(lines), None)
        if error is not None:
            raise error
        raise YouTubeFetchError(f"yt-dlp {label} probe failed (exit {returncode}): {chr(10).join(lines)}")

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
