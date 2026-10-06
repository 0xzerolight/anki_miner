"""The API's wire vocabulary: schema, commands, stable error codes (API.md)."""

from __future__ import annotations

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.anki_service import is_transient_anki_transport_error

API_SCHEMA = 1
COMMANDS = ("mine", "check", "version", "profiles", "settings-export", "render", "media", "settings-import", "setup")
#: What this build adds beyond 3.5.0's API, by stable name (API.md, "version"). One name per
#: addition, appended when it ships; a caller checks a name instead of comparing versions.
FEATURES = (
    "sentence-rules-off",
    "bold-target",
    "named-words-whitelisted",
    "script-fold",
    "filter-names",
    "word-from-line",
    "dry-run",
    "render",
    "media",
    "settings-import",
    "setup",
)

BUSY = "BUSY"
ANKI_UNREACHABLE = "ANKI_UNREACHABLE"
SETUP_ERROR = "SETUP_ERROR"  # the proposal's smaller version of the six setup codes
PROFILE_UNREADABLE = "PROFILE_UNREADABLE"
SUBTITLE_UNREADABLE = "SUBTITLE_UNREADABLE"
VIDEO_UNREADABLE = "VIDEO_UNREADABLE"
BAD_RUN_FILE = "BAD_RUN_FILE"
BAD_ARGUMENTS = "BAD_ARGUMENTS"  # not in the proposal: a command line that does not parse
MINING_FAILED = "MINING_FAILED"  # not in the proposal: the pipeline returned a failure
CANCELLED = "CANCELLED"  # a run, or a whole call (setup)
YOUTUBE_REFUSED = "YOUTUBE_REFUSED"  # fetch: a video Video -> YouTube refuses before downloading
FETCH_FAILED = "FETCH_FAILED"  # fetch: the probe, download or subtitle step failed
INTERNAL = "INTERNAL"


class ApiError(Exception):
    """A refusal or failure with its stable code; ``message`` is the English text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def setup_failure(exc: BaseException) -> ApiError:
    """A preflight exception as its code: a dead connection is ANKI_UNREACHABLE, the rest SETUP_ERROR."""
    if isinstance(exc, AnkiConnectionError) and is_transient_anki_transport_error(exc):
        return ApiError(ANKI_UNREACHABLE, str(exc))
    return ApiError(SETUP_ERROR, str(exc))


def run_verdict(run_id: str, *, error: ApiError | None = None, file: str | None = None) -> dict[str, object]:
    """One entry of a verdict's ``runs``."""
    return {
        "run_id": run_id,
        "ok": error is None,
        "error": error.code if error else None,
        "message": error.message if error else None,
        "file": file,
    }
