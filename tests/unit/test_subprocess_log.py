"""Tests for subprocess argv masking and command result logging."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from anki_miner.utils.subprocess_log import (
    STDERR_TAIL_LINES,
    log_command,
    log_command_result,
    mask_argv,
    tail_for_log,
)

_LOGGER_NAME = "anki_miner.tests.subprocess_log"
logger = logging.getLogger(_LOGGER_NAME)


@pytest.fixture
def at_debug(caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        yield caplog


def test_mask_argv_masks_credentials_but_keeps_paths_and_urls() -> None:
    masked = mask_argv(
        [
            "yt-dlp",
            "--cookies",
            "/home/u/c.txt",
            "--username",
            "me",
            "--password=pw",
            "https://u:p@proxy/x",
            "URL",
        ]
    )

    assert masked == [
        "yt-dlp",
        "--cookies",
        "/home/u/c.txt",
        "--username",
        "<masked>",
        "--password=<masked>",
        "https://proxy/x",
        "URL",
    ]


def test_mask_argv_masks_every_credential_flag() -> None:
    argv = [
        "yt-dlp",
        "--ap-username",
        "u",
        "--ap-password",
        "p",
        "--video-password",
        "v",
        "--client-certificate-password",
        "c",
    ]

    assert mask_argv(argv) == [
        "yt-dlp",
        "--ap-username",
        "<masked>",
        "--ap-password",
        "<masked>",
        "--video-password",
        "<masked>",
        "--client-certificate-password",
        "<masked>",
    ]


def test_mask_argv_renders_path_like_and_odd_tokens_verbatim() -> None:
    masked = mask_argv([Path("/opt/ffmpeg"), "-f", "bestvideo+bestaudio/best", "C:\\Users\\u\\v.mkv", "[]:"])

    assert masked == ["/opt/ffmpeg", "-f", "bestvideo+bestaudio/best", "C:\\Users\\u\\v.mkv", "[]:"]


def test_log_command_records_masked_argv_cwd_and_timeout(at_debug: pytest.LogCaptureFixture) -> None:
    log_command(logger, "yt-dlp", ["yt-dlp", "--password", "pw", "https://y/x"], cwd="/tmp/w", timeout_s=30.0)

    text = at_debug.text
    assert "yt-dlp: argv=" in text
    assert "pw" not in text
    assert "<masked>" in text
    assert "cwd=/tmp/w" in text
    assert "timeout=30.0s" in text
    assert at_debug.records[-1].levelno == logging.DEBUG


def test_log_command_renders_missing_cwd_and_timeout_as_dash(at_debug: pytest.LogCaptureFixture) -> None:
    log_command(logger, "alass", ["alass", "a.srt"])

    assert "cwd=-" in at_debug.text
    assert "timeout=-s" in at_debug.text


def test_log_command_result_failure_header_carries_tail(at_debug: pytest.LogCaptureFixture) -> None:
    log_command_result(
        logger,
        "yt-dlp",
        ["yt-dlp", "--password", "pw", "URL"],
        returncode=1,
        state="failed",
        stderr_tail=["ERROR: boom", "second line"],
        elapsed_s=1.5,
        level=logging.WARNING,
    )

    record = at_debug.records[-1]
    assert record.levelno == logging.WARNING
    message = record.getMessage()
    assert message.startswith("yt-dlp failed: state=failed rc=1 elapsed=1.50s argv=")
    assert "pw" not in message
    assert message.endswith("\n  ERROR: boom\n  second line")


def test_log_command_result_failure_without_tail_has_no_trailing_lines(
    at_debug: pytest.LogCaptureFixture,
) -> None:
    log_command_result(
        logger,
        "ffmpeg",
        ["ffmpeg", "-i", "in.mkv"],
        returncode=None,
        state="timed-out",
        elapsed_s=9.0,
        level=logging.WARNING,
    )

    message = at_debug.records[-1].getMessage()
    assert "ffmpeg failed: state=timed-out rc=- elapsed=9.00s argv=" in message
    assert "\n" not in message


def test_log_command_result_success_is_a_debug_ok_line(at_debug: pytest.LogCaptureFixture) -> None:
    log_command_result(
        logger,
        "ffmpeg",
        ["ffmpeg", "-i", "in.mkv"],
        returncode=0,
        state="completed",
        elapsed_s=0.25,
        level=logging.DEBUG,
    )

    record = at_debug.records[-1]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == "ffmpeg ok: rc=0 elapsed=0.25s"


def test_tail_for_log_keeps_the_last_lines_and_drops_blanks() -> None:
    text = "\n".join(f"line {index}" for index in range(60)) + "\n\n"

    tail = tail_for_log(text)

    assert len(tail) == STDERR_TAIL_LINES
    assert tail[0] == "line 40"
    assert tail[-1] == "line 59"


def test_tail_for_log_applies_the_noise_filter() -> None:
    text = "keep 1\n[x] noise\nkeep 2\r\n[x] more noise\n"

    tail = tail_for_log(text, noise_filter=lambda line: line.startswith("[x]"))

    assert tail == ["keep 1", "keep 2"]


def test_tail_for_log_of_empty_text_is_empty() -> None:
    assert tail_for_log("") == []
    assert tail_for_log("   \n\n") == []
