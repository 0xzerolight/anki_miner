"""YouTube fetcher service: probe metadata and download video+subs via yt-dlp."""

from __future__ import annotations

import collections
import contextlib
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

import psutil  # type: ignore[import-untyped]

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import (
    BotDetectionError,
    CookieDatabaseLockedError,
    FfmpegNotFoundError,
    VideoTooLongError,
    YouTubeFetchError,
)
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.models.youtube import FetchedMedia, SubMode, VideoInfo
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_VIDEO_EXTS = {".mp4", ".webm", ".mkv"}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PROGRESS_RE = re.compile(r"\[ankimine_dl\] (\S+) (\S+)")
_POSTPROCESS_MARKERS = ("[Merger]", "[FixupM3u8]", "[SubtitleConvertor]", "[ExtractAudio]")

# JS runtimes yt-dlp can solve YouTube's n-challenge with. "deno" is omitted: it is
# yt-dlp's built-in default, so when the user has deno nothing needs doing. Ordered
# by preference for the failing case (node is the common Windows setup). Issue #64.
_JS_RUNTIMES = ("node", "bun", "quickjs")


@functools.lru_cache(maxsize=1)
def _ytdlp_supports_js_runtimes() -> bool:
    """True if the installed yt-dlp recognizes ``--js-runtimes``.

    Cached for the process lifetime; the binary on PATH does not change mid-run.
    Guards against older yt-dlp that lacks the flag — passing an unknown option
    would break all YouTube mining. Any failure (yt-dlp missing, timeout) returns
    False -> behave as before.
    """
    try:
        proc = subprocess.run(
            ["yt-dlp", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "--js-runtimes" in (proc.stdout or "")


@functools.lru_cache(maxsize=1)
def _ytdlp_supports_remote_components() -> bool:
    """True if the installed yt-dlp recognizes ``--remote-components``.

    Cached for the process lifetime; the binary on PATH does not change mid-run.
    Probed separately from ``--js-runtimes`` so an older yt-dlp that knows one
    flag but not the other still degrades safely. Any failure (yt-dlp missing,
    timeout) returns False -> behave as before. Issue #64.
    """
    try:
        proc = subprocess.run(
            ["yt-dlp", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
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

    This service is stateless beyond holding the currently active fetch
    subprocess handle so that cancellation can reach it. It does not keep
    any cross-call caches.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol | None = None,
    ) -> None:
        self._config = config
        self._presenter = presenter
        self._popen: subprocess.Popen[str] | None = None

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
        logger.info("youtube probe starting: %s", url)
        cmd: list[str] = [
            "yt-dlp",
            "--skip-download",
            "--dump-single-json",
            "--no-playlist",
        ]
        cmd.extend(self._cookie_args())
        cmd.extend(self._js_runtime_args())
        cmd.extend(self._remote_component_args())
        cmd.append(url)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            raise YouTubeFetchError(f"yt-dlp metadata probe timed out after {e.timeout}s") from e
        except FileNotFoundError as e:
            raise YouTubeFetchError("yt-dlp executable not found on PATH. Install yt-dlp and retry.") from e

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-20:]
            raise YouTubeFetchError(
                "yt-dlp metadata probe failed (exit " f"{proc.returncode}): {chr(10).join(stderr_tail)}"
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

        duration_s = int(duration)
        if duration_s > self._config.youtube_max_duration_s:
            raise VideoTooLongError(
                f"Video duration {duration_s}s exceeds configured maximum " f"{self._config.youtube_max_duration_s}s"
            )

        subs = data.get("subtitles") or {}
        has_manual_ja = bool(subs.get("ja"))
        has_auto_ja = self._has_native_auto_ja(data)

        logger.info("youtube probe ok: id=%s duration=%s", video_id, duration_s)
        return VideoInfo(
            video_id=video_id,
            title=str(title),
            duration_s=duration_s,
            has_manual_ja_subs=has_manual_ja,
            has_auto_ja_subs=has_auto_ja,
            thumbnail_url=data.get("thumbnail"),
            uploader=data.get("uploader"),
            is_live=bool(data.get("is_live")),
            is_age_restricted=int(data.get("age_limit") or 0) >= 18,
        )

    @staticmethod
    def _has_native_auto_ja(data: dict) -> bool:
        """Detect native Japanese auto-captions, ignoring translated-from-X.

        The mere presence of ``automatic_captions.ja`` does NOT mean the video
        is actually Japanese: yt-dlp lists auto-translated tracks (e.g. Japanese
        auto-translated from English) under the same key. So this also checks
        the top-level ``language`` field and each track's ``name`` for
        'translated' / 'from X' markers, and treats those as non-native.
        """
        auto = data.get("automatic_captions") or {}
        if "ja" not in auto or not auto["ja"]:
            return False
        lang = (data.get("language") or "").lower()
        if lang and lang != "ja":
            return False
        for track in auto["ja"]:
            name = (track.get("name") or "").lower()
            if "from " in name or "translated" in name:
                return False
        return True

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
    ) -> FetchedMedia:
        """Download the video + Japanese subtitles into *workspace*.

        Raises:
            FfmpegNotFoundError: ffmpeg preflight failed.
            BotDetectionError / CookieDatabaseLockedError: well-known yt-dlp
                failure modes detected in the tail of stderr.
            YouTubeFetchError: any other non-zero exit, cancellation, or
                missing/zero-byte output file.
        """
        logger.info("youtube fetch starting: id=%s workspace=%s", video_id, workspace)
        self._preflight_ffmpeg()

        cmd = self._build_fetch_cmd(url, workspace, sub_mode)

        self._popen = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            text=True,
        )

        tail: collections.deque[str] = collections.deque(maxlen=50)
        postprocessing_seen = False
        cancelled = False

        assert self._popen.stdout is not None
        try:
            for raw in self._popen.stdout:
                line = raw.rstrip("\n")
                tail.append(line)

                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    self._kill_tree()
                    break

                m = _PROGRESS_RE.search(line)
                if m is not None:
                    if progress_cb is not None:
                        downloaded_s, total_s = m.group(1), m.group(2)
                        if total_s == "NA":
                            progress_cb("Downloading video", None)
                        else:
                            try:
                                downloaded = float(downloaded_s)
                                total = float(total_s)
                                frac = downloaded / total if total > 0 else None
                            except ValueError:
                                frac = None
                            progress_cb("Downloading video", frac)
                    continue

                if not postprocessing_seen and self._is_postprocess_line(line):
                    postprocessing_seen = True
                    if progress_cb is not None:
                        progress_cb("Merging", None)
                    continue

                # Forward other output to the presenter for visibility.
                if self._presenter is not None and line.strip():
                    try:
                        self._presenter.show_info(line)
                    except Exception:  # pragma: no cover - presenter best effort
                        logger.debug("presenter.show_info raised; ignoring")
        finally:
            returncode = self._popen.wait()
            self._popen = None

        if cancelled:
            raise YouTubeFetchError("Cancelled by user")

        if returncode != 0:
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
                "ffmpeg not found on PATH. Install ffmpeg or set the " "'youtube_ffmpeg_location' config option."
            )

    def _build_fetch_cmd(self, url: str, workspace: Path, sub_mode: SubMode) -> list[str]:
        max_height = self._config.youtube_max_height
        output_tpl = f"{workspace}/%(id)s.%(ext)s"
        fmt = f"bestvideo[height<={max_height}]+bestaudio/" f"best[height<={max_height}]"

        cmd: list[str] = ["yt-dlp"]
        if sub_mode == "manual_only":
            cmd.append("--write-sub")
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
                "--output",
                output_tpl,
                "--newline",
                "--progress-template",
                "download:[ankimine_dl] %(progress.downloaded_bytes)s " "%(progress.total_bytes)s",
                "--retries",
                "3",
                "--fragment-retries",
                "3",
            ]
        )

        cmd.extend(self._cookie_args())
        cmd.extend(self._js_runtime_args())
        cmd.extend(self._remote_component_args())
        ffmpeg_location = self._effective_ffmpeg_location()
        if ffmpeg_location is not None:
            cmd.extend(["--ffmpeg-location", ffmpeg_location])

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
        if not _ytdlp_supports_js_runtimes():
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
        if not _ytdlp_supports_remote_components():
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
            msg = f"Cookie database is locked. Close {browser} and retry, or " "set Cookies → Browser to None."
            if sys.platform.startswith("linux") and ("profile" in joined_lower and "not found" in joined_lower):
                msg += (
                    " If you installed Firefox via Flatpak or Snap, use the "
                    "system-package Firefox instead, or set Cookies file in "
                    "Settings → YouTube to an exported cookies.txt."
                )
            raise CookieDatabaseLockedError(msg)

        raise YouTubeFetchError(f"yt-dlp exited non-zero: {_tail(tail, 20)}")

    def _resolve_outputs(self, workspace: Path, video_id: str, sub_mode: SubMode) -> FetchedMedia:
        candidates = list(workspace.glob(f"{video_id}*"))
        video_candidates: list[Path] = []
        subtitle_candidates: list[Path] = []
        for c in candidates:
            # Subtitle after --convert-subs srt -> "<id>.ja.srt"
            if c.name.endswith(".ja.srt"):
                subtitle_candidates.append(c)
                continue
            if c.suffix.lower() in _VIDEO_EXTS:
                video_candidates.append(c)

        if len(video_candidates) > 1:
            names = sorted(p.name for p in video_candidates)
            raise YouTubeFetchError(f"Multiple video outputs found in workspace: {names}")
        if len(subtitle_candidates) > 1:
            names = sorted(p.name for p in subtitle_candidates)
            raise YouTubeFetchError(f"Multiple subtitle outputs found in workspace: {names}")

        video_file = video_candidates[0] if video_candidates else None
        subtitle_file = subtitle_candidates[0] if subtitle_candidates else None

        if video_file is None or subtitle_file is None:
            raise YouTubeFetchError(
                "yt-dlp exited 0 but expected output files are missing "
                f"(video={video_file}, subtitle={subtitle_file})"
            )
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

    def _kill_tree(self) -> None:
        if not self._popen:
            return
        try:
            parent = psutil.Process(self._popen.pid)
        except psutil.NoSuchProcess:
            return

        try:
            children = parent.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []

        for c in children:
            with contextlib.suppress(psutil.NoSuchProcess):
                c.terminate()
        logger.info("killing yt-dlp process tree: pid=%s", parent.pid)
        with contextlib.suppress(psutil.NoSuchProcess):
            parent.terminate()

        try:
            _, alive = psutil.wait_procs([parent, *children], timeout=5)
        except psutil.NoSuchProcess:
            alive = []
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess):
                p.kill()
