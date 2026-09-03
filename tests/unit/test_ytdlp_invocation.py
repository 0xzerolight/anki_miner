"""Error-tail classification against yt-dlp's own message text.

Every stderr string in this module is pasted **verbatim** from the cited
``yt_dlp/cookies.py`` line, not retyped from memory or invented. That is the
whole point of the file: the cookie branch of ``classify_error_tail`` used to
match only ``"database is locked"``, a string no yt-dlp release emits (chromium
and firefox both copy the DB before opening it — ``_open_database_copy`` — so
sqlite never reports a lock). The branch was dead on every platform for every
browser, and the tests did not notice because they fed it that same invented
string. Verbatim text is the only thing that can catch the same rot next time.

When bumping yt-dlp, re-check the cited lines. A marker that stops matching is a
silent regression: the user sees yt-dlp's raw text and a link to yt-dlp's issue
tracker instead of the remedy this app already knows.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.ytdlp_invocation import (
    COOKIE_TAGS,
    classify_error_tail,
    cookie_args,
    cookie_failure_message,
    ytdlp_supports_js_runtimes,
    ytdlp_supports_remote_components,
)

# ---------------------------------------------------------------------------
# Verbatim yt-dlp stderr, with the source line that produces it.
# ---------------------------------------------------------------------------

#: cookies.py:363 — Windows PermissionError(errno 13) copying a chromium cookie
#: DB the running browser holds open. This is Issue #119's exact failure.
CHROME_COPY_FAILED = (
    "ERROR: Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271  for more info"
)

#: cookies.py:1099 — DPAPI decrypt failure (Chrome app-bound encryption).
DPAPI_DECRYPT_FAILED = (
    "ERROR: Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info"
)

#: cookies.py:318 — chromium cookie DB absent under the browser's search root.
#: Captured by running the shipped yt-dlp on a machine with no Chrome installed.
CHROME_DB_NOT_FOUND = 'ERROR: could not find chrome cookies database in "/home/u/.config/google-chrome"'

#: cookies.py:146 — same, firefox branch (unquoted search root).
FIREFOX_DB_NOT_FOUND = "ERROR: could not find firefox cookies database in /home/u/.mozilla/firefox"


class TestCookieClassification:
    """Each real cookie failure must classify, and to the right tag."""

    @pytest.mark.parametrize(
        ("stderr", "expected"),
        [
            (CHROME_COPY_FAILED, "cookie_locked"),
            (DPAPI_DECRYPT_FAILED, "cookie_decrypt"),
            (CHROME_DB_NOT_FOUND, "cookie_missing"),
            (FIREFOX_DB_NOT_FOUND, "cookie_missing"),
            # Legacy sqlite wording — no current release emits it, but a user may
            # have pinned an old build.
            ("ERROR: could not decrypt cookies: database is locked", "cookie_locked"),
        ],
    )
    def test_real_stderr_classifies(self, stderr: str, expected: str) -> None:
        assert classify_error_tail(stderr.lower()) == expected

    def test_every_cookie_tag_is_reachable(self) -> None:
        """No tag may exist that no marker can produce (or vice versa)."""
        produced = {
            classify_error_tail(s.lower()) for s in (CHROME_COPY_FAILED, DPAPI_DECRYPT_FAILED, CHROME_DB_NOT_FOUND)
        }
        assert produced == COOKIE_TAGS

    def test_cookie_tags_precede_format_missing(self) -> None:
        """A cookie failure that also mentions formats still reads as a cookie failure.

        yt-dlp keeps going after a cookie warning on some paths, so both markers
        can land in one tail; the cookie source is the cause and the format line
        is the consequence.
        """
        tail = f"{CHROME_COPY_FAILED}\nERROR: Requested format is not available"
        assert classify_error_tail(tail.lower()) in COOKIE_TAGS


class TestNonCookieClassification:
    """The other tags, and the deliberate None fallback."""

    def test_bot_wall(self) -> None:
        tail = "ERROR: Sign in to confirm you're not a bot. Use --cookies-from-browser"
        assert classify_error_tail(tail.lower()) == "bot"

    def test_format_missing(self) -> None:
        assert classify_error_tail("error: requested format is not available") == "format_missing"

    @pytest.mark.parametrize(
        "stderr",
        [
            "ERROR: Video unavailable",
            "ERROR: [youtube] abc123: Private video. Sign in if you've been granted access",
            "ERROR: unable to download video data: HTTP Error 403: Forbidden",
            "",
        ],
    )
    def test_unrecognized_returns_none(self, stderr: str) -> None:
        """Unknown failures must stay unclassified so callers show the raw tail.

        Note the private-video case: it contains "sign in" but not "confirm", so
        it must not be mistaken for the bot wall and sent to the cookies remedy.
        """
        assert classify_error_tail(stderr.lower()) is None


class TestCookieFailureMessage:
    """Each tag's remedy must actually fit its failure."""

    def test_locked_says_close_the_browser(self) -> None:
        msg = cookie_failure_message("cookie_locked", "chrome", CHROME_COPY_FAILED.lower(), platform="win32")
        assert "Close chrome" in msg
        # yt-dlp's own text and issue link must not reach the user.
        assert "Could not copy" not in msg
        assert "github.com" not in msg

    def test_decrypt_does_not_say_close_the_browser(self) -> None:
        """Restarting the browser never clears a DPAPI failure — saying so would
        send the user round a loop that cannot work."""
        msg = cookie_failure_message("cookie_decrypt", "chrome", DPAPI_DECRYPT_FAILED.lower(), platform="win32")
        assert "Close chrome" not in msg
        assert "cookies.txt" in msg

    def test_missing_does_not_say_close_the_browser(self) -> None:
        msg = cookie_failure_message("cookie_missing", "chrome", CHROME_DB_NOT_FOUND.lower(), platform="linux")
        assert "Close chrome" not in msg
        assert "No cookie database found" in msg

    def test_unset_browser_reads_as_a_sentence(self) -> None:
        msg = cookie_failure_message("cookie_locked", None, "", platform="win32")
        assert "Close the browser" in msg

    def test_linux_missing_names_flatpak(self) -> None:
        msg = cookie_failure_message("cookie_missing", "firefox", FIREFOX_DB_NOT_FOUND.lower(), platform="linux")
        assert "Flatpak or Snap" in msg

    @pytest.mark.parametrize("platform", ["win32", "darwin"])
    def test_non_linux_missing_omits_flatpak(self, platform: str) -> None:
        msg = cookie_failure_message("cookie_missing", "firefox", FIREFOX_DB_NOT_FOUND.lower(), platform=platform)
        assert "Flatpak or Snap" not in msg

    def test_chrome_miss_does_not_mention_firefox(self) -> None:
        """The Flatpak hint is about Firefox's profile location; on a Chrome miss
        it points the user at a browser they did not choose."""
        msg = cookie_failure_message("cookie_missing", "chrome", CHROME_DB_NOT_FOUND.lower(), platform="linux")
        assert "Flatpak or Snap" not in msg
        assert "Firefox" not in msg


