"""YouTube fetcher exceptions."""

from .base import AnkiMinerException


class YouTubeFetchError(AnkiMinerException):
    """Base class for YouTube fetch failures."""

    pass


class FfmpegNotFoundError(AnkiMinerException):
    """Raised when ffmpeg cannot be located during preflight."""

    pass


class BotDetectionError(YouTubeFetchError):
    """Raised when yt-dlp hits the 'sign in to confirm' anti-bot flow."""

    pass


class CookieDatabaseLockedError(YouTubeFetchError):
    """Raised when yt-dlp cannot read a cookies-from-browser database.

    Covers all three ways that source fails, which
    ``ytdlp_invocation._COOKIE_MARKERS`` tells apart and ``cookie_failure_message``
    gives distinct remedies for: the database is held open by the running browser
    (the literal locked case), its contents cannot be decrypted, or no database
    exists for the configured browser at all. One exception type serves all three
    because every caller treats them identically — each is deterministic, so each
    sits in the queue worker's ``_DETERMINISTIC_FETCH_ERRORS`` and none is worth a
    retry. The remedy the user needs lives in the message, not the type.
    """

    pass


class VideoTooLongError(YouTubeFetchError):
    """Raised when a video's duration exceeds the configured maximum."""

    pass


class YtdlpNotFoundError(YouTubeFetchError):
    """Raised when the yt-dlp executable cannot be located/executed.

    A specific subclass so callers can catch the "binary missing" case and
    steer the user to Settings → YouTube → Update yt-dlp now.
    """

    pass


class NoJapaneseSubtitlesError(YouTubeFetchError):
    """Raised when yt-dlp exited 0 but wrote no Japanese subtitle file.

    yt-dlp reports "There are no subtitles for the requested languages" as an
    *info* line and exits 0, and it writes subtitles before the video, so the whole
    video downloads after yt-dlp already knew there was nothing to write.

    This is a deterministic failure: retrying downloads the same video a second time
    and fails identically. ``YouTubeQueueWorker`` catches this subclass ahead of its
    generic ``YouTubeFetchError`` retry so the second download never happens.

    Deliberately a *subclass* of :class:`YouTubeFetchError`, unlike
    :class:`FfmpegNotFoundError` which opts out of the retry by not inheriting from
    it: ``YouTubeFetchError`` is the documented catch-all for ``fetch_video`` and
    ``process_youtube_url``, so a sibling would leak past every caller that relies on
    it. Except clauses are matched in order, which is what makes the narrower catch
    work.
    """

    pass


class DubAudioUnavailableError(YouTubeFetchError):
    """Raised on the ``auto_dub`` route when no format matches the pinned JA-audio selector.

    Deterministic — a retry re-downloads the video and fails identically, since the
    dub track (or, if the selector's video side is at fault, the video itself) is
    simply gone from what yt-dlp reports, not a transient server hiccup.

    Deliberately a *subclass* of :class:`YouTubeFetchError` for the same reason as
    :class:`NoJapaneseSubtitlesError`: it must still satisfy every caller's
    catch-all while opting out of the queue worker's retry ahead of it.
    """

    pass
