"""Shared yt-dlp argv builders and error-tail classification (D2).

:class:`~anki_miner.services.youtube_fetcher.YouTubeFetcherService` and
:class:`~anki_miner.services.media_downloader.MediaDownloaderService` both
build yt-dlp command lines (cookies, JS-runtime/EJS challenge-solver flags,
ffmpeg location) and recognize the same well-known failure signatures in
yt-dlp's stderr. This module is the single source of truth for both, so a
change to yt-dlp semantics only needs to land once instead of being kept in
sync by hand across two files.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.utils.ffmpeg_resolver import resolve_ffmpeg
from anki_miner.utils.subprocess_utils import no_window_kwargs
from anki_miner.utils.ytdlp_resolver import managed_ytdlp_lock

# Message appended to YtdlpNotFoundError so the user can self-serve the fix.
YTDLP_MISSING_HINT = "yt-dlp executable not found. Use Settings → YouTube → Update yt-dlp now, then retry."

PROGRESS_RE = re.compile(r"\[ankimine_dl\] (\S+) (\S+)")

# Union of both services' postprocessor markers. youtube_fetcher's yt-dlp
# invocation never requests --embed-thumbnail/--embed-metadata/thumbnail
# conversion, so the markers those postprocessors emit never appear in its
# output tail — including them here is inert there and only ever matches for
# media_downloader, which does support those options.
POSTPROCESS_MARKERS = (
    "[Merger]",
    "[ExtractAudio]",
    "[EmbedThumbnail]",
    "[Metadata]",
    "[SubtitleConvertor]",
    "[ThumbnailsConvertor]",
    "[FixupM3u8]",
)

# JS runtimes yt-dlp can solve YouTube's n-challenge with. "deno" is omitted: it is
# yt-dlp's built-in default, so when the user has deno nothing needs doing. Ordered
# by preference for the failing case (node is the common Windows setup). Issue #64.
JS_RUNTIMES = ("node", "bun", "quickjs")


def tail_lines(lines: Sequence[str], n: int = 20) -> str:
    """Return the last *n* lines of *lines* joined by newlines."""
    return "\n".join(list(lines)[-n:])


# Keyed on the resolved yt-dlp path (unbounded cache), NOT a 1-entry cache: the
# resolved path changes after a self-update download, and a 1-entry cache keyed
# on nothing would then report the OLD binary's capabilities for the NEW one.
@functools.cache
def ytdlp_supports_js_runtimes(ytdlp_path: str) -> bool:
    """True if the yt-dlp at *ytdlp_path* recognizes ``--js-runtimes``.

    Cached per resolved path. Guards against older yt-dlp that lacks the flag —
    passing an unknown option would break all YouTube mining. Any failure (yt-dlp
    missing, timeout) returns False -> behave as before.
    """
    try:
        with managed_ytdlp_lock(ytdlp_path):
            proc = subprocess.run(
                [ytdlp_path, "--ignore-config", "--help"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "--js-runtimes" in (proc.stdout or "")


@functools.cache
def ytdlp_supports_remote_components(ytdlp_path: str) -> bool:
    """True if the yt-dlp at *ytdlp_path* recognizes ``--remote-components``.

    Cached per resolved path (see ``ytdlp_supports_js_runtimes`` for why the
    path is the cache key). Probed separately from ``--js-runtimes`` so an older
    yt-dlp that knows one flag but not the other still degrades safely. Any
    failure (yt-dlp missing, timeout) returns False -> behave as before. Issue #64.
    """
    try:
        with managed_ytdlp_lock(ytdlp_path):
            proc = subprocess.run(
                [ytdlp_path, "--ignore-config", "--help"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                **no_window_kwargs(),  # hide the Windows cmd.exe flash (Issue #79)
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return "--remote-components" in (proc.stdout or "")


def cookie_args(config: AnkiMinerConfig) -> list[str]:
    """yt-dlp cookie flags.

    A cookies file (``--cookies``) takes precedence over the browser
    dropdown (``--cookies-from-browser``); the two flags are mutually
    exclusive — yt-dlp errors if both are passed.
    """
    if config.youtube_cookies_file:
        return ["--cookies", str(config.youtube_cookies_file)]
    if config.youtube_cookies_from_browser:
        return ["--cookies-from-browser", config.youtube_cookies_from_browser]
    return []


def js_runtime_args(config: AnkiMinerConfig, executable: str) -> list[str]:
    """Enable an available JS runtime so yt-dlp can solve the n-challenge.

    YouTube extraction needs a JavaScript runtime, but yt-dlp's
    ``--js-runtimes`` defaults to deno only. When the user has node (or bun /
    quickjs) but not deno, extraction fails with "n challenge solving failed".
    Auto-pass the first available runtime. No-op when the installed yt-dlp
    lacks the flag or no supported runtime is on PATH (deno, yt-dlp's default,
    needs no flag). Issue #64.

    *config* is accepted (but currently unused) so all argv-builder functions
    in this module share one calling convention; the capability probe is keyed
    solely on the resolved binary path.
    """
    if not ytdlp_supports_js_runtimes(executable):
        return []
    for runtime in JS_RUNTIMES:
        if shutil.which(runtime):
            return ["--js-runtimes", runtime]
    return []


def remote_component_args(config: AnkiMinerConfig, executable: str) -> list[str]:
    """Allow yt-dlp to fetch the EJS challenge-solver script when needed.

    A JS runtime alone is not enough: yt-dlp (>= ~2026.03) split YouTube
    challenge solving into a runtime *plus* the EJS solver script (the
    ``yt-dlp-ejs`` component), which it no longer auto-downloads. Without it,
    signature / n-sig solving fails ("Remote component challenge solver
    script ... was skipped"). ``ejs:github`` enables fetching it on first use
    (then yt-dlp caches it); ``ejs:npm`` is Deno/Bun-only, so github is the
    node-safe choice.

    Not gated on a runtime being found: deno-only users (whom
    ``js_runtime_args`` deliberately skips, deno being yt-dlp's default) need
    the solver script too. Harmless when EJS is already bundled or pip-installed
    — yt-dlp prefers a local copy and the flag only *allows* a fetch when one is
    missing. No-op when the installed yt-dlp lacks the flag. Issue #64.

    *config* is accepted (but currently unused) — see ``js_runtime_args``.
    """
    if not ytdlp_supports_remote_components(executable):
        return []
    return ["--remote-components", "ejs:github"]


def effective_ffmpeg_location(config: AnkiMinerConfig) -> str | None:
    """Resolve the ffmpeg path to hand yt-dlp, or None to rely on PATH.

    Precedence:
    1. ``youtube_ffmpeg_location`` explicit override (existence is validated
       separately by the caller, e.g. ``YouTubeFetcherService._preflight_ffmpeg``).
    2. ``resolve_ffmpeg(config)`` — picks up the bundled binary in frozen
       builds (or a ``ffmpeg_location`` override). Returned only when it is a
       real absolute file; the bare literal ``"ffmpeg"`` means "use PATH".

    Returns:
        An absolute file path string, or ``None`` to let yt-dlp do its own
        PATH lookup.
    """
    loc = config.youtube_ffmpeg_location
    if loc is not None:
        return str(loc)
    resolved = resolve_ffmpeg(config)
    if resolved != "ffmpeg" and Path(resolved).is_file():
        return resolved
    return None


#: Cookie-source classifications, in match order. Every marker here is a literal
#: substring of a message yt-dlp really emits — cited by ``yt_dlp/cookies.py``
#: line so the next yt-dlp bump has one place to re-check.
#:
#: DO NOT invent a signature for this table. A marker matching nothing is
#: indistinguishable from a marker matching everything until a user hits it:
#: the pre-2026-08 table matched only ``"database is locked"``, which no yt-dlp
#: release emits (chromium *and* firefox copy the DB first — ``cookies.py``
#: ``_open_database_copy`` — so sqlite never reports a lock), and the branch sat
#: dead on every platform because the tests fed it a hand-written string. The
#: tests in ``tests/unit/test_ytdlp_invocation.py`` now paste yt-dlp's own text.
_COOKIE_MARKERS: tuple[tuple[tuple[str, ...], str], ...] = (
    # cookies.py:363 — Windows-only PermissionError(errno 13) while shutil.copy'ing
    # a chromium cookie DB the running browser holds open. yt-dlp issue #7271.
    (("could not copy", "cookie database"), "cookie_locked"),
    # cookies.py:1099 — DPAPI/app-bound-encryption decrypt failure. Closing the
    # browser does NOT clear this one. yt-dlp issue #10927.
    (("failed to decrypt with dpapi",), "cookie_decrypt"),
    # cookies.py:146 and :318 — FileNotFoundError, no cookie DB under the browser's
    # search root: browser not installed, or its profile lives somewhere else
    # (Flatpak/Snap Firefox is the common Linux case).
    (("cookies database in",), "cookie_missing"),
    # Legacy sqlite wording. No current yt-dlp emits it, but a user may have
    # pinned an old build, and keeping it costs one tuple.
    (("database is locked",), "cookie_locked"),
    (("database locked",), "cookie_locked"),
)

#: The tags :func:`cookie_failure_message` accepts. Callers branch on membership
#: rather than listing the tags themselves, so adding a fourth cookie signature
#: stays a one-line change to ``_COOKIE_MARKERS``.
COOKIE_TAGS: frozenset[str] = frozenset(tag for _markers, tag in _COOKIE_MARKERS)


def classify_error_tail(joined_lower: str) -> str | None:
    """Classify a well-known yt-dlp failure signature from lower-cased stderr tail.

    Returns ``"bot"`` (login/bot-check wall), one of the three cookie-source
    tags in :data:`_COOKIE_MARKERS` (``"cookie_locked"`` / ``"cookie_decrypt"`` /
    ``"cookie_missing"`` — pass any of them to :func:`cookie_failure_message`),
    ``"format_missing"`` (no matching format), or ``None`` when nothing
    recognizable matched. Each caller maps the classification to its own
    exception type; a caller with additional, service-specific failure
    signatures (e.g. youtube_fetcher's extractor-freshness markers) layers its
    own extra checks around this result.
    """
    if "sign in" in joined_lower and "confirm" in joined_lower:
        return "bot"
    for markers, tag in _COOKIE_MARKERS:
        if all(marker in joined_lower for marker in markers):
            return tag
    if "requested format is not available" in joined_lower:
        return "format_missing"
    return None


def cookie_failure_message(tag: str, browser: str | None, joined_lower: str, *, platform: str) -> str:
    """Build the user-facing remedy for a cookie-source classification.

    The three tags fail for different reasons and want different remedies, so
    "close the browser" is wrong for two of them: a DPAPI failure survives every
    browser restart, and a missing database means the browser or profile was
    never there to lock.

    Args:
        tag: a cookie tag from :func:`classify_error_tail`.
        browser: the configured ``--cookies-from-browser`` value, or None.
        joined_lower: the lower-cased stderr tail, for sub-case guidance.
        platform: ``sys.platform`` of the caller, for platform-specific hints.

    Returns:
        One sentence naming the remedy, phrased against Settings → YouTube.
    """
    named = browser or "the browser"
    if tag == "cookie_decrypt":
        return (
            f"Windows could not decrypt {named}'s cookies. Export a cookies.txt and set it as "
            "Cookies file in Settings → YouTube, or set Cookies → Browser to None. Restarting "
            "the browser does not help."
        )
    if tag == "cookie_missing":
        msg = (
            f"No cookie database found for {named}. Pick a browser you actually use in "
            "Settings → YouTube, set Cookies → Browser to None, or point Cookies file at an "
            "exported cookies.txt."
        )
        # Flatpak/Snap Firefox keeps its profile outside ~/.mozilla, so yt-dlp
        # searches the right path and finds nothing. Only tell a Firefox user
        # this: on a Chrome miss it is noise pointing at the wrong browser.
        if platform.startswith("linux") and browser is not None and "firefox" in browser.lower():
            msg += (
                " If you installed Firefox via Flatpak or Snap, use the "
                "system-package Firefox instead, or use the cookies.txt route."
            )
        return msg
    msg = f"Cookie database is locked. Close {named} and retry, or set Cookies → Browser to None."
    if platform.startswith("linux") and ("profile" in joined_lower and "not found" in joined_lower):
        msg += (
            " If you installed Firefox via Flatpak or Snap, use the "
            "system-package Firefox instead, or set Cookies file in "
            "Settings → YouTube to an exported cookies.txt."
        )
    return msg