# ---------------------------------------------------------------------------
# Capability probes and cookie source: the evidence a yt-dlp report needs
# ---------------------------------------------------------------------------


class TestCapabilityProbeLogging:
    """A swallowed capability probe must leave a record.

    ``--js-runtimes`` and ``--remote-components`` are dropped silently when the
    probe fails, and the user then sees "n challenge solving failed" with no
    hint that the flag that would have fixed it was never passed.
    """

    def test_missing_binary_logs_a_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        ytdlp_supports_js_runtimes.cache_clear()
        try:
            with (
                caplog.at_level(logging.WARNING, logger="anki_miner.services.ytdlp_invocation"),
                patch("subprocess.run", side_effect=FileNotFoundError("no such file: yt-dlp")),
            ):
                assert ytdlp_supports_js_runtimes("/nowhere/yt-dlp") is False
        finally:
            ytdlp_supports_js_runtimes.cache_clear()
        assert "yt-dlp capability probe failed" in caplog.text
        assert "flag=--js-runtimes" in caplog.text
        assert "path=/nowhere/yt-dlp" in caplog.text
        assert "FileNotFoundError" in caplog.text
        assert "no such file" in caplog.text

    def test_timeout_logs_a_warning_for_its_own_flag(self, caplog: pytest.LogCaptureFixture) -> None:
        ytdlp_supports_remote_components.cache_clear()
        try:
            with (
                caplog.at_level(logging.WARNING, logger="anki_miner.services.ytdlp_invocation"),
                patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=30)),
            ):
                assert ytdlp_supports_remote_components("/slow/yt-dlp") is False
        finally:
            ytdlp_supports_remote_components.cache_clear()
        assert "flag=--remote-components" in caplog.text
        assert "TimeoutExpired" in caplog.text

    def test_success_records_both_capabilities(self, caplog: pytest.LogCaptureFixture) -> None:
        help_text = MagicMock(returncode=0, stdout="--js-runtimes RUNTIMES\n--paths TYPES\n", stderr="")
        ytdlp_supports_js_runtimes.cache_clear()
        try:
            with (
                caplog.at_level(logging.DEBUG, logger="anki_miner.services.ytdlp_invocation"),
                patch("subprocess.run", return_value=help_text),
            ):
                assert ytdlp_supports_js_runtimes("/opt/yt-dlp") is True
        finally:
            ytdlp_supports_js_runtimes.cache_clear()
        assert "yt-dlp capability: js_runtimes=True remote_components=False path=/opt/yt-dlp" in caplog.text


class TestCookieSourceLogging:
    """Which cookie source a run used is the first question of a cookie bug."""

    def test_file_source(self, caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
        cookies = tmp_path / "cookies.txt"
        config = AnkiMinerConfig(youtube_cookies_file=cookies)
        with caplog.at_level(logging.INFO, logger="anki_miner.services.ytdlp_invocation"):
            assert cookie_args(config) == ["--cookies", str(cookies)]
        # The cookie FILE PATH is a path, not a secret (locked decision).
        assert f"yt-dlp cookie source: source=file value={cookies}" in caplog.text

    def test_browser_source(self, caplog: pytest.LogCaptureFixture) -> None:
        config = AnkiMinerConfig(youtube_cookies_from_browser="firefox")
        with caplog.at_level(logging.INFO, logger="anki_miner.services.ytdlp_invocation"):
            assert cookie_args(config) == ["--cookies-from-browser", "firefox"]
        assert "yt-dlp cookie source: source=browser value=firefox" in caplog.text

    def test_no_source(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="anki_miner.services.ytdlp_invocation"):
            assert cookie_args(AnkiMinerConfig()) == []
        assert "yt-dlp cookie source: source=none value=-" in caplog.text
